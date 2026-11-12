# Payload контроллеров по коду движка (рукописное, 2026-09-24)

`generated/controllers.md` перечисляет теги и атрибуты; тела payload документированных контроллеров — в публичной доке. Здесь — то, чего в доке нет: payload контроллеров без доки и поля, которые у документированных контроллеров есть в коде, но не описаны. Всё сверено по коду витрины на дату в заголовке. Payload доступен под алиасом из `as` (по умолчанию `controller`) только внутри тега контроллера.

## Контроллеры без доки

### `ya-kit-products-group-controller`

Товары по списку вариантов.

- Атрибуты: `as`, `variant-ids` — массив id вариантов товара, обязателен.
- Payload: `model` — объект `{<id варианта>: товар-сниппет}` или `null`, `isLoading`.
- Id вариантов из настроек секции собирает `mapBy`: `variant-ids="{{mapBy settings.modules 'product.variantId'}}"`, если модули выбраны через `$ref ProductVariant` (см. `pitfalls.md`). Сквозной путь от формы до витрины живьём не проверен.

### `ya-kit-discount-ladder-controller`

Товары акции «лесенка скидок» с пагинацией и сортировкой.

- Атрибуты: `as`, `per-page` — целое больше нуля; иначе берётся размер страницы листинга по умолчанию.
- Payload: `model` = `{discountLadder, items, total, productsQuantity, pagination: {current, total}, sorting: {value, options}}`, `isLoading`, `onPageChange(page)`, `onSortingChange(sorting)`, `getPageUrl(page)`.

### `ya-kit-product-feedback-listing-controller`

Отзывы о товаре. Работает на странице товара и на странице отзывов и только при включённых отзывах магазина; в других местах список пуст, а `onOpenFeedback` и `onOpenMedia` ничего не делают.

- Атрибуты: `as`.
- Payload: `items` (загруженные отзывы, до загрузки `[]`), `totalCount`, `currentIndex`, `meta` (сводка отзывов товара), `filters`, `sorting`, `filter` (slug фильтра или `null`), `isLoading`, `hasMore`, `hasError`, `loadNextPage()`, `onIndexChange(index)`, `onSortingChange`, `onFilterChange`, `onOpenFeedback(feedback)`, `onOpenMedia(item)`.
- Атомы отзыва: `ya-kit-product-feedback-card` (`feedback`), `ya-kit-product-feedback-summary`, `ya-kit-product-feedback-overview` (`meta`), `ya-kit-product-feedback-media-tile` (`media`), `ya-kit-rating-stars` (`value`, `count`). Атрибуты — в `tags.md`.

### `ya-kit-product-feedback-media-list-controller`

Медиа из всех отзывов товара. **Только внутри** `ya-kit-product-feedback-listing-controller`: вне его атом бросает исключение, и секция в проде исчезает (гейт G20).

- Атрибуты: `as`.
- Payload: `items` (до загрузки `[]`), `totalCount`, `isLoading`, `hasMore`, `hasError`, `loadNextPage()`.

### `ya-kit-product-feedback-viewer-controller`

Просмотрщик отзыва и его медиа. **Только внутри** `ya-kit-product-feedback-listing-controller` (гейт G20).

- Атрибуты: `as`.
- Payload: `isOpen`, `model` (открытый отзыв или `null`), `items` (медиа просмотрщика; закрыт — `[]`), `currentIndex`, `isLoading`, `hasError`, `onIndexChange(index)`, `onRetry()`, `onClose()`.

## Поля документированных контроллеров, которых нет в доке

