# Промо и лояльность — промокоды, скидки, подарки, бейджи, услуги

Endpoint map for the promo/loyalty domain. Auth, base URL, tool flags,
pagination and global conventions live in [`api.md`](api.md).
Mutating = POST/PUT/PATCH/DELETE (all need `--confirm`).

## Промокоды

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/promocodes` | Получение списка промокодов (обязателен `-q status=…`) |
| POST | `/v1/promocodes` | Создание промокода |
| GET | `/v1/promocodes/{id}` | Получение промокода по ID |
| PATCH | `/v1/promocodes/{id}` | Обновление промокода |
| GET | `/v1/promocodes/{id}/categories` | Категории, к которым применяется промокод |
| GET | `/v1/promocodes/{id}/collections` | Коллекции, к которым применяется промокод |
| GET | `/v1/promocodes/{id}/variants` | Товары промокода |
| POST | `/v1/promocodes/{id}/objects/add` | Добавление объектов в промокод |
| POST | `/v1/promocodes/{id}/objects/remove` | Удаление объектов из промокода |

## Группы промокодов

Группа — это один набор правил и много кодов внутри (например, персональные
коды на рассылку). Одиночные промокоды выше и группы ниже — разные ресурсы.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/promocode_groups` | Список групп промокодов (пагинация) |
| POST | `/v1/promocode_groups` | Атомарное создание группы вместе с набором кодов |
| GET | `/v1/promocode_groups/{id}` | Группа промокодов по ID |
| PUT | `/v1/promocode_groups/{id}` | Обновление группы (полная замена, не PATCH) |
| DELETE | `/v1/promocode_groups/{id}` | Удаление группы вместе со всеми её кодами |
| POST | `/v1/promocode_groups/{id}/objects/add` | Привязка товаров/категорий/коллекций (≤500 за запрос) |
| POST | `/v1/promocode_groups/{id}/objects/remove` | Отвязка объектов (≤500 за запрос) |
| GET | `/v1/promocode_groups/{group_id}/codes` | Список кодов в группе (пагинация) |
| POST | `/v1/promocode_groups/{group_id}/codes` | Добавление кода в группу |
| PATCH | `/v1/promocode_groups/{group_id}/codes/{code_id}` | Изменение строкового значения кода |
| DELETE | `/v1/promocode_groups/{group_id}/codes/{code_id}` | Удаление кода из группы |

Ньюансы: обновление группы — это `PUT` (не `PATCH`, как у остальных ресурсов).
`409 Conflict` приходит, когда добавляемый или переименовываемый код уже занят в
другой группе, а также при изменении периода действия, конфликтующем с уже
выданными кодами. `DELETE` группы удаляет все её коды — операция необратима, её
надо показывать пользователю особенно явно.

## Скидки

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/discounts` | Получение списка скидок (обязателен `-q status=…`) |
| POST | `/v1/discounts` | Создание скидки |
| GET | `/v1/discounts/{id}` | Получение скидки по ID |
| PATCH | `/v1/discounts/{id}` | Обновление скидки |
| GET | `/v1/discounts/{id}/categories` | Категории, к которым применяется скидка |
| GET | `/v1/discounts/{id}/collections` | Коллекции, к которым применяется скидка |
| GET | `/v1/discounts/{id}/variants` | Товары скидки |
| POST | `/v1/discounts/{id}/archive` | Архивация скидки |
| POST | `/v1/discounts/{id}/unarchive` | Разархивация скидки |
| POST | `/v1/discounts/{id}/objects/add` | Добавление объектов в скидку |
| POST | `/v1/discounts/{id}/objects/remove` | Удаление объектов из скидки |

### Правила поведения скидок и услуг

- Архивную скидку нельзя обновлять — сначала `unarchive`, потом `PATCH`.
- Привязка категорий или коллекций к скидке **автоматически** переключает её
  режим в `SELECTED_CATEGORIES_COLLECTIONS`; предупреждайте об этом, если
  пользователь ожидал прежнюю область действия.
- У услуги (addon) `binding_mode` нельзя изменить после создания.

## Подарки

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/gifts` | Получение списка подарков |
| POST | `/v1/gifts` | Создание подарка |
| GET | `/v1/gifts/{id}` | Получение подарка по ID |
| PATCH | `/v1/gifts/{id}` | Обновление подарка |
| DELETE | `/v1/gifts/{id}` | Удаление подарка |
| GET | `/v1/gifts/{id}/variants` | Товары подарка |
| POST | `/v1/gifts/{id}/variants` | Добавление товаров в подарок |
| DELETE | `/v1/gifts/{id}/variants` | Удаление товаров из подарка |

## Подарочные карты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/gift_cards` | Получение списка подарочных карт |
| GET | `/v1/gift_cards/{gift_card_id}` | Получение подарочной карты по ID |

## Бейджи

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/badges` | Получение списка бейджей |
| POST | `/v1/badges` | Создание бейджа |
| GET | `/v1/badges/{badge_id}` | Получение бейджа по ID |
| PATCH | `/v1/badges/{badge_id}` | Обновление бейджа |
| DELETE | `/v1/badges/{badge_id}` | Удаление бейджа |
| GET | `/v1/badges/{badge_id}/variants` | Товары бейджа |
| GET | `/v1/badges/{badge_id}/categories` | Категории бейджа |
| GET | `/v1/badges/{badge_id}/collections` | Коллекции бейджа |
| POST | `/v1/badges/{badge_id}/objects/add` | Добавление объектов в бейдж |
| POST | `/v1/badges/{badge_id}/objects/remove` | Удаление объектов из бейджа |

## Услуги (addons)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/addons` | Получение списка услуг |
| POST | `/v1/addons` | Создание услуги |
| GET | `/v1/addons/{id}` | Получение услуги по ID |
| PATCH | `/v1/addons/{id}` | Обновление услуги |
| DELETE | `/v1/addons/{id}` | Удаление услуги |
| GET | `/v1/addons/{id}/variants` | Товары услуги |
| GET | `/v1/addons/{id}/categories` | Категории услуги |
| GET | `/v1/addons/{id}/collections` | Коллекции услуги |
| POST | `/v1/addons/{id}/objects/add` | Добавление объектов в услугу |
| POST | `/v1/addons/{id}/objects/remove` | Удаление объектов из услуги |
