"""Load and look up presets and recipes shipped under references/."""

from __future__ import annotations

import re
from pathlib import Path

from designlib.common import (
    CENTRED_MAX_CHARS,
    EXIT_NOT_FOUND,
    IWT_MAX_CHARS,
    PRESETS_DIR,
    RECIPES_DIR,
    DesignError,
    find_markers,
    get_path,
    read_json,
)

THEME = "theme"
MYSTIQUE = "YandexKit.Mystique"

# Every native widget the storefront BFF describes, plus the custom-section widget.
# A plan naming anything else is a typo or an invention.
KNOWN_WIDGETS = frozenset(
    {
        "YandexKit.AccountLoyalty",
        "YandexKit.AccountNewsletters",
        "YandexKit.AccountOrderDetails",
        "YandexKit.AccountOrdersListing",
        "YandexKit.AccountProfile",
        "YandexKit.AccountRefunds",
        "YandexKit.AnnouncementBar",
        "YandexKit.BannersCarousel",
        "YandexKit.Blog",
        "YandexKit.BlogListing",
        "YandexKit.CollectionsListing",
        "YandexKit.DiscountLadder",
        "YandexKit.EmailSubscription",
        "YandexKit.EmailUnsubscribe",
        "YandexKit.FavouriteProducts",
        "YandexKit.Faq",
        "YandexKit.FeaturedBlogs",
        "YandexKit.FeaturedCollections",
        "YandexKit.FeaturedProducts",
        "YandexKit.Footer",
        "YandexKit.GiftCard",
        "YandexKit.Header",
        "YandexKit.ImageWithText",
        "YandexKit.LoyaltyProgram",
        "YandexKit.PreviewShoppingCart",
        "YandexKit.ProductDetails",
        "YandexKit.ProductReviewsListing",
        "YandexKit.ProductsBundleConstructor",
        "YandexKit.ProductsListing",
        "YandexKit.RichText",
        "YandexKit.ShoppingCart",
        MYSTIQUE,
    }
)

PAGE_TYPES = (
    "home",
    "category",
    "catalog",
    "product",
    "cart",
    "404",
    "about",
    "lookbook",
    "contacts",
    "service",
    "faq",
    "b2b",
    "promo",
    "loyalty",
    "search",
    "reviews",
    "bundle",
    "blog",
    "system",
    "other",
)


# Evidence resting on fewer than 3 stores of the sample. A preset or recipe that says so is an
# optional idea: shown, never picked by default (the anonymity rule of the sample, AGENTS.md).
FEW_STORES = re.compile(r"(?<![\d.])\b(?:1|2|one|two)\s+stores?\b|\b2-3 stores\b|a pattern from [12] store", re.I)


CHARACTER_COUNTS = re.compile(r"(\d[\d,]*)(?:\s*-\s*(\d[\d,]*))?\s+characters")


def _promised_lengths(preset: "dict[str, object]") -> "list[int]":
    """Every character count the preset's `use` and `fill` promise (the upper end of a range)."""
    text = " ".join([str(preset.get("use", ""))] + [str(item) for item in preset.get("fill", []) or []])
    return [int((high or low).replace(",", "")) for low, high in CHARACTER_COUNTS.findall(text)]


def _length_limit(widget: object, preset: "dict[str, object]") -> "int | None":
    """The `long-copy` threshold this preset's copy falls under, if any."""
    settings = preset.get("settings") or {}
    if widget == "YandexKit.RichText" and isinstance(settings, dict) and settings.get("align") == "center":
        return CENTRED_MAX_CHARS
    if widget == "YandexKit.ImageWithText":
        return IWT_MAX_CHARS
    return None


def is_idea(entry: "dict[str, object]") -> bool:
    return entry.get("idea") is True


def needs_idea_mark(preset: "dict[str, object]") -> bool:
    """A preset whose evidence rests on fewer than 3 stores must be marked `"idea": true`.

    The one exception is `basis`: a preset that names the platform behaviour or the principle
    it rests on rests on that, not on how many stores of the sample show it, so its evidence
    may say «in 2 stores» and it may still be a default (`lead-under-hero`: the storefront
    makes the first titled section the H1). A `basis` therefore lifts the idea mark for
    presets of 1-2 stores; write one only where the behaviour or principle decides the preset."""
    return bool(FEW_STORES.search(str(preset.get("evidence", "")))) and not is_idea(preset) and not preset.get("basis")


