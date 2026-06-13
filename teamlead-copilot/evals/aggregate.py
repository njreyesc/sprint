#!/usr/bin/env python3
"""Аггрегатор прогонов teamlead-copilot.

Обходит <runs_dir>, собирает grading.json по всем прогонам, считает:
  - pass_rate mean/stddev/min/max по конфигурациям;
  - bootstrap-95% CI для каждой конфигурации и для ДЕЛЬТЫ (with−without) — основная метрика;
  - эмпирические флаги: non-discriminating ассершены (проходят одинаково в обеих конфигурациях),
    flaky/high-variance evals и ассершены-флипперы.

Пишет:
  <runs_dir>/benchmark.json   — схема skill-creator (viewer читает имена полей буквально)
  <runs_dir>/assertion_discrimination.json — таблица по ассершенам

Bootstrap детерминирован (фиксированный seed), чтобы CI воспроизводился.

Использование:
  python aggregate.py <runs_dir> [--evals-dir <dir>] [--B 10000]
"""

import argparse
import json
import random
import statistics as st
from collections import defaultdict
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def find_gradings(runs_dir: Path):
    out = []
    for g in runs_dir.rglob("grading.json"):
        d = load_json(g)
        if d and "configuration" in d:
            d["_dir"] = str(g.parent)
            out.append(d)
    return out


