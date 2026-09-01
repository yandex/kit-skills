# Яндекс.Кит cabinet — public API reference

This page is the source of truth for how the skill talks to the cabinet. On
conflict, live API behavior wins; update this page to match. Official docs:
<https://yandex.ru/dev/kit/ru/>.

## Authentication

- The API uses Bearer tokens: `Authorization: Bearer <token>`.
- **One token = one store.** There is no site/store selection in the API; the
  token itself scopes every call to a single store. `store` shows which one.

### Getting a token (agent-guided onboarding)

When there is no token yet (`kit.py token status` → exit 2), walk the user
through this:

1. Give the user the direct link to the cabinet's API page:
   `https://<store-slug>.b2b.kit.yandex.ru/settings/api`
   (in the cabinet UI: **Настройки → API**). If the slug is unknown, the user
   knows their cabinet URL.
2. The user clicks **«Сгенерировать токен»** and copies the token immediately —
   it is shown only once (the list keeps a masked entry like
   `**************Buf3`). Multiple tokens per store are allowed.
3. Take the token in, by the route that matches where this client runs. The
   environment decides this, not the user:

   | Where you run | Route |
   |---|---|
   | On the user's machine | `kit-storefront-constructor`'s `kit.py token web` — a one-shot page on `127.0.0.1`; the value goes browser → token file and never through the conversation. Same shared file, so one run covers every Kit skill. Without that skill: `kit.py token save` in a real terminal (hidden `getpass` prompt). |
   | Hosted — web session, container, cloud sandbox, SSH | The page cannot reach the user's browser and refuses on purpose. Offer `KIT_TOKEN` in the session's environment or secret settings first — that keeps the value out of the transcript. If the user cannot reach those settings, take the token in the conversation, as text or as a file, and save it with `kit.py token save` reading stdin. A supported route: say the value stays in the history, and tell them they can revoke it in the cabinet afterwards. |

   In every route the token stays off the command line.
4. The token is stored in `~/.yandex-kit-skills/kit_api.token` (mode 600, set at
   creation), where every Kit skill finds it automatically on any OS.
5. Verify with `kit.py whoami` (email + role) and `kit.py store` (which store).

If the token is compromised: delete it in the cabinet (Настройки → API) and
generate a new one, then save it again by the same route.

### Token resolution order

The tool looks for the token in this order and uses the first hit
(`kit.py token status` reports the active source without revealing the token):

1. `KIT_TOKEN` environment variable — the token itself.
2. `KIT_TOKEN_FILE` environment variable — path to a file with the token.
3. `~/.yandex-kit-skills/kit_api.token` — the default file written by
   `kit.py token save`.

The token is never printed, logged, committed, or passed as a process
argument. If nothing is found, the tool exits with code `2`.

## Base URL and limits

- Production base URL: `https://api.kit.yandex.net`. Paths are appended to it as
  written below (`/v1/products`, `/v1/store`, …).
- Rate limit: **10 requests/second per store**. The tool retries `429`
  responses automatically (up to 3 times, honoring `Retry-After`).

### Base URL resolution order

The first hit wins (`kit.py env` prints the resolved base URL and the token
source without revealing the token):

1. `--base-url` — full base URL for a single command.
2. `KIT_API_BASE_URL` environment variable — full base URL.
3. `https://api.kit.yandex.net` — the production default.

The production host serves this API at the root, so paths are appended to the
base URL unchanged. Other deployments serve it under the backend prefix
`/api/external`, which has to be part of the override
(`https://host/api/external` + `/v1/store`). Use the overrides only for a mock
or an approved non-production contour; they are not evidence that an endpoint is
publicly available.

## Tool — `scripts/kit.py`

Run it with `python3` on macOS/Linux and `python` on Windows (stdlib only, no
pip installs):

