"""Apply a preset over a base settings object: a pulled section, a skeleton, or the theme.

A preset never carries store values (ids, media, texts, palette ids), so applying one
cannot invent anything. What it cannot supply is listed back as `fill` and as open
findings on the merged result, and the agent fills those from the store or the merchant.

A pulled section is often sparse (the cabinet stores `{"section": {}}` and the storefront
falls back to code defaults). When a preset creates an object such a section did not
have, the object gets the schema defaults of its required keys (`required_defaults` of
the widget file), so the constructor does not refuse it for keys nobody asked for; a
required key that holds a store value gets a `<fill: …>` marker instead and shows in `open`.
A required key whose default is platform copy (a consent text) takes that copy from the
store's own skeleton when `--schema` passes one that shows it; otherwise it stays a marker.
"""

from __future__ import annotations

import copy

from designlib import claims, forms, typeset
from designlib.palettes import reuse_by_role as reuse_palettes_by_role
from designlib.catalog import THEME, normalize_widget
from designlib.common import (
    DesignError,
    deep_merge,
    find_markers,
    find_media_objects,
    find_platform_placeholders,
    get_path,
    media_is_empty,
    read_json,
    unset_path,
)
from designlib.lint import hidden_text_paths
from designlib.workspace import section_settings

HEADER = "YandexKit.Header"
BANNERS = "YandexKit.BannersCarousel"
FAQ = "YandexKit.Faq"
FC = "YandexKit.FeaturedCollections"
CL = "YandexKit.CollectionsListing"
LISTING = forms.LISTING
SHELF = "YandexKit.FeaturedProducts"
HEADER_FLAT_UNTIL = forms.HEADER_FLAT_UNTIL

# Where each flat `modules.main` key lives in the nested header layout.
HEADER_NESTED = {
    "menu": ("menu", "menu"),
    "menuVariant": ("menu", "menuVariant"),
    "menuDisplayType": ("menu", "menuDisplayType"),
    "logo": ("logo", "logo"),
    "logoPosition": ("logo", "logoPosition"),
    "searchVariant": ("additional", "searchVariant"),
    "enableActionTitle": ("additional", "enableActionTitle"),
}

# Markers `required_defaults` writes for platform copy: the store's skeleton may supply the text.
PLATFORM_TEXT_MARKER = "<fill: text"

WIDGETS_WITH_MODULE_LIST = {
    "YandexKit.AnnouncementBar": "modules",
    "YandexKit.BannersCarousel": "modules",
    "YandexKit.FeaturedCollections": "modules",
    "YandexKit.Faq": "modules",
}


def header_layout(settings: object) -> "tuple[str, bool]":
    """(layout, seen): `nested` when modules.main groups its keys into menu/logo/additional,
    `flat` when it holds the leaves directly. A header that shows neither has no layout of its
    own (`seen` False); the caller takes the live schema's then."""
    main = get_path(settings, "modules.main")
    if isinstance(main, dict):
        if any(isinstance(main.get(key), dict) for key in ("menu", "logo", "additional")):
            return "nested", True
        if any(key in main for key in HEADER_NESTED):
            return "flat", True
    return "nested", False


def load_schema_skeleton(path: str, widget: str) -> "dict[str, object]":
    """The store's skeleton of one widget, from `kit.py … schema widget --name W --skeleton` output."""
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise DesignError(f"{path} must hold a JSON object: the output of `kit.py … schema widget --name {widget} --skeleton`.")
    if raw.get("widget") not in (None, widget):
        raise DesignError(f"{path} describes {raw.get('widget')}, not {widget}.")
    skeleton = raw.get("skeleton")
    return skeleton if isinstance(skeleton, dict) else raw


def schema_header_layout(path: str) -> str:
    """The header shape of the store's live schema, from `kit.py … schema widget --name
    YandexKit.Header --skeleton` output (a skeleton built from the schema shows the shape)."""
    layout, seen = header_layout(load_schema_skeleton(path, HEADER))
    if not seen:
        raise DesignError(
            f"{path} shows no header shape. Pass the output of `kit.py … schema widget --name {HEADER} --skeleton`."
        )
    return layout


