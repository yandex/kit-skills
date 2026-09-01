# Коллекции — статические, динамические и контекстные

Endpoint map for the collections domain. Auth, base URL, tool flags,
pagination and global conventions live in [`api.md`](api.md).
Mutating = POST/PUT/PATCH/DELETE (all need `--confirm`).

## Коллекции

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/collections` | Получение списка коллекций (обязателен `-q status=…`) |
| POST | `/v1/collections` | Создание коллекции |
| GET | `/v1/collections/{collection_id}` | Получение коллекции по ID |
| PATCH | `/v1/collections/{collection_id}` | Обновление коллекции |
| DELETE | `/v1/collections/{collection_id}` | Удаление коллекции |
| GET | `/v1/collections/{collection_id}/variants` | Товары коллекции |
| POST | `/v1/collections/{collection_id}/cards/add` | Добавление карточек в статическую коллекцию |
| POST | `/v1/collections/{collection_id}/cards/remove` | Удаление карточек из статической коллекции |
| GET | `/v1/collections/{collection_id}/cards/manual-order` | Ручной порядок карточек статической коллекции |
| POST | `/v1/collections/{collection_id}/cards/move` | Перемещение карточек на позицию в ручном порядке |

Ручной порядок работает только для **статических** коллекций: `manual-order`
возвращает идентификаторы карточек в порядке ручной сортировки, `cards/move`
переставляет их. Заданный порядок применяется к витрине.

## Правила поведения (не выражены в схеме)

- Типы: **STATIC** (наполняется вручную), **DYNAMIC** (наполняется по фильтрам)
  и **SYSTEM**. Системные коллекции через внешний API не видны и не
  редактируются — попытка обновить или удалить такую вернёт `400`.
- Карточки добавляются/удаляются и переставляются только у статических
  коллекций; у динамических состав определяют фильтры.
- `DELETE /v1/collections/{collection_id}` необратим.

## Контекстные коллекции

| Метод | Путь | Описание |
|---|---|---|
| GET | `/v1/context-collections` | Получение списка контекстных коллекций |
| POST | `/v1/context-collections` | Создание контекстной коллекции |
| GET | `/v1/context-collections/{id}` | Получение контекстной коллекции по ID |
| PATCH | `/v1/context-collections/{id}` | Обновление контекстной коллекции |
| DELETE | `/v1/context-collections/{id}` | Удаление контекстной коллекции |
