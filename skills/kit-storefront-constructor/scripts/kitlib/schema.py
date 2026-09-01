"""Storefront BFF schema engine: fetch with ETag cache, $ref resolve, validate, skeletons.

The BFF (`<storefront-origin>/constructor-api`) serves the native-widget
settings schemas without authentication. The validator implements exactly the JSON Schema
draft-07 subset measured in the live document: $ref, type, properties, required, items,
enum, const, minimum, maximum, anyOf, not, additionalProperties. `oneOf`, `allOf`,
`if/then/else`, and `patternProperties` do not occur and are intentionally unsupported —
an unknown constraint keyword must not silently pass, so their presence fails closed.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from kitlib.common import ApiError, UsageError, http_json, require_mapping

SCHEMA_ROUTE = "/constructor-api/schema"
MIGRATE_ROUTE = "/constructor-api/migrate"
MIGRATE_PROBE_KEY = "version-probe"
DEFAULT_CACHE_DIR = Path.home() / ".yandex-kit-skills" / "cache"
MYSTIQUE_WIDGET = "YandexKit.Mystique"

# Keywords the validator enforces; every other keyword present in the live document is
# either resolved structure (definitions/properties/items) or annotation (default, title,
# description, examples) and carries no constraint semantics for instances.
UNSUPPORTED_CONSTRAINT_KEYWORDS = ("oneOf", "allOf", "if", "then", "else", "patternProperties")
_STOREFRONT_HOST_PATTERN = re.compile(r"^[a-z0-9-]+\.yastore\.yandex\.ru$")
_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "null": lambda value: value is None,
}


def resolve_bff_url(override: "str | None") -> str:
    """Resolve the BFF base URL: flag, then env, then an honest derivation attempt.

    Derivation only works when the Core API and the storefront share a host. They
    do not on production, where the Core API is a separate gateway — there the
    origin is the store's own storefront, which `kit.py store` reports as
    `b2c_url`. This function performs no network call; it refuses instead.
    """
    candidate = (override or os.environ.get("KIT_BFF_BASE_URL", "")).strip().rstrip("/")
    if candidate:
        if not candidate.startswith(("http://", "https://")):
            raise UsageError("The storefront address must start with http:// or https://.")
        return candidate
    core_url = os.environ.get("KIT_API_BASE_URL", "").strip()
    core_host = re.sub(r"^https?://", "", core_url).split("/", 1)[0].split(":", 1)[0].lower()
    if _STOREFRONT_HOST_PATTERN.match(core_host):
        return f"https://{core_host}"
    raise UsageError(
        "The storefront address is not set, and it cannot be worked out from the store "
        "API address. Run `kit.py store`: the `b2c_url` it reports is the address of "
        "this store's own site, which is what is needed here. Pass it as --bff-url or "
        "set KIT_BFF_BASE_URL. Take the value as it comes — do not build it from the "
        "store name, because a store on its own domain has a different one."
    )


def cache_dir() -> Path:
    """Return the schema cache directory (override: KIT_SCHEMA_CACHE_DIR)."""
    configured = os.environ.get("KIT_SCHEMA_CACHE_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_CACHE_DIR


def _cache_key(bff_url: str) -> str:
    host = re.sub(r"^https?://", "", bff_url).split("/", 1)[0]
    return re.sub(r"[^A-Za-z0-9.-]", "_", host)


def cache_paths(bff_url: str) -> "tuple[Path, Path]":
    """Return (schema_path, meta_path) for one BFF host."""
    key = _cache_key(bff_url)
    directory = cache_dir()
    return directory / f"bff-schema-{key}.json", directory / f"bff-schema-{key}.meta.json"


def validate_schema_document(value: object) -> "dict[str, object]":
    """Validate the BFF schema envelope and fail closed on unsupported constraints."""
    document = require_mapping(value, "BFF schema")
    build_version = document.get("buildVersion")
    if not isinstance(build_version, str) or not build_version:
        raise UsageError("BFF schema.buildVersion must be a non-empty string.")
    definitions = document.get("definitions")
    if not isinstance(definitions, dict):
        raise UsageError("BFF schema.definitions must be a JSON object.")
    sections = document.get("sections")
    if not isinstance(sections, list) or not sections:
        raise UsageError("BFF schema.sections must be a non-empty array.")
    for index, item in enumerate(sections):
        section = require_mapping(item, f"BFF schema.sections[{index}]")
        widget = section.get("widget")
        if not isinstance(widget, str) or not widget:
            raise UsageError(f"BFF schema.sections[{index}].widget must be a non-empty string.")
        require_mapping(section.get("schema"), f"BFF schema.sections[{index}].schema")
    global_settings = require_mapping(document.get("globalSettings"), "BFF schema.globalSettings")
    require_mapping(global_settings.get("schema"), "BFF schema.globalSettings.schema")
    unsupported = _find_unsupported_keywords(document)
    if unsupported:
        raise UsageError(
            "BFF schema uses constraint keywords this client does not implement: "
            f"{', '.join(sorted(unsupported))}. Refusing to validate partially."
        )
    return document


def _find_unsupported_keywords(node: object) -> "set[str]":
    found: "set[str]" = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in UNSUPPORTED_CONSTRAINT_KEYWORDS:
                found.add(key)
            found.update(_find_unsupported_keywords(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_find_unsupported_keywords(item))
    return found


def fetch_schema(bff_url: str, *, force: bool = False, timeout: float = 30.0) -> "dict[str, object]":
    """GET the BFF schema with an ETag-conditional request and refresh the cache."""
    schema_path, meta_path = cache_paths(bff_url)
    cached_meta: "dict[str, object]" = {}
    if not force and schema_path.is_file() and meta_path.is_file():
        try:
            cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_meta = {}
    headers = {}
    etag = cached_meta.get("etag")
    if isinstance(etag, str) and etag and not force:
        headers["If-None-Match"] = etag

    url = f"{bff_url}{SCHEMA_ROUTE}"
    status, response_headers, payload = http_json("GET", url, headers=headers, timeout=timeout)
    if status == 304:
        document = load_cached_schema(bff_url)
        return {
            "refreshed": False,
            "source": url,
            "cache_path": str(schema_path),
            "build_version": document.get("buildVersion"),
            "widget_count": len(document["sections"]),  # type: ignore[arg-type]
        }
    document = validate_schema_document(payload)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "etag": response_headers.get("etag", ""),
                "build_version": document["buildVersion"],
                "source": url,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "refreshed": True,
        "source": url,
        "cache_path": str(schema_path),
        "build_version": document["buildVersion"],
        "widget_count": len(document["sections"]),  # type: ignore[arg-type]
    }


def load_cached_schema(bff_url: "str | None" = None) -> "dict[str, object]":
    """Load the cached BFF schema without network access.

    With an unknown BFF URL, a single cached schema is used as an unambiguous fallback.
    """
    if bff_url is not None:
        schema_path, _meta = cache_paths(bff_url)
    else:
        candidates = sorted(cache_dir().glob("bff-schema-*.json")) if cache_dir().is_dir() else []
        candidates = [path for path in candidates if not path.name.endswith(".meta.json")]
        if len(candidates) != 1:
            raise UsageError(
                "BFF schema cache is ambiguous or empty; pass --bff-url or set "
                "KIT_BFF_BASE_URL, then run 'schema fetch'."
            )
        schema_path = candidates[0]
    if not schema_path.is_file():
        raise UsageError(f"No cached BFF schema at {schema_path}; run 'schema fetch' first.")
    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UsageError(f"Cannot read cached BFF schema {schema_path}: {error}") from None
    return validate_schema_document(payload)


# --- $ref resolution -------------------------------------------------------------------


def resolve_node(
    node: object,
    definitions: "dict[str, object]",
) -> "tuple[object, str | None]":
    """Follow a $ref chain to the concrete schema node; return (node, last ref name)."""
    ref_name = None
    seen: "set[str]" = set()
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        reference = node["$ref"]
        if reference in seen:
            raise UsageError(f"BFF schema contains a $ref cycle at {reference}.")
        seen.add(reference)
        if not reference.startswith("#/definitions/"):
            raise UsageError(f"Unsupported $ref target: {reference}.")
        ref_name = reference[len("#/definitions/") :]
        if ref_name not in definitions:
            raise UsageError(f"BFF schema $ref points to a missing definition: {ref_name}.")
        node = definitions[ref_name]
    return node, ref_name


def widget_schema(document: "dict[str, object]", widget: str) -> "dict[str, object]":
    """Return the raw (unresolved) schema node for one widget."""
    for section in document["sections"]:  # type: ignore[union-attr]
        if isinstance(section, dict) and section.get("widget") == widget:
            return require_mapping(section.get("schema"), f"schema for widget {widget}")
    known = sorted(
        str(section.get("widget"))
        for section in document["sections"]  # type: ignore[union-attr]
        if isinstance(section, dict)
    )
    raise UsageError(f"Widget {widget!r} is not present in the BFF schema. Known widgets: {', '.join(known)}.")


def theme_schema(document: "dict[str, object]") -> "dict[str, object]":
    """Return the raw globalSettings schema node."""
    global_settings = require_mapping(document.get("globalSettings"), "BFF schema.globalSettings")
    return require_mapping(global_settings.get("schema"), "BFF schema.globalSettings.schema")


# --- validator -------------------------------------------------------------------------


def validate_instance(
    instance: object,
    schema: object,
    definitions: "dict[str, object]",
    path: str = "$",
) -> "list[str]":
    """Validate one instance against the measured draft-07 subset; return error strings."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: schema forbids any value"]
    if not isinstance(schema, dict):
        return [f"{path}: schema node must be an object"]
    schema, _ref = resolve_node(schema, definitions)
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: schema forbids any value"]
    if not isinstance(schema, dict):
        return [f"{path}: schema node must be an object"]

    errors: "list[str]" = []

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal const {json.dumps(schema['const'], ensure_ascii=False)}")
    if "enum" in schema:
        enum_values = schema["enum"]
        if isinstance(enum_values, list) and instance not in enum_values:
            errors.append(f"{path}: value is not one of {len(enum_values)} enum values")

    type_spec = schema.get("type")
    if type_spec is not None:
        allowed = type_spec if isinstance(type_spec, list) else [type_spec]
        checks = [_TYPE_CHECKS.get(str(name)) for name in allowed]
        if not any(check(instance) for check in checks if check is not None):
            errors.append(f"{path}: expected type {'/'.join(str(name) for name in allowed)}")
            return errors  # deeper keyword checks would only cascade

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{path}: {instance} is below minimum {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"{path}: {instance} is above maximum {maximum}")

    if isinstance(instance, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for name in schema.get("required", []) or []:
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        for name, value in instance.items():
            if name in properties:
                errors.extend(validate_instance(value, properties[name], definitions, f"{path}.{name}"))
            elif "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                if additional is False:
                    errors.append(f"{path}: additional property {name!r} is not allowed")
                elif additional is not True:
                    errors.extend(validate_instance(value, additional, definitions, f"{path}.{name}"))

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate_instance(item, schema["items"], definitions, f"{path}[{index}]"))

    if "anyOf" in schema:
        branches = schema["anyOf"]
        if isinstance(branches, list):
            branch_errors = [
                validate_instance(instance, branch, definitions, path) for branch in branches
            ]
            if not any(not failed for failed in branch_errors):
                shortest = min(branch_errors, key=len) if branch_errors else []
                detail = shortest[0] if shortest else "no anyOf branch matched"
                errors.append(f"{path}: no anyOf branch matched ({detail})")

    if "not" in schema and not validate_instance(instance, schema["not"], definitions, path):
        errors.append(f"{path}: value matches a forbidden 'not' schema")

    return errors


