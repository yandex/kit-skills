"""Theme palettes: which ones the pages use, which play the light and dark roles, which to drop.

A palette is `{id, name, backgroundPrimary, text}` in `global_settings.appearance.colors.palettes`;
sections point at one by its id in a `palette` key (the section's own, a footer's, a listing
tile's); a custom section also in any `…Palette` key and in any field its template's json_schema
declares as a Palette (a shaped divider's `backdropPalette`). A starter template brings palettes
of its own, and a preset that only adds palettes leaves a store with several that nothing uses. This module reads a workspace, never writes it:
`design.py theme palettes --prune --out` writes a new `global_settings` for `kit.py theme set`.
"""

from __future__ import annotations

import copy
import json
import re

from designlib.common import get_path

HEX = re.compile(r"^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")
# A palette plays a role when its text reads on its background (WCAG AA for body text)
# and its background is clearly light or dark.
MIN_CONTRAST = 4.5
LIGHT_FROM = 0.75
DARK_UNTIL = 0.12
BASE_ID = "default"


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: object) -> "float | None":
    """Relative luminance of `#RRGGBB` (an alpha byte is ignored); None for anything else."""
    match = HEX.match(color) if isinstance(color, str) else None
    if not match:
        return None
    rgb = match.group(1)
    r, g, b = (int(rgb[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: object, b: object) -> "float | None":
    la, lb = luminance(a), luminance(b)
    if la is None or lb is None:
        return None
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def role(palette: "dict[str, object]") -> "str | None":
    """`light`, `dark` or None: the role a palette can play by its colours."""
    background = luminance(palette.get("backgroundPrimary"))
    ratio = contrast(palette.get("backgroundPrimary"), palette.get("text"))
    if background is None or ratio is None or ratio < MIN_CONTRAST:
        return None
    if background >= LIGHT_FROM:
        return "light"
    if background <= DARK_UNTIL:
        return "dark"
    return None


def theme_palettes(global_settings: object) -> "list[dict[str, object]]":
    palettes = get_path(global_settings, "appearance.colors.palettes")
    return [p for p in palettes if isinstance(p, dict)] if isinstance(palettes, list) else []


# A key that holds a palette id: `palette`, or any camelCase `…Palette` (a custom section's
# `backdropPalette`, the colour on the other side of a shaped divider).
PALETTE_KEY = re.compile(r"^(?:palette|[a-z][A-Za-z0-9]*Palette)$")


def _is_palette_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    if str(schema.get("$ref", "")).endswith("/Palette"):
        return True
    return any(_is_palette_schema(option) for key in ("anyOf", "oneOf", "allOf") for option in schema.get(key) or [])


def schema_palette_keys(schema: object, found: "set[str] | None" = None) -> "set[str]":
    """Keys a custom template's json_schema declares as palettes (`$ref` to the Palette definition), whatever their name."""
    found = set() if found is None else found
    if isinstance(schema, dict):
        properties = schema.get("properties")
        for key, prop in (properties.items() if isinstance(properties, dict) else []):
            if _is_palette_schema(prop):
                found.add(str(key))
            schema_palette_keys(prop, found)
        nested = [schema.get("items"), *(schema.get(k) or [] for k in ("anyOf", "oneOf", "allOf"))]
        definitions = schema.get("definitions")
        if isinstance(definitions, dict):
            nested.extend(definitions.values())
        for item in nested:
            for sub in item if isinstance(item, list) else [item]:
                schema_palette_keys(sub, found)
    return found


def _palette_refs(value: object, found: "list[str]", keys: "frozenset[str] | set[str]" = frozenset()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (PALETTE_KEY.match(str(key)) or key in keys):
                found.append(item)
            else:
                _palette_refs(item, found, keys)
    elif isinstance(value, list):
        for item in value:
            _palette_refs(item, found, keys)


def _template_palette_keys(work: "dict[str, object]") -> "dict[str, set[str]]":
    """Per custom template id: the fields its json_schema declares as palettes."""
    templates = work.get("section_templates")
    found: "dict[str, set[str]]" = {}
    for t in templates if isinstance(templates, list) else []:
        if not (isinstance(t, dict) and isinstance(t.get("id"), str)):
            continue
        schema = t.get("json_schema")
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                continue
        keys = schema_palette_keys(schema)
        if keys:
            found[t["id"]] = keys
    return found


def usage(work: "dict[str, object]") -> "list[dict[str, object]]":
    """Per theme palette: its colours, contrast, role, and how many sections on which pages point at it.

    A section stored once and copied to several pages (the header, the footer) counts once per page."""
    palettes = theme_palettes(work.get("global_settings"))
    refs: "dict[str, int]" = {}
    pages: "dict[str, set[str]]" = {}
    template_keys = _template_palette_keys(work)
    for page in work.get("pages") or []:
        if not isinstance(page, dict):
            continue
        label = str(page.get("alias") or page.get("pattern") or page.get("id"))
        for section in page.get("layout") or []:
            if not isinstance(section, dict):
                continue
            found: "list[str]" = []
            _palette_refs(section.get("settings"), found, template_keys.get(str(section.get("template_id")), set()))
            for pid in found:
                refs[pid] = refs.get(pid, 0) + 1
                pages.setdefault(pid, set()).add(label)
    rows = []
    for palette in palettes:
        pid = str(palette.get("id"))
        ratio = contrast(palette.get("backgroundPrimary"), palette.get("text"))
        rows.append(
            {
                "id": pid,
                "name": palette.get("name"),
                "backgroundPrimary": palette.get("backgroundPrimary"),
                "text": palette.get("text"),
                "contrast": round(ratio, 1) if ratio is not None else None,
                "role": role(palette),
                "refs": refs.get(pid, 0),
                "pages": sorted(pages.get(pid, set())),
            }
        )
    known = {row["id"] for row in rows}
    for pid in sorted(set(refs) - known):
        rows.append({"id": pid, "name": None, "missing": True, "refs": refs[pid], "pages": sorted(pages[pid])})
    return rows


def prune(global_settings: "dict[str, object]", rows: "list[dict[str, object]]") -> "tuple[dict[str, object], list[str]]":
    """`global_settings` without the palettes no section points at; the base palette always stays."""
    result = copy.deepcopy(global_settings)
    unused = {row["id"] for row in rows if not row.get("missing") and not row["refs"] and row["id"] != BASE_ID}
    colors = get_path(result, "appearance.colors")
    if not isinstance(colors, dict) or not isinstance(colors.get("palettes"), list):
        return result, []
    colors["palettes"] = [p for p in colors["palettes"] if not (isinstance(p, dict) and p.get("id") in unused)]
    return result, sorted(unused)


def reuse_by_role(
    current: "list[object]", overlay: "list[object]", roles: "dict[str, str]"
) -> "tuple[list[object], list[str]]":
    """Overlay palettes that stand for a role (`palette-light` → light) take the place of an existing
    palette of that role instead of being added next to it: the existing one keeps its id and its
    colours, which the store chose, and takes the role's name. Returns (overlay left to merge, notes)."""
    left: "list[object]" = []
    notes: "list[str]" = []
    ids = {p.get("id") for p in current if isinstance(p, dict)}
    taken: "set[object]" = set()
    for palette in overlay:
        wanted = roles.get(str(palette.get("id"))) if isinstance(palette, dict) else None
        if not wanted or palette.get("id") in ids:  # type: ignore[union-attr]
            left.append(palette)
            continue
        match = next(
            (
                p for p in current
                if isinstance(p, dict) and p.get("id") != BASE_ID and p.get("id") not in taken and role(p) == wanted
            ),
            None,
        )
        if match is None:
            left.append(palette)
            continue
        taken.add(match.get("id"))
        old_name = match.get("name")
        match["name"] = palette.get("name")  # type: ignore[union-attr]
        notes.append(
            f"palettes: reused the store's {wanted} palette {match.get('id')!r} ({old_name!r}, {match.get('backgroundPrimary')} / "
            f"{match.get('text')}) as «{palette.get('name')}» instead of adding {palette.get('id')!r}; point sections at {match.get('id')!r}"  # type: ignore[union-attr]
        )
    return left, notes