```
kit.py env                          # active contour: base URL + token source (no secrets)
kit.py endpoints [PATTERN]          # list API operations from the bundled spec (offline)
kit.py describe <METHOD> <path>     # parameters + request body schema (offline)
kit.py validate <METHOD> <path> --data <json>   # check a drafted body (offline)
kit.py schema <Name>                # one named schema (offline)
kit.py token save                   # hidden prompt -> ~/.yandex-kit-skills/kit_api.token
kit.py token status                 # which token source is active (never the token)
kit.py whoami                       # GET /v1/users/current — token check
kit.py store                        # GET /v1/store — store id, slug, b2c_url
kit.py api GET <path> [-q k=v ...]  # any read
kit.py list <path> [-q k=v ...]     # every page of a collection + coverage verdict
kit.py api POST|PATCH|PUT|DELETE <path> [--data <json>] --confirm   # any write
kit.py upload <file> --confirm      # multipart POST /v1/files (max 100 MB)
kit.py ask analyst --prompt "..."   # AI-ассистент: аналитика и отчёты по магазину
kit.py ask support --prompt "..."   # AI-ассистент: как пользоваться кабинетом
```

- `--data` accepts an inline JSON string, `@path/to/body.json`, or `-` (stdin).
  Prefer `@file` on Windows — cmd/PowerShell mangle inline JSON quoting.
- `-q/--query key=value` is repeatable: `-q page=1 -q per_page=50`. `list` owns
  `page`/`per_page` itself and ignores them if passed.
- `--confirm` — required for any mutating verb (POST/PUT/PATCH/DELETE).
  Without it, mutating calls are refused before any network request. It works
  both before and after the subcommand.
- `--dry-run` — validate the body and print the request that would be sent,
  including the resolved `Content-Type`, without sending it.
- `--skip-validation` — send a mutating request whose body the bundled spec
  rejects. Only for an endpoint the vendored spec does not cover yet; it is not
  a way around a body you could not get right.
- `--redact` — mask customer personal data and third-party free text in the
  printed response: `first_name`, `last_name`, `patronymic`, `phone`, `email`,
  `buyer_name`, `buyer_phone`, `buyer_email`, `holder_email`, `address`,
  `courier_address`, `pickup_point_address`, `self_pick_up_address`, `entrance`,
  `floor`, `intercom`, `note`, `delivery_notes`. A masked string keeps
  its length (`«скрыто, 12 симв.»`), so the field is still visibly present.
  **Output only** — the request body is never redacted, because what is sent has
  to stay exactly what the user confirmed. `name` and `description` are
  deliberately not masked: they are the merchant's own catalog copy.
- `ask` takes the prompt from `--prompt`, `--prompt-file`, or stdin. It is POST
  but read-only, so it needs no `--confirm`; it runs against the experimental
  API with a 180-second timeout. See [`ai-assistant.md`](ai-assistant.md).

Exit codes:

- `0` — success.
- `1` — API/network error the API decided (non-2xx it answered, failed read).
- `2` — usage error, missing token, or a request body that violates the
  documented schema (nothing was sent).