# --- skeleton --------------------------------------------------------------------------


def build_skeleton(schema: object, definitions: "dict[str, object]", _depth: int = 0) -> object:
    """Build a minimal instance from defaults so it validates against its own schema."""
    if _depth > 64:
        raise UsageError("Schema nesting is too deep to build a skeleton.")
    if isinstance(schema, bool):
        return {} if schema else None
    if not isinstance(schema, dict):
        raise UsageError("Schema node must be an object to build a skeleton.")
    schema, _ref = resolve_node(schema, definitions)
    if isinstance(schema, bool):
        return {}
    assert isinstance(schema, dict)

    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if "default" in schema and not isinstance(schema.get("default"), dict):
        candidate = copy.deepcopy(schema["default"])
        if not validate_instance(candidate, schema, definitions):
            return candidate

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        for branch in schema["anyOf"]:
            candidate = build_skeleton(branch, definitions, _depth + 1)
            if not validate_instance(candidate, schema, definitions):
                return candidate

    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return copy.deepcopy(schema["enum"][0])

    type_spec = schema.get("type")
    type_name = type_spec[0] if isinstance(type_spec, list) and type_spec else type_spec

    if type_name == "object" or (type_name is None and ("properties" in schema or "required" in schema)):
        default = schema.get("default")
        result: "dict[str, object]" = copy.deepcopy(default) if isinstance(default, dict) else {}
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        for name, subschema in properties.items():
            if name in result:
                continue
            resolved, _sub_ref = resolve_node(subschema, definitions)
            has_default = isinstance(resolved, dict) and "default" in resolved
            if name in required or has_default:
                result[name] = build_skeleton(subschema, definitions, _depth + 1)
        return result
    if type_name == "array":
        default = schema.get("default")
        return copy.deepcopy(default) if isinstance(default, list) else []
    if type_name == "string":
        return ""
    if type_name == "integer" or type_name == "number":
        minimum = schema.get("minimum")
        value: object = minimum if isinstance(minimum, (int, float)) else 0
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and isinstance(value, (int, float)) and value > maximum:
            value = maximum
        return value
    if type_name == "boolean":
        return False
    if type_name == "null":
        return None
    return {}


