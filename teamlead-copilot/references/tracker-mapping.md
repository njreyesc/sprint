# Tracker mapping — контракт сущностей и операций

Скилл не завязывается на Jira напрямую. Все обращения к трекеру идут через этот
семантический контракт; **Jira — первая (и на старте единственная) реализация**.
Это сохраняет путь к другим трекерам через фолбэк, не делая их частью скоупа.

> Источник правды — трекер. Контракт ничего не персистит: всё `derived-on-read`,
> пересчёт из трекера на каждый запрос (ADR-001).

## Содержание

- [Сущности](#сущности)
- [Операции чтения](#операции-чтения)
- [Операции записи (мутации)](#операции-записи-мутации)
- [Контракт «velocity-история»](#контракт-velocity-история) — для планирования
- [Jira-реализация](#jira-реализация)
- [Фолбэк для не-Jira трекеров](#фолбэк-для-не-jira-трекеров)

## Сущности

| Сущность | Поля контракта |
|---|---|
| **Задача** | `id`, `title`, `description`, `status`, `assignee`, `priority`, `points`(SP), `sprintId`, `updated`, `links[]`, `acceptanceCriteria` |
| **Связь** | `type` (`blocked_by` / `blocks` / `relates`), `fromId`, `toId` |
| **Спринт** | `id`, `boardId`, `state` (`active`/`closed`/`future`), `goal`, `startDate`, `endDate`, `completeDate` |
| **Активность** | `issueId`, `timestamp`, `kind` (status/comment/field), `since`-курсор |
| **Velocity-запись** | `sprintId`, `committed`(SP на старте), `completed`(SP на закрытии) |

`assignee` и потенциально `description` — кандидаты на редакцию (см.
[redaction-rules.md](redaction-rules.md)); контракт отдаёт их сырыми, токенизация —
на слое redaction до отправки в модель.

## Операции чтения

- `getActiveSprint(boardId)` → спринт + его задачи (дейли, поиск, планирование).
- `searchIssues(filter)` → задачи по {assignee, text, status, sprintId, links}.
- `getIssue(id)` → одна задача с полями и связями.
- `getActivity(issueId | sprintId, since)` → события активности после курсора
  `since`. **Нужна для stall-детекции и измерения свежести** (ADR-003).
- `getLinks(id)` → рёбра графа зависимостей.

**Курсор `since`:** источник дельты активности. Чтобы не было гонки на общем
state, `since` берётся из max(`updated`) прочитанного набора или из метки
предыдущего запроса, переданной явно в вызов, — не из скрытого глобального стора.

## Операции записи (мутации)

Все — только после подтверждения (см. инвариант 3 в SKILL.md).

| Операция | Обратимость |
|---|---|
| `createIssue(draft)` | обратимо → батч |
| `updateIssue(id, patch)` | обратимо → батч |
| `linkIssues(fromId, toId, type)` | обратимо → батч |
| `reassign(id, assignee)` | критично → поштучно |
| `deleteIssue(id)` | необратимо → поштучно |
| `closeSprint(id)` | необратимо → поштучно |

## Контракт «velocity-история»

> Подгружать **только в режиме планирования** (ленивая подгрузка, ADR-004).

- `getClosedSprints(boardId)` → закрытые спринты с `completeDate`, `goal`,
  `committed`/`completed` SP.
- `getVelocity(boardId)` → `[{sprintId, committed, completed}]` по последним
  спринтам.

**Правило основы плана (cold-start, T5):**

| Закрытых спринтов | Основа velocity | Пометка |
|---|---|---|
| 0 | нет → планируй от заявленной capacity команды | «без velocity-основы, риск переобещания» |
| 1–2 | факт по этим спринтам | «низкая уверенность» |
| ≥3 | медиана `completed` последних трёх | базовый ориентир |

Любой вывод про velocity несёт provenance:
`jira-closed-sprint(N)` | `active-sprint` | `cold-start`.

## Jira-реализация

| Контракт | Jira endpoint |
|---|---|
| `getActiveSprint` | `GET /rest/agile/1.0/board/{boardId}/sprint?state=active` + `/sprint/{id}/issue` |
| `searchIssues` | `GET /rest/api/3/search?jql=...` |
| `getActivity` | changelog задачи (`expand=changelog`) + comments |
| `getClosedSprints` | `GET /rest/agile/1.0/board/{boardId}/sprint?state=closed` |
| `getVelocity` | `GET /rest/greenhopper/1.0/rapid/charts/velocity?rapidViewId={boardId}` → `velocityStatEntries` |
| детализация спринта | `GET /rest/greenhopper/1.0/rapid/charts/sprintreport?rapidViewId={boardId}&sprintId={id}` |

⚠️ velocity/sprintreport — **greenhopper-эндпоинты** (полу-приватные, не стабильный
публичный Agile REST). Коупинг-риск изолирован за этим контрактом: при сломе меняем
только реализацию, не режимы.

Перед использованием проверь наличие MCP-коннектора к Jira и доступ к
Agile/sprint-эндпоинтам.

## Фолбэк для не-Jira трекеров

Вне скоупа старта. У трекеров без нативной истории спринтов (Linear/Asana и пр.)
контракт «velocity-история» закрывается агентским snapshot-фолбэком (append-only
лог закрытых спринтов). На Jira он **не активируется** — история живёт в трекере.

## Mock для прогона без боевой Jira

Mock tracker-mcp эмулирует Jira-семантику. Обязательные фикстуры:
- активный спринт с `goal`, задачами, исполнителями, SP;
- бэклог с приоритетами; carry-over из прошлого спринта;
- задачи со связями, **включая цикл блокеров** (X→Y→X) для теста обхода;
- история активности/changelog (не только текущее состояние — нужна stall);
- ≥3 закрытых спринта с `committed`/`completed` для теста планирования по velocity;
- отдельная фикстура **без закрытых спринтов** для теста cold-start.
