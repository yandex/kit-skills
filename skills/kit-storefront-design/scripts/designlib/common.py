"""Shared helpers: paths, exit codes, JSON I/O, deep merge, placeholder detection."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
REFERENCES = SKILL_ROOT / "references"
PRESETS_DIR = REFERENCES / "presets"
RECIPES_DIR = REFERENCES / "recipes"

# Copy lengths the lint warns above (`long-copy`); presets that promise a length must stay within.
IWT_MAX_CHARS = 350
CENTRED_MAX_CHARS = 200

# Same scale as kit-custom-sections: 1 and 4 belong to the constructor's network
# outcomes and never occur here, because this skill makes no request at all.
EXIT_OK = 0
EXIT_FINDINGS = 2
EXIT_USAGE = 3
EXIT_NOT_FOUND = 5


class DesignError(Exception):
    """A usage problem the user can fix; carries the exit code to return."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


def read_json(path: "str | Path") -> object:
    """Read one JSON document from a path, or from stdin when the path is '-'."""
    try:
        if str(path) == "-":
            return json.loads(sys.stdin.read())
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise DesignError(f"Cannot read {path}: {error.strerror or error}") from None
    except json.JSONDecodeError as error:
        raise DesignError(f"{path} is not valid JSON: {error}") from None


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def write_json(path: "str | Path", value: object) -> None:
    Path(path).write_text(dump_json(value) + "\n", encoding="utf-8")


def deep_merge(base: object, overlay: object) -> object:
    """Overlay wins; objects merge key by key; arrays and scalars are replaced whole."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            result[key] = deep_merge(result.get(key), value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(overlay)


def get_path(value: object, dotted: str, default: object = None) -> object:
    """Read `a.b.c` from nested dicts; `default` when any step is missing."""
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def unset_path(settings: "dict[str, object]", dotted: str) -> object:
    """Delete `a.b.c` from nested dicts; return the removed value, or None when absent.

    A preset's `unset` list names keys it takes away from the base: a contained hero must not
    keep the `"0"` side paddings of the edge-to-edge hero it replaces, and a merge alone can
    only add or overwrite keys."""
    *parents, key = dotted.split(".")
    current: object = settings
    for part in parents:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            return None
        current = current[part]
    if isinstance(current, dict) and key in current:
        return current.pop(key)
    return None


def walk_strings(value: object, path: str = "$"):
    """Yield (path, string) for every string inside a JSON value."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")


# `<fill: …>` is how presets and plans mark a value that must come from the store
# or the merchant; a whole string in angle or square brackets («<brand>», «[Название
# бренда]») is the same thing written freehand.
# A bare HTML tag («<br>», «<hr/>») is markup, not a placeholder.
MARKER = re.compile(
    r"<fill:[^>]*>"
    r"|^\s*<(?!/?(?:br|hr|p|div|span|em|strong|ul|ol|li|h[1-6]|img|a|table|tr|td)\b)[^<>]{2,}>\s*$"
    r"|^\s*\[[^\[\]]{2,}\]\s*$"
)

# A starter template marks the text a merchant must replace as `[! … !]`, anywhere inside a
# longer value; markdown bold and escaped brackets (`**\[!** … **!\]**`) hide the brackets
# from the patterns above, so markers are also looked for in the text without markdown.
TEMPLATE_MARKER = re.compile(r"\[!.{1,600}?!\]", re.S)
_MARKDOWN_NOISE = re.compile(r"\*\*|__|\\(?=[\[\]])")


def strip_markdown(text: str) -> str:
    """`**\\[!** text **!\\]**` → `[! text !]`: bold markers and bracket escapes removed."""
    return _MARKDOWN_NOISE.sub("", text)


def is_marker(text: str) -> bool:
    """A `<fill: …>` marker, a whole value in brackets, or a template's `[! … !]` marker."""
    if MARKER.search(text):
        return True
    plain = strip_markdown(text)
    return bool(MARKER.search(plain) or TEMPLATE_MARKER.search(plain))


# Text the platform or its migrate step seeds into new sections. Prefixes only:
# enough to recognise them, nothing copied beyond that.
PLATFORM_PLACEHOLDERS = (
    "Шаблон для страницы",
    "Коротко опишите ваш бренд",
    "Здесь будет отображаться",
    "Заголовок секции",
    "Заголовок вопроса",
    "Вот пример длинного ответа",
    "Название коллекции",
    "Lorem ipsum",
)
ZERO_PHONE = re.compile(r"\(?0{3}\)?[\s-]*0{3}[\s-]*0{2}[\s-]*0{2}")
DATA_URI_IMAGE = re.compile(r"^data:image/", re.I)


def find_markers(value: object) -> "list[tuple[str, str]]":
    return [(path, text) for path, text in walk_strings(value) if is_marker(text)]


def find_platform_placeholders(value: object) -> "list[tuple[str, str]]":
    hits = []
    for path, text in walk_strings(value):
        if any(prefix.lower() in text.lower() for prefix in PLATFORM_PLACEHOLDERS):
            hits.append((path, text[:60]))
        elif ZERO_PHONE.search(text):
            hits.append((path, text[:60]))
    return hits


def is_media_object(value: object) -> bool:
    return isinstance(value, dict) and ("filePath" in value or "fileId" in value)


# Font files of the theme (`typography.fontFamily.*.sources.*`) have the shape of a media
# object, and most slots are empty by design: one weight uploaded, the rest not. They are
# the cabinet's (hard rule 9), never an image to upload.
FONT_PATH = "$.typography.fontFamily"


def find_media_objects(value: object, path: str = "$"):
    """Yield (path, media object) for image/video objects (anything with fileId/filePath)."""
    if path.startswith(FONT_PATH):
        return
    if is_media_object(value):
        yield path, value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from find_media_objects(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from find_media_objects(item, f"{path}[{index}]")


def media_is_empty(media: dict) -> bool:
    file_path = media.get("filePath")
    if not isinstance(file_path, str) or not file_path.strip():
        return True
    return bool(DATA_URI_IMAGE.match(file_path.strip()))
