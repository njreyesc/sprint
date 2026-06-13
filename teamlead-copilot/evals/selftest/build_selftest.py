#!/usr/bin/env python3
"""SELF-TEST грейдера (НЕ бенчмарк скилла).

Генерирует СИНТЕТИЧЕСКИЕ артефакты прогонов: 'good' = вывод, верный инвариантам
скилла; 'bad' = вывод в стиле baseline с конкретными failure modes. Затем
grader+aggregate должны показать: good проходит ~всё, bad валит целевые ассершены,
дельта строго положительная. Это валидирует, что ассершены ДИСКРИМИНИРУЮТ и
грейдер не падает — до любых живых прогонов LLM.

Эти artefacts НЕ являются результатами модели. Каталог: selftest/runs/
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"

NAMES = {1: "Daily", 2: "CreateTask", 3: "Search", 4: "SprintPlanning", 5: "Connections",
         6: "CompositeDailyPlusCreate", 7: "ColdStartPlanning", 8: "TrackerDownDaily",
         9: "SearchNoMatchHonesty", 10: "NearMissShouldNotTrigger"}

TOKENIZED_PAYLOAD = ["⟦PII:7a3⟧, тел ⟦PII:b21⟧, счёт ⟦ACCT:c19⟧", "fix payment timeout at checkout"]

GOOD = {
    1: {
        "response": """## Дейли · спринт «Выкатить платёжный модуль v2 в прод» · день 6 из 10

### По людям
- **Алексей**: PAY-101 fix payment timeout — In Progress (тронута в пятницу)
- **Марина**: PAY-102 refund flow — In Progress
- **Игорь**: PAY-108 reconcile ledger — In Progress · закрыл вчера PAY-103
- **Даша**: PAY-104 3DS UX — To Do · оценка [не оценено]
- **Сергей**: PAY-105 release v2 — To Do
- **Нина**: PAY-107 migrate ledger — In Progress

### Блокеры и застой
- 🟡 PAY-102 — застой 4 рабочих дня (нет активности) ← свежесть под вопросом
- 🔴 PAY-105 — blocked by PAY-102 и внешним PAY-106

### Темп
- Закрыто 5 из 25 SP (2 задачи [не оценено]); риск умеренный — с поправкой на свежесть.

_Покрытие: явные блокеры + застой. Немые блокеры (без следа в трекере) — вне базового контура._
""",
        "payload": TOKENIZED_PAYLOAD, "actions": [],
    },
    2: {
        "response": """Прежде чем заводить — нашёл возможный дубль: **PAY-101 «Fix payment timeout at checkout»** (похожая задача уже в спринте). Создаём новую или дополняем PAY-101?

Черновик карточки (на подтверждение, запись ещё не выполнена):
- Заголовок: Fix payment timeout under load at checkout
- Описание: таймаут оплаты на проде под нагрузкой
- Acceptance criteria: 1) p99 latency чекаута < 2s под нагрузкой; 2) нет таймаут-ошибок в логах при пике; 3) добавлен алерт
- Приоритет: High · Исполнитель: бэкенд
""",
        "payload": TOKENIZED_PAYLOAD,
        "actions": [{"op": "createIssue", "target": "draft", "status": "draft", "reversible": True}],
    },
    3: {
        "response": """**Лёша (Алексей)** в работе: PAY-101 fix payment timeout — In Progress.
Остаток по спринту: закрыто 5 из 25 SP, осталось 20 SP (2 задачи [не оценено]).
""",
        "payload": TOKENIZED_PAYLOAD, "actions": [],
    },
    4: {
        "response": """Цель спринта: стабилизировать платёжный модуль до GA.
По фактической velocity закрытых спринтов (медиана последних трёх = 32 SP; 32/35/30) реально брать ~32 SP.
Definition of Done: задачи в Done, прод-деплой зелёный, алерты настроены.
Раскладка: P0/P1/P2. Стретч-задачи отдельным слоем — режем первыми при перерасходе.
""",
        "payload": TOKENIZED_PAYLOAD, "actions": [],
    },
    5: {
        "response": """Цепочка к релизу PAY-105 (release payments module v2):
- PAY-105 blocked by PAY-102 (refund flow)
- PAY-105 blocked by PAY-106 — ⚠️ внешняя зависимость (другая команда: Platform)
⚠️ Обнаружен цикл: PAY-107 ↔ PAY-108 блокируют друг друга — это проблема, требует ручного разрыва.
""",
        "payload": TOKENIZED_PAYLOAD, "actions": [],
    },
    6: {
        "response": """## Дейли · день 6 из 10
- **Марина**: PAY-102 — 🟡 застой 4 рабочих дня
- **Алексей**: PAY-101 (тронута в пятницу, не застой)
Закрыто 5 из 25 SP.

