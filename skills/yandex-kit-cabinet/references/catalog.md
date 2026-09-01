# Каталог — категории, характеристики, продукты, товары, файлы, склады

Endpoint map for the catalog domain. Auth, base URL, tool flags, pagination and
global conventions live in [`api.md`](api.md). Mutating = POST/PUT/PATCH/DELETE
(all need `--confirm`).

## Domain conventions

- «Продукт» (`/v1/products`) groups variants; «товар» (`/v1/variants`) is the
  sellable unit with price/stocks/media. Media references file IDs from
  `kit.py upload`.
- Variant (товар) statuses: `PUBLISHED` | `HIDDEN` | `ARCHIVED`. Only an
  `ARCHIVED` variant can be deleted permanently.

## Правила поведения (не выражены в схеме)

Проверено по коду хендлеров; схему полей смотрите через
`kit.py describe <METHOD> <path>`.

- **Архив блокирует запись.** Архивные товары, характеристики и склады нельзя
  обновлять через `PATCH` — вернётся `400`. Сначала `unarchive`, потом правка.
- **`archive` / `unarchive` идемпотентны**: повторный вызов для уже архивного
  (или уже активного) объекта проходит успешно и ничего не меняет.
- **Продукт обязан лежать хотя бы в одной категории.** Последнюю категорию
  продукта, у которого есть неархивные товары, нельзя заархивировать без
  `archive_variants=true`. Передача архивной категории в продукт **не**
  разархивирует её автоматически.
- **`slug` генерируется сам** из названия у категорий и складов, если не
  передан, и обязан быть уникальным. Конфликт склада разрешается суффиксом.
- **`PATCH /v1/warehouses/{id}` не архивирует.** Для смены статуса есть только
  `archive` / `unarchive`.
- **Устаревшее поле.** В характеристиках товара используйте `values` (массив),
  а не `value` (строка): singular помечен deprecated и логируется на бэкенде.
- **`DELETE /v1/variants/{id}` необратим** и работает только для `ARCHIVED`.
- Размерная сетка ссылается на `file_id`, который должен быть заранее загружен
  через `POST /v1/files`.
- `per_page` ограничен сотней; `page` начинается с 1.

## Категории

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/categories` | Получение списка категорий (обязателен `-q status=…`) |
| POST | `/v1/categories` | Создание новой категории |
| GET | `/v1/categories/{id}` | Получение категории по ID |
| PATCH | `/v1/categories/{id}` | Обновление категории |
| POST | `/v1/categories/{id}/archive` | Архивирование категории |
| POST | `/v1/categories/{id}/unarchive` | Восстановление категории из архива |

У категории нет флага видимости: «убрать категорию с витрины» — это `archive`,
а «вернуть» — `unarchive`.

## Характеристики

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/characteristics` | Получение списка характеристик (обязателен `-q status=…`) |
| POST | `/v1/characteristics` | Создание новой характеристики |
| GET | `/v1/characteristics/{id}` | Получение характеристики по ID |
| PATCH | `/v1/characteristics/{id}` | Обновление характеристики |
| POST | `/v1/characteristics/{id}/archive` | Архивирование характеристики |
| POST | `/v1/characteristics/{id}/unarchive` | Восстановление характеристики из архива |
| GET | `/v1/characteristics/groups` | Получение списка групп характеристик |
| POST | `/v1/characteristics/groups` | Создание группы характеристик |
| GET | `/v1/characteristics/groups/{id}` | Получение группы характеристик по ID |
| PATCH | `/v1/characteristics/groups/{id}` | Обновление группы характеристик |
| DELETE | `/v1/characteristics/groups/{id}` | Удаление группы характеристик |
| GET | `/v1/characteristics/colors` | Список цветов значений характеристик (пагинация + поиск) |
| PATCH | `/v1/characteristics/colors` | Обновление hex-кода для строкового значения цветовой характеристики |

