# AI-ассистент магазина — experimental API

Two endpoints expose the cabinet's built-in AI assistant (Alty) to the same
store-scoped token the rest of this skill uses.

## Transport

- The agents live on the **experimental** API (`/api/experimental/v1`), mounted
  next to the external one — not under `/v1/...` like the rest of this skill.
- `scripts/kit.py` derives that base URL from the configured one: the public
  host `https://api.kit.yandex.net` serves the external API at the root, other
  deployments serve it under `/api/external`, so the client swaps that suffix
  for `/api/experimental/v1`. One `KIT_API_BASE_URL` keeps working for both.
- Authentication is the same `Authorization: Bearer <yakit_…>` token. **The
  store is decoded from the token itself** — no host, path or body parameter
  names it. One token = one store.
- The server adds store context (store info, database coordinates, Metrica
  counter) on its own; the client sends only `prompt`.
- Timeout is 180 s, against a measured 9–20 s in production.

## Operations

| Method | Path | operationId | CLI |
|---|---|---|---|
| POST | `/api/experimental/v1/ai-assistant/data-analyst` | `RunAIAssistantDataAnalyst` | `kit.py ask analyst --prompt …` |
| POST | `/api/experimental/v1/ai-assistant/support` | `RunAIAssistantSupport` | `kit.py ask support --prompt …` |

Request: `{"prompt": "<non-blank text>"}` (`minLength: 1`).
Response: `{"answer": "<generated text>"}`.

The prompt comes from `--prompt`, `--prompt-file`, or stdin. `--dry-run` prints
the prepared request without sending it.

## Заглушка: агент выключен на сервере

Сейчас обе ручки **всегда** отдают заглушку (рефакторинг Althia; прямые агенты
закрыты). Раньше сервер умел выключать агентов, не убирая ручки. И `data-analyst`, и
`support` на любой промпт отвечают одной и той же заглушкой:

- код ответа — **`200`**, тело — обычное `{"answer": "…"}`;
- `answer` начинается словами **«ИИ-помощник временно недоступен»**, дальше —
  приглашение написать в поддержку.

По статусу и форме это неотличимо от ответа, поэтому клиент узнаёт заглушку по
первым словам `answer` и не выдаёт её за результат:

```json
{"unavailable": true, "answer": "ИИ-помощник временно недоступен. …"}
```

— на stdout, с пояснением на stderr и кодом выхода **`1`**. Что делать дальше:

- **не пересказывать заглушку как ответ ассистента** и не цитировать её
  контакты как решение вопроса пользователя;
- **не повторять промпт** — ни сразу, ни в цикле: пока агент выключен, ответ
  тот же;
- вопрос про данные магазина посчитать самому по `list /v1/orders` и
  `list /v1/variants`, назвав покрытие (см. `workflow.md`), или честно сказать,
  что сводные показатели сейчас есть только в кабинете, раздел «Главная».
  Трафика и конверсии в публичном API нет — их без ассистента не получить;
- вопрос о работе с кабинетом — ответить по справочным страницам скила и
  таблице «Чего в публичном API нет» в `SKILL.md`, называя раздел кабинета.

### `analyst` — data-analyst agent

Answers questions about the store's own data and assembles free-form reports:
продажи, заказы, выручка, средний чек, трафик, конверсия, срезы по каталогу,
динамика и сравнение периодов. It reaches the store's analytics itself, so a
prompt can ask for something no single endpoint returns («сравни выручку по
категориям за два месяца и объясни просадку»).

Prefer a real endpoint when one answers exactly — `api GET /v1/orders` for a
list of orders, `api GET /v1/variants` for a catalog dump. The agent is for
interpretation and reporting, not for fetching records — and only while it is
switched on (see «Заглушка» above).

### `support` — support agent

Answers how-to questions about the cabinet and its features, grounded in the
product documentation: как добавить товар, как настроить доставку или оплату,
где включить промокоды, что умеет тот или иной раздел. When it is on, it is the
honest answer for areas that have **no public API** (например, конструктор
витрины): the agent explains how to do it in the UI instead of the skill
improvising an endpoint. When it answers with the stub, the same job falls to
the table «Чего в публичном API нет» in `SKILL.md`.