def normalize_widget(name: str) -> str:
    """Accept `YandexKit.Faq`, `Faq`, `faq`; `theme` stays `theme`."""
    raw = name.strip()
    if raw.lower() in (THEME, "global_settings", "globalsettings"):
        return THEME
    short = raw.split(".", 1)[1] if raw.lower().startswith("yandexkit.") else raw
    for widget in KNOWN_WIDGETS:
        if widget.split(".", 1)[1].lower() == short.lower():
            return widget
    return raw


def short_name(widget: str) -> str:
    return widget.split(".", 1)[1] if widget.startswith("YandexKit.") else widget


def _preset_file(widget: str) -> Path:
    return PRESETS_DIR / f"{short_name(widget)}.json"


def load_widget_doc(widget: str) -> "dict[str, object]":
    widget = normalize_widget(widget)
    path = _preset_file(widget)
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in PRESETS_DIR.glob("*.json")))
        raise DesignError(f"No presets for {widget!r}. Widgets with presets: {available}.", EXIT_NOT_FOUND)
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise DesignError(f"{path.name} must hold a JSON object.")
    return doc


def all_widget_docs() -> "list[dict[str, object]]":
    return [read_json(path) for path in sorted(PRESETS_DIR.glob("*.json"))]  # type: ignore[misc]


def find_preset(widget: str, name: str) -> "tuple[dict[str, object], dict[str, object]]":
    doc = load_widget_doc(widget)
    for preset in doc.get("presets", []):  # type: ignore[union-attr]
        if preset.get("name") == name:
            return doc, preset
    names = ", ".join(p.get("name", "?") for p in doc.get("presets", []))  # type: ignore[union-attr]
    raise DesignError(f"{doc.get('widget')} has no preset {name!r}. Presets: {names}.", EXIT_NOT_FOUND)


def _preset_is_idea(widget: str, name: str) -> bool:
    try:
        _doc, preset = find_preset(widget, name)
    except DesignError:
        return False
    return is_idea(preset)


def preset_names(widget: str) -> "set[str]":
    try:
        doc = load_widget_doc(widget)
    except DesignError:
        return set()
    return {p.get("name") for p in doc.get("presets", [])}  # type: ignore[union-attr]


def all_recipes() -> "list[dict[str, object]]":
    recipes = [read_json(path) for path in sorted(RECIPES_DIR.glob("*.json"))]

    def order(recipe: "dict[str, object]") -> "tuple[int, str]":
        page_type = recipe.get("page_type")
        rank = PAGE_TYPES.index(page_type) if page_type in PAGE_TYPES else len(PAGE_TYPES)
        return rank, str(recipe.get("id", ""))

    return sorted(recipes, key=order)  # type: ignore[arg-type]


def find_recipe(recipe_id: str) -> "dict[str, object]":
    path = RECIPES_DIR / f"{recipe_id}.json"
    if path.is_file():
        recipe = read_json(path)
        if isinstance(recipe, dict):
            return recipe
    known = ", ".join(r.get("id", "?") for r in all_recipes())  # type: ignore[union-attr]
    raise DesignError(f"No recipe {recipe_id!r}. Recipes: {known}.", EXIT_NOT_FOUND)


