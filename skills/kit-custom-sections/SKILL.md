---
name: kit-custom-sections
description: |
  Build a custom Яндекс.Кит storefront section (YandexKit.Mystique) offline from a catalog of
  lint-checked patterns when no native widget fits: hero, overlay banner, intro, CTA, advantages,
  steps, stats, team, services, FAQ, reviews, logos, video reels, newsletter, contacts, product
  shelves, product page, listing, dividers. Fill a pattern's settings, get a validated html + css
  + json_schema triple plus kit-storefront-constructor upload commands; slot mode builds a
  store-wide header or footer. Also lints an exported template offline (tags, helpers, $ref
  pickers, theme variables, schema bijection) and answers questions on tags, controllers, $ref
  types. Use for «сделай кастомную секцию для Кита», «свёрстай секцию витрины»,
  «нужен FAQ / отзывы / полка товаров на Ките», «своя шапка или подвал магазина», «поправь мою
  кастомную секцию». Not for native page design (kit-storefront-design), upload or publishing
  (kit-storefront-constructor), catalog and orders (yandex-kit-cabinet). Offline, no token.
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
3. Кастомная секция — ровно тройка html + css + json_schema. Поле `script` у шаблона в контракте бэка есть, но этот скил JS не генерирует — это политика скила, а не ограничение платформы: скрипт исполняется только на собственном домене магазина, а на `*.yandex.ru` (адрес `<slug>.yastore.yandex.ru`, превью конструктора) не исполняется вовсе, и офлайн его поведение не проверить. Механика поля — `references/manual/rules.md`.
4. `{{{ }}}` не поддерживается; `{{!-- --}}` печатается как текст; блочный хелпер не работает внутри значения атрибута.
5. Теги — только из реестра движка (114: 8 встроенных + 44 контроллера + 62 атома; точные счётчики печатает `doctor`), из них два без префикса `ya-kit`: `yandex-pay-badge-discount`, `yandex-pay-badge-split`. Неизвестный блочный хелпер = падение секции; неизвестный тег = молчаливая пустота.
6. `<script>` и `<style>` внутри html вырезаются молча — CSS только в поле `css`.
7. CSS скоупится платформой автоматически (`#ya-kit-<templateId>`) — руками не префиксовать. `:global(` запрещён: так переписываются стили всей витрины. Одно исключение — контент `ya-kit-drawer`/`ya-kit-paranja`: платформа рендерит его порталом вне скоупа секции, поэтому он стилизуется только `:global(.<уникальный префикс> …)` (гейт G6 — WARNING).
8. Символ `<` в CSS запрещён (вырезается вместе с хвостом до `>`); комбинаторы `>` и `+` разрешены.
9. Цвета и отступы — `var(--ys-*)`; хардкод hex/rgb перебивает тему магазина, потому что #id-скоуп даёт высокую специфичность.
10. Каждое `{{settings.X}}` обязано быть в `properties` json_schema и наоборот.
11. json_schema — без `oneOf/allOf/if/then/else/patternProperties`, иначе секцию потом нельзя редактировать через `kit-storefront-constructor`.
12. `$ref` — только из белого списка (21 резервный + 5 служебных + `#/definitions/partials/*` + собственные определения того же документа). Резервный `#/definitions/ProductVariant` (выбор варианта товара) тела в схеме витрины не имеет: в шаблоне опираться только на ключ `variantId` его значения.
13. `alias` шаблона пуст у любой секции страницы. `YandexKit.Header` / `YandexKit.Footer` ставит только режим слота (`new` / `freeform --slot header|footer`) и только с `--confirm-slot`: такой шаблон становится шапкой или подвалом **каждой** страницы витрины, включая страницы, которые сервер добавит сам. Скрыть секцию в слоте сервер позволяет, но витрина тогда останется без шапки или подвала: нативный блок сам не вернётся (по коду, живьём не проверено). Запуск без `--confirm-slot` печатает предупреждение — показать его пользователю и получить явное согласие именно на смену шапки или подвала всей витрины; `--confirm-slot` без такого согласия не подставлять. Правила слота (без `h1` и `script`, секция без отступов и на всю ширину, меню только через `$ref: #/definitions/MenuId` с id из нативного слота, порядок 1/9999 держит сам слот) — `references/manual/rules.md`, гейт G12.
14. `template_id` вешать только на секции с виджетом `YandexKit.Mystique`. Ни бэк, ни push-гейты конструктора это не проверяют.
15. Ничего не заливать самому — печатать готовые команды соседа; `--confirm` не подставлять и чужой скрипт не запускать.
16. Живой шаблон править нельзя: витрина инлайнит его актуальную строку во все версии контента, поэтому правка задним числом меняет и старые версии. Залитая секция правится переездом на **новый** шаблон: `templates export` → правки → `lint` → `templates create` → `content pull` заново → `section set --template-id новый --settings-file` → `content push --no-publish` на просмотр → публикация. Настройки при переезде обязательны: перед тем как отдать команду, сверьте, что каждое заполненное поле живой секции есть в новой схеме. Поля, которому в новой схеме места нет, — это потеря данных: назовите его пользователю и дождитесь решения, молча не выбрасывайте. Обратное тоже меняет вид: ключам новой схемы, которых в живых настройках нет, витрина подставляет их `default` (по коду витрины с 26.10.2026), поэтому новая функция с умолчанием «включено» появится и у перенесённой секции. Назовите такие ключи пользователю; прежний вид сохраняют только явные значения в `--settings-file` (что меняется у паттернов каталога при переезде со старой версии, написано в их превью). Просмотр без публикации здесь обязателен, а не по желанию: отката версии у конструктора нет. Если у живого шаблона непустое поле `script`, а в новом шаблоне его нет, переезд теряет JS — назовите это пользователю до переезда; переносимый JS покажите ему целиком: у скрипта доступ к странице, и линт его не проверяет.

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
scripts/section.py patterns show faq-accordion --params --looks
scripts/section.py new --pattern faq-accordion --answers answers.json --out ./out [--look cards-two-columns]
# печатает точные команды kit-storefront-constructor для заливки:
# новая секция — section add, замена шаблона у живой — section set (--replace <id секции>)
```

Паттерна нет (честный отказ, затем — свободная сборка только с подтверждения):
```bash
scripts/section.py patterns match "таймер распродажи"   # exit 5 = паттерна нет
# сказать пользователю прямо; с его подтверждения:
scripts/section.py freeform --html s.html --css s.css --schema s.json \
    [--settings s-settings.json] [--title "…"] --out ./out --confirm
