# Eval-харнесс teamlead-copilot

Дисциплина: **failure modes → baseline → sample size → metric → статсиг.** Reliability > capability.
Скилл — runbook для LLM-агента поверх трекера (Jira/MCP), поэтому большинство инвариантов
доверия проверяются **программно** (provenance/структура), а не «на глаз».

## Что где

| Файл | Назначение |
|---|---|
| `evals.json` | 10 промптов (6 core + 4 guardrail), у каждого — именованные ассершены с `check`, `failure_mode`, `expected_discriminating` |
| `fixtures/` | mock tracker-mcp (Jira-семантика): `tracker_active.json`, `cold_start.json`, `mcp_down.json`, `now.json` (инъектируемое «сейчас») |
| `grader.py` | программный грейдер: гоняет `check`-функции против артефактов прогона + `derived_ground_truth` фикстуры → `grading.json` |
| `aggregate.py` | bootstrap-95% CI, **дельта pass_rate (with−without)**, флаги non-discriminating / flaky → `benchmark.json` |
| `eval-viewer/` | стандартный `generate_review.py` + `viewer.html` (skill-creator, не кастомный HTML) |
| `selftest/` | синтетические good/bad прогоны — валидируют, что ассершены **дискриминируют** и грейдер не падает (НЕ бенчмарк скилла) |

## Failure modes → ассершены

FM1 выдуманный id/факт · FM2 догадка вместо плейсхолдера · FM3 stall по статусу/в календарных днях ·
FM4 запись без подтверждения / необратимое пачкой · FM5 утечка сырого PII · FM6 цикл блокеров ·
FM7 cold-start velocity · FM8 граница покрытия / отказ подменён пустой сводкой · FM9 near-miss триггер.
Один ассершен — один режим (см. поле `failure_mode` в `evals.json`).

## Контракт артефактов прогона (это пишет раннер)

Каждый прогон — каталог `runs/eval<ID>/<config>/trial<K>/`:

```
eval_metadata.json   {eval_id, eval_name, configuration: with_skill|without_skill, run_number, triggered?}
outputs/response.md      user-facing ответ агента (после re-hydration; PII внутри периметра допустим)
outputs/llm_payload.json ["...маскированный текст, реально ушедший в модель..."]   (опц.)
outputs/actions.json     [{op,target,status: draft|confirmed|...,reversible,batched}]  (опц.)
```

Семантика отсутствия:
- нет `llm_payload.json` → редакции **не было** → грейдер считает, что в модель ушёл сырой текст
  карточек (фикстура) → PII-ассершен падает. Это и моделирует baseline-утечку.
- нет `actions.json` → мутаций не заявлено (для дейли/поиска — норма).

## Как прогнать (когда решишь запускать живые прогоны)

Раннер не зашит намеренно: его роль — воспроизвести два конфига для каждого промпта × N.

**with_skill:** дай суб-агенту `SKILL.md` + `references/*` + содержимое нужной фикстуры
(как ответ tracker-mcp) + `now.json` + промпт. Попроси записать артефакты по контракту выше.

**without_skill (baseline):** тот же промпт + та же фикстура, **без** SKILL.md/references —
голый агент. Те же артефакты.

Шаблоны промптов раннера — в `runner_prompts.md`.

```bash
# 1) (опц.) валидация грейдера
python3 selftest/build_selftest.py
for d in $(find selftest/runs -type d -name 'trial*'); do python3 grader.py "$d"; done
python3 aggregate.py selftest/runs           # ждём DELTA > 0, significant=True

# 2) живые прогоны: разложить артефакты в runs/eval<ID>/<config>/trial<K>/ (N=10 на конфиг)
# 3) грейдинг + аггрегация
for d in $(find runs -type d -name 'trial*'); do python3 grader.py "$d"; done
python3 aggregate.py runs --B 10000

# 4) ревью
python3 eval-viewer/generate_review.py runs --skill-name teamlead-copilot \
        --benchmark runs/benchmark.json
# или статический файл:
python3 eval-viewer/generate_review.py runs --benchmark runs/benchmark.json --static runs/review.html
```

## Метрика и статсиг

- Основная: **дельта pass_rate = mean(with_skill) − mean(without_skill)**, с bootstrap-95% CI
  (`aggregate.py`, seed фиксирован). `significant=True`, если CI дельты не пересекает 0.
- `assertion_discrimination.json`: по каждому ассершену доля прохождения в обоих конфигах,
  Δ, флаги `non_discriminating` (|Δ|<0.1 — ничего не измеряет) и `flaky_candidate`
  (доля прохождения в 0.2–0.8 — чинить метрику или растить N).
- N=10 — дефолт; high-variance evals (stddev pass_rate > 0.25) аггрегатор помечает в `notes`.

## Что переиспользуется

`generate_review.py` + `viewer.html` и схема `benchmark.json`/`grading.json` — из skill-creator,
без кастомного HTML. Фикстуры — единый источник истины для всех режимов (как `references/` у скилла).
