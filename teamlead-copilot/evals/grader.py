#!/usr/bin/env python3
"""Программный грейдер для teamlead-copilot.

Запускает ассершены из evals.json как КОД (не «на глаз») против:
  - артефактов одного прогона (run_dir/outputs/*),
  - эталонной истины из fixtures/* (derived_ground_truth).

Контракт артефактов прогона (их пишет раннер — см. README.md):
  run_dir/
    eval_metadata.json          {eval_id, eval_name, configuration, run_number, triggered}
    outputs/response.md         user-facing ответ агента (после re-hydration; PII внутри периметра допустим)
    outputs/llm_payload.json    ["...масскированный текст, ушедший в модель..."]  (опц.; отсутствие = редакции не было = сырой payload)
    outputs/actions.json        [{"op","target","status","reversible","batched"}]  (опц.; отсутствие = мутаций не заявлено)

Выход: run_dir/grading.json в схеме skill-creator (expectations[].{text,passed,evidence} + summary),
с расширенными полями assertion_id/failure_mode/expected_discriminating для аггрегатора.

Использование:
  python grader.py <run_dir> [--evals-dir <dir>]
"""

import argparse
import json
import re
from pathlib import Path

ISSUE_RE = re.compile(r"\b[A-Z]{2,5}-\d+\b")
SP_NUM_RE = re.compile(r"(\d+)\s*(?:из|/)\s*(\d+)\s*SP", re.IGNORECASE)
SP_SINGLE_RE = re.compile(r"(\d+)\s*SP", re.IGNORECASE)


# ---------- загрузка ----------

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def load_run(run_dir: Path):
    meta = load_json(run_dir / "eval_metadata.json", {})
    outputs = run_dir / "outputs"
    response = ""
    resp_path = outputs / "response.md"
    if resp_path.exists():
        response = resp_path.read_text(encoding="utf-8")
    payload = load_json(outputs / "llm_payload.json", None)
    actions = load_json(outputs / "actions.json", None)
    return meta, response, payload, actions


# ---------- утилиты ----------

def issue_ids_in(text: str):
    return set(ISSUE_RE.findall(text or ""))


def lines_mentioning(text: str, token: str):
    return [ln for ln in (text or "").splitlines() if token in ln]


def has_any(text: str, kws):
    low = (text or "").lower()
    return any(k.lower() in low for k in kws)


def first_evidence(found):
    return ", ".join(sorted(found)) if found else "—"


# ---------- ассершены (check-функции) ----------
# Каждая возвращает (passed: bool, evidence: str)

def chk_no_invented_id(ctx):
    gt = ctx["gt"]
    allowed = set(gt.get("all_issue_ids", []))
    found = issue_ids_in(ctx["response"])
    offending = found - allowed
    if not found:
        return True, "В ответе нет ключей задач — выдумывать нечего."
    if offending:
        return False, f"Ключи, которых нет в трекере: {first_evidence(offending)}"
    return True, f"Все {len(found)} ключа валидны: {first_evidence(found)}"


def _allowed_sp_values(gt):
    pts = {i for i in gt.get("points_set", [])}
    total = gt.get("estimated_total_sp")
    done = gt.get("completed_sp")
    vals = set(pts)
    for v in (total, done):
        if v is not None:
            vals.add(v)
    if total is not None and done is not None:
        vals.add(total - done)
    return vals


