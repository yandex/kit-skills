"""Read a constructor workspace (`kit.py content pull --out work.json`) without changing it.

The workspace is the constructor's file; this module only reads it to turn a page into
a plan for the lint, and never writes it back.
"""

from __future__ import annotations

import copy
import json
import re

from designlib.common import EXIT_NOT_FOUND, DesignError, get_path

PAGE_TYPE_BY_ALIAS = {
    "main": "home",
    "catalog": "catalog",
    "categoryAndCollection": "category",
    "productCard": "product",
    "cart": "cart",
    "404": "404",
    "delivery": "service",
    "payment": "service",
    "return": "service",
    "details": "service",
    "oferta": "service",
    "privacy": "service",
    "giftCardQuestions": "service",
    "loyaltyOffer": "service",
    "loyaltyLanding": "loyalty",
    "search": "search",
    "productReviews": "reviews",
    "productBundle": "bundle",
    "blog": "blog",
    "blogEntry": "blog",
    "unsubscribe": "system",
    "subscription": "system",
    "favouriteProducts": "system",
    "giftCard": "system",
    "discountLadder": "system",
    "accountLoyalty": "system",
    "accountNewsletters": "system",
    "accountOrderDetails": "system",
    "accountOrders": "system",
    "accountProfile": "system",
    "accountRefunds": "system",
    "cartPreview": "system",
    "emailConfirmed": "system",
    "emailConfirmationFailed": "system",
}
# The theme keys the lint reads: the phone-height estimate (designlib.touch.Theme), title
# alignment and the font (designlib.typeset).
THEME_KEYS = ("appearance", "collectionSnippet", "productSnippet", "typography")
# A custom template whose alias names a system widget takes that widget's place: the
# header or footer slot, or the product card or listing of its page.
ROLE_BY_TEMPLATE_ALIAS = {
    "YandexKit.Header": "header",
    "YandexKit.Footer": "footer",
    "YandexKit.ProductDetails": "product",
    "YandexKit.ProductsListing": "listing",
}


def _pages(work: "dict[str, object]") -> "list[dict[str, object]]":
    pages = work.get("pages")
    if not isinstance(pages, list):
        raise DesignError("The workspace has no pages array. Pass the file written by `kit.py content pull --out`.")
    return [page for page in pages if isinstance(page, dict)]


# A custom page has no alias; its address names what it is (`/about`, `/faq`, `/promo-summer`).
PAGE_TYPE_BY_PATTERN = (
    (re.compile(r"^/(?:about|o-nas|o-kompanii|brand|story)(?:/|$)", re.I), "about"),
    (re.compile(r"^/(?:faq|help|voprosy)(?:/|$)", re.I), "faq"),
    (re.compile(r"^/(?:contacts?|kontakty)(?:/|$)", re.I), "contacts"),
    (re.compile(r"^/(?:lookbook|lukbuk)", re.I), "lookbook"),
    (re.compile(r"^/(?:promo|sale|akci)", re.I), "promo"),
    (re.compile(r"^/(?:opt|wholesale|b2b)(?:/|$|-)", re.I), "b2b"),
)


def page_type(page: "dict[str, object]") -> str:
    alias = page.get("alias")
    if isinstance(alias, str) and alias:
        return PAGE_TYPE_BY_ALIAS.get(alias, "other")
    pattern = page.get("pattern")
    for regex, kind in PAGE_TYPE_BY_PATTERN if isinstance(pattern, str) else ():
        if regex.search(pattern):  # type: ignore[arg-type]
            return kind
    return "other"


def page_label(page: "dict[str, object]") -> str:
    alias = page.get("alias")
    title = page.get("title")
    variant = page.get("variant_type")
    label = alias if isinstance(alias, str) and alias else f"custom:{page.get('pattern')}"
    if variant and variant != "default":
        label += f" ({variant}: {title})"
    return label


def find_pages(work: "dict[str, object]", keys: "list[str]", all_pages: bool) -> "list[dict[str, object]]":
    pages = _pages(work)
    if all_pages:
        return pages
    found = []
    for key in keys:
        matches = [p for p in pages if key in (p.get("id"), p.get("alias"), p.get("pattern"))]
        if not matches:
            known = ", ".join(sorted({str(p.get("alias") or p.get("pattern")) for p in pages}))
            raise DesignError(f"No page {key!r} in the workspace. Pages: {known}.", EXIT_NOT_FOUND)
        # An alias with alternatives (productCard, categoryAndCollection): the default first.
        matches.sort(key=lambda p: p.get("variant_type") != "default")
        found.extend(matches if key == matches[0].get("alias") and len(matches) > 1 else matches[:1])
    return found


def _settings_by_section_id(work: "dict[str, object]") -> "dict[str, dict[str, object]]":
    """Settings per section id, preferring the parent copy, then any non-empty copy."""
    chosen: "dict[str, tuple[int, dict[str, object]]]" = {}
    for page in _pages(work):
        for section in page.get("layout") or []:
            if not isinstance(section, dict) or not isinstance(section.get("id"), str):
                continue
            settings = section.get("settings")
            if not isinstance(settings, dict) or not settings:
                continue
            rank = 0 if section.get("section_type") == "parent" else 1
            current = chosen.get(section["id"])
            if current is None or rank < current[0]:
                chosen[section["id"]] = (rank, settings)
    return {key: value for key, (_rank, value) in chosen.items()}


def copies_by_section_id(work: "dict[str, object]") -> "dict[str, int]":
    counts: "dict[str, int]" = {}
    for page in _pages(work):
        for section in page.get("layout") or []:
            if isinstance(section, dict) and isinstance(section.get("id"), str):
                counts[section["id"]] = counts.get(section["id"], 0) + 1
    return counts


