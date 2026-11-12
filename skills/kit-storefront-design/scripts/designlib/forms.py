"""Settings whose shape depends on the settings version the object is stored with.

The storefront migrates stored settings up to its current shape when it renders them:
every migration runs when the stored `version` is at or below the migration's own
version, and settings without a `version` count as the oldest. So the key a value must
sit under is decided by the version the object carries, not by the newest schema, and a
key of the other shape is ignored or dropped:

- ProductsListing. Up to version 1791195496953 the filter position is the root
  `filtersPosition`; the migration from that version builds `filters.{position,
  enableAutoApply, enableChips}` from it. A section stored at a newer version reads only
  `filters`. The older migrations rebuild the settings field by field, so a `filters`
  object written into an older section is dropped.
- Theme (`global_settings`). Up to version 1790598353696 the order of the product card
  is `productSnippet.general.order` (`price-desc`, `desc-price`) with the reviews line
  in `productReviews.positionInProductSnippet` (`top`, `bottom`); the migration from
  that version builds `productSnippet.general.elementsOrder` from both, replacing any
  stored one. Newer settings read only `elementsOrder` and keep the two older keys
  hidden. `theme set` writes no version, so a theme without one reads the older keys.
- Header. Up to version 1766500000000 a flat `modules.main` is regrouped into
  `menu`/`logo`/`additional`; a newer header is read nested only.

Presets are written in one shape (the listing in `filters`, the theme card in `order`);
`presets apply` moves their values to the shape the base's version reads, and the lint
reads the shape that renders.
"""

from __future__ import annotations

import copy

from designlib.common import get_path

LISTING = "YandexKit.ProductsListing"
# The last ProductsListing version that reads the root `filtersPosition`.
LISTING_ROOT_FILTERS_UNTIL = 1791195496953
# A root-shape listing stored after this version also carries the root filter flags
# (`enableFiltersAutoApply`, `enableFilterChips`), which the migration from
# LISTING_ROOT_FILTERS_UNTIL moves into `filters`; at this version or older the migration from
# it sets both to off, so the flags cannot be kept there.
LISTING_ROOT_FLAGS_AFTER = 1776251403293
# The last theme version that reads `order` and `positionInProductSnippet`.
THEME_ORDER_KEYS_UNTIL = 1790598353696
# Header settings stored at this version or older carry `modules.main` flat (or mixed); the
# storefront regroups it into menu/logo/additional when it renders them. Newer ones are read
# nested only: a flat `modules.main.menu` there is no menu at all.
HEADER_FLAT_UNTIL = 1766500000000

ROOT, FILTERS = "root", "filters"
LISTING_KEY = {FILTERS: "`filters.position`", ROOT: "root `filtersPosition`"}
ORDER_KEYS, ELEMENTS_ORDER = "order", "elementsOrder"

# The storefront's default card order and the elements it knows.
CARD_ELEMENTS = ("reviews", "price", "title")
FILTER_FLAGS_AT_ROOT = {"enableAutoApply": "enableFiltersAutoApply", "enableChips": "enableFilterChips"}


