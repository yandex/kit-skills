# kit-skills — Яндекс.Кит skills

Skills that let Claude Code / Codex manage the **Яндекс.Кит** e-com website
builder cabinet. Every skill runs on **Windows, macOS and desktop Linux** and is
compatible with both **Claude Code** and **Codex**.

They need **Python 3** and nothing else — no pip install, no other dependency.
macOS and most Linux desktops already have it; on Windows it comes from the
Microsoft Store or [python.org](https://www.python.org/downloads/).

To use the skills, paste this repository's link into Claude Code and it
self-installs everything for you.

## What's inside

`install.py` installs all four. They are meant to work together and hand tasks
over to each other.

| Skill | What it does | Needs |
|---|---|---|
| `yandex-kit-cabinet` | Catalog, prices and stock, orders, promo codes and discounts, collections, webhooks; questions to the store's AI assistant. The entry point: this is where the token is set up and access is checked. | Store token |
| `kit-store-checkup` | Weekly store revision: published but impossible to buy, running out of stock, orders waiting or stuck, products with no photo, price or dimensions, duplicate SKUs. Read-only — every fix it finds is handed to `yandex-kit-cabinet`. | Store token |
| `kit-storefront-constructor` | The storefront: read pages, sections and versions, edit them in a local draft, publish the result, create custom-section templates, upload images and video. | Store token |
| `kit-custom-sections` | Builds a custom storefront section (html + css + json_schema) from a catalog of verified patterns, and checks an existing one. Uploads nothing itself — prints the `kit-storefront-constructor` commands to run. | Nothing: works offline, no token |

## Install

Give Claude Code (or Codex) the repository link and ask it to install the
skills, or do it yourself:

```bash
git clone https://github.com/yandex/kit-skills.git
cd kit-skills
python3 install.py
```

The installer discovers every skill under `skills/<name>/SKILL.md` and copies it
into the skill directory of each agent it finds on the machine — an agent counts
as present when its home directory exists:

- Claude Code → `~/.claude/skills/<name>/`
- Codex → `~/.codex/skills/<name>/`

If neither directory exists, the installer says so and installs for both rather
than doing nothing.

Useful flags:

```bash
python3 install.py --dry-run                 # show what would be installed
python3 install.py --agent claude-code       # only one agent
python3 install.py --skill yandex-kit-cabinet  # only one skill
```

Re-running the installer updates already-installed skills (the copy is
idempotent). Restart your agent afterwards to pick up the changes.

## Получение API-токена

Каждому скилу нужен API-токен магазина (один токен = один магазин). Проще всего
попросить агента: «подключи мой магазин Яндекс.Кит» — дальше он сам выберет один
из двух маршрутов. Выбирает не пользователь, а то, **где запущен агент**.

**Агент работает на вашем компьютере** (Claude Code или Codex локально) — он
откроет одноразовую страницу на `127.0.0.1`, вы введёте токен там. Значение
попадёт из браузера сразу в файл токена и в переписку не пойдёт. Это маршрут по
умолчанию.

**Агент работает на сервере** (веб-сессия, облачная песочница, контейнер, SSH) —
локальная страница там бесполезна: `127.0.0.1` для неё это сервер, а не ваш
компьютер. Агент это определяет сам и не даёт ссылку, которую вы не сможете
открыть. Тогда токен передаётся так:

1. если у сессии есть настройки окружения или секретов — задайте там `KIT_TOKEN`;
   значение не попадёт в переписку. Предпочтительно;
2. если таких настроек нет — отправьте токен прямо в диалоге, текстом или
   файлом. Это нормальный, предусмотренный маршрут. Учтите одно: значение
   останется в истории переписки, поэтому по окончании работы токен стоит
   отозвать в кабинете (**Настройки → API**) и при необходимости выпустить новый.

Токен не передаётся аргументом команды ни в одном из маршрутов — этого не делает
ни агент, ни вы.

Ниже — те же шаги вручную, без агента:

1. **Откройте раздел API в кабинете**:
   `https://<ваш-магазин>.b2b.kit.yandex.ru/settings/api`
   (или в кабинете: **Настройки → API**).
2. **Нажмите «Сгенерировать токен»** и скопируйте его сразу — повторно токен
   не показывается (в списке он останется замаскированным, вида
   `**************Buf3`).
3. **Сохраните токен на этой машине** — запустите и вставьте токен в скрытый
   ввод (он не попадёт ни в чат, ни в историю команд):

    ```bash
    # macOS / Linux
    python3 ~/.claude/skills/yandex-kit-cabinet/scripts/kit.py token save

    # Windows (PowerShell)
    python $HOME\.claude\skills\yandex-kit-cabinet\scripts\kit.py token save
    ```

    Токен ляжет в `~/.yandex-kit-skills/kit_api.token` (права 600), откуда все
    скилы Кита читают его автоматически — никакие переменные окружения
    настраивать не нужно.

4. **Проверьте доступ**:

    ```bash
    python3 ~/.claude/skills/yandex-kit-cabinet/scripts/kit.py whoami
    python3 ~/.claude/skills/yandex-kit-cabinet/scripts/kit.py store
    ```

    `whoami` покажет вашу учётку и роль, `store` — магазин, к которому привязан
    токен.

Если токен скомпрометирован — удалите его в кабинете (Настройки → API) и
сгенерируйте новый. Для нестандартных схем есть переменные окружения `KIT_TOKEN`
(сам токен) и `KIT_TOKEN_FILE` (путь к файлу с токеном) — они имеют приоритет
над файлом по умолчанию.

Локальная страница ввода — подкоманда `token web` скила
`kit-storefront-constructor`:

```bash
python3 ~/.claude/skills/kit-storefront-constructor/scripts/kit.py token web
```

Токен из неё ложится в тот же общий файл, так что одного запуска хватает всем
скилам Кита сразу.

## Переменные окружения

По умолчанию скилы ходят в прод — `https://api.kit.yandex.net`. Для отладки на
другом контуре домен и токен переопределяются переменными окружения; ничего из
этого не нужно для обычной работы.

| Переменная         | Что задаёт                                                                 |
| ------------------ | -------------------------------------------------------------------------- |
| `KIT_API_BASE_URL` | Полный base URL API. У каждого скила он свой, см. его `references/api.md`. |
| `KIT_TOKEN`        | Сам токен.                                                                 |
| `KIT_TOKEN_FILE`   | Путь к файлу с токеном.                                                    |

Порядок для домена: флаг `--base-url` → `KIT_API_BASE_URL` → прод по умолчанию.
Порядок для токена: `KIT_TOKEN` → `KIT_TOKEN_FILE` →
`~/.yandex-kit-skills/kit_api.token`.

Проверить, какой контур активен сейчас (токен не печатается):

```bash
python3 ~/.claude/skills/yandex-kit-cabinet/scripts/kit.py env
```

## Layout

```
kit-skills/
├── README.md                 # this file
├── AGENTS.md                 # repo-level agent contract / safety rules
├── LICENSE                   # MIT
├── CONTRIBUTING              # CLA notice for external contributors
├── SECURITY.md               # how to report a vulnerability
├── install.py                # cross-platform self-installer (stdlib only)
└── skills/
    ├── yandex-kit-cabinet/   # manage the Кит cabinet via public API
    │   ├── SKILL.md          # frontmatter (name, description) + agent guide
    │   ├── AGENTS.md         # skill safety contract
    │   ├── agents/openai.yaml  # Codex compatibility layer
    │   ├── references/       # API + workflow docs
    │   └── scripts/kit.py    # safe, read-only-by-default API client
    ├── kit-store-checkup/    # weekly store revision
    │   ├── SKILL.md
    │   ├── AGENTS.md
    │   ├── agents/openai.yaml
    │   ├── references/
    │   └── scripts/checkup.py  # read-only: no write verbs, hands fixes to the cabinet skill
    ├── kit-storefront-constructor/  # read and edit the storefront, publish by full replace
    │   ├── SKILL.md
    │   ├── AGENTS.md
    │   ├── agents/openai.yaml
    │   ├── references/
    │   └── scripts/kit.py    # storefront client, every change behind --confirm
    └── kit-custom-sections/  # build and lint a custom section offline
        ├── SKILL.md
        ├── AGENTS.md
        ├── agents/openai.yaml
        ├── references/       # patterns, gates, generated tag/helper reference
        └── scripts/section.py  # fully offline: no network, no token
```

## Compatibility

- **Claude Code / Codex** — the `SKILL.md` frontmatter (`name`, `description`)
  follows the public Agent Skills standard; `agents/openai.yaml` provides the
  Codex layer.
- **Cross-platform** — all scripts are Python 3 standard-library only and resolve
  paths with `pathlib`, so they behave identically on Windows, macOS and Linux.
- **Public API first** — skills call API endpoints and never scrape the web
  cabinet when an endpoint exists. Most of that is the published API at
  `api.kit.yandex.net` ([reference](https://yandex.ru/dev/kit/ru/)). Two areas are
  not: `kit-storefront-constructor` and the cabinet's `ask` commands run on the
  experimental contour `/api/experimental/v1`, which is outside the published
  reference and can change without notice.

## Self-update

`kit-storefront-constructor` keeps itself current. The Кит API names the latest
published skill version in a response header, so no request is made to find out;
when this copy turns out to be older, the client downloads this repository from
GitHub and re-runs `install.py` for the agent it is installed under. That happens
after your command has finished, never instead of it, and it prints one line
starting with `kit-skills:`. The download is the only connection these skills make
outside the Кит API and the storefront, and it sends nothing about you beyond what
fetching a public URL implies.

Turn it off and the client will only ever tell you a newer version exists:

```bash
export YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED=1
```

This is a separate switch from the telemetry one below, on purpose: one governs
what leaves your machine, the other what arrives on it.

## Telemetry

The skills send **no events anywhere** — no analytics endpoint, no install
report, no background call of their own. The only telemetry is a set of headers
on the API requests the skills already make, so the Кит API can tell skill
traffic apart from the rest (the self-update download above is the one other
connection, and it carries none of these):

| Header | Value |
|---|---|
| `X-Skill` | the skill's name, e.g. `yandex-kit-cabinet` |
| `X-Skill-Session` | a random UUID for the current assistant thread, when the assistant supplies one |
| `X-Skill-Prompt` | the request that made the assistant call the skill — UTF-8, truncated to 1000 characters, Base64 — when the assistant supplies it |
| `X-Skill-Model` | the model driving the skill, e.g. `claude-opus-5`, when the assistant supplies it |
| `X-Skill-Harness` | the assistant itself, e.g. `claude-code` or `codex`, when it supplies it |
| `X-Skill-OS` | the operating system the skill runs on, e.g. `Darwin 25.3.0` — the one value the skill reads for itself |

They ride along with a request you are already making, to the same API, under
your own store token. Nothing is sent when the skills are idle. Your storefront
gets `X-Skill` only: everything else stays on the API that already holds your
store's data.

Telemetry is on by default. Opt out at any time, and every one of these headers
stops being sent:

```bash
export YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1
```

## Contributing

Pull requests are welcome. Before your first one, read
[`CONTRIBUTING`](CONTRIBUTING) — contributions are accepted under Yandex's
[CLA](https://yandex.ru/legal/cla/).

Every skill is covered by an executable evaluation suite that drives it at the
command line against a fixture-backed mock. Those suites are maintained
alongside the API they mirror and are not part of this repository; a change here
is reviewed together with its suite.

## Security

Found a vulnerability? Do not open a public issue — follow
[`SECURITY.md`](SECURITY.md).

## License

[MIT](LICENSE) © 2026 YANDEX LLC