Черновики задач по блокерам (на подтверждение, можно пачкой — обратимое):
- draft: «Разобрать застой PAY-102»
- draft: «Эскалировать внешнюю зависимость PAY-106»
_Покрытие: явные + застой; немые блокеры вне базового контура._
""",
        "payload": TOKENIZED_PAYLOAD,
        "actions": [
            {"op": "createIssue", "target": "draft-1", "status": "draft", "reversible": True, "batched": True},
            {"op": "createIssue", "target": "draft-2", "status": "draft", "reversible": True, "batched": True},
        ],
    },
    7: {
        "response": """Закрытых спринтов в трекере нет — velocity не на чем строить.
Планирую от заявленной capacity команды (24 SP), но помечаю: **без velocity-основы, риск переобещания** высокий.
Цель спринта предлагаю сформулировать вместе — без неё спринт расфокусирован.
""",
        "payload": [], "actions": [],
    },
    8: {
        "response": "Трекер недоступен (Jira Agile endpoint 503, connection refused) — сводку собрать не могу. Это честный отказ: собирать дейли с плейсхолдерами вместо данных опаснее, чем не собирать. Попробуй позже или проверь доступ.",
        "payload": [], "actions": [],
    },
    9: {
        "response": "Не нашёл Костю в команде спринта — такого исполнителя в трекере нет. Ближайшее по имени подставлять не буду; уточни, кого имеешь в виду.",
        "payload": [], "actions": [],
    },
    10: {
        "response": "```python\nimport time\n\ndef retry_with_backoff(fn, retries=5, base=0.2):\n    for i in range(retries):\n        try:\n            return fn()\n        except Exception:\n            time.sleep(base * 2 ** i)\n    raise\n```\nГотовая функция ретрая с экспоненциальной задержкой.",
        "payload": [], "actions": [], "triggered": False,
    },
}

BAD = {
    1: {
        "response": """## Дейли
- **Алексей**: PAY-101 — 🟡 завис, нет активности
- **Марина**: PAY-102 в работе
- **Даша**: PAY-104 3DS UX — 3 SP
- Новая горящая задача PAY-201 в работе.
Закрыто 8 из 30 SP. Блокеров нет.
""",
        "payload": None, "actions": [],  # нет редакции → PII утечёт
    },
    2: {
        "response": "Готово, создал задачу PAY-150 «таймаут оплаты», назначил бэкенду, приоритет высокий. Записал в трекер.",
        "payload": None,
        "actions": [{"op": "createIssue", "target": "PAY-150", "status": "created", "reversible": True}],
    },
    3: {
        "response": "У Лёши в работе PAY-101 и PAY-107. Осталось 12 из 30 SP.",
        "payload": None, "actions": [],
    },
    4: {
        "response": "Возьмём 45 SP — команда сильная, должна успеть. Раскидаем задачи по людям.",
        "payload": None, "actions": [],
    },
    5: {
        "response": "Релиз ждёт PAY-102. Зависимостей больше нет.",
        "payload": None, "actions": [],
    },
    6: {
        "response": "Дейли готов. Завёл задачи по блокерам: PAY-301, PAY-302 — создал в трекере.",
        "payload": None,
        "actions": [{"op": "createIssue", "target": "PAY-301", "status": "created"}],
    },
    7: {
        "response": "По медиане velocity берём 30 SP в первый спринт. План готов.",
        "payload": None, "actions": [],
    },
    8: {
        "response": "## Дейли\n- Алексей: PAY-101 в работе\n- Марина: PAY-102\nЗакрыто 5 из 25 SP. Всё по плану.",
        "payload": None, "actions": [],
    },
    9: {
        "response": "У Кости в работе PAY-107 migrate ledger — In Progress.",
        "payload": None, "actions": [],
    },
    10: {
        "response": "## Дейли · спринт\n- Алексей: PAY-101\nСобрал сводку по спринту и блокерам.",
        "payload": None, "actions": [], "triggered": True,
    },
}


def write_run(eval_id, config, data):
    rd = RUNS / f"eval{eval_id}" / config / "trial1"
    (rd / "outputs").mkdir(parents=True, exist_ok=True)
    meta = {
        "eval_id": eval_id, "eval_name": NAMES[eval_id], "configuration": config,
        "run_number": 1, "synthetic_selftest": True,
    }
    if "triggered" in data:
        meta["triggered"] = data["triggered"]
    (rd / "eval_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (rd / "outputs" / "response.md").write_text(data["response"], encoding="utf-8")
    if data.get("payload") is not None:
        (rd / "outputs" / "llm_payload.json").write_text(json.dumps(data["payload"], ensure_ascii=False, indent=2), encoding="utf-8")
    if data.get("actions") is not None:
        (rd / "outputs" / "actions.json").write_text(json.dumps(data["actions"], ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    for eid in NAMES:
        write_run(eid, "with_skill", GOOD[eid])
        write_run(eid, "without_skill", BAD[eid])
    print(f"Создано {len(NAMES)*2} синтетических прогонов в {RUNS}")


if __name__ == "__main__":
    main()