def stored_version(settings: object) -> "int | None":
    version = settings.get("version") if isinstance(settings, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _prune(value: "dict[str, object]", path: "tuple[str, ...]") -> None:
    """Drop the objects along `path` that the move left empty, deepest first."""
    for depth in range(len(path), 0, -1):
        parent = get_path(value, ".".join(path[: depth - 1])) if depth > 1 else value
        if isinstance(parent, dict) and parent.get(path[depth - 1]) == {}:
            del parent[path[depth - 1]]


# --- ProductsListing filters --------------------------------------------------------------


def listing_shape(settings: object) -> "str | None":
    """The shape the settings show: `filters`, `root`, or None when they show neither."""
    if not isinstance(settings, dict):
        return None
    if isinstance(settings.get("filters"), dict):
        return FILTERS
    if "filtersPosition" in settings:
        return ROOT
    return None


def listing_form(settings: object, new_section: bool) -> "tuple[str, str]":
    """(form, reason): where this listing's filter options must be written.

    A stored version decides. Settings without one are either a new section, which
    `section add` stamps with the storefront's current version (so the shape of the store's
    skeleton decides, and the current `filters` without one), or a stored section the
    storefront migrates from the oldest shape, which reads the root key."""
    version = stored_version(settings)
    if version is not None:
        if version > LISTING_ROOT_FILTERS_UNTIL:
            return FILTERS, f"listing filters: `filters.position` (settings version {version} reads `filters`)"
        return ROOT, (
            f"listing filters: root `filtersPosition` (settings version {version} reads the root key and the storefront"
            " migrates it into `filters`)"
        )
    shape = listing_shape(settings)
    if new_section:
        form = shape or FILTERS
        return form, f"listing filters: {'`filters.position`' if form == FILTERS else 'root `filtersPosition`'} (the shape of the new section's skeleton)"
    return ROOT, "listing filters: root `filtersPosition` (the section stores no version: the storefront migrates it from the oldest shape)"


def root_flags_read(version: "int | None") -> bool:
    return version is not None and version > LISTING_ROOT_FLAGS_AFTER


def plain_listing_form(settings: object, skeleton: object = None) -> "tuple[str, str]":
    """(form, reason) for plain settings without a version (`--base` that is neither a pulled
    section nor a skeleton). Nothing says which stored section they come from, so they are
    written as for a new section: `section add` stamps the storefront's current version, and
    `section set` keeps the version of the section it rewrites. The store's skeleton
    (`--schema`) shows the current shape; without it, the settings' own shape, else `filters`."""
    if listing_shape(settings) == ROOT and listing_shape(skeleton) == FILTERS:
        # The settings may come from a stored section without a version (an earlier `--out` of
        # one), which `section set` keeps unversioned and the storefront reads by the root key;
        # moving them into `filters` would let the oldest migration drop it. Keep their shape.
        return ROOT, (
            "listing filters: root `filtersPosition`, as the settings show it. They carry no version: right for `section set` on"
            " a stored section without one, wrong for `section add`, which stamps the current version and reads `filters` (see `open`)"
        )
    source = skeleton if listing_shape(skeleton) else settings
    form, _reason = listing_form(source, new_section=True)
    where = (
        "the store's skeleton shows" if source is skeleton
        else "the settings show" if listing_shape(settings)
        else "the current settings version reads"
    )
    return form, (
        f"listing filters: {LISTING_KEY[form]} (plain settings carry no version, so they are written as for a new"
        f" section, in the shape {where}. For a stored section pass it with its version: `--work` with"
        " `--section-id`, or the output of `kit.py section show`)"
    )


def listing_to_form(
    settings: "dict[str, object]", form: str, version: "int | None" = None
) -> "tuple[dict[str, object], list[str]]":
    """Move the filter options of preset or pulled settings into `form`; returns (settings, notes).

    `version` is the stored version of the section the result lands in: a root-shape section
    stored after LISTING_ROOT_FLAGS_AFTER keeps the filter flags under their root keys."""
    result = copy.deepcopy(settings)
    notes: "list[str]" = []
    if form == FILTERS and "filtersPosition" in result:
        position = result.pop("filtersPosition")
        filters = result.setdefault("filters", {})
        if isinstance(filters, dict):
            filters.setdefault("position", position)
        for flag, root_key in FILTER_FLAGS_AT_ROOT.items():
            if root_key in result:
                value = result.pop(root_key)
                if isinstance(filters, dict):
                    filters.setdefault(flag, value)
    elif form == ROOT and isinstance(result.get("filters"), dict):
        filters = result.pop("filters")
        if "position" in filters:
            result.setdefault("filtersPosition", filters["position"])
        if version is not None and version > LISTING_ROOT_FLAGS_AFTER:
            for flag, root_key in FILTER_FLAGS_AT_ROOT.items():
                if flag in filters:
                    result.setdefault(root_key, filters[flag])
        dropped = [key for key in filters if key != "position" and not (key in FILTER_FLAGS_AT_ROOT and root_flags_read(version))]
        if dropped:
            notes.append(
                "listing filters: " + ", ".join(f"`filters.{key}`" for key in dropped) + " left out: this section's settings"
                " version predates them, and the storefront migrates them to off. Re-save the section in the cabinet"
                " to get them"
            )
    return result, notes


# --- theme: product card order ------------------------------------------------------------


def theme_form(settings: object) -> "tuple[str, str]":
    """(form, reason): `order` (the order keys) or `elementsOrder`, by the theme's version.

    A theme without a version counts as the oldest: the storefront runs every migration
    over it, and the one from 1790598353696 rebuilds `elementsOrder` from the order keys,
    replacing a stored `elementsOrder`. `theme set` writes no version, so a theme built from
    a skeleton or from plain settings reads the order keys even when it shows `elementsOrder`."""
    version = stored_version(settings)
    if version is not None:
        if version > THEME_ORDER_KEYS_UNTIL:
            return ELEMENTS_ORDER, f"product card order: `elementsOrder` (theme version {version} reads it)"
        return ORDER_KEYS, (
            f"product card order: `order` and `positionInProductSnippet` (theme version {version} reads them; the"
            " storefront builds `elementsOrder` from them once it moves the theme to the newer version)"
        )
    if isinstance(get_path(settings, "productSnippet.general.elementsOrder"), list):
        return ORDER_KEYS, (
            "product card order: `order` and `positionInProductSnippet` (the theme stores no version, so the storefront"
            " migrates it from the oldest shape and rebuilds `elementsOrder` from these two keys, replacing the stored one)"
        )
    return ORDER_KEYS, "product card order: `order` and `positionInProductSnippet` (the theme stores no version: the storefront migrates it from the oldest shape)"


def normalize_elements(value: object) -> "list[str]":
    """The storefront's normalisation: known elements once each, the missing ones appended in the default order."""
    seen: "list[str]" = []
    for element in value if isinstance(value, list) else []:
        if element in CARD_ELEMENTS and element not in seen:
            seen.append(element)
    return seen + [element for element in CARD_ELEMENTS if element not in seen]


def elements_from_order(order: object, reviews: object) -> "list[str]":
    """The migration's mapping: `desc-price` puts the title before the price; `bottom` puts reviews last."""
    pair = ["title", "price"] if order == "desc-price" else ["price", "title"]
    return pair + ["reviews"] if reviews == "bottom" else ["reviews"] + pair


def theme_to_form(overlay: "dict[str, object]", base: "dict[str, object]", form: str) -> "tuple[dict[str, object], list[str]]":
    """Move the card order of a theme preset into `form`, keeping what the preset does not set.

    Into `elementsOrder`: `order` swaps the title and the price in the places they hold in
    the base's order, `positionInProductSnippet` moves the reviews to the start or the end;
    the two older keys leave the overlay, since that version no longer reads them. Into the
    order keys: an `elementsOrder` becomes `order` and, when the reviews are first or last,
    `positionInProductSnippet`."""
    result = copy.deepcopy(overlay)
    notes: "list[str]" = []
    general = get_path(result, "productSnippet.general")
    reviews_block = result.get("productReviews")
    if form == ELEMENTS_ORDER:
        order = general.pop("order", None) if isinstance(general, dict) else None
        position = reviews_block.pop("positionInProductSnippet", None) if isinstance(reviews_block, dict) else None
        if order is None and position is None:
            return result, notes
        current = normalize_elements(
            get_path(base, "productSnippet.general.elementsOrder")
            if isinstance(get_path(base, "productSnippet.general.elementsOrder"), list)
            else elements_from_order(get_path(base, "productSnippet.general.order"), get_path(base, "productReviews.positionInProductSnippet"))
        )
        if isinstance(general, dict) and isinstance(general.get("elementsOrder"), list):
            current = normalize_elements(general["elementsOrder"])
        if order is not None:
            slots = [index for index, element in enumerate(current) if element in ("price", "title")]
            pair = ["title", "price"] if order == "desc-price" else ["price", "title"]
            for index, element in zip(slots, pair):
                current[index] = element
        if position is not None:
            current = [element for element in current if element != "reviews"]
            current = current + ["reviews"] if position == "bottom" else ["reviews"] + current
        result.setdefault("productSnippet", {}).setdefault("general", {})["elementsOrder"] = current  # type: ignore[union-attr]
        _prune(result, ("productReviews",))
        notes.append(f"product card order: wrote `elementsOrder` {current} in place of the older `order`/`positionInProductSnippet`")
        return result, notes
    elements = general.pop("elementsOrder", None) if isinstance(general, dict) else None
    if elements is None:
        sets_order = (isinstance(general, dict) and "order" in general) or (
            isinstance(reviews_block, dict) and "positionInProductSnippet" in reviews_block
        )
        if sets_order and isinstance(get_path(base, "productSnippet.general.elementsOrder"), list):
            notes.append(
                "product card order: wrote `order`/`positionInProductSnippet`; the storefront rebuilds the theme's stored"
                " `elementsOrder` from them when it migrates this theme, so that key is left as it is"
            )
        return result, notes
    current = normalize_elements(elements)
    general["order"] = "desc-price" if current.index("title") < current.index("price") else "price-desc"  # type: ignore[index]
    if current[0] == "reviews" or current[-1] == "reviews":
        result.setdefault("productReviews", {})["positionInProductSnippet"] = "top" if current[0] == "reviews" else "bottom"  # type: ignore[union-attr]
    else:
        notes.append("product card order: this theme version places reviews only first or last; their place is left as the store has it")
    notes.append(f"product card order: wrote `order` {general['order']!r} in place of `elementsOrder` {current}")  # type: ignore[index]
    return result, notes


def theme_required(defaults: "dict[str, dict[str, object]]", form: str) -> "dict[str, dict[str, object]]":
    """Required defaults are generated for the order keys; swap them for `elementsOrder` when that is the form."""
    if form != ELEMENTS_ORDER:
        return defaults
    result = {path: dict(values) for path, values in defaults.items()}
    general = result.get("productSnippet.general")
    if isinstance(general, dict) and "order" in general:
        general.pop("order")
        general["elementsOrder"] = list(CARD_ELEMENTS)
    reviews = result.get("productReviews")
    if isinstance(reviews, dict):
        reviews.pop("positionInProductSnippet", None)
    return result
