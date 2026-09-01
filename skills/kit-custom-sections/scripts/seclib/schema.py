"""Подмножество JSON Schema draft-07: обход, пути свойств, ключевые слова, $ref.

Вторая реализация подмножества (kitlib конструктора приватен и не импортируется);
расхождение контролируется поведенческим тестом parity во внутренней сюите.
"""

from __future__ import annotations

# Ядро draft-07, которое понимает форма конструктора.
CORE_KEYWORDS = {
    "$ref",
    "$schema",
    "additionalProperties",
    "const",
    "default",
    "definitions",
    "description",
    "enum",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
    "uniqueItems",
}

# Жёсткий запрет: схема с ними не редактируется конструктором.
FORBIDDEN_KEYWORDS = {"oneOf", "allOf", "anyOf", "if", "then", "else", "not", "patternProperties"}
# anyOf/not формально живут в draft-07 и даже встречаются в ТЕЛАХ платформенных
# определений BFF, но в схеме секции форма конструктора их не рендерит — запрет.

IMPLICIT_MODULE_KEYS = {"id"}  # платформа сама добавляет модулям id


def allowed_keywords(registry: dict) -> set[str]:
    return (
        set(CORE_KEYWORDS)
        | set(registry.get("schema_extra_keywords", []))
        | set(registry.get("form_extra_keywords", []))
    )


def walk(schema: object, path: str = ""):
    """(путь-узла, узел) для каждого словаря схемы, вглубь properties/items."""
    if not isinstance(schema, dict):
        return
    yield path, schema
    for name, sub in (schema.get("properties") or {}).items():
        yield from walk(sub, f"{path}.{name}" if path else name)
    if isinstance(schema.get("items"), dict):
        yield from walk(schema["items"], f"{path}[]")


def leaf_paths(schema: dict) -> set[str]:
    """Пути «настроек» схемы: листья + сами массивы/объекты."""
    paths: set[str] = set()
    for path, node in walk(schema):
        if not path:
            continue
        paths.add(path)
    return paths


def property_exists(schema: dict, dotted: str) -> bool:
    """Есть ли путь вида section.hPaddingDesktop / modules[].question в схеме."""
    return dotted in leaf_paths(schema)


def node_at(schema: dict, dotted: str) -> tuple[dict | None, bool]:
    """(узел схемы по пути, пересечена ли граница $ref-модели).

    Путь — формата G4: `section.hPaddingTouch`, `modules[].question`, `cover.url.link`.
    Если на пути встретился узел с `$ref` (модель платформы), дальше в его тело не
    ходим: возвращаем (None, True) — подполя модели схема секции не описывает,
    и это легально (`{{settings.cover.url.link}}` при `url: $ref LinkSource`).
    """
    node: dict = schema
    parts = dotted.split(".") if dotted else []
    for i, part in enumerate(parts):
        iterate = part.endswith("[]")
        name = part[:-2] if iterate else part
        props = node.get("properties") or {}
        child = props.get(name)
        if not isinstance(child, dict):
            return None, False
        if iterate:
            items = child.get("items")
            if not isinstance(items, dict):
                return None, False
            child = items
        if i < len(parts) - 1 and isinstance(child.get("$ref"), str):
            return None, True  # дальше — внутрь модели
        node = child
    return (node, False) if parts else (schema, False)


SCALAR_TYPES = {"string", "number", "integer", "boolean"}


def kind_of(node: dict | None, schema: dict, definitions: dict | None, _seen: frozenset = frozenset()) -> str:
    """Классификация значения узла: scalar | object | array | mixed | unknown.

    $ref резолвится сначала в собственных definitions документа, затем в
    вендоренной выжимке BFF (references/generated/definitions.json).
    anyOf в теле платформенного типа (например Palette) -> mixed: судить нельзя.
    """
    if not isinstance(node, dict):
        return "unknown"
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in _seen:
            return "unknown"
        name = ref.rsplit("/", 1)[-1]
        body = (schema.get("definitions") or {}).get(name)
        if body is None and definitions:
            body = (definitions.get("definitions") or {}).get(name)
        if not isinstance(body, dict):
            return "unknown"
        return kind_of(body, schema, definitions, _seen | {ref})
    if "anyOf" in node or "oneOf" in node:
        return "mixed"
    ntype = node.get("type")
    if ntype in SCALAR_TYPES:
        return "scalar"
    if ntype == "object":
        return "object"
    if ntype == "array":
        return "array"
    if "enum" in node:
        return "scalar"
    if "properties" in node:
        return "object"
    if "items" in node:
        return "array"
    return "unknown"


