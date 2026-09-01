---
name: kit-custom-sections
description: |
  Build a custom Яндекс.Кит storefront section (Mystique widget, YandexKit.Mystique) offline from a
  catalog of 14 verified patterns — hero, advantages, CTA band, text+media, FAQ accordion, product
  shelves, product card, reviews, before/after, listing: fill a pattern's settings and get a
  validated html + css + json_schema triple with defaults plus the kit-storefront-constructor
  commands for upload. Also lints an existing or exported section template offline (ya-kit tags,
  helpers, JSON Schema $ref pickers, theme CSS variables, settings-to-schema bijection) and answers
  questions about ya-kit tags, helpers, data controllers and $ref types. Use for «сделай кастомную
  секцию для Кита», «свёрстай секцию витрины», «нужен FAQ / отзывы / полка товаров на Ките»,
  «поправь мою кастомную секцию», «что принимает ya-kit-carousel». Not for uploading, publishing or
  native widgets (kit-storefront-constructor), catalog and orders (yandex-kit-cabinet), diagnostics
  (kit-store-checkup). Fully offline, no network calls, no token.
user-invocable: true
allowed-tools: Bash(scripts/section.py:*)
---

When editing this skill or its scripts, first read [`AGENTS.md`](AGENTS.md), which in turn points at the repository contract.

# Яндекс.Кит custom sections (offline)

**This skill produces files.** It assembles and validates the html + css + json_schema triple of a custom storefront section locally. It makes **no network calls, needs no token and uploads nothing** — uploading, binding and publishing belong to `kit-storefront-constructor`, whose exact commands this skill prints for you.

## Knowledge freshness

1. First action of any scenario: `scripts/section.py doctor` — prints the reference build date, age in days, coverage counters and the maximum available check level.
2. The vendored knowledge (tag registry, helpers, tokens, schemas) is a **snapshot**. If a tag, helper or `$ref` is not in the reference — do not invent it; say the knowledge may be stale and suggest rebuilding the reference instead of guessing.
3. `references/generated/*` are machine-produced; never edit them by hand and never quote them wholesale into context — query them via `scripts/section.py ref`.

## Hard rules

1. Не писать html руками — начинать с `patterns list` / `patterns match`.
2. Свободный HTML только через `freeform --confirm` и только после того, как `patterns match` вернул пусто.
3. Кастомная секция — ровно тройка html + css + json_schema. Поля `script` в контракте нет ни в одном контуре — JS не генерировать.
4. `{{{ }}}` не поддерживается; `{{!-- --}}` печатается как текст; блочный хелпер не работает внутри значения атрибута.
5. Теги — только из реестра 94 (+ два без префикса `ya-kit`: `yandex-pay-badge-discount`, `yandex-pay-badge-split`). Неизвестный блочный хелпер = падение секции; неизвестный тег = молчаливая пустота.
6. `<script>` и `<style>` внутри html вырезаются молча — CSS только в поле `css`.
7. CSS скоупится платформой автоматически (`#ya-kit-<templateId>`) — руками не префиксовать. `:global(` запрещён без исключений: это единственный способ переписать стили всей витрины.
8. Символ `<` в CSS запрещён (вырезается вместе с хвостом до `>`); комбинаторы `>` и `+` разрешены.
9. Цвета и отступы — `var(--ys-*)`; хардкод hex/rgb перебивает тему магазина, потому что #id-скоуп даёт высокую специфичность.
10. Каждое `{{settings.X}}` обязано быть в `properties` json_schema и наоборот.
11. json_schema — без `oneOf/allOf/if/then/else/patternProperties`, иначе секцию потом нельзя редактировать через `kit-storefront-constructor`.
12. `$ref` — только из белого списка (20 резервных + 5 служебных + `#/definitions/partials/*` + собственные определения того же документа).
13. `alias` шаблона не заполнять; значения `YandexKit.Header` / `YandexKit.Footer` запрещены навсегда.
14. `template_id` вешать только на секции с виджетом `YandexKit.Mystique`. Ни бэк, ни push-гейты конструктора это не проверяют.
15. Ничего не заливать самому — печатать готовые команды соседа; `--confirm` не подставлять и чужой скрипт не запускать.
16. Живой шаблон править нельзя: витрина инлайнит его актуальную строку во все версии контента, поэтому правка задним числом меняет и старые версии. Залитая секция правится переездом на **новый** шаблон: `templates export` → правки → `lint` → `templates create` → `content pull` заново → `section set --template-id новый --settings-file` → `content push --no-publish` на просмотр → публикация. Настройки при переезде обязательны: перед тем как отдать команду, сверьте, что каждое заполненное поле живой секции есть в новой схеме. Поля, которому в новой схеме места нет, — это потеря данных: назовите его пользователю и дождитесь решения, молча не выбрасывайте. Просмотр без публикации здесь обязателен, а не по желанию: отката версии у конструктора нет.