def regroup_mixed_header(settings: "dict[str, object]") -> "list[str]":
    """A nested header that also keeps flat leaves in `modules.main` (a string `menu` beside
    `logo` and `additional` groups): move each leaf into its group, as the storefront's
    `normalizeMainSettings` does for the headers it still migrates. A key the group already
    holds wins over the flat one. Returns the moved leaves as `key: value` notes."""
    main = get_path(settings, "modules.main")
    if not isinstance(main, dict):
        return []
    moved: "list[str]" = []
    for key, (group, name) in HEADER_NESTED.items():
        value = main.get(key)
        if value is None or isinstance(value, dict):
            continue
        del main[key]
        target = main.get(group)
        if not isinstance(target, dict):
            target = {}
            main[group] = target
        if name not in target:
            target[name] = value
            moved.append(f"modules.main.{key} → modules.main.{group}.{name} ({value!r})")
    return moved


def header_to_layout(preset_settings: "dict[str, object]", layout: str) -> "dict[str, object]":
    """Rewrite a flat-layout header preset into the nested layout when needed."""
    if layout == "flat":
        return copy.deepcopy(preset_settings)
    result = copy.deepcopy(preset_settings)
    main = get_path(result, "modules.main")
    if not isinstance(main, dict):
        return result
    nested: "dict[str, dict[str, object]]" = {}
    for key, value in main.items():
        group, name = HEADER_NESTED.get(key, (None, key))
        if group is None:
            nested.setdefault(key, value)  # type: ignore[arg-type]
            continue
        nested.setdefault(group, {})[name] = value
    result["modules"]["main"] = nested  # type: ignore[index]
    return result


def _merge_palettes(base: "list[object]", overlay: "list[object]") -> "list[object]":
    """Merge palette entries by id; new ids are appended in the preset's order."""
    merged = copy.deepcopy(base)
    index = {p.get("id"): i for i, p in enumerate(merged) if isinstance(p, dict)}
    for palette in overlay:
        if not isinstance(palette, dict):
            continue
        pid = palette.get("id")
        if pid in index:
            merged[index[pid]] = deep_merge(merged[index[pid]], palette)
        elif all(isinstance(palette.get(key), str) for key in ("id", "name", "backgroundPrimary", "text")):
            merged.append(copy.deepcopy(palette))
            index[pid] = len(merged) - 1
        else:
            raise DesignError(
                f"The theme has no palette {pid!r} to adjust. Pull the workspace again and apply the preset over its global_settings."
            )
    return merged


def load_base(base_path: "str | None", work_path: "str | None", section_id: "str | None", widget: str) -> "tuple[dict[str, object], str]":
    """Return (base settings, kind) where kind is `skeleton`, `pulled`, `settings` or `theme`.

    `settings` is a `--base` file of plain section settings: unlike a pulled section (`--work`
    with `--section-id`, or `kit.py section show` output) nothing says which stored section,
    if any, it comes from."""
    if work_path and base_path:
        raise DesignError("Give either --base or --work, not both.")
    if section_id and not work_path:
        raise DesignError("--section-id selects a section inside --work.")
    if work_path:
        work = read_json(work_path)
        if not isinstance(work, dict):
            raise DesignError(f"{work_path} is not a constructor workspace.")
        if widget == THEME:
            settings = work.get("global_settings")
            if not isinstance(settings, dict):
                raise DesignError(f"{work_path} has no global_settings object.")
            return copy.deepcopy(settings), "theme"
        if not section_id:
            raise DesignError("--work needs --section-id for a section preset (or use --base).")
        found_widget, settings = section_settings(work, section_id)
        if found_widget != widget:
            raise DesignError(f"Section {section_id} is {found_widget}, not {widget}.")
        return settings, "pulled"
    if not base_path:
        raise DesignError("Give the base: --base FILE (kit.py schema widget --skeleton, kit.py section show, or plain settings) or --work FILE.")
    raw = read_json(base_path)
    if not isinstance(raw, dict):
        raise DesignError(f"{base_path} must hold a JSON object.")
    if isinstance(raw.get("skeleton"), dict):
        declared = raw.get("widget")
        if isinstance(declared, str) and declared != widget:
            raise DesignError(f"{base_path} is a skeleton of {declared}, not {widget}.")
        return copy.deepcopy(raw["skeleton"]), "skeleton"
    if isinstance(raw.get("settings"), dict) and isinstance(raw.get("widget"), str):
        if raw["widget"] != widget:
            raise DesignError(f"{base_path} holds a {raw['widget']} section, not {widget}.")
        return copy.deepcopy(raw["settings"]), "pulled"
    if widget == THEME and isinstance(raw.get("global_settings"), dict):
        return copy.deepcopy(raw["global_settings"]), "theme"
    return copy.deepcopy(raw), "theme" if widget == THEME else "settings"