def build_widget_skeleton(document: "dict[str, object]", widget: str) -> "dict[str, object]":
    """Build and self-validate a settings skeleton for one native widget."""
    definitions = document["definitions"]
    assert isinstance(definitions, dict)
    schema = widget_schema(document, widget)
    skeleton = build_skeleton(schema, definitions)
    errors = validate_instance(skeleton, schema, definitions)
    if errors:
        raise UsageError(
            f"Locally built skeleton for {widget} does not validate against its own schema: "
            + "; ".join(errors[:5])
        )
    if not isinstance(skeleton, dict):
        raise UsageError(f"Skeleton for {widget} must be a JSON object.")
    return skeleton


# --- projections -----------------------------------------------------------------------


def _type_label(resolved: object, ref_name: "str | None", definitions: "dict[str, object]") -> str:
    if not isinstance(resolved, dict):
        return "any"
    type_spec = resolved.get("type")
    label = "/".join(str(name) for name in type_spec) if isinstance(type_spec, list) else type_spec
    if label == "array" and "items" in resolved:
        try:
            item_node, item_ref = resolve_node(resolved["items"], definitions)
        except UsageError:
            return "array"
        return f"array of {item_ref or _type_label(item_node, None, definitions)}"
    if label == "object" and ref_name:
        return f"object<{ref_name}>"
    if label is None and "anyOf" in resolved:
        return "anyOf"
    return str(label) if label else "any"