def summ(values):
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {
        "mean": round(st.mean(values), 4),
        "stddev": round(st.pstdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "n": len(values),
    }


def bootstrap_ci(values, B, rng, agg=st.mean):
    if not values:
        return [0.0, 0.0]
    n = len(values)
    stats = []
    for _ in range(B):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(agg(sample))
    stats.sort()
    lo = stats[int(0.025 * B)]
    hi = stats[int(0.975 * B) - 1]
    return [round(lo, 4), round(hi, 4)]


def bootstrap_delta_ci(with_vals, without_vals, B, rng):
    if not with_vals or not without_vals:
        return [0.0, 0.0]
    nw, nb = len(with_vals), len(without_vals)
    deltas = []
    for _ in range(B):
        a = st.mean(with_vals[rng.randrange(nw)] for _ in range(nw))
        b = st.mean(without_vals[rng.randrange(nb)] for _ in range(nb))
        deltas.append(a - b)
    deltas.sort()
    lo = deltas[int(0.025 * B)]
    hi = deltas[int(0.975 * B) - 1]
    return [round(lo, 4), round(hi, 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_dir")
    ap.add_argument("--evals-dir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--B", type=int, default=10000)
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    evals_doc = load_json(Path(args.evals_dir) / "evals.json")
    skill_name = evals_doc.get("skill_name", "skill")
    rng = random.Random(20260613)

    gradings = find_gradings(runs_dir)
    if not gradings:
        raise SystemExit(f"Нет grading.json под {runs_dir}. Сначала прогоны + grader.py.")

    # сырые прогоны для viewer
    runs = []
    by_config_rates = defaultdict(list)            # config -> [pass_rate per run]
    # per assertion: (eval_id, assertion_id) -> config -> [passed bool]
    per_assert = defaultdict(lambda: defaultdict(list))
    assert_text = {}
    assert_fm = {}
    assert_expected = {}

    for g in gradings:
        cfg = g["configuration"]
        pr = g["summary"]["pass_rate"]
        by_config_rates[cfg].append(pr)
        runs.append({
            "eval_id": g.get("eval_id"),
            "eval_name": g.get("eval_name"),
            "configuration": cfg,
            "run_number": g.get("run_number"),
            "result": {
                "pass_rate": pr,
                "passed": g["summary"]["passed"],
                "failed": g["summary"]["failed"],
                "total": g["summary"]["total"],
            },
            "expectations": [
                {"text": e["text"], "passed": e["passed"], "evidence": e["evidence"]}
                for e in g["expectations"]
            ],
        })
        for e in g["expectations"]:
            key = (g.get("eval_id"), e.get("assertion_id"))
            per_assert[key][cfg].append(bool(e["passed"]))
            assert_text[key] = e["text"]
            assert_fm[key] = e.get("failure_mode")
            assert_expected[key] = e.get("expected_discriminating")

    # сводка по конфигам
    run_summary = {}
    for cfg, rates in by_config_rates.items():
        run_summary[cfg] = {
            "pass_rate": {**summ(rates), "ci95_bootstrap": bootstrap_ci(rates, args.B, rng)},
        }

    w = by_config_rates.get("with_skill", [])
    b = by_config_rates.get("without_skill", [])
    delta_mean = round((st.mean(w) if w else 0) - (st.mean(b) if b else 0), 4)
    delta_ci = bootstrap_delta_ci(w, b, args.B, rng)
    run_summary["delta"] = {
        "pass_rate": f"{'+' if delta_mean >= 0 else ''}{delta_mean}",
        "pass_rate_ci95_bootstrap": delta_ci,
        "significant": (delta_ci[0] > 0 or delta_ci[1] < 0),
    }

    # дискриминация по ассершенам
    discrimination = []
    notes = []
    for key, cfgmap in sorted(per_assert.items(), key=lambda kv: (kv[0][0] or 0, str(kv[0][1]))):
        eval_id, aid = key
        wv = cfgmap.get("with_skill", [])
        bv = cfgmap.get("without_skill", [])
        w_frac = round(sum(wv) / len(wv), 3) if wv else None
        b_frac = round(sum(bv) / len(bv), 3) if bv else None
        delta = None if (w_frac is None or b_frac is None) else round(w_frac - b_frac, 3)
        # non-discriminating: проходит (или падает) одинаково в обеих конфигурациях
        non_disc = (delta is not None and abs(delta) < 0.1)
        # flaky: доля прохождения в середине (флиппер) хотя бы в одной конфигурации
        def flippy(v):
            if not v:
                return False
            f = sum(v) / len(v)
            return 0.2 <= f <= 0.8
        flaky = flippy(wv) or flippy(bv)
        rec = {
            "eval_id": eval_id, "assertion_id": aid, "text": assert_text[key],
            "failure_mode": assert_fm[key],
            "with_skill_pass_frac": w_frac, "without_skill_pass_frac": b_frac,
            "delta": delta,
            "expected_discriminating": assert_expected[key],
            "non_discriminating": non_disc,
            "flaky_candidate": flaky,
        }
        discrimination.append(rec)
        if non_disc:
            notes.append(f"NON-DISCRIMINATING: ассершен '{aid}' (eval {eval_id}) проходит одинаково в обеих конфигурациях (Δ={delta}) — ничего не измеряет про ценность скилла.")
        if flaky:
            notes.append(f"FLAKY: ассершен '{aid}' (eval {eval_id}) — высокая дисперсия прохождения (with={w_frac}, without={b_frac}). Кандидат: чинить метрику или растить N.")

    # flaky по eval-уровню (дисперсия pass_rate)
    eval_rates = defaultdict(lambda: defaultdict(list))
    for g in gradings:
        eval_rates[g.get("eval_id")][g["configuration"]].append(g["summary"]["pass_rate"])
    for eid, cfgmap in sorted(eval_rates.items(), key=lambda kv: kv[0] or 0):
        for cfg, rates in cfgmap.items():
            if len(rates) > 1 and st.pstdev(rates) > 0.25:
                notes.append(f"FLAKY EVAL: eval {eid}/{cfg} — pass_rate stddev={round(st.pstdev(rates),3)} (>0.25). Кандидат на flaky.")

    n_per_cfg = {cfg: len(r) for cfg, r in by_config_rates.items()}
    benchmark = {
        "metadata": {
            "skill_name": skill_name,
            "primary_metric": "delta_pass_rate_vs_baseline",
            "runs_per_configuration": max(n_per_cfg.values()) if n_per_cfg else 0,
            "runs_counted_per_config": n_per_cfg,
            "ci": "bootstrap_95",
            "bootstrap_B": args.B,
        },
        "runs": sorted(runs, key=lambda r: (r["eval_id"] or 0, r["configuration"], r["run_number"] or 0)),
        "run_summary": run_summary,
        "assertion_discrimination": discrimination,
        "notes": notes,
    }

    (runs_dir / "benchmark.json").write_text(json.dumps(benchmark, ensure_ascii=False, indent=2), encoding="utf-8")
    (runs_dir / "assertion_discrimination.json").write_text(json.dumps(discrimination, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Прогонов: {len(gradings)} | per-config: {n_per_cfg}")
    print(f"with_skill pass_rate mean={run_summary.get('with_skill',{}).get('pass_rate',{}).get('mean')}")
    print(f"without_skill pass_rate mean={run_summary.get('without_skill',{}).get('pass_rate',{}).get('mean')}")
    print(f"DELTA pass_rate={run_summary['delta']['pass_rate']} CI95={delta_ci} significant={run_summary['delta']['significant']}")
    print(f"non-discriminating: {sum(1 for d in discrimination if d['non_discriminating'])} | flaky-candidates: {sum(1 for d in discrimination if d['flaky_candidate'])}")
    print(f"→ {runs_dir/'benchmark.json'}")


if __name__ == "__main__":
    main()