def copy_as_new(pulled: "dict[str, object]", skeleton: "dict[str, object]", doc: "dict[str, object]") -> "tuple[dict[str, object], list[str]]":
    """Settings for a new section built like a pulled one (`section add` on another page).

    `section add` validates a new section outright, while a pulled section is often sparse
    and carries the cabinet's drift. So: the store's skeleton (with the schema defaults its
    builder can lose), the pulled settings over it, no `version` (`section add` stamps it)
    and no empty-string side paddings (the skeleton's value, or the key left to inherit)."""
    notes = ["new section: the pulled settings over the store's skeleton; `version` left to `section add`"]
    base = copy.deepcopy(skeleton)
    fix = doc.get("skeleton_fix")
    if isinstance(fix, dict) and fix:
        base = deep_merge(base, fix)  # type: ignore[assignment]
    own = {key: value for key, value in pulled.items() if key != "version"}
    if normalize_widget(str(doc.get("widget"))) == LISTING:
        # `section add` stamps the current version: the filter options go where the skeleton keeps them.
        form, _reason = forms.listing_form(skeleton, new_section=True)
        if forms.listing_shape(own) not in (None, form):
            own, _dropped = forms.listing_to_form(own, form)
            notes.append(f"listing filters: moved into the shape of the new section ({'`filters`' if form == forms.FILTERS else 'root `filtersPosition`'})")
    result = deep_merge(base, own)
    if normalize_widget(str(doc.get("widget"))) == SHELF and isinstance(result.get("dataSource"), dict):  # type: ignore[union-attr]
        notes += _new_shelf_source(result["dataSource"])  # type: ignore[index]
    section = result.get("section") if isinstance(result, dict) else None  # type: ignore[union-attr]
    if isinstance(section, dict):
        for key in ("hPaddingDesktop", "hPaddingMobile"):
            if section.get(key) == "":
                fallback = get_path(skeleton, f"section.{key}")
                if isinstance(fallback, str) and fallback:
                    section[key] = fallback
                    notes.append(f"section.{key}: the pulled empty string is refused in a new section; took the skeleton's {fallback!r}")
                else:
                    del section[key]
                    notes.append(f"section.{key}: the pulled empty string is refused in a new section; left out to inherit the theme's")
    return result, notes  # type: ignore[return-value]


AUTO_SOURCES = ("relevant", "last_seen")


def _new_shelf_source(source: "dict[str, object]") -> "list[str]":
    """`section add` refuses a shelf without `dataSource.id` (the schema requires `id`, `slug`
    and `type`), and live shelves often carry none. For relevant and last_seen the id is the
    type (checked on a live store); for a category or collection it is the cabinet's id, which
    only yandex-kit-cabinet knows, so it stays a marker listed in `open`."""
    if str(source.get("id") or "").strip():
        return []
    kind = source.get("type")
    if kind in AUTO_SOURCES:
        source["id"] = kind
        if not str(source.get("slug") or "").strip():
            source["slug"] = kind
        return [f"dataSource.id: the pulled shelf has none, which `section add` refuses; wrote {kind!r}, the type, as a {kind} shelf takes it"]
    source["id"] = "<fill: id of this category or collection from yandex-kit-cabinet (list /v1/categories or /v1/collections)>"
    return ["dataSource.id: the pulled shelf has none, which `section add` refuses; it is the cabinet id of the category or collection (see `open`)"]