def section_settings(work: "dict[str, object]", section_id: str) -> "tuple[str, dict[str, object]]":
    by_id = _settings_by_section_id(work)
    for page in _pages(work):
        for section in page.get("layout") or []:
            if isinstance(section, dict) and section.get("id") == section_id:
                return str(section.get("widget")), copy.deepcopy(by_id.get(section_id, {}))
    raise DesignError(f"No section {section_id!r} in the workspace.", EXIT_NOT_FOUND)


def palette_ids(work: "dict[str, object]") -> "list[str] | None":
    palettes = get_path(work, "global_settings.appearance.colors.palettes")
    if not isinstance(palettes, list):
        return None
    return [p["id"] for p in palettes if isinstance(p, dict) and isinstance(p.get("id"), str)]


def _template_headings(work: "dict[str, object]") -> "dict[str, str]":
    """Per template id: `h1` when its markup carries a literal h1, `title` when it prints a
    section title (`ya-kit-section-title`, which the storefront makes the page's H1 when it
    comes first), nothing otherwise."""
    templates = work.get("section_templates")
    found: "dict[str, str]" = {}
    for t in templates if isinstance(templates, list) else []:
        html = t.get("html") if isinstance(t, dict) else None
        if not (isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(html, str)):
            continue
        if re.search(r"<h1\b|as=[\"']h1[\"']", html):
            found[t["id"]] = "h1"
        elif "ya-kit-section-title" in html:
            found[t["id"]] = "title"
    return found


def _template_schemas(work: "dict[str, object]") -> "dict[str, dict[str, object]]":
    """Per template id: its json_schema (stored as a JSON string or an object)."""
    templates = work.get("section_templates")
    found: "dict[str, dict[str, object]]" = {}
    for t in templates if isinstance(templates, list) else []:
        if not (isinstance(t, dict) and isinstance(t.get("id"), str)):
            continue
        schema = t.get("json_schema")
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError:
                continue
        if isinstance(schema, dict):
            found[t["id"]] = schema
    return found


def _template_aliases(work: "dict[str, object]") -> "dict[str, str]":
    templates = work.get("section_templates")
    if not isinstance(templates, list):
        return {}
    return {
        t["id"]: t["alias"]
        for t in templates
        if isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("alias"), str)
    }


def page_plan(work: "dict[str, object]", page: "dict[str, object]") -> "dict[str, object]":
    """A lint plan for one pulled page: widgets in display order with effective settings.

    Sections the store keeps hidden render nothing and are left out."""
    by_id = _settings_by_section_id(work)
    copies = copies_by_section_id(work)
    aliases = _template_aliases(work)
    headings = _template_headings(work)
    schemas = _template_schemas(work)
    layout = [s for s in page.get("layout") or [] if isinstance(s, dict)]
    layout.sort(key=lambda s: s.get("display_sequence") if isinstance(s.get("display_sequence"), (int, float)) else 0)
    hidden_sections = sum(1 for s in layout if s.get("status") == "hidden")
    layout = [s for s in layout if s.get("status") != "hidden"]
    sections = []
    for section in layout:
        section_id = section.get("id")
        settings = section.get("settings")
        if not isinstance(settings, dict) or not settings:
            settings = by_id.get(section_id, {}) if isinstance(section_id, str) else {}
        entry: "dict[str, object]" = {
            "widget": section.get("widget"),
            "settings": copy.deepcopy(settings),
            "id": section_id,
            "copies": copies.get(section_id, 1) if isinstance(section_id, str) else 1,
        }
        if section.get("widget") == "YandexKit.Mystique":
            entry["template_id"] = section.get("template_id")
            alias = aliases.get(str(section.get("template_id")), "")
            role = ROLE_BY_TEMPLATE_ALIAS.get(alias)
            if role:
                entry["role"] = role
            if alias:
                entry["template_alias"] = alias
            schema = schemas.get(str(section.get("template_id")))
            if schema is not None:
                entry["template_schema"] = schema
            heading = headings.get(str(section.get("template_id")))
            if heading:
                entry["heading"] = heading
        sections.append(entry)
    theme = work.get("global_settings") if isinstance(work.get("global_settings"), dict) else {}
    plan: "dict[str, object]" = {
        "page": page_label(page),
        "page_type": page_type(page),
        "palettes": palette_ids(work),
        "stage": "final",
        "pulled": True,
        "variant_type": page.get("variant_type"),
        "theme": {key: theme[key] for key in THEME_KEYS if key in theme},  # type: ignore[index]
        "sections": sections,
    }
    if isinstance(page.get("id"), str):
        plan["page_id"] = page["id"]
    if isinstance(page.get("meta"), dict):
        plan["meta"] = copy.deepcopy(page["meta"])
    if isinstance(page.get("alias"), str):
        plan["alias"] = page["alias"]
    assigned = _assigned(page)
    if assigned is not None:
        plan["assigned"] = assigned
    elif page.get("variant_type") not in (None, "default") and page.get("alias") in ("productCard", "categoryAndCollection"):
        plan["assigned_unknown"] = True
    if page.get("status") == "hidden":
        plan["hidden"] = True
    if hidden_sections:
        plan["hidden_sections"] = hidden_sections
    return plan


def _assigned(page: "dict[str, object]") -> "int | None":
    """Products (product page) or categories and collections (category page) assigned to a variant."""
    alias = page.get("alias")
    keys = ("product_cards_count",) if alias == "productCard" else ("categories_count", "collections_count") if alias == "categoryAndCollection" else ()
    counts = [page.get(key) for key in keys]
    if not counts or not all(isinstance(c, int) for c in counts):
        return None
    return sum(counts)  # type: ignore[arg-type]