# без --confirm exit 3; гейты те же, что у new (хардкод цвета — WARNING),
# G11 не применяется — паттерна нет; compose/gap-report — ещё заглушки
```

Проверить чужой или экспортированный шаблон:
```bash
scripts/section.py lint ./exported-template --json
scripts/section.py lint ./exported-header --slot header   # шаблон шапки — по правилам слота
```

Шапка или подвал всей витрины (режим слота, правило 13):
```bash
scripts/section.py new --pattern <паттерн слота> --answers a.json --out ./out --slot header
# exit 3 + предупреждение: показать пользователю; только с его явного согласия:
scripts/section.py new --pattern <паттерн слота> --answers a.json --out ./out --slot header --confirm-slot
# своя вёрстка: freeform … --confirm --slot header|footer --confirm-slot
# печатает templates create --chrome-slot header и шаги переключения слота
```
id меню — до сборки, из нативного слота: `kit.py section show --id <id слота>`
(у шапки `modules.main.menu` — строка у шапки старой версии, объект с id в `modules.main.menu.menu`
у текущей — и `modules.additional.menu`; у подвала `modules[].menu`, `legalInfo.menu`).
Сценарий целиком — `references/manual/workflow.md`.

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

**Сначала натив.** Какие секции поставить на страницу, в каком порядке и с какими настройками нативных виджетов — скил `kit-storefront-design` (рецепты страниц, пресеты, тема). Этот скил нужен, когда блок нативным виджетом не выразить. Типовые развилки:

- Баннер с текстом поверх фото — нативный `YandexKit.BannersCarousel` (заголовок, затемнение, кнопка, отдельное фото или видео для телефона); `hero-overlay` — только когда нужно то, чего у натива нет: своё выравнивание и кадр 1:1 или 3:4 на телефоне, кадр 16:9 или 4:3 на компьютере, две кнопки, `h1`. Одно широкое видео и логотип-картинка баннером — тоже `BannersCarousel`.
- FAQ — нативный `YandexKit.Faq` (подзаголовок, «остров», стрелки или плюс, первый вопрос открыт, узкий по центру или с заголовком слева). Разметку FAQPage для поисковиков отдают оба: натив и `faq-accordion` с версии 3 (тем же тегом `ya-kit-json-ld` и хелпером `jsonLdSchema`), поэтому разметка — не повод уходить в кастом. `faq-accordion` — две колонки вопросов, ответ со смещением, карточка на вопрос.
- Стена логотипов, ровная сетка категорий или фото-ссылок — нативный `FeaturedCollections` (для логотипов: 2:1, `imageFit` contain, 4–6 колонок, две на телефоне); `logo-wall` — подписи и ссылки у логотипов или бегущая строка, `tile-mosaic` — ячейки разной ширины и высоты.
- Заголовок с абзацем — нативный `RichText`; `section-intro` — крупный заголовок раздела, полоса над полкой, манифест с тегами-ценностями.
- Страница товара — нативная карточка остаётся, блоки под ней — `pdp-product-facts` и паттерны со страницей pdp. `custom-product-card` — только на явную просьбу заменить карточку целиком: вместе с нативной уходят липкая кнопка покупки на телефоне, строки Сплита и Плюса и модуль преимуществ, а выбор вариантов и доставку кастомная карточка рисует по-своему.

**Информационный блок ведёт дальше.** У `advantages`, `steps`, `stats-band`, `testimonial-spotlight`, `static-reviews`, `divider-rows`, `faq-accordion`, `cards-with-drawer` и `before-after` есть необязательное поле `cta` `{text, link, style: button|link}` — кнопка или ссылка под содержимым и его сноской, по линии заголовка (каталог, условия доставки, все отзывы). Кнопку класть внутрь блока, а не отдельной секцией `button-row`: та висит между блоками. Без `cta` `new` печатает `info no-exit`. Здесь и у каждой кнопки каталога нужны обе половины: подпись без ссылки — `warning cta-link` (кнопка не выводится), ссылка без подписи — `ERROR cta-text`.

### Первый экран и заголовки

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `hero` | первый экран или закрывающий блок: надзаголовок, заголовок h1/h2, подзаголовок, кнопка, значки доверия, фото (отдельное для телефона); вид с отступами или панель 50/50 с заливкой; без фото — текст в одну колонку | titleText (+eyebrow, кнопка, фото с alt, layout) | main, landing |
| `hero-overlay` | фото на всю ширину блока с живым заголовком, текстом и до двух кнопок поверх; своё выравнивание и кадр на телефоне | title, image, imageAlt | main, landing, listing |
| `section-intro` | крупный заголовок раздела над нативным блоком, полоса над полкой или манифест с тегами-ценностями | titleText (+лид, ссылка, теги) | main, landing, pdp |
| `type-band` | одно слово или фраза во всю ширину живым текстом: шапка полки со «Смотреть все», вордмарк, название категории | word | main, landing, listing, pdp |

### Призыв к действию

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `cta-band` | полоса призыва: текст слева и кнопка справа или всё по центру, в том числе одна кнопка на подложке | buttonText, buttonUrl (+titleText, subtitleText, layout) | main, landing |
| `button-row` | отдельная кнопка или ссылка-полоса во всю ширину под нативной секцией без своей кнопки (полка, подборка, RichText); информационным блокам каталога — их поле `cta` | text, link | main, landing, listing, pdp |
| `email-subscribe` | своя форма подписки на e-mail, необязательный промокод в заголовке | headline (+ promo {promocodeId}) | main, landing |

### О магазине и услугах

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `text-media-split` | текст и изображение в две колонки («о нас», история, состав); по желанию надзаголовок, кнопка, сноска, картинка для телефона, цветная панель, встык | titleText, contentText (markdown); с изображением — imageAlt | main, landing, pdp |
| `advantages` | преимущества: карточки с иконкой или номером, мозаика равных плиток с фото, карусель; лента на телефоне | modules (title+description) | main, landing, pdp |
| `steps` | пронумерованные шаги процесса («как это работает», как заказать) | modules (title+text) | main, landing, pdp |
| `stats-band` | ключевые цифры крупно с подписями («о нас в цифрах»); цифры только от мерчанта | modules (value+caption) | main, landing |
| `media-cards` | карточки «фото, заголовок, подпись, текст»: команда, мастера, амбассадоры, истории клиентов, ингредиенты, материалы; сетка или карусель | modules (title+text, фото) | main, landing |
| `service-rows` | ряды услуг или направлений: пара фото (или одно), описание со списком и кнопка в каждом ряду; фото слева, справа или зигзагом (один ряд — `text-media-split`) | modules (title+text) | main, landing |
| `cards-with-drawer` | сетка карточек, у каждой шторка с подробностями: услуги, доставка, оплата | modules (title+description, details) | main, landing |
| `divider-rows` | строки «название — значение» с линиями: состав, характеристики, прайс, кому подойдёт | modules (name+value+description) | main, landing |
| `contact-cards` | контакты магазина из настроек кабинета (телефоны, почта, адреса, режим работы, соцсети): карточки, строки или кнопки-мультиссылка; свои строки — реквизиты, отделы, ссылки | — (контакты берутся из кабинета; modules — по желанию) | main, landing |
| `faq-accordion` | вопросы и ответы аккордеоном: две колонки, ответ со смещением, карточки | modules (question+answer) | main, landing, pdp |

### Отзывы, доверие, медиа

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `static-reviews` | карусель карточек отзывов: рейтинг, автор, текст | titleText, modules (отзывы) | main, landing |
| `testimonial-spotlight` | один отзыв на слайд крупной цитатой по центру: звёзды, автор, подпись, фото (живые отзывы о товаре — нативная страница отзывов) | modules (text+author) | main, landing |
| `logo-wall` | логотипы партнёров, клиентов или изданий: сетка с подписями и ссылками или бегущая строка | modules (name + логотип) | main, landing |
| `before-after` | сравнение двух фото с перетаскиваемой границей | titleText (картинки — в форме) | main, landing |
| `video-reels` | карусель коротких вертикальных видео с подписью и ссылкой | titleText (ролики — в форме) | main, landing, pdp |
| `social-feed` | шапка с кнопками соцсетей магазина из кабинета и сетка фото-ссылок на публикации; без фото — полоса «подписывайтесь». Публикации выбираются вручную, это не живая лента; видео — `video-reels`, контакты и мессенджеры — `contact-cards` | modules (image+alt+link; `[]` — полоса) | main, landing, pdp |

### Товары

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `goods-snippets` | полка товаров: сетка, лента на телефоне или карусель; карточка по теме | titleText (подборка — в форме) | main, landing |
| `goods-cover` | текстовая обложка (заголовок h2, описание, кнопка с подписью) рядом с каруселью товаров; обложка слева или справа, две карточки в ряду на телефоне | cover (title+description; кнопка — buttonText и url) | main, landing |
| `goods-filters` | полка товаров с вкладками-подборками | titleText, modules (вкладки) | main, landing |
| `product-editorial` | один товар с кнопкой покупки рядом с 1–2 фото кампании (запуск, новинка, товар недели); товар — первый из источника, лучше подборка из одного товара | dataSource, imageMain (+imageSide, alt) | main, landing |
| `tile-mosaic` | бенто-сетка ячеек-ссылок разной ширины и высоты: фото и текстовые ячейки, лукбук-коллаж без зазоров | modules (kind+size, фото с imageAlt) | main, landing, listing |
| `selected-custom-snippets` | избранные товары покупателя | — | main, landing |

### Страница товара и листинг

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `pdp-product-facts` | блок под нативной карточкой товара: выбранные характеристики из каталога (состав, уход, комплектация) аккордеоном, строками или в две колонки | modules (названия характеристик) | pdp |
| `custom-product-card` | карточка товара вместо нативной: галерея, цена, варианты, липкая панель покупки на телефоне, свои блоки из характеристик; только на явную просьбу заменить карточку | — (свои блоки — modules) | pdp |
| `listing-grid` | листинг категории сеткой со своей карточкой: колонки, плотность, бренд, бейджи, фильтры, сортировка | — (всё в настройках) | listing |
| `custom-snippets` | листинг категории списком (строка на товар) с фильтрами | — | listing |
| `category-subnav` | ряд подкатегорий или пунктов меню ссылками либо чипсами; шапка категории: крошки, обложка, название | source: current или menu (+ id меню) | listing; с menu — любая |

### Ленты и разделители

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `running-line` | бегущая строка-marquee из повторяющихся фраз | modules (фразы) | main, landing |
| `ticker-band` | тонкая лента-разделитель в одну строку: фразы с разделителем, видна на светлой теме | modules (фразы) | main, landing |
| `shaped-divider` | фигурный край между блоками разных палитр: волна, фестоны, зубцы, дуга, скос; без текста и картинок | palette + tone соседа, к которому примыкает фигура | main, landing, listing, pdp, cart |

### Шапка и подвал всей витрины (режим слота, правило 13)

| id | Что это | Какие данные нужны | Страницы |
|---|---|---|---|
| `site-header` | шапка: логотип, меню строкой (список или мега-меню), поиск, вход, корзина; на телефоне бургер с уровнями меню. Содержимое по ширине и полям темы, как у остальных секций. Больше 5 пунктов первого уровня, логотип по центру или поле поиска — `layout.navMode: wide`; больше 6 — служебные пункты во второе меню или `burger` | mainMenu (id меню нативной шапки) | все страницы; только `new --slot header --confirm-slot` |
| `site-footer` | подвал: бренд и соцсети, колонки из меню магазина, контакты, документы | modules (id меню из нативного подвала) | все страницы; только `new --slot footer --confirm-slot` |

Часть паттернов — handmade из атомов реестра (в официальной доке таких секций нет), остальные импортированы из doc-примеров с патчами; какой паттерн откуда и с какими патчами — в `references/patterns/INDEX.md`, полный список печатает `patterns list`. Картинки, ссылки и источники товаров передаются в answers объектами (изображение — из вывода `kit.py media upload`: `fileId` = `id`, `filePath` = `public_path`, а при непустом `file_metadata.thumb_hash` ещё `metadata` = `{thumbHash, backgroundColors}` из `file_metadata` — без него картинка грузится без размытого превью; ссылка — `{link, linkId}`; товары — `{id, slug, type}`) либо задаются мерчантом в форме — `new` печатает незаполненные заглушки двумя списками: «обязательно заполнить» и «не нужно при этих answers» (необязательное поле или условное — `conditional_fields` в `pattern.yaml`); пустую необязательную картинку или ссылку `new` в settings не пишет. Тексты стартовых настроек и `default` схемы пустые, кроме меток интерфейса («В корзину», «Нет в наличии»): фразу-заглушку каталога (слоган, лид, текст модуля), попавшую в settings из answers, `new` называет (`demo-copy`); типовой заголовок или подпись кнопки («Частые вопросы», «Контакты», «Смотреть все») — нет: такую подпись магазин вправе выбрать сам. У паттерна, где у изображения есть поле alt, оно обязательно, если изображение задано, — и у каждого элемента `modules` тоже (`patterns show` печатает строки `alt_required`, гейт G21). Отступы по краям на компьютере паттерны берут из темы, как нативные секции: `section.hPaddingDesktop` задавать, только когда блок нарочно шире или уже соседей. Вертикальный ритм — как у нативных соседей: `verticalRhythm`/`verticalRhythmTouch` по умолчанию `0`, отступ дают `section.paddingTop`/`paddingBottom`; 300–500 — только намеренный разрыв главы (смена палитры, закрывающий блок, лендинг целиком из кастома), `new` без `--page-rhythm` напоминает об этом. Чего в каталоге нет (карта пунктов выдачи, таймер распродажи) — честный отказ + `gap-report` (итерация 3). Шапка и подвал витрины — только режим слота: паттерн слота `patterns list` помечает `[слот header|footer]`, своя вёрстка — `freeform --slot`.

## Сочетания и виды

Страница, собранная из одних и тех же блоков на настройках по умолчанию, выглядит шаблонно. Поэтому:

- **У паттерна несколько видов.** `patterns show <id> --looks` печатает 2–4 именованных вида с настройками (`advantages`: numbered, tiles-photo, carousel, cards-icons). `new` без флага собирает стартовый вид; другой — `new --look <имя>`. У части паттернов стартовый вид отличается от вида прошлых версий (`advantages`: numbered вместо cards-icons), у остальных он и есть прежнее оформление — `new` печатает другие виды, выбирать из них осознанно. Картинок видов нет: вид смотреть в превью proposal-версии. `patterns match` печатает 2–3 кандидата с их видами — сравнить, а не брать первого.
- **Два блока-сетки карточек подряд не ставить** (`advantages`, `cards-with-drawer`, `media-cards`, `steps` в виде карточек): между ними — блок другой плотности.
- **После полки товаров менять плотность:** `text-media-split`, `testimonial-spotlight`, `type-band` шапкой следующей полки, а не ещё одна сетка.
- **На странице не больше одного блока «преимущества»**; второй набор тезисов — `stats-band` (цифры) или `steps` (порядок действий).
- **Смена палитры — с переходом:** `shaped-divider` или `ticker-band` на стыке, а не голая граница цветов.
- **На главной хотя бы один блок с крупной типографикой или фото во всю ширину:** `hero-overlay`, `type-band`, `section-intro` в виде display, `tile-mosaic`.
- **Один вид — один раз:** два блока одного паттерна на странице — в разных видах (`--look`), иначе они читаются копией. Папки секций одной страницы — рядом (у общего `work.json`): `new` напоминает (`same-look`, info), если в соседней папке тот же паттерн уже собран в том же виде.
- **Ритм и выходы:** ритм как у соседей (`verticalRhythm` 0), информационный блок ведёт дальше полем `cta`.

## Reference map

| Тема | Страница | Источник |
|---|---|---|
| Теги и атрибуты | `references/generated/tags.md` | generated — руками не править |
| Хелперы | `references/generated/helpers.md` | generated |
| Контроллеры и payload | `references/generated/controllers.md` | generated |
| Payload контроллеров по коду движка | `references/manual/controller-payloads.md` | manual |
| CSS-токены и правила | `references/generated/tokens.md` | generated |
| JSON Schema и $ref | `references/generated/schema-keywords.md` | generated |
| Контекст данных | `references/generated/data-context.md` | generated |
| Механика движка | `references/manual/rules.md` | manual |
| Ловушки | `references/manual/pitfalls.md` | manual |
| Сценарии и передача соседу | `references/manual/workflow.md` | manual |
| Что проверяется гейтами | `references/gates.md` | manual |
| Каталог паттернов: назначение, страницы, происхождение | `references/patterns/INDEX.md` | generated |

`references/generated/*.json` и тела паттернов в контекст не грузить никогда — их читает только `section.py`.

## Exit codes

- `0` ok; `2` локальная валидация / гейты / строгая свежесть; `3` требуется явный флаг или команда ещё не реализована; `5` паттерн не найден (мягкий ожидаемый исход).
- `1` и `4` зарезервированы под шкалу конструктора (HTTP/API, «правка опубликована старым сервером») и здесь не возникают никогда — сети нет. `6` зарезервирован под будущий исход «рендер выполнен, секция пуста».

## Scope boundaries

- Заливка, привязка (`section add`), публикация, версии, страницы, медиа — **kit-storefront-constructor**. Шаблон шапки или подвала заливается там же (`templates create --chrome-slot`); переключение нативного слота на шаблон и возврат к нативному — шаг в конструкторе кабинета, а не команда.
- Схемы нативных виджетов и их настройки — **kit-storefront-constructor** (storefront BFF); какие нативные секции поставить на страницу, в каком порядке и с какими настройками — **kit-storefront-design**.
- Каталог, товары, заказы, цены — **yandex-kit-cabinet**.
- Диагностика магазина — **kit-store-checkup**.
- Этот скил не делает ни одного HTTP-запроса и не читает ни `KIT_TOKEN`, ни `KIT_TOKEN_FILE`, ни `~/.yandex-kit-skills/kit_api.token`.