- **`ya-kit-email-subscription-controller`** — `enable-agreement` по умолчанию `true`: без привязанного `ya-kit-checkbox` (`value="agreement"`, `is-selected="{{alias.model.agreement}}"`, `on-change="{{alias.onAgreementChange}}"`) подписка не отправится никогда, поэтому режим без галочки задаётся явным `enable-agreement="{{eql …}}"`, а не отсутствием атрибута. Отмеченная заранее и спрятанная галочка — тёмный паттерн согласия. `model.currentStep` на деле принимает только `FORM` и `SUCCESS`: `FAILURE` есть в типе, но не выставляется. Ответ сервера («почта уже подписана», «уже отписана», сбой) приходит строкой в `model.errors.email`; `model.errors.email` и `model.errors.agreement` — строки или пусто (в доке указан `number`), поэтому в `is-error` передавать `{{if alias.model.errors.email true false}}`. Отправка — только вызовом `onSubmit` (кнопкой): тега формы нет, Enter в поле ничего не отправляет. Промокод из `promocode` уходит вместе с адресом.
- **`ya-kit-promocode-controller`** — `model` (`PromocodeModel` или `null`) в коде содержит ещё `minimumOrderAmount` (строка с минимальной суммой заказа в рублях), которого нет в доке; `discountValue` — строка: для `percent` печатается как есть с `%`, для `fixed_amount` — через `formatPrice`. Сравнивать суммы через `gt` можно только после приведения к числу: `gt (multiply alias.model.minimumOrderAmount 1) 0` (`gt` со строкой всегда `false`). Пустой `promocode-id` даёт `model = null`. `isLoading` равен `true`, пока нет ответа (так и при пустом id), поэтому заглушку на время загрузки показывать только при заданном id.
- **`ya-kit-featured-products-controller`** — режим бесконечной подгрузки: `is-feed="true"` и `per-page` (по умолчанию — `count`). Работает для источников типа `category`, `collection`, `last_seen`, `contextCollection`; тогда в payload появляется `feed` = `{isLoading, hasMore, loadNextPage(), perPage}`. Несколько бесконечных полок на одной странице подгружают каждая свои товары.
- **`ya-kit-delivery-options-controller`** — у каждой опции доставки `serviceName`, `isFastest`, `isCheapest`. С 08.10.2026 `isFastest` и `isCheapest` всегда `false` (поля оставлены ради старых шаблонов, бейджей «быстрее всех» и «дешевле всех» платформа больше не считает): ветка `{{#if option.isFastest}}` не покажется никогда.
- **`ya-kit-product-detail-controller`** — `model.feedback` (рейтинг, сводка и превью отзывов товара или `null`) и `onSubscribe` (подписка на поступление товара, которого нет в наличии).
- **`ya-kit-product-snippet-controller`** — `onSubscribe`. У самого товара (`product` сниппета и элементы `model.items` листинга) кроме полей доки есть `brand` (бренд, строка), `categoryName`, `sku` и `feedbackSummary` (`{rating, count}`, бывает не у всех товаров).
- **`ya-kit-products-listing-controller`** — `onSortingChange(sorting)` (значение — `model.sorting.option.value` выбранного варианта; в доке метода нет, им пользуется официальный пример листинга) и `containerRef`. `model` не бывает `null`: `model.pagination` — объект всегда (`{current, total}`, `total` — число страниц), поэтому переключатель страниц показывают по `(gt listing.model.pagination.total 1)`, а не по истинности `model.pagination`. `model.categories.selected` — категория текущей страницы из дерева категорий (поиск по тем же двум slug) или `null` (название — `.title`; `children` есть, `media` всегда `null` — обложку берут из `ya-kit-categories-controller include-images="true"`); `isLoading` — `true` до первого ответа. `per-page` — размер страницы, число (в типе пропсов обязателен). Запрос товаров у контроллера один на страницу и общий с нативным листингом. Если поставить второй контроллер только ради категорий, он разделит с листингом этот запрос. Как тогда выбирается размер страницы, не проверено.
- **`ya-kit-value-controller`** — `onReset`; без `initial-value` значение начинается с `null` (см. `pitfalls.md`).
- **`ya-kit-company-controller`** — `model` (`CompanyModel` или `null` до загрузки), `isLoading`. Кроме полей из доки в модели есть `name` (название магазина) и `invertedLogo` (светлый вариант логотипа, `ImageModel`). `phone`, `mail`, `address`, `workingMode` — массивы `{name, value}`: `name` — «Название поля», которое мерчант задаёт в кабинете (по умолчанию «Телефон», «Эл. почта», «Адрес», «Режим работы»), `value` — значение как введено; первый телефон — `company.model.phone.0.value`. Записи, скрытые в кабинете, уже отфильтрованы; если видимость не задана ни у одной записи, приходит только первая. Нативный подвал выводит только первый элемент каждого массива. `socialNetworks` — `{name, value}`, где `name` — код сети (`telegram`, `vk`, `dzen`, `ok`, `youtube`, `tiktok`, `rhythm`, `whatsapp`, `max`), `value` — ссылка; коды `ok` и `whatsapp` не совпадают с именами иконок (`odnoklassniki`, `whats-app`). Пустой массив в `{{#if}}` — истина: наличие записей проверять через `length`. Нативные шапка и подвал выбирают `logo` или `invertedLogo` по светлоте фона палитры и при отсутствии одного показывают другой; в контексте шаблона светлоты палитры нет — выбор делается настройкой. `formatPhoneNumber` форматирует только российский 11-значный номер (на 7 или 8) в `8 (XXX) XXX-XX-XX`, для остальных возвращает пусто: вывод с запасным значением — `{{when (formatPhoneNumber p.value) (formatPhoneNumber p.value) p.value}}`.
- **`ya-kit-menu-controller`** — `model.items` — дерево: верхний уровень, вложенные пункты в `children` (у пункта без вложенных — `[]`); пункт — `{id, title, url, children}`, ещё `image` (ImageModel, может отсутствовать) и `displaySequence`. `menu` принимает и массив id: тогда `model` — массив меню в том же порядке, `null` на месте ненайденного. Пустой или несуществующий id даёт `model` = `null`; `menu` пустой (`null` или нет атрибута) — запроса нет. Пустую строку из незаполненного поля лучше не передавать: `menu="{{when settings.menu settings.menu null}}"`.
- **`ya-kit-shopping-cart-controller`** — `onApplyPromocode(code)`, `onRemovePromocode()`, `model.promocode` (для атома `ya-kit-shopping-cart-promocode`), `model.discountLadder` (лесенка скидок корзины; у бандла — признак `isLadder`), `getLineMaxQuantity`, `refreshShoppingCart`, `refreshShoppingCartAfterAuth`; `onUpdateProductQuantity` принимает также `itemKey` и `bundleId`.
- **`ya-kit-location-controller`** — `isLoading` всегда `false`; `model` — текущий город покупателя (выбранный вручную, иначе определённый автоматически) или `null`; `onLocationSelect()` открывает выбор города витрины. Сам контроллер всплывающее окно не показывает: предложение выбрать город витрина показывает сама, когда город неизвестен.
- **`ya-kit-user-controller`** — `onLogin()` уводит на вход Яндекса, `onOpenLoginModal()` открывает окно входа витрины.
- **`ya-kit-categories-controller`** — `model` — дерево: корневые категории, вложенные в `children`, дети отсортированы в порядке кабинета. Поля категории: `id`, `slug`, `title`, `url`, `parentId` (у корневой нет), `isHiddenInMenu`, `displaySequence`, `children`, `media`. Картинка категории — поле `media` со строчной буквы (в доке `Media`): `ImageModel` или `null`, заполнено только при `include-images="true"`. В payload есть функция `selectById(x)`: ищет по всему дереву категорию с `id` или `slug`, равным `x`, и возвращает `null`, если не нашла. Вызывается хелпером `call`: `{{#with (call cats.selectById route.context.path.categorySlug) as |current|}}`. `route.context.path.categorySlug` — slug категории на странице категории; на фасетной коллекции slug лежит в `route.context.facetCollection.categorySlug`.
- **`ya-kit-breadcrumbs-controller`** — `model` = `route.context.breadcrumbs` (без крошек — `[]`), `isLoading` всегда `false`. У крошки `url` необязателен.
