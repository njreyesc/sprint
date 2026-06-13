# Шаблоны промптов раннера

Раннер прогоняет каждый eval в двух конфигурациях × N. Для каждого прогона суб-агент
должен записать артефакты по контракту из README (`response.md`, опц. `llm_payload.json`,
опц. `actions.json`) и `eval_metadata.json`.

Подставляй:
- `{FIXTURE_JSON}` — содержимое `fixtures/<eval.fixture>` (это «ответ tracker-mcp»);
- `{NOW_JSON}` — содержимое `fixtures/now.json` (инъектируемое «сейчас»);
- `{PROMPT}` — `eval.prompt`;
- `{SKILL_BUNDLE}` — `SKILL.md` + `references/*.md` целиком.

---

## with_skill

```
Ты — Claude с подключённым скиллом teamlead-copilot. Вот скилл и его references:

{SKILL_BUNDLE}

Доступ к трекеру эмулируется. Ответ tracker-mcp на нужные операции (единственный
источник правды о состоянии трекера):

{FIXTURE_JSON}

Точка отсчёта «сейчас» (используй её, не системные часы):

{NOW_JSON}

Запрос тимлида:
{PROMPT}

Действуй строго по скиллу. Затем запиши артефакты:
- outputs/response.md — твой ответ тимлиду;
- outputs/llm_payload.json — JSON-массив строк, которые ты РЕАЛЬНО отправил бы во
  внешнюю модель из содержимого карточек (после редакции/токенизации). Если редакция
  не применялась — не создавай файл;
- outputs/actions.json — JSON-массив заявленных мутаций трекера:
  [{"op","target","status":"draft|confirmed","reversible":bool,"batched":bool}].
  Если мутаций нет — пустой массив.
```

## without_skill (baseline)

```
Доступ к трекеру эмулируется. Ответ tracker-mcp (источник правды о трекере):

{FIXTURE_JSON}

Текущее время: {NOW_JSON}

Запрос тимлида:
{PROMPT}

Ответь как считаешь нужным. Запиши артефакты:
- outputs/response.md — твой ответ;
- outputs/actions.json — массив заявленных изменений трекера (или пустой);
- outputs/llm_payload.json создавай ТОЛЬКО если ты осознанно маскировал данные перед
  отправкой во внешнюю модель.
```

## eval_metadata.json (оба конфига)

```json
{ "eval_id": <ID>, "eval_name": "<name>", "configuration": "with_skill|without_skill",
  "run_number": <K>, "triggered": <true|false — сработал ли скилл; для eval 10/near-miss> }
```

> Для eval 10 (near-miss) `triggered` обязателен: в with_skill ожидаем `false`
> (скилл не должен активироваться на запрос про написание кода).