def _project_object_schema(
    schema: object,
    definitions: "dict[str, object]",
    label: str,
) -> "dict[str, object]":
    resolved, ref_name = resolve_node(schema, definitions)
    resolved = require_mapping(resolved, label)
    properties = resolved.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = resolved.get("required")
    required = set(required) if isinstance(required, list) else set()
    fields = []
    for name in sorted(properties):
        try:
            sub_resolved, sub_ref = resolve_node(properties[name], definitions)
        except UsageError as error:
            fields.append({"name": name, "type": "unresolvable", "error": str(error)})
            continue
        field: "dict[str, object]" = {
            "name": name,
            "type": _type_label(sub_resolved, sub_ref, definitions),
            "required": name in required,
        }
        if isinstance(sub_resolved, dict):
            if "default" in sub_resolved:
                encoded = json.dumps(sub_resolved["default"], ensure_ascii=False)
                field["default"] = sub_resolved["default"] if len(encoded) <= 200 else "…large default omitted…"
            enum_values = sub_resolved.get("enum")
            if isinstance(enum_values, list):
                field["enum"] = enum_values if len(enum_values) <= 8 else f"{len(enum_values)} values"
            title = sub_resolved.get("title") or sub_resolved.get("description")
            if isinstance(title, str) and title:
                field["title"] = title[:120]
            if _type_label(sub_resolved, sub_ref, definitions).startswith("object") and isinstance(
                sub_resolved.get("properties"), dict
            ):
                field["field_count"] = len(sub_resolved["properties"])
        fields.append(field)
    return {
        "ref": ref_name,
        "required": sorted(required),
        "fields": fields,
    }