def _split_path(path: str) -> "list[str]":
    """`modules[].appearance` → ["modules", "[]", "appearance"]."""
    parts: "list[str]" = []
    for chunk in path.split("."):
        if chunk.endswith("[]"):
            parts += [chunk[:-2], "[]"]
        elif chunk:
            parts.append(chunk)
    return parts


def _objects_at(value: object, parts: "list[str]", trail: str = "$"):
    """Yield (concrete path, object) for every object at a path with `[]` wildcards."""
    if not parts:
        if isinstance(value, dict):
            yield trail, value
        return
    head, rest = parts[0], parts[1:]
    if head == "[]":
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from _objects_at(item, rest, f"{trail}[{index}]")
    elif isinstance(value, dict) and head in value:
        yield from _objects_at(value[head], rest, f"{trail}.{head}")


def _object_paths(value: object, trail: str = "$") -> "set[str]":
    """Concrete paths of every object inside a JSON value, the value itself included."""
    paths: "set[str]" = set()
    if isinstance(value, dict):
        paths.add(trail)
        for key, item in value.items():
            paths |= _object_paths(item, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths |= _object_paths(item, f"{trail}[{index}]")
    return paths


def _header_required(defaults: "dict[str, dict[str, object]]", layout: str) -> "dict[str, dict[str, object]]":
    """Required defaults are written for the flat header; regroup `modules.main` when nested."""
    flat = defaults.get("modules.main")
    if layout == "flat" or not isinstance(flat, dict):
        return defaults
    result = {path: values for path, values in defaults.items() if path != "modules.main"}
    grouped = get_path(header_to_layout({"modules": {"main": flat}}, "nested"), "modules.main")
    main: "dict[str, object]" = {}
    for group, values in grouped.items():  # type: ignore[union-attr]
        if isinstance(values, dict):
            main[group] = {}
            result[f"modules.main.{group}"] = values
        else:
            main[group] = values
    result["modules.main"] = main
    return result


def _platform_text(skeleton: "dict[str, object] | None", concrete: str, key: str) -> "str | None":
    """The store skeleton's non-empty text at `$.a.b` + key; None inside module lists."""
    if not isinstance(skeleton, dict) or "[" in concrete:
        return None
    path = ".".join(part for part in (concrete[2:], key) if part)
    value = get_path(skeleton, path)
    return value if isinstance(value, str) and value.strip() and not find_markers(value) else None


def complete_new_objects(
    doc: "dict[str, object]",
    base: "dict[str, object]",
    result: "dict[str, object]",
    layout: str,
    skeleton: "dict[str, object] | None" = None,
) -> "list[str]":
    """Give objects the merge created (absent from the base) the required keys the schema demands.

    Objects the base already had are left alone: gaps there are the store's own drift, which
    the constructor tolerates. Platform copy comes from the store's `skeleton` where it shows
    it. Returns the concrete paths of the keys it wrote.
    """
    defaults = doc.get("required_defaults")
    if not isinstance(defaults, dict) or not defaults:
        return []
    widget = normalize_widget(str(doc.get("widget")))
    if widget == HEADER:
        defaults = _header_required(defaults, layout)  # type: ignore[arg-type]
    elif widget == THEME:
        defaults = forms.theme_required(defaults, forms.theme_form(base)[0])  # type: ignore[arg-type]
    existing = _object_paths(base)
    written: "list[str]" = []
    for path in sorted(defaults, key=lambda p: len(_split_path(p))):  # parents first
        values = defaults[path]
        if not isinstance(values, dict):
            continue
        for concrete, obj in _objects_at(result, _split_path(path)):
            if concrete in existing:
                continue
            for key, value in values.items():
                if key not in obj:
                    text = _platform_text(skeleton, concrete, key) if isinstance(value, str) and value.startswith(PLATFORM_TEXT_MARKER) else None
                    obj[key] = text if text is not None else copy.deepcopy(value)
                    written.append(f"{concrete}.{key}" + (" (platform text from --schema)" if text is not None else ""))
    return written


# Keys of a module that identify it rather than show anything.
MODULE_ID_KEYS = frozenset({"id", "display_sequence"})


def _leaves(value: object, trail: str = "") -> "dict[str, object]":
    """Dotted path → scalar for every leaf of a JSON object (lists and media objects count as leaves)."""
    if isinstance(value, dict) and not ("filePath" in value or "fileId" in value):
        out: "dict[str, object]" = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{trail}.{key}" if trail else key))
        return out
    return {trail: value} if trail else {}


def module_has_content(module: "dict[str, object]", style: "dict[str, object]") -> bool:
    """A module already set up by the store: some text or image outside the keys the style writes."""
    styled = set(_leaves(style))
    for path, value in _leaves(module).items():
        if path in styled or path.split(".")[0] in MODULE_ID_KEYS:
            continue
        if isinstance(value, str) and value.strip() and not find_markers(value):
            return True
        if isinstance(value, dict) and not media_is_empty(value):
            return True
    return False


def style_modules(
    modules: "list[object]", style: "dict[str, object]", list_key: str, restyle: bool
) -> "tuple[list[object], list[str]]":
    """Apply `module_style` to each module. A module that already carries the store's content
    (a configured slide, a tile with its title or image) keeps the style keys it has: the
    preset may overwrite a dark text chosen for a light photo with white. Every key kept or
    overwritten is reported. `restyle` overwrites them all."""
    result: "list[object]" = []
    kept: "list[str]" = []
    changed: "list[str]" = []
    styled = 0
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            result.append(module)
            continue
        own = _leaves(module)
        differs = [(path, own[path], value) for path, value in _leaves(style).items() if path in own and own[path] != value]
        if module_has_content(module, style) and not restyle:
            result.append(deep_merge(style, module))
            kept += [f"$.{list_key}[{index}].{path}: kept {old!r} (the preset writes {new!r})" for path, old, new in differs]
        else:
            result.append(deep_merge(module, style))
            changed += [f"$.{list_key}[{index}].{path}: {old!r} → {new!r}" for path, old, new in differs]
        styled += 1
    notes = [f"module_style applied to {styled} module(s)"]
    if kept:
        notes.append(
            "module_style: modules that already carry content keep their own style; rerun with --restyle-modules to overwrite: "
            + "; ".join(kept)
        )
    if changed:
        notes.append("module_style changed: " + "; ".join(changed))
    return result, notes


def _normalize_dark(result: "dict[str, object]", list_key: "str | None") -> int:
    """backgroundDark "" (common in live data, invalid in the schema) → 0 in the section this
    writes: the storefront reads `backgroundDark || 0` and draws no darkening at 0 either way."""
    fixed = 0
    modules = result.get(list_key) if list_key else None
    for module in modules if isinstance(modules, list) else []:
        appearance = module.get("appearance") if isinstance(module, dict) else None
        if isinstance(appearance, dict) and appearance.get("backgroundDark") == "":
            appearance["backgroundDark"] = 0
            fixed += 1
    text = result.get("text")
    if isinstance(text, dict) and text.get("backgroundDark") == "":
        text["backgroundDark"] = 0
        fixed += 1
    return fixed


def resolve_header_layout(base: "dict[str, object]", live: "str | None") -> "tuple[str, str]":
    """(layout, note). The section's own shape wins: it matches the settings version the
    storefront migrates it by, and rewriting it would drop the stored menu ids. A section
    that shows no shape takes the live schema's; without one there is nothing to go on.

    One exception: flat keys stored at a version newer than `HEADER_FLAT_UNTIL` are never
    regrouped, so the storefront renders that header without its menu and logo settings.
    Such a header takes the nested shape, and `apply_preset` regroups its stored leaves."""
    layout, seen = header_layout(base)
    version = forms.stored_version(base)
    if seen and layout == "flat" and version is not None and version > HEADER_FLAT_UNTIL:
        return "nested", (
            f"header layout: nested. The section stores the flat shape at settings version {version}, which the storefront"
            " no longer regroups: it reads only the nested shape there, so this header renders without its main menu"
            " and logo settings. Its stored leaves were regrouped into the nested shape; check the main menu on the preview"
        )
    if seen:
        note = f"header layout: {layout} (as the section stores it)"
        if live and live != layout:
            note += (
                f"; the live schema is {live}. The section keeps its stored shape and settings version, which the"
                " storefront migrates, and the constructor tolerates the difference as existing drift"
            )
        return layout, note
    if live:
        return live, f"header layout: {live} (the section shows none; taken from the live schema)"
    raise DesignError(
        "The header shows no modules.main shape (flat or nested), and storefront builds differ. Pass --schema with the "
        f"output of `kit.py … schema widget --name {HEADER} --skeleton` (saved to a file)."
    )


def apply_preset(
    doc: "dict[str, object]",
    preset: "dict[str, object]",
    base: "dict[str, object]",
    kind: str,
    live_header_layout: "str | None" = None,
    skeleton: "dict[str, object] | None" = None,
    new_section: bool = False,
    theme: "dict[str, object] | None" = None,
    restyle_modules: bool = False,
) -> "tuple[dict[str, object], list[str]]":
    """Merge the preset over the base; return (settings, notes about what was adapted).

    `live_header_layout` (`flat` or `nested`) is the header shape of the store's live schema;
    a Header section that shows no shape of its own needs it. `skeleton` is the store's own
    skeleton of the widget (`--schema`): platform copy a new object requires comes from it.
    `new_section` marks a base without a version that `section add` will stamp (a skeleton,
    or `--as-new`): the settings shapes that follow the version follow the base's shape then.
    `theme` is the store's `global_settings` when known (`--work`): a section that must follow
    the theme's title alignment (a titled Faq) is fitted to it, and a text preset to the
    theme's box when `maxWidthDesktop` is wider than the default (`typeset.fit_measure_to_theme`).
    `restyle_modules` lets the preset's `module_style` overwrite the style of modules that
    already carry content."""
    widget = normalize_widget(str(doc.get("widget")))
    notes: "list[str]" = []
    result = copy.deepcopy(base)
    layout, layout_note = resolve_header_layout(result, live_header_layout) if widget == HEADER else ("flat", "")
    if widget == HEADER and layout == "nested" and header_layout(result) == ("flat", True):
        result = header_to_layout(result, "nested")
    elif widget == HEADER and layout == "nested":
        moved = regroup_mixed_header(result)
        if moved:
            notes.append(
                "header modules.main: the section mixes flat leaves with nested groups; moved them into their groups so the "
                "stored ids survive the preset: " + "; ".join(moved)
            )
    listing_form = None
    if widget == LISTING and kind == "settings" and forms.stored_version(result) is None:
        listing_form, listing_note = forms.plain_listing_form(result, skeleton)
        notes.append(listing_note)
        if forms.listing_shape(result) not in (None, listing_form):
            result, dropped = forms.listing_to_form(result, listing_form)
            notes += dropped
            notes.append(f"listing filters: the settings' own filter options moved into {forms.LISTING_KEY[listing_form]}")
    elif widget == LISTING:
        listing_form, listing_note = forms.listing_form(result, new_section or kind == "skeleton")
        notes.append(listing_note)
    if kind == "skeleton" and isinstance(doc.get("skeleton_fix"), dict) and doc["skeleton_fix"]:
        fix = doc["skeleton_fix"]
        if widget == HEADER:
            fix = header_to_layout(fix, layout)  # type: ignore[arg-type]
        elif listing_form:
            fix, _dropped = forms.listing_to_form(fix, listing_form)  # type: ignore[arg-type]
        result = deep_merge(result, fix)  # type: ignore[assignment]
        notes.append("skeleton: restored schema defaults the skeleton builder can lose next to $ref")
    overlay = copy.deepcopy(preset.get("settings") or {})
    if widget == HEADER:
        overlay = header_to_layout(overlay, layout)  # type: ignore[arg-type]
        notes.append(layout_note)
    if listing_form:
        overlay, moved = forms.listing_to_form(overlay, listing_form, forms.stored_version(result))
        notes += moved
    if widget == THEME and forms.stored_version(result) is None:
        notes.append(
            "theme: the base carries no `version`, so the storefront runs every theme migration over it; one of them rebuilds "
            "`productSnippet` and drops the price, title, discount and effects settings of the current shape. Apply theme presets "
            "over the pulled `global_settings` (`--work`, or a file an earlier `presets apply theme --work` wrote), never over a "
            "skeleton or plain settings"
        )
    if widget == THEME:
        order_form, order_note = forms.theme_form(result)
        overlay, moved = forms.theme_to_form(overlay, result, order_form)
        if moved:
            notes += [order_note] + moved
    if widget == BANNERS and "aspectRatio" in result and "aspectRatios" not in result and "aspectRatios" in overlay:
        ratios = overlay.pop("aspectRatios")
        overlay["aspectRatio"] = ratios.get("desktop")  # type: ignore[union-attr]
        notes.append(
            "banner layout: this section stores the older single `aspectRatio`; wrote the desktop ratio "
            f"{ratios.get('desktop')!r} there. The mobile image still needs the paired ratio {ratios.get('mobile')!r}."  # type: ignore[union-attr]
        )
    if widget == THEME:
        palettes = get_path(overlay, "appearance.colors.palettes")
        if isinstance(palettes, list):
            del overlay["appearance"]["colors"]["palettes"]  # type: ignore[index]
            current = copy.deepcopy(get_path(result, "appearance.colors.palettes"))
            current = current if isinstance(current, list) else []
            roles = preset.get("palette_roles")
            if isinstance(roles, dict) and roles:
                palettes, reused = reuse_palettes_by_role(current, palettes, roles)
                notes += reused
            merged = _merge_palettes(current, palettes)
            result.setdefault("appearance", {}).setdefault("colors", {})["palettes"] = merged  # type: ignore[union-attr]
            notes.append("palettes merged by id")
    if widget in (FC, CL) and overlay.get("alignTitle") == "default":
        reference = skeleton if isinstance(skeleton, dict) else base if kind == "skeleton" else None
        if isinstance(reference, dict) and reference.get("alignTitle") in ("left", "center"):
            # An older storefront build: its alignTitle enum is left|center and its skeleton shows no `default`.
            del overlay["alignTitle"]
            notes.append("alignTitle: this storefront build knows only left and center; left the section's own value")
    result = deep_merge(result, overlay)  # type: ignore[assignment]
    if widget == FAQ and preset.get("title_follows_theme"):
        theme_align = typeset.theme_title_align(theme)
        if theme_align is None and get_path(result, "appearance.align") == "left" and result.get("title") != "":
            notes.append("Faq: the theme is unknown here; under a centred theme write appearance.align center (pass --work to fit it)")
        notes += typeset.fit_faq_to_theme(result, theme_align)
    notes += typeset.fit_measure_to_theme(widget, preset.get("settings") or {}, result, theme)  # type: ignore[arg-type]
    style = preset.get("module_style")
    list_key = WIDGETS_WITH_MODULE_LIST.get(widget)
    if isinstance(style, dict) and list_key:
        modules = result.get(list_key)
        if isinstance(modules, list) and modules:
            result[list_key], style_notes = style_modules(modules, style, list_key, restyle_modules)
            notes += style_notes
        else:
            notes.append("module_style: no modules yet; apply it to each module you add (`presets show` prints it as module_example)")
    written = complete_new_objects(doc, base, result, layout, skeleton)
    if written:
        shown = ", ".join(written[:8]) + (f" and {len(written) - 8} more" if len(written) > 8 else "")
        notes.append(
            "objects the preset created got the keys the schema requires (defaults; a <fill> marker, listed in "
            f"`open`, where the value is the store's or the merchant's): {shown}"
        )
    for dotted in preset.get("unset") or []:
        removed = unset_path(result, str(dotted))
        if removed is not None:
            notes.append(
                f"{dotted}: removed (it was {removed!r}); the preset leaves it to the theme, so the section inherits it"
            )
    fixed = _normalize_dark(result, list_key)
    if fixed:
        notes.append(f"backgroundDark \"\" → 0 in {fixed} place(s): the schema wants a number, and 0 renders the same")
    return result, notes


LISTING_UNVERSIONED_ROOT = (
    "$.filtersPosition: these settings keep the root filter position and carry no version. For `section set` on a stored section"
    " without a version that is what the storefront reads; for `section add` it is not (a new section is stamped with the current"
    " version, which reads `filters.position`): build a new listing with `--as-new` from the pulled one, or pass a stored one with"
    " `--work … --section-id`"
)


def listing_open_items(
    widget: str, base: "dict[str, object]", kind: str, settings: "dict[str, object]", skeleton: "dict[str, object] | None" = None
) -> "list[str]":
    """Open items that depend on where the base came from, not only on the result: plain listing
    settings in the root shape, where the store's current shape (its skeleton, else the newest) is `filters`."""
    if (
        normalize_widget(widget) == LISTING
        and kind == "settings"
        and forms.stored_version(base) is None
        and forms.listing_shape(settings) == forms.ROOT
        and forms.listing_shape(skeleton) in (None, forms.FILTERS)
    ):
        return [LISTING_UNVERSIONED_ROOT]
    return []


def open_items(widget: str, settings: "dict[str, object]") -> "list[str]":
    """What still has to come from the store or the merchant before this section can ship."""
    widget = normalize_widget(widget)
    items: "list[str]" = []
    for path, text in find_markers(settings):
        items.append(f"{path}: placeholder {text[:50]!r}")
    hidden = hidden_text_paths(widget, settings)
    for path, text in find_platform_placeholders(settings):
        if path not in hidden:
            items.append(f"{path}: platform placeholder text {text!r}")
    # The theme carries no image of its own; its font files are the cabinet's (hard rule 9).
    for path, media in find_media_objects(settings) if widget != THEME else ():
        if media_is_empty(media) and widget != "YandexKit.Footer":
            items.append(f"{path}: image is empty or a data URI — upload a real one")
    list_key = WIDGETS_WITH_MODULE_LIST.get(widget)
    if list_key and not settings.get(list_key):
        items.append(f"$.{list_key}: no modules yet")
    if widget == "YandexKit.FeaturedProducts" and not isinstance(settings.get("dataSource"), dict):
        items.append("$.dataSource: missing — the shelf renders empty without it")
    if widget == BANNERS:
        for index, module in enumerate(settings.get("modules") or []):
            if isinstance(module, dict) and not get_path(module, "slideMedia.media"):
                items.append(f"$.modules[{index}].slideMedia.media: missing image")
            elif isinstance(module, dict) and not get_path(module, "slideMedia.mediaMobile"):
                items.append(f"$.modules[{index}].slideMedia.mediaMobile: missing mobile image")
    if widget == "YandexKit.EmailSubscription" and get_path(settings, "form.warning") == "":
        items.append("$.form.warning: consent text is empty")
    for slot, kind, text in claims.advantages(widget, settings):
        shown = f"text «{text[:50]}»" if text else "the platform wording" + (f" «{claims.PLATFORM_WORDING[kind]}»" if kind in claims.PLATFORM_WORDING else "")
        confirm = f"; {claims.CONFIRM_TYPES[kind]}, confirm with the seller" if kind in claims.CONFIRM_TYPES else ""
        items.append(f"$.modules.advantages.{slot}: {kind}, {shown}: a policy claim, keep it only as the merchant confirms it{confirm}")
    if widget == THEME and get_path(settings, "delivery.enableProgress") is not False:
        items.append(claims.DELIVERY_PROGRESS)
    if widget == THEME:
        font = typeset.describe_font(settings)
        if font and font.get("risk"):
            items.append(f"$.typography.fontFamily: {font['risk']}. {typeset.FONT_ADVICE}")
    return items