- `3` — mutating operation refused (no `--confirm`).
- `4` — **ambiguous write**: timeout, dropped connection, 408 or 5xx on a
  mutating call. The request may or may not have been applied. Verify with a
  read; never resend it blindly. See
  [`workflow.md`](workflow.md#write-outcomes).

## Offline validation of a request body

`kit.py validate <METHOD> <path> --data @body.json` checks a drafted body
against the bundled spec without a token or a network call. It reports:

- **violations** — missing required field, wrong type, invalid enum value,
  a body where the operation takes none (or the other way round). Exit `2`.
- **warnings** — fields the spec does not document. These are not errors: the
  API ignores unknown fields silently, which is exactly why an invented field
  name (`prise` instead of `pricing.price`) otherwise looks like a successful
  write.

Every mutating `api` call runs the same check before sending, so an invalid
body never reaches the store; `--dry-run` runs it too. Paths may be concrete
(`/v1/variants/019f…`) or templated (`/v1/variants/{id}`) — both resolve to the
same operation.

## Request content types

Request bodies are `application/json`, except five operations that take **JSON
Merge Patch** (`application/merge-patch+json`): `UpdateVariant`,
`UpdateCategory`, `UpdateCharacteristic`, `UpdateVariantAttachment`,
`UpdateWarehouse`. Send only the fields you change; `null` clears a field only
where the schema marks it nullable. The client reads the content type from the
spec and sets the header itself — `describe` prints which one applies.
`POST /v1/files` and `POST /v1/videos` are `multipart/form-data` (`kit.py upload`).

## Conventions that live behavior confirmed

- Pagination is `page` + `per_page` (not `limit`/`offset`, `per_page` max 100);
  unknown query params are silently ignored. `kit.py list <path>` walks every
  page and states whether the coverage is `complete` or `partial` — prefer it
  over a single `api GET` whenever the count matters
  ([`workflow.md`](workflow.md#coverage-how-much-did-you-actually-read)).
- List responses are wrapped: `{"<resource>": [...], "total_count": N}`.
- Nine list endpoints **require** the `status` query param (omitting it is a
  `400 VALIDATION_ERROR`): `/v1/categories`, `/v1/characteristics`,
  `/v1/warehouses`, `/v1/promocodes`, `/v1/collections`, `/v1/blogs`,
  `/v1/discounts`, `/v1/videos`, `/v1/alerts`. The allowed values differ per
  endpoint — check with `kit.py describe GET <path>` instead of guessing.
- IDs are UUIDv7 strings. Errors look like
  `{"code": "VALIDATION_ERROR", "message": "...", "trace_id": "..."}`.

## Request schemas — the bundled specification

The API does **not** serve its own specification in production, so the skill
ships one: `references/openapi.json`, the public external-API spec converted to
JSON (161 operations, 231 schemas). Three offline commands read it — no token,
no network:

```
kit.py endpoints [PATTERN]           # list operations, filter by path/summary/operationId
kit.py describe POST /v1/variants    # parameters + request body: required fields, enums, examples
kit.py schema CreateVariantRequest   # one named schema
```

`describe` marks required fields with `*`, prints enum values inline and shows
nested objects by schema name; expand those with `kit.py schema <Name>`. Add
`--raw` to either command for the exact JSON fragment.

**Never invent a request body.** Run `describe` for the endpoint first, build
the body from what it prints, then show it to the user and send it with
`--confirm`. The domain pages below tell you *which* endpoint to use and how it
behaves; the spec tells you *what to send*.

## Endpoint map (router)

The full endpoint tables are split by domain — load only the page(s) the task
needs. Mutating = POST/PUT/PATCH/DELETE (all need `--confirm`).

| Задача касается… | Страница |
|---|---|
| Категорий, характеристик, продуктов/карточек, товаров (вариантов), файлов/медиа, складов | [`catalog.md`](catalog.md) |
| Промокодов, скидок, подарков, подарочных карт, бейджей, услуг (addons) | [`promo.md`](promo.md) |
| Коллекций (статических/динамических) и контекстных коллекций | [`collections.md`](collections.md) |
| Заказов и клиентов | [`orders.md`](orders.md) |
| Блога (новостей), редиректов, вебхуков, гео | [`site.md`](site.md) |
| Вопросов к AI-ассистенту: аналитика магазина, справка по кабинету | [`ai-assistant.md`](ai-assistant.md) |

Витрина (конструктор: страницы, секции, меню, кастомные секции) **не покрыта
публичным API**; не обходите это скрейпингом кабинета. Это граница этого скила,
а не отказ пользователю: страницы, секции, тему и публикацию ведёт скил
`kit-storefront-constructor`, вёрстку кастомной секции — `kit-custom-sections`.

### Магазин и пользователь (core)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/users/current` | Получение текущего пользователя |
| GET | `/v1/store` | Получение информации о магазине |

For exact request/response schemas beyond these tables, consult the official
docs at <https://yandex.ru/dev/kit/ru/> — these pages track paths, verbs and
live-verified gotchas, not full body schemas.