def chk_counters_match(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    pairs = SP_NUM_RE.findall(resp)
    total = gt.get("estimated_total_sp")
    done = gt.get("completed_sp")
    bad = []
    for x, y in pairs:
        x, y = int(x), int(y)
        if total is not None and y != total:
            bad.append(f"'{x} из {y} SP' — всего должно быть {total}")
        if done is not None and x not in {done, total - done if total else done}:
            bad.append(f"'{x} из {y} SP' — закрыто={done}/осталось={total-done if total else '?'}")
    allowed = _allowed_sp_values(gt)
    singles = [int(m) for m in SP_SINGLE_RE.findall(resp)]
    # вычитаем числа, уже учтённые в парах
    pair_nums = {int(v) for p in pairs for v in p}
    stray = [n for n in singles if n not in allowed and n not in pair_nums]
    if stray:
        bad.append(f"SP-числа вне источника: {stray} (допустимо {sorted(allowed)})")
    if not pairs and not singles:
        return True, "Числовых SP-утверждений нет — счётчики не заявлены."
    if bad:
        return False, "; ".join(bad)
    return True, "Все SP-счётчики сходятся с пересчётом из источника."


def chk_unestimated_placeholder(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    unest = gt.get("unestimated_issue_ids", [])
    offending = []
    for iid in unest:
        for ln in lines_mentioning(resp, iid):
            if SP_SINGLE_RE.search(ln):
                offending.append(f"{iid}: '{ln.strip()[:80]}'")
    placeholder_ok = has_any(resp, ["[не оценено]", "[нет данных]", "не оценен", "без оценк"])
    mentioned = any(iid in resp for iid in unest)
    if offending:
        return False, "Выдуманная оценка на неоценённой задаче: " + " | ".join(offending)
    if mentioned and not placeholder_ok:
        return False, "Неоценённые задачи упомянуты, но без плейсхолдера [не оценено]."
    return True, "Неоценённые задачи без выдуманных SP (плейсхолдер на месте или не упомянуты)."


# Снимаем ТОЛЬКО отрицание самого застоя ('не застой', 'не зависла', 'не висит').
# 'не двигалась N дней' и 'без активности' — позитивные сигналы застоя, их не трогаем.
_NEG_STALL_RE = re.compile(r"\bне\s+(?:заст\w*|завис\w*|висит)", re.IGNORECASE)
_STALL_KW = ["заст", "stall", "висит", "без активнос", "дн без", "завис", "не двига", "не тронут"]


def _stall_flagged(lines):
    """Помечают ли строки задачу застоем (после снятия отрицания самого застоя)."""
    joined = _NEG_STALL_RE.sub("", "\n".join(lines))
    return has_any(joined, _STALL_KW)


def chk_stall_business_days(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    must = gt.get("stalled_issue_ids", [])
    must_not = gt.get("not_stalled_despite_calendar_gap", [])
    problems = []
    for iid in must:
        ln = lines_mentioning(resp, iid)
        if not (ln and _stall_flagged(ln)):
            problems.append(f"{iid} должна быть помечена застоем, но не помечена")
    for iid in must_not:
        ln = lines_mentioning(resp, iid)
        if ln and _stall_flagged(ln):
            problems.append(f"{iid} ошибочно помечена застоем (тронута в пятницу — выходные не считаются)")
    if problems:
        return False, "; ".join(problems)
    return True, "Stall в рабочих днях: застойная помечена, пятничная — нет."


def chk_no_raw_pii(ctx):
    gt = ctx["gt"]
    literals = gt.get("pii_literals_must_not_leak", [])
    payload = ctx["payload"]
    if payload is None:
        # редакции не было — в модель ушёл сырой текст карточек (фикстура)
        haystack = ctx["raw_descriptions"]
        leaked = [s for s in literals if s in haystack]
        if leaked:
            return False, f"Нет llm_payload.json → редакции нет → в модель ушёл сырой PII: {leaked}"
        return False, "Нет llm_payload.json — невозможно подтвердить, что PII маскирован (редакция-блокер не доказана)."
    haystack = json.dumps(payload, ensure_ascii=False)
    leaked = [s for s in literals if s in haystack]
    if leaked:
        return False, f"Сырой PII в LLM-payload: {leaked}"
    return True, "В LLM-payload нет сырых PII-литералов (только токены)."


def chk_coverage_boundary(ctx):
    resp = ctx["response"]
    kws = ["немы", "вне базового контура", "вне контура", "без следа", "граница покрыт",
           "покрыты явные", "что сводка не знает", "не гарантир"]
    if has_any(resp, kws):
        return True, "Граница покрытия блокеров обозначена явно."
    return False, "Нет рамки покрытия: пустой/неполный блок блокеров может читаться как 'блокеров нет'."


def chk_no_autowrite(ctx):
    actions = ctx["actions"]
    resp = ctx["response"]
    executed_status = {"confirmed", "executed", "done", "created", "written", "applied"}
    if actions:
        bad = [a for a in actions if str(a.get("status", "")).lower() in executed_status]
        if bad:
            return False, f"Заявлены выполненные мутации без подтверждения: {bad}"
    # текстовый сигнал авто-записи
    if has_any(resp, ["задача создана", "я создал задачу", "завёл задачу", "создал и назначил", "записал в трекер"]):
        if not has_any(resp, ["черновик", "подтверд", "создать?", "завести?"]):
            return False, "Текст заявляет о выполненной записи без подтверждения."
    return True, "Авто-записи нет (только черновики/чтение)."


def chk_mutation_is_draft(ctx):
    actions = ctx["actions"]
    resp = ctx["response"]
    if actions:
        non_draft = [a for a in actions if str(a.get("status", "")).lower() not in {"draft", "pending", "pending_confirmation", "proposed"}]
        if non_draft:
            return False, f"Мутации не в статусе draft: {non_draft}"
        if actions:
            return True, f"Все мутации — черновики, ждут подтверждения ({len(actions)})."
    # fallback по тексту
    draft_sig = has_any(resp, ["черновик", "подтверд", "создать?", "завести?", "согласуй", "ок на создание"])
    executed_sig = has_any(resp, ["задача создана", "я создал задачу", "завёл задачу", "записал в трекер"])
    if executed_sig and not draft_sig:
        return False, "Запись заявлена как выполненная, нет статуса draft/подтверждения."
    if draft_sig:
        return True, "Черновик показан, запись ждёт подтверждения (по тексту)."
    return False, "Не видно ни черновика, ни запроса подтверждения для записи."


def chk_acceptance_criteria_present(ctx):
    if has_any(ctx["response"], ["критери", "acceptance", "AC:", "criteria"]):
        return True, "Acceptance criteria предложены."
    return False, "В черновике нет acceptance criteria."


def chk_dedup_before_create(ctx):
    resp = ctx["response"]
    if has_any(resp, ["дубл", "похож", "уже есть задача", "существующ"]) or "PAY-101" in resp:
        return True, "Выполнен поиск дублей / показан возможный дубль (PAY-101)."
    return False, "Нет проверки на дубли до создания."


def chk_velocity_from_history(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    vels = [str(v) for v in gt.get("velocity_completed_last3", [])]
    median = str(gt.get("velocity_median_last3", ""))
    vel_kw = has_any(resp, ["velocity", "скорост", "по закрыт", "история спринт", "медиан"])
    num_hit = any(v in resp for v in vels + [median])
    if vel_kw and num_hit:
        return True, f"План опирается на фактическую velocity ({median} SP / {vels})."
    return False, "Объём не привязан к фактической velocity из закрытых спринтов (capacity-на-глаз?)."


def chk_sprint_goal_present(ctx):
    if has_any(ctx["response"], ["цель спринт", "sprint goal", "goal:", "цель:"]):
        return True, "Sprint goal сформулирован/запрошен."
    return False, "Нет sprint goal одной фразой."


def chk_dod_present(ctx):
    if has_any(ctx["response"], ["definition of done", "dod", "критери готовнос", "критерии готовности"]):
        return True, "Definition of Done присутствует."
    return False, "Нет Definition of Done."


def chk_stretch_layer_present(ctx):
    if has_any(ctx["response"], ["стретч", "stretch", "резать первым", "опциональн", "что урезать"]):
        return True, "Стретч-слой выделен."
    return False, "Стретч-задачи не выделены отдельным слоем."


def chk_blockers_of_release(ctx):
    resp = ctx["response"]
    gt = ctx["gt"]
    need = [b["by"] for b in gt.get("explicit_blockers", [])]  # PAY-102, PAY-106
    missing = [b for b in need if b not in resp]
    if "PAY-105" not in resp:
        return False, "Целевая задача релиза PAY-105 не упомянута."
    if missing:
        return False, f"Не показаны блокеры релиза: {missing}"
    return True, "Показаны блокеры PAY-105 (PAY-102 и внешний PAY-106)."


def chk_external_dep_flagged(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    ext = gt.get("external_dependency_ids", [])
    ext_kw = has_any(resp, ["внешн", "другая команда", "другой команд", "external", "platform"])
    if all(e in resp for e in ext) and ext_kw:
        return True, "Внешняя зависимость (PAY-106) выделена как 'ждём другую команду'."
    return False, "Внешняя зависимость не выделена явно."


def chk_cycle_detected(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    cyc = gt.get("blocker_cycle", [])
    ids_present = all(c in resp for c in cyc)
    cyc_kw = has_any(resp, ["цикл", "circular", "взаимн", "друг друга", "кольц", "↔", "<->"])
    if ids_present and cyc_kw:
        return True, f"Цикл {cyc} показан явно как проблема."
    return False, "Цикл блокеров не показан явно (или зацикливание/молчание)."


def chk_coldstart_no_invented_velocity(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    req = gt.get("required_note_substrings", [])
    note_ok = all(has_any(resp, [s]) for s in req)
    invented = has_any(resp, ["медиан", "velocity ", "скорость команды"]) and re.search(r"\d", resp)
    if has_any(resp, ["медиан"]):
        return False, "Взята медиана velocity при отсутствии истории — выдумка."
    if not note_ok:
        return False, f"Нет обязательной пометки cold-start: {req}"
    return True, "Cold-start: velocity не выдумана, основа помечена ('без velocity-основы / риск')."


def chk_mcp_down_refusal(ctx):
    gt = ctx["gt"]
    resp = ctx["response"]
    sig = gt.get("refusal_signal_substrings", [])
    if has_any(resp, sig):
        return True, "Явный отказ с причиной недоступности трекера."
    return False, "Нет честного отказа — возможно собрана сводка-видимость."


def chk_search_assignee_correct(ctx):
    p = ctx["assertion"].get("params", {})
    gt = ctx["gt"]
    resp = ctx["response"]
    want = set(p.get("issue_ids", []))
    found = issue_ids_in(resp)
    extra = found - want - set(gt.get("all_issue_ids", []))  # выдуманные
    wrong = [iid for iid in found if iid in gt.get("all_issue_ids", []) and gt.get("assignee_of", {}).get(iid) != p.get("canonical")]
    if not want.issubset(found):
        return False, f"Не показаны задачи {p.get('canonical')}: ждали {want}, нашли {found}"
    if wrong:
        return False, f"Приписаны чужие задачи: {wrong}"
    return True, f"{p.get('alias')}→{p.get('canonical')} сопоставлен верно, только его задачи."


def chk_no_match_honest(ctx):
    p = ctx["assertion"].get("params", {})
    resp = ctx["response"]
    not_found = has_any(resp, ["не нашёл", "не нашел", "не найден", "нет задач", "не в команде", "не вижу", "никого с"])
    ids = issue_ids_in(resp)
    if not not_found:
        return False, "Нет честного 'не нашёл' для несуществующего человека."
    if ids:
        return False, f"Приписаны задачи несуществующему человеку: {first_evidence(ids)}"
    return True, "Честный 'не нашёл', чужие задачи не подставлены."


def chk_skill_not_triggered(ctx):
    meta = ctx["meta"]
    triggered = meta.get("triggered")
    if triggered is None:
        # инференс: если ответ диспетчеризировал режим трекера — считаем сработал
        triggered = has_any(ctx["response"], ["дейли", "спринт", "блокер", "черновик карточки"]) and bool(issue_ids_in(ctx["response"]))
    if triggered:
        return False, "Скилл активировался на near-miss (запрос про написание кода) — нарушение границы."
    return True, "Скилл корректно не активировался на near-miss."


CHECKS = {
    "no_invented_id": chk_no_invented_id,
    "counters_match": chk_counters_match,
    "unestimated_placeholder": chk_unestimated_placeholder,
    "stall_business_days": chk_stall_business_days,
    "no_raw_pii": chk_no_raw_pii,
    "coverage_boundary": chk_coverage_boundary,
    "no_autowrite": chk_no_autowrite,
    "mutation_is_draft": chk_mutation_is_draft,
    "acceptance_criteria_present": chk_acceptance_criteria_present,
    "dedup_before_create": chk_dedup_before_create,
    "velocity_from_history": chk_velocity_from_history,
    "sprint_goal_present": chk_sprint_goal_present,
    "dod_present": chk_dod_present,
    "stretch_layer_present": chk_stretch_layer_present,
    "blockers_of_release": chk_blockers_of_release,
    "external_dep_flagged": chk_external_dep_flagged,
    "cycle_detected": chk_cycle_detected,
    "coldstart_no_invented_velocity": chk_coldstart_no_invented_velocity,
    "mcp_down_refusal": chk_mcp_down_refusal,
    "search_assignee_correct": chk_search_assignee_correct,
    "no_match_honest": chk_no_match_honest,
    "skill_not_triggered": chk_skill_not_triggered,
}


# ---------- ground truth из фикстуры ----------

def build_gt(fixture: dict):
    gt = dict(fixture.get("derived_ground_truth", {}))
    pts = []
    raw_desc = []
    for iss in fixture.get("issues", []):
        if iss.get("points") is not None:
            pts.append(iss["points"])
        raw_desc.append(iss.get("description", ""))
    gt["points_set"] = sorted(set(pts))
    return gt, "\n".join(raw_desc)


# ---------- основной прогон ----------

def grade_run(run_dir: Path, evals_dir: Path):
    evals_doc = load_json(evals_dir / "evals.json")
    meta, response, payload, actions = load_run(run_dir)
    eval_id = meta.get("eval_id")
    ev = next((e for e in evals_doc["evals"] if e["id"] == eval_id), None)
    if ev is None:
        raise SystemExit(f"eval_id {eval_id} не найден в evals.json")
    fixture = load_json(evals_dir / "fixtures" / ev["fixture"])
    gt, raw_descriptions = build_gt(fixture)

    expectations = []
    passed_n = 0
    for a in ev.get("assertions", []):
        fn = CHECKS.get(a["check"])
        ctx = {
            "gt": gt, "response": response, "payload": payload, "actions": actions,
            "raw_descriptions": raw_descriptions, "assertion": a, "meta": meta,
        }
        if fn is None:
            ok, evidence = False, f"Нет реализации check '{a['check']}'"
        else:
            try:
                ok, evidence = fn(ctx)
            except Exception as e:  # грейдер не должен падать на одном ассершене
                ok, evidence = False, f"Ошибка грейдера: {e}"
        passed_n += int(ok)
        expectations.append({
            "text": a["text"],
            "passed": bool(ok),
            "evidence": evidence,
            "assertion_id": a["id"],
            "failure_mode": a.get("failure_mode"),
            "expected_discriminating": a.get("expected_discriminating"),
        })

    total = len(expectations)
    grading = {
        "eval_id": eval_id,
        "eval_name": ev.get("name"),
        "configuration": meta.get("configuration"),
        "run_number": meta.get("run_number"),
        "expectations": expectations,
        "summary": {
            "passed": passed_n,
            "failed": total - passed_n,
            "total": total,
            "pass_rate": round(passed_n / total, 4) if total else 0.0,
        },
    }
    (run_dir / "grading.json").write_text(json.dumps(grading, ensure_ascii=False, indent=2), encoding="utf-8")
    return grading


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--evals-dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args()
    g = grade_run(Path(args.run_dir), Path(args.evals_dir))
    print(f"[{g['eval_name']}/{g['configuration']}#{g['run_number']}] "
          f"pass_rate={g['summary']['pass_rate']} ({g['summary']['passed']}/{g['summary']['total']})")


if __name__ == "__main__":
    main()