def consistency_errors() -> "list[str]":
    """Internal consistency of the shipped references; `doctor` and the suite both use it."""
    errors: "list[str]" = []
    for doc in all_widget_docs():
        widget = doc.get("widget")
        if widget != THEME and widget not in KNOWN_WIDGETS:
            errors.append(f"presets: unknown widget {widget!r}")
        if not doc.get("presets") and not doc.get("leave_default"):
            errors.append(f"presets {widget}: no presets and no `leave_default` advice")
        if doc.get("leave_default") and doc.get("presets"):
            errors.append(f"presets {widget}: `leave_default` and presets at once")
        names = [p.get("name") for p in doc.get("presets", [])]
        if len(names) != len(set(names)):
            errors.append(f"presets {widget}: duplicate preset names")
        for preset in doc.get("presets", []):
            label = f"presets {widget}/{preset.get('name')}"
            for field in ("name", "use", "evidence", "settings", "fill"):
                if field not in preset:
                    errors.append(f"{label}: missing {field!r}")
            if "version" in (preset.get("settings") or {}):
                errors.append(f"{label}: settings.version is never part of a preset")
            if find_markers(preset.get("settings")) or find_markers(preset.get("module_style")):
                errors.append(f"{label}: settings carry a <fill> marker; store values belong in 'fill'")
            if needs_idea_mark(preset):
                errors.append(f"{label}: evidence from fewer than 3 stores must be marked \"idea\": true (or name its `basis`)")
            limit = _length_limit(widget, preset)
            if limit is not None and any(length > limit for length in _promised_lengths(preset)):
                errors.append(f"{label}: promises copy longer than the {limit} characters the lint calls long (`long-copy`)")
            for dotted in preset.get("unset", []) or []:
                if get_path(preset.get("settings") or {}, str(dotted)) is not None:
                    errors.append(f"{label}: unset {dotted!r} is also written by settings")
            for partner in preset.get("pairs_with", []) or []:
                if partner not in names:
                    errors.append(f"{label}: pairs_with names unknown preset {partner!r}")
    ids = set()
    for recipe in all_recipes():
        rid = recipe.get("id")
        if rid in ids:
            errors.append(f"recipes: duplicate id {rid!r}")
        ids.add(rid)
        if recipe.get("page_type") not in PAGE_TYPES:
            errors.append(f"recipe {rid}: unknown page_type {recipe.get('page_type')!r}")
        if FEW_STORES.search(str(recipe.get("evidence", ""))) and not is_idea(recipe):
            errors.append(f"recipe {rid}: evidence from fewer than 3 stores must be marked \"idea\": true")
        for theme_preset in recipe.get("theme", []) or []:
            if theme_preset not in preset_names(THEME):
                errors.append(f"recipe {rid}: unknown theme preset {theme_preset!r}")
        slots = [step.get("slot") for step in recipe.get("steps", []) if step.get("slot")]
        if len(slots) != len(set(slots)):
            errors.append(f"recipe {rid}: duplicate slot names")
        for index, step in enumerate(recipe.get("steps", [])):
            variants = step.get("variants") or []
            if variants and not step.get("slot"):
                errors.append(f"recipe {rid} step {index}: variants need a `slot` name")
            for number, option in enumerate(variants, start=2):
                where = f"recipe {rid} step {index} option {number}"
                option_widget = option.get("widget")
                if option_widget not in KNOWN_WIDGETS:
                    errors.append(f"{where}: unknown widget {option_widget!r}")
                elif option.get("preset") and option["preset"] not in preset_names(option_widget):
                    errors.append(f"{where}: {option_widget} has no preset {option['preset']!r}")
                elif option_widget == MYSTIQUE and not option.get("pattern"):
                    errors.append(f"{where}: a custom option names its kit-custom-sections pattern")
                if not str(option.get("when") or "").strip():
                    errors.append(f"{where}: say when to choose it (`when`)")
                if option.get("preset") and _preset_is_idea(str(option_widget), str(option["preset"])) and not option.get("idea"):
                    errors.append(f"{where}: the idea preset {option['preset']!r} is marked \"idea\": true in the option")
            widget = step.get("widget")
            if widget not in KNOWN_WIDGETS:
                errors.append(f"recipe {rid} step {index}: unknown widget {widget!r}")
            preset = step.get("preset")
            if preset and preset not in preset_names(widget):
                errors.append(f"recipe {rid} step {index}: {widget} has no preset {preset!r}")
            elif preset and not is_idea(recipe) and not step.get("optional") and _preset_is_idea(widget, preset):
                # An idea rests on fewer than 3 stores: a default step would make it a default.
                errors.append(f"recipe {rid} step {index}: the idea preset {preset!r} in a default step; mark the step optional")
        for theme_preset in recipe.get("theme", []) or []:
            if not is_idea(recipe) and _preset_is_idea(THEME, theme_preset):
                errors.append(f"recipe {rid}: the idea theme preset {theme_preset!r} in a default recipe")
    return errors