## Диалога нет: каждый вызов независим

В кабинете ассистент живёт в тредах — их открывают и продолжают, и это одна из
самых частых операций там. **В этом API тредов нет.** `POST …/data-analyst` и
`POST …/support` принимают один `prompt` и возвращают один `answer`: ни
`thread_id`, ни истории, ни ссылки на прошлый разговор. Агент не помнит ничего
из предыдущего вызова — ни своего, ни сделанного из кабинета.

Что из этого следует:

- **Контекст переносите в сам промпт.** Уточняющий вопрос («а что с
  конверсией?») без пересказа предыдущего — это вопрос в пустоту. Соберите в
  новый промпт то, что нужно: период, разрез, вывод прошлого ответа.
- **Не изображайте продолжение.** Если пользователь просит вернуться к
  вчерашнему разговору, скажите, что переписка недоступна через API, и
  предложите сформулировать вопрос заново — с контекстом, который помните вы
  или он. Не выдавайте новый ответ за продолжение старого.
- **История разговоров не читается и не пишется** — за ней только в кабинет.

## Answers are generated text

`answer` is prose, not structured store data. Report it as the assistant's
answer, keep it attributed, and verify any number that drives a decision against
the API. Do not reshape an answer into a table of "facts" the user did not ask
for, and do not treat a refusal or a gap in the answer as an API error.

## Latency

Agents generate text, so they are slow by design.

- Measured in production: 9–23 s, with no observed ceiling below the client
  timeout.
- Client timeout: 180 s; on expiry the client exits `1` with
  `Network error after up to 180s`.
- Callers must allow more than 180 s for the process — a 120-second Bash
  timeout kills the call mid-answer and looks like a failure.
- Run one prompt at a time. Parallel prompts add nothing and burn the rate
  limit; a timeout is not an invitation to retry in a loop.

## Safety

- **Read-only.** Both calls are POST but change nothing in the store, so they
  are deliberately outside the `--confirm` gate. Never express a mutation as a
  prompt: catalog, order and promo changes go through
  `api <VERB> … --confirm` with an explicit confirmation.
- **Prompts leave the machine** for an AI service. Never put a token, a
  password, or customer personal data in one. Three shapes are refused locally,
  before the token is loaded and before a connection is opened (exit `2`):
  a `yakit_`-shaped value, an email address, and a phone number.
- The personal-data guard is deliberately blunt, and a false positive is the
  cheap failure: rewrite the question around an order number or a customer id
  and look the person up through the API. A phone number that reached the AI
  service cannot be recalled, a rejected prompt costs one retry.
- The guard reads the prompt only. Data pulled from the store and pasted into a
  prompt by hand is exactly as sensitive — `list /v1/customers --redact` is the
  way to look at that data without carrying it around.
- **`--redact` does not apply to an agent's answer.** The agent replies with
  prose, and masking works on named fields, not on sentences — so an analyst
  answer may well name a customer it read from the store. Treat the answer as
  data that has already left the machine, and do not paste it onward.
- Blank or whitespace-only prompts are refused locally (the API answers `400`).

## Routes that are deliberately absent

| Route | Status | Why |
|---|---|---|
| `/api/external/v1/ai-assistant/*` | 404 | The agents are experimental-only; the server test suite locks this. |
| `/api/experimental/v1/ai-assistant/traffic-analyst` | 404 | A traffic-analyst agent exists server-side but is deliberately not exposed. |

Do not add either. The rest of the experimental specification
(`/v1/constructor/*`, `/v1/section-templates`) belongs to
`kit-storefront-constructor`.

## Errors

| Status | Cause |
|---:|---|
| 400 | Blank prompt or malformed JSON body. |
| 401 | Missing, malformed, or non-matching bearer token. |
| 500 | Agent or upstream failure. |
| 504 | A gateway gave up before the agent answered. Not an application error — the body is plain text with no `trace_id`. Retry once, serially. |

Error bodies are `{"code", "message", "trace_id"}` — quote `trace_id` when
reporting a failure. The stub of a switched-off agent is not in this table: it
arrives as `200` (see «Заглушка» above).