## Уровни проверки и деградация

| Уровень | Что требуется | Что ловит | Чего НЕ ловит | Фраза при недоступности |
|---|---|---|---|---|
| L0 lint | ничего (офлайн) | структуру, whitelist-ы, биекцию, CSS, схему | рендер, данные, красоту | — (всегда доступен) |
| L1 dryrun | ничего (итерация 2) | разрешение путей против синтетического контекста | реальные данные контроллеров | «dryrun ещё не реализован — проверено только статикой L0» |
| L2 рендер | браузер + стенд (не в скиле) | пустую секцию, падение | красоту | «реальный рендер офлайн не проверяется — смотрите превью proposal-версии» |
| L3 readback | kit-storefront-constructor | расхождение залитого с собранным | — | «сверку с сервером делает сосед: templates show после create» |
| L4 публикация | человек | продуктовую пригодность | — | «публикацию подтверждает человек в превью» |

## Команды по сценариям

Новая секция из паттерна:
```bash
scripts/section.py doctor
scripts/section.py patterns match "нужен faq на главной"
scripts/section.py patterns show faq-accordion --params
scripts/section.py new --pattern faq-accordion --answers answers.json --out ./out
# печатает точные команды kit-storefront-constructor для заливки
```

Паттерна нет (честный отказ, затем — свободная сборка только с подтверждения):
```bash
scripts/section.py patterns match "hero с таймером"   # exit 5 = паттерна нет
# сказать пользователю прямо; с его подтверждения:
scripts/section.py freeform --html s.html --css s.css --schema s.json \
    [--settings s-settings.json] [--title "…"] --out ./out --confirm
# без --confirm exit 3; гейты те же, что у new (хардкод цвета — WARNING),
# G11 не применяется — паттерна нет; compose/gap-report — ещё заглушки
```

Проверить чужой или экспортированный шаблон:
```bash
scripts/section.py lint ./exported-template --json
```

Справочник (вместо чтения страниц целиком):
```bash
scripts/section.py ref tag ya-kit-carousel
scripts/section.py ref schema-ref ImageModel
scripts/section.py ref helper or
```

Правка залитой секции — переезд на новый шаблон (командами соседа, см. правило 16):
```bash
# kit.py templates export --id <uuid> --out DIR   # текущая вёрстка
# kit.py section show --id <uuid>                 # чем поля заполнены сейчас
scripts/section.py lint DIR                       # работает и над чужим экспортом
# kit.py templates create ... --confirm
# kit.py content pull --out work.json             # заново: новый шаблон должен попасть в снимок
# kit.py section set --work work.json --id <uuid> --template-id <новый> --settings-file s.json
# kit.py content push --work work.json --commit-message "..." --no-publish --confirm
```
`adopt` и `lint --against-settings` (автоматический перенос настроек и сверка новой
схемы с живыми) — итерация 2. До них соответствие полей задаёт человек, а страховкой
служит просмотр без публикации.