## Продукты и карточки

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/products` | Получение списка продуктов |
| POST | `/v1/products` | Создание нового продукта |
| GET | `/v1/products/{id}` | Получение продукта по ID |
| PATCH | `/v1/products/{id}` | Обновление продукта |
| GET | `/v1/products/cards/{product_card_id}/similar` | Получение списка похожих карточек товара |
| POST | `/v1/products/cards/{product_card_id}/similar/add` | Добавление похожих карточек товара |
| POST | `/v1/products/cards/{product_card_id}/similar/remove` | Удаление похожих карточек товара |

## Товары (варианты)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/variants` | Получение списка товаров (фильтры: `product_id`, `product_card_id`, `status`, `name`) |
| POST | `/v1/variants` | Создание нового товара |
| GET | `/v1/variants/{id}` | Получение товара по ID |
| PATCH | `/v1/variants/{id}` | Обновление товара |
| DELETE | `/v1/variants/{id}` | Безвозвратное удаление архивного товара |
| POST | `/v1/variants/{id}/archive` | Архивирование товара |
| POST | `/v1/variants/{id}/unarchive` | Восстановление товара из архива |
| GET | `/v1/variants/{id}/external_ids` | Получение внешних идентификаторов товара |
| PUT | `/v1/variants/{id}/external_ids/{system_type}` | Установка внешнего идентификатора |
| DELETE | `/v1/variants/{id}/external_ids/{system_type}` | Удаление внешнего идентификатора |

`system_type` — это перечисление в пути, а не свободная строка. Допустимые
значения: `moysklad`, `1C`, `wildberries`, `ozon`, `yml`, `yandex_market`,
`external_store`, `external_store_api`. «МойСклад» в пути — это `moysklad`;
любое другое написание даст `404`, а не ошибку валидации. Это рабочая связка
для интеграций с товароучёткой и маркетплейсами: сначала прочитайте
`GET …/external_ids`, чтобы менять или снимать существующую привязку осознанно,
а не вслепую.

### Массовые обновления цен и остатков

| Метод | Путь | Описание |
|---|---|---|
| POST | `/v1/variants/prices/bulk_update` | Массовое обновление цен (до 5000 элементов за запрос) |
| POST | `/v1/variants/stocks/bulk_update` | Массовое обновление остатков (до 5000 пар товар+склад) |

Для регулярной синхронизации цен и остатков используйте именно эти ручки, а не
`PATCH /v1/variants/{id}` по одному товару. Пара «товар + склад» в одном запросе
на остатки не может повторяться.

### Документы товара

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/variants/{id}/attachments` | Документы товара (инструкции, сертификаты, паспорта) |
| POST | `/v1/variants/{id}/attachments` | Прикрепление документа по ID ранее загруженного файла |
| PATCH | `/v1/variants/{id}/attachments/{file_id}` | Обновление названия и/или порядка документа |
| DELETE | `/v1/variants/{id}/attachments/{file_id}` | Открепление документа от товара |

Сначала загрузите файл (`kit.py upload`), затем прикрепите его по `file_id`.
`DELETE` открепляет локально — сам файл остаётся и может использоваться другими
товарами.

## Файлы

| Метод | Путь | Описание |
|---|---|---|
| POST | `/v1/files` | Загрузка файла (multipart, поле `file`; используйте `kit.py upload`) |
| GET | `/v1/files/{id}` | Получение файла по ID |

## Видео

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/videos` | Список видео (обязателен `-q status=…`, сортировка по дате) |
| POST | `/v1/videos` | Загрузка видео (multipart), возвращает `video_id` |
| POST | `/v1/videos/from_url` | Загрузка видео по публичной ссылке |
| GET | `/v1/videos/{video_id}` | Видео и текущий статус обработки |

Обработка асинхронная: после загрузки опрашивайте `GET /v1/videos/{video_id}`
до терминального статуса. Видео добавляется в `media` товара только вместе как
минимум с одним изображением, а `PATCH /v1/variants/{id}` заменяет список
`media` целиком — передавайте изображения и видео одним списком.

## Склады

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/warehouses` | Получение списка складов (обязателен `-q status=…`) |
| POST | `/v1/warehouses` | Создание нового склада |
| GET | `/v1/warehouses/{id}` | Получение склада по ID |
| PATCH | `/v1/warehouses/{id}` | Обновление склада |
| POST | `/v1/warehouses/{id}/archive` | Архивирование склада |
| POST | `/v1/warehouses/{id}/unarchive` | Восстановление склада из архива |