def project_widgets(document: "dict[str, object]") -> "dict[str, object]":
    """List every widget with navigation facts, never the full schema bodies."""
    definitions = document["definitions"]
    assert isinstance(definitions, dict)
    widgets = []
    for section in document["sections"]:  # type: ignore[union-attr]
        if not isinstance(section, dict):
            continue
        entry: "dict[str, object]" = {"widget": str(section.get("widget"))}
        try:
            resolved, ref_name = resolve_node(section.get("schema"), definitions)
            properties = resolved.get("properties") if isinstance(resolved, dict) else None
            entry["schema_ref"] = ref_name
            entry["field_count"] = len(properties) if isinstance(properties, dict) else 0
            entry["has_version_field"] = isinstance(properties, dict) and "version" in properties
        except UsageError as error:
            entry["error"] = str(error)
        if "previews" in section:
            entry["has_previews"] = True
        widgets.append(entry)
    return {
        "build_version": document.get("buildVersion"),
        "widget_count": len(widgets),
        "widgets": widgets,
    }


def project_widget(document: "dict[str, object]", widget: str) -> "dict[str, object]":
    """Compact one widget's resolved settings schema: fields, types, required, defaults."""
    definitions = document["definitions"]
    assert isinstance(definitions, dict)
    projection = _project_object_schema(widget_schema(document, widget), definitions, f"widget {widget} schema")
    projection["widget"] = widget
    projection["build_version"] = document.get("buildVersion")
    return projection


def project_theme(document: "dict[str, object]") -> "dict[str, object]":
    """Compact the globalSettings schema."""
    definitions = document["definitions"]
    assert isinstance(definitions, dict)
    projection = _project_object_schema(theme_schema(document), definitions, "globalSettings schema")
    projection["build_version"] = document.get("buildVersion")
    return projection


# --- widget version via migrate --------------------------------------------------------


def migrate_probe_body(widget: str) -> "dict[str, object]":
    """The exact migrate body this client is allowed to send: settings are always {}."""
    return {"sections": [{"key": MIGRATE_PROBE_KEY, "widget": widget, "settings": {}}]}


def fetch_widget_version(bff_url: str, widget: str, *, timeout: float = 30.0) -> "int | None":
    """POST migrate with empty settings and return only the actual settings.version."""
    url = f"{bff_url}{MIGRATE_ROUTE}"
    status, _headers, payload = http_json("POST", url, body=migrate_probe_body(widget), timeout=timeout)
    if status != 200:
        raise ApiError(status, payload)
    response = require_mapping(payload, "Migrate response")
    sections = response.get("sections")
    if not isinstance(sections, list) or len(sections) != 1:
        raise UsageError("Migrate response.sections must contain exactly the probed section.")
    section = require_mapping(sections[0], "Migrate response.sections[0]")
    if section.get("key") != MIGRATE_PROBE_KEY or section.get("widget") != widget:
        raise UsageError("Migrate response does not match the probe request.")
    settings = require_mapping(section.get("settings"), "Migrate response.sections[0].settings")
    version = settings.get("version")
    if version is None:
        return None
    if isinstance(version, bool) or not isinstance(version, int):
        raise UsageError("Migrate response settings.version must be an integer when present.")
    return version