## Pattern map

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `hero` | первый экран: заголовок + подзаголовок + CTA + фото | titleText (+subtitle, кнопка) | main, landing |
| `advantages` | сетка плиток преимуществ 3–4 шт | modules (title+description) | main, landing |
| `cta-band` | акцентная полоса призыва с кнопкой | titleText, buttonText | main, landing |
| `text-media-split` | текст + изображение в две колонки («о нас») | titleText, contentText (markdown) | main, landing |
| `faq-accordion` | вопросы-ответы с аккордеонами | titleText, modules (question+answer) | main, landing |
| `static-reviews` | карусель карточек отзывов | titleText, modules (отзывы) | main, landing |
| `before-after` | сравнение до/после с ползунком | titleText (картинки — в форме) | main, landing |
| `running-line` | бегущая строка-marquee | modules (фразы) | main, landing |
| `goods-snippets` | сетка товаров из подборки | titleText (подборка — в форме) | main, landing |
| `goods-cover` | обложка с текстом + карусель товаров | cover (title+description) | main, landing |
| `goods-filters` | полка товаров с вкладками | titleText, modules (вкладки) | main, landing |
| `selected-custom-snippets` | избранные товары пользователя | — | main, landing |
| `custom-snippets` | листинг категории с фильтрами | — | listing |
| `custom-product-card` | полная кастомная карточка товара | — | pdp |

Первые четыре — handmade из атомов реестра (в официальной доке секций для главной нет), остальные десять импортированы из doc-примеров с патчами; происхождение и патчи — в `references/patterns/INDEX.md`. Картинки, ссылки и источники товаров передаются в answers объектами (изображение — из вывода `kit.py media upload`; ссылка — `{link, linkId}`; товары — `{id, slug, type}`) либо задаются мерчантом в форме — `new` печатает список незаполненных заглушек. Вертикальный ритм страницы — настройки `verticalRhythm`/`verticalRhythmTouch` каждого паттерна (spacing-токены). Чего в каталоге нет (подписка на рассылку, карта пунктов выдачи, таймер распродажи, сетка категорий, баннер с текстом поверх) — честный отказ + `gap-report` (итерация 3).

## Reference map

| Тема | Страница | Источник |
|---|---|---|
| Теги и атрибуты | `references/generated/tags.md` | generated — руками не править |
| Хелперы | `references/generated/helpers.md` | generated |
| Контроллеры и payload | `references/generated/controllers.md` | generated |
| CSS-токены и правила | `references/generated/tokens.md` | generated |
| JSON Schema и $ref | `references/generated/schema-keywords.md` | generated |
| Контекст данных | `references/generated/data-context.md` | generated |
| Механика движка | `references/manual/rules.md` | manual |
| Ловушки | `references/manual/pitfalls.md` | manual |
| Сценарии и передача соседу | `references/manual/workflow.md` | manual |
| Что проверяется гейтами | `references/gates.md` | manual |

`references/generated/*.json` и тела паттернов в контекст не грузить никогда — их читает только `section.py`.

## Exit codes

- `0` ok; `2` локальная валидация / гейты / строгая свежесть; `3` требуется явный флаг или команда ещё не реализована; `5` паттерн не найден (мягкий ожидаемый исход).
- `1` и `4` зарезервированы под шкалу конструктора (HTTP/API, «правка опубликована старым сервером») и здесь не возникают никогда — сети нет. `6` зарезервирован под будущий исход «рендер выполнен, секция пуста».

## Scope boundaries

- Заливка, привязка (`section add`), публикация, версии, страницы, медиа — **kit-storefront-constructor**.
- Схемы нативных виджетов и их настройки — **kit-storefront-constructor** (storefront BFF).
- Каталог, товары, заказы, цены — **yandex-kit-cabinet**.
- Диагностика магазина — **kit-store-checkup**.
- Этот скил не делает ни одного HTTP-запроса и не читает ни `KIT_TOKEN`, ни `KIT_TOKEN_FILE`, ни `~/.yandex-kit-skills/kit_api.token`.