def collect_keywords(schema: dict) -> dict[str, list[str]]:
    """ключевое слово -> где встречается (пути узлов)."""
    seen: dict[str, list[str]] = {}
    for path, node in walk(schema):
        for key in node:
            seen.setdefault(key, []).append(path or "<root>")
    return seen


def collect_refs(schema: dict) -> list[tuple[str, str]]:
    return [
        (path or "<root>", node["$ref"])
        for path, node in walk(schema)
        if isinstance(node.get("$ref"), str)
    ]


def local_definition_refs(schema: dict) -> set[str]:
    return {f"#/definitions/{name}" for name in (schema.get("definitions") or {})}


def resolve_ref_body(
    schema: dict, ref: str, definitions: dict | None, _seen: frozenset = frozenset()
) -> dict | None:
    """Тело $ref: сначала definitions документа, затем вендоренная выжимка BFF.
    Цепочки ($ref на $ref) резолвятся; anyOf/oneOf-тела не возвращаются (судить нельзя)."""
    if ref in _seen or not ref.startswith("#/definitions/"):
        return None
    name = ref.rsplit("/", 1)[-1]
    body = (schema.get("definitions") or {}).get(name)
    if body is None and definitions:
        body = (definitions.get("definitions") or {}).get(name)
    if not isinstance(body, dict):
        return None
    if "anyOf" in body or "oneOf" in body:
        return None
    inner = body.get("$ref")
    if isinstance(inner, str):
        resolved = resolve_ref_body(schema, inner, definitions, _seen | {ref})
        if resolved is not None:
            merged = dict(resolved)
            merged.update({k: v for k, v in body.items() if k != "$ref"})
            return merged
        return None
    return body


def validate_instance(
    schema: dict,
    instance: object,
    path: str = "",
    forbid_additional: bool = False,
    definitions: dict | None = None,
    _root: dict | None = None,
) -> list[str]:
    """Минимальная валидация значения против подмножества (для answers и settings).

    forbid_additional=True — ключи вне properties запрещены (для answers:
    лишний ключ молча уехал бы в живую секцию, но форма мерчанта его не
    показывает и теряет при сохранении).

    definitions — вендоренная выжимка BFF: с ней валидируются и тела $ref
    (enum/типы/required). Без резолва невалидное значение enum внутри $ref
    (например hPaddingDesktop: "250") отклонял бы только сервер при привязке.
    Внутрь тел $ref forbid_additional не распространяется (модели платформы
    могут нести свои дополнительные поля).
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors
    root = _root if _root is not None else schema
    if "$ref" in schema:
        body = resolve_ref_body(root, schema["$ref"], definitions)
        if body is None:
            return errors  # anyOf-тело (Palette) или неизвестный тип — не судим
        return validate_instance(body, instance, path, False, definitions, root)
    stype = schema.get("type")
    where = path or "<root>"
    if stype == "object":
        if not isinstance(instance, dict):
            errors.append(f"{where}: ожидался object, получен {type(instance).__name__}")
            return errors
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{where}: отсутствует обязательное поле {req!r}")
        props = schema.get("properties") or {}
        for key, value in instance.items():
            sub = props.get(key)
            if sub is not None:
                errors.extend(
                    validate_instance(
                        sub, value, f"{where}.{key}", forbid_additional, definitions, root
                    )
                )
            elif forbid_additional and props:
                errors.append(
                    f"{where}: неизвестный ключ {key!r} — его нет в схеме ответов; "
                    "он уехал бы в живую секцию, но форма мерчанта его не покажет "
                    "и потеряет при сохранении"
                )
    elif stype == "array":
        if not isinstance(instance, list):
            errors.append(f"{where}: ожидался array")
            return errors
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors.extend(
                    validate_instance(
                        items, item, f"{where}[{i}]", forbid_additional, definitions, root
                    )
                )
    elif stype == "string":
        if not isinstance(instance, str):
            errors.append(f"{where}: ожидалась строка")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{where}: значение {instance!r} вне enum {schema['enum']}")
    elif stype == "number" or stype == "integer":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            errors.append(f"{where}: ожидалось число")
    elif stype == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{where}: ожидался boolean")
    return errors
