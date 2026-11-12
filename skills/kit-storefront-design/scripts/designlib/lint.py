"""Plan lint: the machine-checkable design principles over an ordered list of sections.

A plan is either written by the agent (`recipes show <id> --plan` gives the skeleton) or
built from a pulled page (`plan lint --work work.json --page main`). Every rule reads
only what is present: a draft plan that names widgets and presets is checked for
composition, a pulled page is also checked for placeholders, media and links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from designlib import claims, forms, touch, typeset
from designlib.catalog import KNOWN_WIDGETS, MYSTIQUE, PAGE_TYPES, THEME, find_preset, find_recipe, normalize_widget
from designlib.common import (
    CENTRED_MAX_CHARS,
    IWT_MAX_CHARS,
    DesignError,
    deep_merge,
    find_markers,
    find_media_objects,
    find_platform_placeholders,
    get_path,
    walk_strings,
    media_is_empty,
    unset_path,
)

ERROR, WARN, INFO = "error", "warn", "info"

FP = "YandexKit.FeaturedProducts"
FC = "YandexKit.FeaturedCollections"
BC = "YandexKit.BannersCarousel"
ES = "YandexKit.EmailSubscription"
IWT = "YandexKit.ImageWithText"
RT = "YandexKit.RichText"
FAQ = "YandexKit.Faq"
CL = "YandexKit.CollectionsListing"
HEADER = "YandexKit.Header"
FOOTER = "YandexKit.Footer"
BAR = "YandexKit.AnnouncementBar"
PD = "YandexKit.ProductDetails"
PL = "YandexKit.ProductsListing"
CART = "YandexKit.ShoppingCart"
FB = "YandexKit.FeaturedBlogs"
CHROME = {BAR: "announcement", HEADER: "header", FOOTER: "footer"}
# A custom section in the header or footer slot (its template carries the Header or Footer alias)
# plays that part: it is chrome, not content.
CHROME_ROLES = frozenset(CHROME.values())
ONE_PER_PAGE = (HEADER, FOOTER, BAR, ES, "YandexKit.ProductDetails", "YandexKit.ShoppingCart", "YandexKit.ProductsListing")

BANNER_RATIO_PAIRS = {"2:1": "4:3", "5:2": "4:3", "7:2": "7:3", "5:1": "5:2", "10:1": "7:1", "fit-screen": "fit-screen"}
# The pair is the default; content may choose otherwise: a strip with more than a few words
# becomes a 4:3 card on phones, an immersive hero a full-height portrait.
BANNER_RATIO_ALTERNATIVES = {"2:1": ("fit-screen",), "5:2": ("fit-screen",), "5:1": ("4:3",), "10:1": ("4:3",), "7:2": ("4:3",)}
HOME_LENGTH = (8, 14)
HOME_MAX_SHELVES = 4
CUSTOM_MAX = 4
# Custom patterns (kit-custom-sections) and native presets that give a home its own face
# (looks.md, native-vs-custom.md); `running-line` is the loud two-row `ticker-band`. Any option of
# a recipe's `signature` slot counts too, whatever its widget; the suite checks that every option
# of every signature slot is in one of these, so a plan without `slot` is judged alike.
SIGNATURE_PATTERNS = ("type-band", "ticker-band", "running-line", "tile-mosaic", "stats-band", "testimonial-spotlight", "video-reels")
SIGNATURE_PRESETS = (
    ("YandexKit.FeaturedCollections", "lookbook-pair-bleed"),
    ("YandexKit.FeaturedCollections", "editorial-diptych-gapless"),
    ("YandexKit.FeaturedCollections", "captioned-diptych"),
    ("YandexKit.FeaturedCollections", "benefit-mosaic"),
    ("YandexKit.FeaturedProducts", "gallery-shelf-gapless"),
)
# Slots of a home recipe that are chrome: varying them does not vary the page.
CHROME_SLOTS = frozenset({"announcement", "header", "footer"})
RHYTHM_MIN_VARIED = 2
RHYTHM_MIN_KINDS = 5
# One phone line of the bar holds about 33 bold or 47 regular characters, title and body together.
ANNOUNCEMENT_MAX_CHARS = 35
LIVE_TEXT_STRIP_WORDS = 5
HOME_MAX_SCREENS = 10
HOME_MAX_GRID_ROWS = 4
TEXT_MIN_INSET = 150
TEXT_INSET_WIDGETS = (RT, IWT, FAQ, ES, HEADER)
AUTO_SOURCES = ("relevant", "last_seen")
# A promise of a discount, not a mention of one: «получите скидку», «скидка 10%», «−15%»,
# «10% off». «Узнавайте о скидках первыми» or «100% хлопок» promise nothing.
DISCOUNT_PROMISE = re.compile(
    r"(?:получ|подар|дари|дади|даём|даем|дает|дарим|\bget\b|receive|enjoy|\bsave\b)[^.!?\n]{0,40}"
    r"(?:скидк|промокод|discount|promo ?code|\d+\s*%)"
    r"|скидк\w*\s+(?:до\s+)?\d+"
    r"|(?:^|\s)[-−–]\s?\d{1,2}\s*%"
    r"|\b\d{1,2}\s*%\s*(?:скидк|off\b|discount)",
    re.I,
)
ROLES = (
    "announcement", "header", "footer", "hero", "categories", "tiles", "shelf", "banner", "break", "story",
    "text", "heading", "proof", "faq", "subscribe", "closing", "listing", "catalog", "product", "cart",
    "loyalty", "custom", "gallery", "service", "form", "signature",
)

# One line per rule: the principle it enforces, for `plan rules` and the checklist.
RULES = {
    "unknown-widget": (ERROR, "Widget is not a native Кит widget or YandexKit.Mystique."),
    "unknown-preset": (ERROR, "Preset is not in references/presets for this widget."),
    "unknown-role": (WARN, "Role is not in the plan vocabulary."),
    "chrome-count": (ERROR, "At most one of this widget per page."),
    "header-twice": (WARN, "Two Header sections on one page: check that only one renders."),
    "one-hero": (ERROR, "One hero per page (principle 3)."),
    "hero-stack": (WARN, "No hero-sized banner (a hero preset, 2:1, 5:2 or full screen) right under the hero, a custom hero included (principle 3)."),
    "hero-first": (WARN, "Home opens with a hero right under the header (principle 3)."),
    "hero-slides": (WARN, "Hero with more than 3 slides; rotation is for multi-brand or promo retail only (principle 3)."),
    "banner-mobile-image": (ERROR, "Every banner slide has its own mobile image (principle 4)."),
    "banner-ratio-pair": (WARN, "Desktop and mobile banner ratios form a recommended pair, or a strip becomes a 4:3 card and a hero a full-height portrait (principle 4)."),
    "button-text": (ERROR, "A slide with linkType button needs buttonText."),
    "banner-button-contrast": (WARN, "A button on a darkened slide (backgroundDark 24 or more) or under white text is primary: a secondary one reads as switched off."),
    "adjacent-shelves": (ERROR, "No two same-shaped product shelves back to back (principle 7)."),
    "adjacent-shelves-loose": (WARN, "On home, adjacent shelves that are not a bento pair read as one long list (principle 7)."),
    "shelf-source-repeat": (ERROR, "One data source per shelf per page (principle 8)."),
    "shelf-no-source": (ERROR, "A product shelf without dataSource renders empty."),
    "feed-relevant": (ERROR, "layout feed does not work with the relevant source."),
    "grid-count": (WARN, "In a grid, count is a multiple of columns."),
    "shelf-count": (WARN, "More than 4 product shelves on home fatigue the page (principle 22)."),
    "category-entry": (WARN, "Home offers a category entry within the first two content sections (principle 9)."),
    "tile-links": (WARN, "Navigation tiles lead somewhere (principle 10)."),
    "banner-links": (WARN, "A banner that is not the hero leads somewhere (principle 10)."),
    "subscribe-missing": (WARN, "Home closes with EmailSubscription (principle 15)."),
    "subscribe-last": (WARN, "EmailSubscription is the last content section before the Footer (principle 15)."),
    "subscribe-above-fold": (ERROR, "EmailSubscription never sits in the first two content sections."),
    "discount-without-promocode": (ERROR, "A subscription promises a discount only with a linked promocode (principle 23)."),
    "consent-text": (ERROR, "The subscription consent text is never empty."),
    "home-length": (WARN, "Home has 8-14 content sections (principle 22)."),
    "palette-missing": (ERROR, "A section palette id exists in the theme (principle 13)."),
    "palette-churn": (WARN, "Body on the base palette; colour bands are few (principle 13)."),
    "transparent-header": (WARN, "A transparent header needs an edge-to-edge BannersCarousel as the first section: over any other widget it lies on that section's background, and the image starts below it (principle 18)."),
    "transparent-composite": (WARN, "A transparent header on the shared header makes every page transparent (principle 18)."),
    "announcement-length": (WARN, "Each announcement module is 35 characters or less, title and body together (principle 20)."),
    "faq-empty": (ERROR, "Every question has a question and an answer."),
    "faq-titled-twice": (WARN, "No two titled Faq sections on one page."),
    "image-sliver": (WARN, "No 10:1 or 7:1 image in an image-and-text split."),
    "zigzag": (WARN, "Stacked image-and-text blocks alternate sides (principle 16)."),
    "placeholder-marker": (ERROR, "No <fill> marker or bracketed placeholder (a whole value in <…> or […]) is published (principle 23)."),
    "platform-placeholder": (ERROR, "No visible platform placeholder text or all-zero phone is published (principle 23)."),
    "media-missing": (ERROR, "No empty or data-URI image, no banner without slides or slide without an image is published (principle 23); on a custom section only a warning."),
    "custom-without-template": (ERROR, "A YandexKit.Mystique section always carries template_id."),
    "custom-share": (INFO, "Native for commerce: more than 4 custom sections on home (principle 24)."),
    "empty-enum": (INFO, "hPadding and backgroundDark are not empty strings; the cabinet writes them in every store and the constructor tolerates them as existing drift."),
    "service-page-empty": (WARN, "A service page carries the merchant's text before it is linked (principle 23)."),
    "shelf-seeded-source": (WARN, "No shelf, tile or link points at the template's seeded «Категория 1-го уровня»: it shows the template's products or none."),
    "shelf-source-id": (ERROR, "A shelf the plan adds carries dataSource.id: the schema requires it and `section add` refuses a shelf without one."),
    "listing-filters": (ERROR, "ProductsListing writes the filter position its settings version reads; left out, the storefront draws the inline filter sidebar nobody chose."),
    "header-menu-empty": (ERROR, "The Header main menu is set where the settings version reads it: without it phones get no burger menu."),
    "header-burger": (INFO, "On desktop the header shows a flat menu as links: a burger alone (menuVariant icon) hides the categories behind a click; it is for a nested or long menu."),
    "richtext-empty": (ERROR, "No RichText with neither a title nor content (an empty spacer)."),
    "faq-repeated": (ERROR, "No question is asked twice in one Faq."),
    "faq-expand": (WARN, "Faq writes appearance.expand: the schema default opens the first answer; on a product page all answers start closed."),
    "product-card-missing": (ERROR, "A product page carries its product card: ProductDetails, or a custom card (role product) with its template."),
    "pdp-tail": (WARN, "On a product page the recently viewed shelf comes last: never the only shelf and never the first after the product card."),
    "shelf-buttons": (WARN, "All product shelves of a page show card buttons, or none: enableButton default lets the theme card decide."),
    "shelf-heading-orphan": (WARN, "No heading section right above a relevant or last_seen shelf: when the shelf is empty the heading stays alone."),
    "variant-dead": (INFO, "An alternative product or category page has products or categories assigned in the cabinet (a page the workspace shows no counts for is reported to check)."),
    "page-empty": (ERROR, "A published page carries a content section: a header over a footer is a dead page."),
    "system-widget-missing": (ERROR, "A system page (cart, search, blog, account, reviews and the like) carries the widget that makes it work."),
    "touch-stack": (WARN, "No tile grid of 3 or more tiles that stacks one tile per row on phones."),
    "touch-sliver": (WARN, "No 2-column tile carousel with one tile per phone view: the second tile shows as a sliver."),
    "shelf-touch": (WARN, "Product shelves show 2 cards per phone row; home product grids take 4 phone rows or fewer."),
    "touch-odd": (WARN, "A grid with 2 items per phone row holds an even number of items."),
    "touch-length": (WARN, "Home is 10 phone screens or shorter (estimate from the section settings)."),
    "footer-untitled": (WARN, "Every footer menu column has a title: untitled columns print long flat lists on phones."),
    "button-text-hidden": (WARN, "A slide linked as a whole (linkType banner) carries no buttonText: the storefront never shows it."),
    "touch-inset": (WARN, "Text sections keep a phone side inset of 150 or more."),
    "banner-mobile-same": (WARN, "A slide without live text has a mobile image of its own, not the desktop file again."),
    "announcement-contacts": (WARN, "The bar carries no contacts: phones hide them."),
    "long-copy": (INFO, "Image-with-text copy is 350 characters or less; centred RichText 200 or less."),
    "page-dead-end": (WARN, "A cart, 404, catalog or blog page leads to products: a shelf a first visitor sees (not recently viewed alone), or on 404 a linked banner."),
    "seo-meta-empty": (WARN, "A published static or custom page has its own seo_title and seo_description: empty, the title becomes a generic template and the description one that promises bonuses, instalments and cashback."),
    "page-h1": (WARN, "The page's H1 (the first titled section; a listing, card, cart or blog list first) is in the first two content sections and is not the subscription."),
    "advantages-unconfirmed": (WARN, "Product-card and cart advantages carry the merchant's own texts: the platform wording promises cash, pickup points and a courier; template slots promise another store's services."),
    "banner-live-text-fit": (WARN, "On a 7:3, 5:2 or 7:1 phone image the live title and subtitle together run about 5 words or fewer."),
    "story-dead-end": (WARN, "A story block on home leads further: a markdown link in its text (a custom story carries a button) (principle 16)."),
    "story-measure": (WARN, "ImageWithText copy over 200 characters sits in a narrowed section of the theme's box (hPaddingDesktop 1400 or more, not across the screen), about 75 characters a line; a theme box over 1400 px has no padding that narrows it enough: shorter copy or `text-media-split` (principle 16)."),
    "rhythm-monotony": (INFO, "Home varies the recipe: 5 or more kinds of blocks, at least 2 content slots (not the bar, header or footer) off their first option or another theme, one signature block, a named look (principle 6)."),
    "title-align-mixed": (WARN, "Section titles on a page share one alignment: a Faq follows only its own align, the listing H1 is always left (principle 1)."),
    "text-measure": (WARN, "Long text runs 60-80 characters a line: RichText over 400 characters within about 800 px (7 columns of the default 1400 px box, 5 of a 1728-1920 px one), Faq questions within about 910 px (8 columns; 5-6 of a wider box), both in the theme's box rather than across the screen, an article aligned left (principle 25)."),
    "theme-font": (WARN, "The theme's text font is upright and plain: an italic or decorative face is the cabinet's to change (principle 2)."),
    "to-fill": (INFO, "Values still to fill from the store or the merchant."),
}

# A rule whose severity differs on a pulled page (`plan lint --work`): there the section may
# be the store's untouched drift, which renders and which the constructor tolerates.
PULLED_SEVERITY = {
    "listing-filters": WARN,
}
# Rules whose severity depends on the page: the composition rule was calibrated on home.
HOME_ONLY_ERRORS = ("adjacent-shelves",)


@dataclass
class Section:
    index: int
    widget: str
    role: str
    settings: "dict[str, object]"
    preset: "str | None" = None
    template_id: "str | None" = None
    pattern: "str | None" = None
    heading: "str | None" = None
    slot: "str | None" = None
    options: "list[dict[str, object]] | None" = None
    copies: int = 1
    declared_role: bool = False
    # A section the plan keeps from the store (`id` or `"existing": true`): the rules for
    # what a plan adds (`shelf-source-id`, `listing-filters` as an error) treat it as pulled.
    existing: bool = False
    # A custom section's template, as `plan lint --work` reads it from the workspace: the
    # json_schema (which image fields are required) and the alias (the widget it stands for).
    template_schema: "dict[str, object] | None" = None
    template_alias: "str | None" = None


@dataclass
class Context:
    page: str
    page_type: str
    stage: str
    palettes: "list[str] | None"
    pulled: bool = False
    hidden: bool = False
    variant: "str | None" = None
    assigned: "int | None" = None
    assigned_unknown: bool = False
    alias: "str | None" = None
    page_id: "str | None" = None
    meta: "dict[str, object] | None" = None
    theme: "touch.Theme" = field(default_factory=lambda: touch.Theme(None))
    theme_settings: "dict[str, object] | None" = None
    sections: "list[Section]" = field(default_factory=list)
    findings: "list[dict[str, object]]" = field(default_factory=list)

    @property
    def content(self) -> "list[Section]":
        return [s for s in self.sections if s.widget not in CHROME and s.role not in CHROME_ROLES]

    def add(self, rule: str, section: "Section | None", message: str, severity: "str | None" = None) -> None:
        level = severity or RULES[rule][0]
        if severity is None and self.pulled and rule in PULLED_SEVERITY:
            level = PULLED_SEVERITY[rule]
        if severity is None and rule in HOME_ONLY_ERRORS and self.page_type != "home":
            level = WARN
        if level == ERROR and self.hidden:
            # A hidden page renders nothing; its defects block the push that publishes it, not this one.
            level = WARN
            message = f"hidden page, fix before publishing it: {message}"
        self.findings.append(
            {
                "rule": rule,
                "severity": level,
                "position": section.index if section else None,
                "widget": section.widget if section else None,
                "message": message,
            }
        )


def _effective_settings(
    entry: "dict[str, object]", widget: str, theme: "dict[str, object] | None" = None
) -> "tuple[dict[str, object], str | None]":
    """Preset settings (with module_style on given modules) under the plan's own settings,
    fitted to the plan's theme as `presets apply --work` fits them."""
    own = entry.get("settings") if isinstance(entry.get("settings"), dict) else {}
    preset_name = entry.get("preset")
    if not isinstance(preset_name, str) or not preset_name:
        return dict(own), None  # type: ignore[arg-type]
    try:
        _doc, preset = find_preset(widget, preset_name)
    except DesignError:
        return dict(own), preset_name  # type: ignore[arg-type]
    preset_settings = preset.get("settings") or {}
    if widget == PL and forms.stored_version(own) is not None:
        # The plan keeps a section of a known version: the preset lands in the shape that version reads.
        preset_settings, _dropped = forms.listing_to_form(preset_settings, forms.listing_form(own, new_section=False)[0], forms.stored_version(own))  # type: ignore[arg-type]
    merged = deep_merge(preset_settings, own)
    for dotted in preset.get("unset") or []:
        unset_path(merged, str(dotted))  # as `presets apply` removes it from the base
    style = preset.get("module_style")
    if isinstance(style, dict) and isinstance(merged.get("modules"), list):  # type: ignore[union-attr]
        merged["modules"] = [deep_merge(style, m) if isinstance(m, dict) else m for m in merged["modules"]]  # type: ignore[index]
    if widget == FAQ and preset.get("title_follows_theme") and isinstance(merged, dict):
        typeset.fit_faq_to_theme(merged, typeset.theme_title_align(theme))
    if isinstance(merged, dict):
        typeset.fit_measure_to_theme(widget, preset.get("settings") or {}, merged, theme, kept=own)  # type: ignore[arg-type]
    return merged, preset_name  # type: ignore[return-value]


def _infer_role(widget: str, settings: "dict[str, object]", content_index: int) -> str:
    if widget in CHROME:
        return CHROME[widget]
    if content_index == 0 and widget in (BC, MYSTIQUE):
        return "hero"
    if content_index == 0 and widget == FC and get_path(settings, "snippets.columns") == 2 and get_path(settings, "snippets.hGap") == "disabled":
        return "hero"
    return {
        BC: "banner",
        FC: "tiles",
        FP: "shelf",
        ES: "subscribe",
        IWT: "story",
        RT: "text",
        FAQ: "faq",
        CL: "catalog",
        "YandexKit.ProductsListing": "listing",
        "YandexKit.ProductDetails": "product",
        "YandexKit.ShoppingCart": "cart",
        "YandexKit.LoyaltyProgram": "loyalty",
        MYSTIQUE: "custom",
    }.get(widget, "other")


def _theme_settings(value: object) -> "dict[str, object] | None":
    """The plan's theme: a pulled `global_settings` subset, or the recipe's theme preset names."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return None
    merged: "dict[str, object]" = {}
    for name in value:
        try:
            _doc, preset = find_preset(THEME, str(name))
        except DesignError:
            continue
        merged = deep_merge(merged, preset.get("settings") or {})  # type: ignore[assignment]
    return merged or None


def build_context(plan: "dict[str, object]", stage_override: "str | None" = None) -> Context:
    if not isinstance(plan, dict) or not isinstance(plan.get("sections"), list):
        raise DesignError("A plan is a JSON object with a `sections` array (see references/plans.md).")
    page_type = plan.get("page_type") or "other"
    if page_type not in PAGE_TYPES:
        raise DesignError(f"Unknown page_type {page_type!r}. Known: {', '.join(PAGE_TYPES)}.")
    stage = stage_override or plan.get("stage") or "draft"
    if stage not in ("draft", "final"):
        raise DesignError("stage is `draft` or `final`.")
    palettes = plan.get("palettes")
    assigned = plan.get("assigned")
    theme_settings = _theme_settings(plan.get("theme"))
    ctx = Context(
        page=str(plan.get("page") or "plan"),
        page_type=str(page_type),
        stage=str(stage),
        palettes=[p for p in palettes if isinstance(p, str)] if isinstance(palettes, list) else None,
        pulled=plan.get("pulled") is True,
        hidden=plan.get("hidden") is True,
        variant=plan.get("variant_type") if isinstance(plan.get("variant_type"), str) else None,
        assigned=assigned if isinstance(assigned, int) else None,
        assigned_unknown=plan.get("assigned_unknown") is True,
        theme=touch.Theme(theme_settings),
        theme_settings=theme_settings if plan.get("theme") is not None else None,
        alias=plan.get("alias") if isinstance(plan.get("alias"), str) else None,
        page_id=plan.get("page_id") if isinstance(plan.get("page_id"), str) else None,
        meta=plan.get("meta") if isinstance(plan.get("meta"), dict) else None,
    )
    content_index = 0
    for position, entry in enumerate(plan["sections"], start=1):
        options = entry.get("choose") if isinstance(entry, dict) and not entry.get("widget") else None
        if isinstance(options, list) and options and all(isinstance(o, dict) and isinstance(o.get("widget"), str) for o in options):
            # A slot still to choose (`recipes show --plan`): the composition rules read its first option.
            entry = {**{k: v for k, v in entry.items() if k != "choose"}, **{k: options[0][k] for k in ("widget", "preset", "pattern") if k in options[0]}}
            entry["_options"] = options
        if not isinstance(entry, dict) or not isinstance(entry.get("widget"), str):
            raise DesignError(f"sections[{position - 1}] needs a `widget` string (or a `choose` list of options).")
        widget = normalize_widget(entry["widget"])
        settings, preset = _effective_settings(entry, widget, ctx.theme_settings)
        declared = entry.get("role")
        role = declared if isinstance(declared, str) and declared else _infer_role(widget, settings, content_index)
        ctx.sections.append(
            Section(
                index=position,
                widget=widget,
                role=role,
                settings=settings,
                preset=preset,
                template_id=entry.get("template_id") if isinstance(entry.get("template_id"), str) else None,
                pattern=entry.get("pattern") if isinstance(entry.get("pattern"), str) else None,
                heading=entry.get("heading") if entry.get("heading") in ("h1", "title") else None,
                slot=entry.get("slot") if isinstance(entry.get("slot"), str) else None,
                options=entry.get("_options") if isinstance(entry.get("_options"), list) else None,
                copies=entry.get("copies") if isinstance(entry.get("copies"), int) else 1,
                declared_role=isinstance(declared, str) and bool(declared),
                existing=entry.get("existing") is True or (isinstance(entry.get("id"), str) and bool(entry.get("id"))),
                template_schema=entry.get("template_schema") if isinstance(entry.get("template_schema"), dict) else None,
                template_alias=entry.get("template_alias") if isinstance(entry.get("template_alias"), str) else None,
            )
        )
        if widget not in CHROME and role not in CHROME_ROLES:
            content_index += 1
    content = ctx.content
    if len(content) >= 2:
        last, before = content[-1], content[-2]
        # A strip, a legal line or a contact split right after the subscription is the
        # closing band the recipes allow there, not a misplaced section.
        if not last.declared_role and before.widget == ES and last.widget in (BC, RT, IWT, FAQ):
            last.role = "closing"
    return ctx


# --- rules ---------------------------------------------------------------------------


def _check_identity(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget not in KNOWN_WIDGETS:
            ctx.add("unknown-widget", s, f"{s.widget!r} is not a widget the storefront knows.")
        if s.preset:
            try:
                find_preset(s.widget, s.preset)
            except DesignError:
                ctx.add("unknown-preset", s, f"{s.widget} has no preset {s.preset!r} (design.py presets show {s.widget}).")
        if s.declared_role and s.role not in ROLES:
            ctx.add("unknown-role", s, f"Role {s.role!r} is not one of: {', '.join(ROLES)}.")
    for widget in ONE_PER_PAGE:
        found = [s for s in ctx.sections if s.widget == widget]
        if len(found) > 1:
            if widget == HEADER:
                ctx.add("header-twice", found[1], "Two Header sections on this page.")
            else:
                ctx.add("chrome-count", found[1], f"{len(found)} × {widget} on one page; at most one renders.")


HERO_RATIOS = ("2:1", "5:2", "fit-screen")


def _hero_sized(s: Section) -> bool:
    """A banner the size of a hero: a hero preset, or a full-screen, 2:1 or 5:2 desktop ratio."""
    if s.widget != BC:
        return False
    if s.preset and s.preset.startswith(("hero-", "video-hero")):
        return True
    ratios = s.settings.get("aspectRatios")
    desktop = ratios.get("desktop") if isinstance(ratios, dict) else s.settings.get("aspectRatio")
    return desktop in HERO_RATIOS


def _check_hero(ctx: Context) -> None:
    heroes = [s for s in ctx.content if s.role == "hero"]
    if len(heroes) > 1:
        ctx.add("one-hero", heroes[1], f"{len(heroes)} sections play the hero role; keep one.")
    content = ctx.content
    for above, s in zip(content, content[1:]):
        if above.role == "hero" and s.role != "hero" and _hero_sized(s):
            ctx.add(
                "hero-stack", s,
                "A hero-sized banner right under the hero: two first screens in a row push the categories and the products "
                "off the phone's first two screens. Keep one hero; make this a strip (7:2 or thinner) or move it below a shelf.",
            )
    if ctx.page_type == "home" and ctx.content and ctx.content[0].role != "hero":
        ctx.add("hero-first", ctx.content[0], "The first content section is not a hero.")
    for s in heroes:
        modules = s.settings.get("modules")
        if s.widget == BC and isinstance(modules, list) and len(modules) > 3:
            ctx.add("hero-slides", s, f"{len(modules)} slides in the hero.")


def _check_banners(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget != BC:
            continue
        ratios = s.settings.get("aspectRatios")
        if isinstance(ratios, dict):
            desktop, mobile = ratios.get("desktop"), ratios.get("mobile")
            accepted = (BANNER_RATIO_PAIRS.get(desktop),) + BANNER_RATIO_ALTERNATIVES.get(desktop, ())
            if desktop in BANNER_RATIO_PAIRS and mobile not in accepted:
                ctx.add("banner-ratio-pair", s, f"{desktop} on desktop pairs with {' or '.join(accepted)} on mobile, not {mobile}.")
        modules = s.settings.get("modules")
        if ctx.stage == "final" and isinstance(modules, list) and not modules:
            ctx.add("media-missing", s, "The carousel has no slides: it renders nothing.")
        for number, module in enumerate(modules or [], start=1):
            if not isinstance(module, dict):
                continue
            desktop_media = get_path(module, "slideMedia.media")
            mobile_media = get_path(module, "slideMedia.mediaMobile")
            if ctx.stage == "final" and not desktop_media:
                ctx.add("media-missing", s, f"Slide {number} has no image (slideMedia.media).")
            elif desktop_media and not mobile_media:
                ctx.add("banner-mobile-image", s, f"Slide {number} has no mobile image.")
            if module.get("linkType") == "button" and not str(module.get("buttonText") or "").strip():
                ctx.add("button-text", s, f"Slide {number} is a button without buttonText.")
            if module.get("linkType") == "button" and _dark_slide(module) and module.get("buttonColor") in ("secondary", None):
                ctx.add(
                    "banner-button-contrast", s,
                    f"Slide {number}: a secondary button (the schema default) on a darkened slide or under white text reads as grey on grey "
                    "(about 3:1) and looks switched off. Write buttonColor primary.",
                )
            button_text = module.get("buttonText")
            if module.get("linkType") == "banner" and isinstance(button_text, str) and button_text.strip() and not find_markers(button_text):
                ctx.add("button-text-hidden", s, f"Slide {number} links as a whole; its buttonText {button_text.strip()[:30]!r} never shows. Use linkType button or clear it.")
            mobile_ratio = get_path(s.settings, "aspectRatios.mobile")
            words = len(" ".join(str(get_path(module, f"slideText.{k}") or "") for k in ("titleText", "subtitleText")).split())
            if mobile_ratio in ("7:3", "5:2", "7:1") and words > LIVE_TEXT_STRIP_WORDS:
                ctx.add(
                    "banner-live-text-fit", s,
                    f"Slide {number}: {words} words of live title and subtitle on a {mobile_ratio} phone image, about {touch.banner_height(mobile_ratio)} px tall: "
                    "the text runs over the product. Use a 4:3 phone image or drop the subtitle.",
                )
            if _same_media(desktop_media, mobile_media) and not str(get_path(module, "slideText.titleText") or "").strip():
                ctx.add("banner-mobile-same", s, f"Slide {number} uses the desktop file on phones: re-compose it for the mobile ratio.")
            link = get_path(module, "linkSource.link")
            if s.role != "hero" and ctx.stage == "final" and not (isinstance(link, str) and link.strip()):
                ctx.add("banner-links", s, f"Slide {number} of a {s.role} banner links nowhere.")


DARK_SLIDE = 24


def _dark_slide(module: "dict[str, object]") -> bool:
    """A slide whose text sits on a dark ground: darkening of 24 or more, or white text."""
    dark = get_path(module, "appearance.backgroundDark")
    try:
        darkened = int(str(dark)) >= DARK_SLIDE
    except ValueError:
        darkened = False
    color = str(get_path(module, "appearance.textColor") or "").strip().lower()
    return darkened or color in ("#fff", "#ffffff", "white")


def _same_media(a: object, b: object) -> bool:
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    for key in ("fileId", "filePath"):
        value = a.get(key)
        if isinstance(value, str) and value.strip() and not find_markers(value) and value == b.get(key):
            return True
    return False


def _shelf_shape(s: Section) -> "tuple[object, object] | None":
    layout, columns = get_path(s.settings, "snippets.layout"), get_path(s.settings, "snippets.columns")
    return (layout, columns) if layout is not None and columns is not None else None


def _source_key(s: Section) -> "tuple[object, ...] | None":
    """What the storefront fetches a shelf by: `type` + `slug`.

    The live `dataSource.id` is not a per-collection id: one platform-wide value is
    shared by shelves of different collections and categories, so keying on it makes
    «new» and «bestsellers» look like one source. `slugId` and `id` are fallbacks only
    for a source that has no slug.
    """
    source = s.settings.get("dataSource")
    if not isinstance(source, dict):
        return None
    key = (source.get("type"), source.get("slug") or source.get("slugId") or source.get("id"))
    return None if find_markers(key[1]) else key  # an unfilled marker is not a real source yet


# What a new store's template seeds: a placeholder category every new store gets, which the
# merchant's catalog never fills. A shelf or a link into it shows the template's products or none.
SEEDED_SLUGS = frozenset({"kategoriya-1-go-urovnya"})
SEEDED_NAMES = re.compile(r"категория\s*1-?го\s+уровня", re.I)


def _seeded_targets(s: Section) -> "list[str]":
    hits = []
    for path, text in walk_strings(s.settings):
        lowered = text.lower()
        if any(slug in lowered for slug in SEEDED_SLUGS) or SEEDED_NAMES.search(text):
            hits.append(path)
    return hits


def _check_shelves(ctx: Context) -> None:
    content = ctx.content
    for s in ctx.sections:
        seeded = _seeded_targets(s)
        if seeded:
            ctx.add(
                "shelf-seeded-source", s,
                f"{seeded[0]} points at the template's seeded category «Категория 1-го уровня», which the store's catalog does not fill: "
                "take a real category or collection from yandex-kit-cabinet (`list /v1/categories`, `list /v1/collections`).",
            )
    for left, right in zip(content, content[1:]):
        if left.widget != FP or right.widget != FP:
            continue
        a, b = _shelf_shape(left), _shelf_shape(right)
        untitled = right.settings.get("titleText") == ""
        glued = get_path(right.settings, "section.paddingTop") == "none"
        if a is not None and a == b:
            ctx.add("adjacent-shelves", right, f"Same shape as the shelf above ({a[0]}, {a[1]} columns). Put a visual break between them.")
        elif ctx.page_type == "home" and a is not None and b is not None and not (untitled and glued):
            ctx.add("adjacent-shelves-loose", right, "Two shelves in a row. Either separate them or glue them as a bento (second untitled, paddingTop none, other column count).")
    seen: "dict[tuple[object, ...], Section]" = {}
    shelves = [s for s in content if s.widget == FP]
    for s in shelves:
        key = _source_key(s)
        if key is not None:
            if key in seen:
                ctx.add("shelf-source-repeat", s, f"Same source as the shelf at position {seen[key].index}.")
            else:
                seen[key] = s
        source = s.settings.get("dataSource")
        if isinstance(source, dict) and not ctx.pulled and not s.existing and not find_markers(source) and not str(source.get("id") or "").strip():
            ctx.add(
                "shelf-source-id", s,
                "dataSource has no id: for a category or collection it is the cabinet's id (yandex-kit-cabinet `list /v1/categories`, "
                "`list /v1/collections`); for relevant or last_seen it is the type itself.",
            )
        if not isinstance(source, dict):
            if ctx.stage == "final":
                ctx.add("shelf-no-source", s, "No dataSource.")
        elif get_path(s.settings, "dataSource.type") == "relevant" and get_path(s.settings, "snippets.layout") == "feed":
            ctx.add("feed-relevant", s, "feed layout with the relevant source.")
        layout, columns, count = (get_path(s.settings, f"snippets.{k}") for k in ("layout", "columns", "count"))
        if layout == "grid" and isinstance(columns, int) and isinstance(count, int) and columns and count % columns:
            ctx.add("grid-count", s, f"count {count} is not a multiple of {columns} columns.")
    if ctx.page_type == "home" and len(shelves) > HOME_MAX_SHELVES:
        ctx.add("shelf-count", shelves[HOME_MAX_SHELVES], f"{len(shelves)} product shelves on home.")


def _check_navigation(ctx: Context) -> None:
    content = ctx.content
    if ctx.page_type == "home" and content:
        first_two = content[:2]
        if not any(s.widget in (FC, CL) and s.role != "hero" for s in first_two) and not any(s.role == "categories" for s in first_two):
            ctx.add("category-entry", first_two[-1], "No category tiles within the first two content sections.")
    for position, s in enumerate(content):
        if s.widget != FC:
            continue
        navigational = s.role == "categories" or (ctx.page_type == "home" and position <= 1 and s.role != "hero")
        if not navigational:
            continue
        modules = [m for m in s.settings.get("modules") or [] if isinstance(m, dict)]
        dead = [m for m in modules if not str(get_path(m, "linkSource.link") or "").strip()]
        if dead:
            ctx.add("tile-links", s, f"{len(dead)} of {len(modules)} navigation tiles link nowhere.")


def _check_closing(ctx: Context) -> None:
    content = ctx.content
    subscriptions = [i for i, s in enumerate(content) if s.widget == ES]
    if ctx.page_type == "home" and not subscriptions:
        ctx.add("subscribe-missing", content[-1] if content else None, "Home has no EmailSubscription.")
    for i in subscriptions:
        s = content[i]
        if i < 2 and len(content) > 3:
            ctx.add("subscribe-above-fold", s, "Subscription in the first two content sections.")
        tail = content[i + 1:]
        if ctx.page_type == "home" and (len(tail) > 1 or (tail and tail[0].role not in ("closing",))):
            ctx.add("subscribe-last", s, f"{len(tail)} content section(s) after the subscription; only one closing strip may follow it.")
        subtitle = get_path(s.settings, "form.subtitle")
        if isinstance(subtitle, str) and DISCOUNT_PROMISE.search(subtitle) and not get_path(s.settings, "form.promocode.promocodeId"):
            ctx.add("discount-without-promocode", s, "The subtitle promises a discount but no promocode is linked.")
        if get_path(s.settings, "form.warning") == "":
            ctx.add("consent-text", s, "form.warning is empty.")


def _check_length(ctx: Context) -> None:
    if ctx.page_type != "home":
        return
    count = len(ctx.content)
    low, high = HOME_LENGTH
    if count < low or count > high:
        ctx.add("home-length", None, f"{count} content sections on home; the sample's range for good homes is {low}-{high} (median 9).")
    customs = [s for s in ctx.content if s.widget == MYSTIQUE]
    if len(customs) > CUSTOM_MAX:
        ctx.add(
            "custom-share", customs[CUSTOM_MAX],
            f"{len(customs)} custom sections on home: keep shelves, listings, the subscription and category tiles native, custom for the brand's own blocks.",
        )


def _palette_of(s: Section) -> str:
    palette = s.settings.get("palette")
    return palette if isinstance(palette, str) and palette else "default"


def _check_palettes(ctx: Context) -> None:
    for s in ctx.sections:
        palette = s.settings.get("palette")
        if isinstance(palette, str) and palette and not find_markers(palette) and ctx.palettes is not None and palette not in ctx.palettes:
            ctx.add("palette-missing", s, f"Palette {palette!r} is not in the theme ({', '.join(ctx.palettes)}).")
    body = ctx.content
    if len(body) < 4:
        return
    tinted = [s for s in body if _palette_of(s) != "default"]
    if len(tinted) * 2 > len(body):
        ctx.add("palette-churn", tinted[0], f"{len(tinted)} of {len(body)} content sections are off the base palette.")
    changes = sum(1 for a, b in zip(body, body[1:]) if _palette_of(a) != _palette_of(b))
    if changes >= 4 and changes * 2 >= len(body):
        ctx.add("palette-churn", None, f"The palette changes {changes} times down the page; group coloured sections into 1-2 bands.")


def _edge_to_edge(s: Section) -> bool:
    section = s.settings.get("section") if isinstance(s.settings.get("section"), dict) else {}
    ratios = s.settings.get("aspectRatios")
    fit = isinstance(ratios, dict) and ratios.get("desktop") == "fit-screen"
    return (
        section.get("isFullscreen") == "enabled"  # type: ignore[union-attr]
        and section.get("hPaddingDesktop") == "0"  # type: ignore[union-attr]
        and section.get("paddingTop") == "none"  # type: ignore[union-attr]
    ) or (fit and section.get("paddingTop") == "none")  # type: ignore[union-attr]


def _check_header(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget != HEADER or s.settings.get("transparencyMode") not in ("onscroll", "transparent"):
            continue
        first = ctx.content[0] if ctx.content else None
        if first is not None and first.widget != BC:
            ctx.add(
                "transparent-header", s,
                f"The first content section is {first.widget.split('.')[-1]}: the storefront lays the header over the image of a BannersCarousel only; "
                "here it lies on that section's background, the image starts below it, and the bar shows the inverted logo. Write transparencyMode none.",
            )
        elif first is None or not _edge_to_edge(first):
            ctx.add("transparent-header", s, "The first content section is not edge to edge with paddingTop none.")
        if s.copies > 1:
            ctx.add("transparent-composite", s, f"This header is shared by {s.copies} pages; all of them turn transparent.")


def _check_texts(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget == BAR:
            for number, module in enumerate(s.settings.get("modules") or [], start=1):
                if not isinstance(module, dict):
                    continue
                texts = [module.get(key) for key in ("titleText", "bodyText")]
                texts = [text.strip() for text in texts if isinstance(text, str) and text.strip() and not find_markers(text)]
                length = len(" ".join(texts))
                if length > ANNOUNCEMENT_MAX_CHARS:
                    ctx.add("announcement-length", s, f"Module {number} has {length} characters; a phone cuts it at about 33-47.")
            contacts = [get_path(s.settings, f"contacts.{key}") for key in ("primary", "secondary")]
            if not _reported_on_home(ctx, s) and any(isinstance(c, str) and c not in ("", "none") for c in contacts):
                ctx.add("announcement-contacts", s, "Bar contacts are set; phones hide them, and the footer already carries them.")
        if s.widget == FAQ:
            questions: "dict[str, int]" = {}
            for number, module in enumerate(s.settings.get("modules") or [], start=1):
                if isinstance(module, dict) and not (str(module.get("question") or "").strip() and str(module.get("answer") or "").strip()):
                    ctx.add("faq-empty", s, f"Question {number} has an empty question or answer.")
                question = " ".join(str(module.get("question") or "").lower().split()) if isinstance(module, dict) else ""
                if question and not find_markers(question):
                    if question in questions:
                        ctx.add("faq-repeated", s, f"Question {number} repeats question {questions[question]}.")
                    else:
                        questions[question] = number
            expand = get_path(s.settings, "appearance.expand")
            if expand is None:
                ctx.add("faq-expand", s, "appearance.expand is not written: the storefront opens the first answer.")
            elif ctx.page_type == "product" and expand != "none":
                ctx.add("faq-expand", s, f"expand {expand!r} on a product page: an open answer pushes the page down on phones.")
        final = ctx.stage == "final" or ctx.pulled
        if final and s.widget == RT and not str(s.settings.get("content") or "").strip() and not str(s.settings.get("titleText") or "").strip():
            ctx.add("richtext-empty", s, "Neither titleText nor content: an empty spacer. Use paddings instead.")
        if s.widget == IWT:
            content = str(s.settings.get("content") or "")
            if len(content) > IWT_MAX_CHARS:
                ctx.add("long-copy", s, f"{len(content)} characters of copy: on a phone the block runs about 1.5 screens.")
        if s.widget == RT and s.settings.get("align") == "center":
            content = str(s.settings.get("content") or "")
            if len(content) > CENTRED_MAX_CHARS:
                ctx.add("long-copy", s, f"{len(content)} characters of centred text: align longer copy left.")
        if s.widget == IWT and get_path(s.settings, "image.aspectRatio") in ("10:1", "7:1"):
            ctx.add("image-sliver", s, f"Image ratio {get_path(s.settings, 'image.aspectRatio')} in a split.")
    titled_faq = [s for s in ctx.sections if s.widget == FAQ and str(s.settings.get("title") or "").strip()]
    if len(titled_faq) > 1:
        ctx.add("faq-titled-twice", titled_faq[1], f"{len(titled_faq)} titled Faq sections.")
    content = ctx.content
    for left, right in zip(content, content[1:]):
        if left.widget == IWT and right.widget == IWT:
            side_a, side_b = get_path(left.settings, "image.layout"), get_path(right.settings, "image.layout")
            if side_a is not None and side_a == side_b:
                ctx.add("zigzag", right, f"Image on the {side_a} twice in a row.")
    if ctx.page_type == "service" and ctx.stage == "final":
        texts = [s for s in content if s.widget in (RT, FAQ, IWT)]
        filled = any(str(s.settings.get("content") or "").strip() or s.settings.get("modules") for s in texts)
        if not filled:
            ctx.add("service-page-empty", content[0] if content else None, "The service page carries no text of its own.")


def _titled(ctx: Context, s: Section) -> bool:
    """The section shows a title: a non-empty one, or (in a draft) a title key the plan leaves to fill."""
    if s.widget == PL:
        return True
    title = typeset.section_title(s.widget, s.settings)
    if title is None:
        return s.widget in typeset.TITLE_TEXT and not (ctx.pulled or ctx.stage == "final")
    return bool(title)


def _check_title_align(ctx: Context) -> None:
    theme_align = typeset.theme_title_align(ctx.theme_settings)
    placed = []
    for s in ctx.content:
        if not _titled(ctx, s):
            continue
        align = typeset.title_align(s.widget, s.settings, theme_align)
        if align is not None:
            placed.append((s, align))
    if len(placed) < 3:
        return
    counts = {align: sum(1 for _s, a in placed if a == align) for align in ("left", "center")}
    if not all(counts.values()):
        return
    if counts["left"] == counts["center"]:
        odd = theme_align if theme_align in counts else "left"
        odd = "center" if odd == "left" else "left"
    else:
        odd = min(counts, key=counts.get)  # type: ignore[arg-type]
    for s, align in placed:
        if align != odd:
            continue
        why = (
            "a Faq title follows only appearance.align, never the theme" if s.widget == FAQ
            else "the listing's H1 is always on the left" if s.widget == PL
            else "write `default` to follow the theme" if s.widget in typeset.TITLE_ALIGN
            else ""
        )
        ctx.add(
            "title-align-mixed", s,
            f"Title {align}, while {counts['left' if align == 'center' else 'center']} of {len(placed)} titles on this page are "
            f"{'left' if align == 'center' else 'centred'}" + (f": {why}." if why else "."),
        )


SPANS_SCREEN_ADVICE = (
    'the section spans the screen (`section.isFullscreen` enabled, or default under a theme with isFullscreen true): '
    'write `section.isFullscreen: "disabled"` to keep the text in the theme\'s box'
)


def _where(ctx: Context, width: float, full: bool) -> str:
    """Where a line was judged: the window for a spanning section, else the content box (and the theme's box when it is not the default)."""
    if full:
        return f"on a {typeset.DESKTOP_WINDOW} px window"
    box = typeset.theme_box(ctx.theme_settings)
    return f"in a {round(width + 0.5)} px content box" + (f" (the theme's maxWidthDesktop {box})" if box != typeset.THEME_MAX_WIDTH else "")


def _line(ctx: Context, s: Section, columns: int) -> "tuple[float, bool, str]":
    """The desktop line width of a section at `columns` of 12, whether it spans the screen, and where it was judged."""
    width, full = typeset.content_width(s.settings, ctx.theme_settings)
    return width * columns / 12, full, _where(ctx, width, full)


def _narrowing(changes: "dict[str, object] | None", columns_key: str, ctx: Context, max_px: float, extra: str = "") -> str:
    """The advice for a line that runs too long, from what `typeset.fit_line` found the section can write."""
    if changes is None:
        box = typeset.theme_box(ctx.theme_settings)
        return (
            f"no setting of this section brings it within about {round(max_px, -1):.0f} px in the theme's {box} px box; "
            "with the merchant, bring the theme's maxWidthDesktop to 1400 (theme.md)"
        )
    narrow = []
    if "columns" in changes:
        narrow.append(f"{columns_key} {changes['columns']}")
    if "hPaddingDesktop" in changes:
        narrow.append(f'`section.hPaddingDesktop: "{changes["hPaddingDesktop"]}"`')
    if "isFullscreen" in changes:
        return SPANS_SCREEN_ADVICE + extra + (", and " + " and ".join(narrow) if narrow else "")
    return "use " + " and ".join(narrow)


def _check_measure(ctx: Context) -> None:
    theme_align = typeset.theme_title_align(ctx.theme_settings)
    for s in ctx.content:
        if s.widget == RT and s.settings.get("align", "left") == "left":
            content = str(s.settings.get("content") or "")
            columns = s.settings.get("columns", 8)
            if len(content) > typeset.MEASURE_LONG_TEXT and isinstance(columns, int):
                line, full, where = _line(ctx, s, columns)
                if line > typeset.MEASURE_MAX_PX + 1:
                    changes = typeset.fit_line(s.settings, ctx.theme_settings, columns, typeset.MEASURE_COLUMNS, typeset.MEASURE_MAX_PX)
                    advice = _narrowing(changes, "columns", ctx, typeset.MEASURE_MAX_PX)
                    ctx.add(
                        "text-measure", s,
                        f"{len(content)} characters at {columns} columns: a line of about {round(line)} px {where}, where a reading line "
                        f"(60-80 characters) is about {round(typeset.READING_PX, -1):.0f} px: {advice}.",
                    )
        if s.widget == FAQ:
            columns = get_path(s.settings, "appearance.columns")
            if isinstance(columns, int) and get_path(s.settings, "appearance.align") in ("left", "center", None):
                line, full, where = _line(ctx, s, columns)
                if line > typeset.FAQ_MAX_PX + 1:
                    changes = typeset.fit_line(s.settings, ctx.theme_settings, columns, typeset.FAQ_COLUMNS, typeset.FAQ_MAX_PX)
                    advice = _narrowing(changes, "appearance.columns", ctx, typeset.FAQ_MAX_PX, " (every Faq preset does)")
                    ctx.add("text-measure", s, f"Questions across {columns} columns run about {round(line)} px wide {where}: {advice}.")
        if s.widget == typeset.BLOG and theme_align == "center" and s.settings.get("alignContent", "default") == "default":
            ctx.add(
                "text-measure", s,
                "alignContent default follows the centred theme and centres every paragraph of the article: write alignContent left (Blog `article-left`).",
            )


MD_LINK = re.compile(r"\[[^\]]+\]\([^)]+\)|<a\s[^>]*href=", re.I)


def _is_story(ctx: Context, s: Section) -> bool:
    if ctx.page_type != "home":
        return False
    if s.role == "story" and s.widget in (IWT, RT):
        return True
    return s.widget == IWT and not s.declared_role


def _check_story(ctx: Context) -> None:
    for s in ctx.content:
        if not _is_story(ctx, s):
            continue
        content = s.settings.get("content")
        if isinstance(content, str) and content.strip() and not find_markers(content) and not MD_LINK.search(content):
            ctx.add(
                "story-dead-end", s,
                "The story ends without a way on: add a last line with a markdown link to the catalog, a collection or the About page "
                "(or use the kit-custom-sections pattern `text-media-split`, which carries a button).",
            )
        if s.widget == IWT and isinstance(content, str) and len(content) > CENTRED_MAX_CHARS:
            width, full = typeset.content_width(s.settings, ctx.theme_settings)
            column = typeset.story_column(width)
            if column > typeset.STORY_MAX_PX + 1:
                ctx.add(
                    "story-measure", s,
                    f"{len(content)} characters in a half-width column of about {round(column)} px {_where(ctx, width, full)}, where a reading column "
                    f"is about {round(typeset.STORY_READING_PX, -1):.0f} px: {_story_fix(ctx, s, full)}.",
                )


def _story_fix(ctx: Context, s: Section, full: bool) -> str:
    """The advice for a story split whose text column runs too wide, from what `typeset.fit_story` found."""
    changes = typeset.fit_story(s.settings, ctx.theme_settings)
    box = typeset.theme_box(ctx.theme_settings)
    if changes is None:
        # Disabling the spanning helps only where the theme's box is narrower than the window.
        spans = 'write `section.isFullscreen: "disabled"`, but ' if full and box < typeset.DESKTOP_WINDOW else ""
        return (
            f'{spans}even `section.hPaddingDesktop: "{typeset.SECTION_H_PADDINGS[-1]}"` leaves a column of about '
            f"{round(typeset.narrowest_story_column(ctx.theme_settings))} px in the theme's {box} px box: "
            f"keep the copy within {CENTRED_MAX_CHARS} characters, use `text-media-split`, or with the merchant bring the theme's "
            "maxWidthDesktop to 1400 (theme.md)"
        )
    if not changes:
        return "use `text-media-split`"
    padding = changes.get("hPaddingDesktop")
    if "isFullscreen" in changes and padding:
        named = ", as `brand-story-split` writes them" if padding == typeset.STORY_PADDING else ""
        return (
            f'the section spans the screen: write `section.isFullscreen: "disabled"` and `section.hPaddingDesktop: "{padding}"`'
            f"{named}, or use `text-media-split`"
        )
    if "isFullscreen" in changes:
        return 'the section spans the screen: write `section.isFullscreen: "disabled"`, or use `text-media-split`'
    named = ", as `brand-story-split` writes it" if padding == typeset.STORY_PADDING else ""
    return f'narrow the section (`section.hPaddingDesktop: "{padding}"`{named}) or use `text-media-split`'


def _block_kind(s: Section) -> str:
    if s.widget == MYSTIQUE:
        return f"custom:{s.pattern or s.template_id or s.index}"
    return s.widget


def _is_signature(s: Section) -> bool:
    if s.slot == "signature" or (s.declared_role and s.role == "signature"):
        return True
    if s.widget == MYSTIQUE:
        return s.pattern in SIGNATURE_PATTERNS or (s.pattern is None and s.role not in CHROME_ROLES and s.role not in ("product", "listing"))
    return (s.widget, s.preset) in SIGNATURE_PRESETS


def _literal_recipe(ctx: Context, plan: "dict[str, object]") -> bool:
    """The plan stays too close to its recipe: fewer than 2 content slots off their first option,
    with the recipe's own theme. Chrome slots (announcement, header, footer) do not count: a
    different bar or header leaves the page itself as it was."""
    recipe_id = plan.get("recipe")
    if not isinstance(recipe_id, str) or not recipe_id:
        return False
    try:
        recipe = find_recipe(recipe_id)
    except DesignError:
        return False
    if plan.get("theme") != recipe.get("theme"):
        return False
    slots = {
        step["slot"]: step
        for step in recipe.get("steps", [])
        if step.get("variants") and step.get("slot") and step["slot"] not in CHROME_SLOTS
    }
    if any(s.options for s in ctx.sections):
        return False  # still a draft with open choices
    chosen = [s for s in ctx.sections if s.slot in slots]
    if chosen:
        def first(slot: str) -> "tuple[object, object]":
            step = slots[slot]
            return step["widget"], step.get("preset") or step.get("pattern")

        varied = sum(1 for s in chosen if (s.widget, s.preset or s.pattern) != first(str(s.slot)))
        return varied < RHYTHM_MIN_VARIED
    defaults = [(step["widget"], step.get("preset") or step.get("pattern")) for step in recipe.get("steps", []) if not step.get("optional")]
    return [(s.widget, s.preset or s.pattern) for s in ctx.sections] == defaults


def _signature_list() -> str:
    natives: "dict[str, list[str]]" = {}
    for widget, preset in SIGNATURE_PRESETS:
        natives.setdefault(widget.split(".", 1)[-1], []).append(f"`{preset}`")
    return (
        "kit-custom-sections " + ", ".join(f"`{p}`" for p in SIGNATURE_PATTERNS) + ", or natively "
        + ", ".join(f"{widget} {', '.join(names)}" for widget, names in natives.items())
    )


SIGNATURE_ADVICE = (
    "No signature block: one block that only this store has, chosen by the look (an option of the recipe's `signature` slot: "
    + _signature_list() + ")."
)


def _check_rhythm(ctx: Context, plan: "dict[str, object]") -> None:
    if ctx.page_type != "home" or len(ctx.content) < 4:
        return
    kinds = {_block_kind(s) for s in ctx.content}
    if len(kinds) < RHYTHM_MIN_KINDS:
        ctx.add("rhythm-monotony", None, f"{len(kinds)} kinds of blocks on home: the page reads as one list. Vary at least 2 slots (references/looks.md).")
    if _literal_recipe(ctx, plan):
        ctx.add(
            "rhythm-monotony", None,
            f"The plan repeats {plan.get('recipe')}: fewer than {RHYTHM_MIN_VARIED} content slots off their first option (the announcement bar, "
            "header and footer do not count) and the recipe's theme. Vary at least 2 slots by the brief and the look, so the store does not "
            "look like every other store built from it.",
        )
    if not any(_is_signature(s) for s in ctx.content):
        ctx.add("rhythm-monotony", None, SIGNATURE_ADVICE)
    look = plan.get("look")
    if not ctx.pulled and (not isinstance(look, str) or not look.strip() or find_markers(look)):
        ctx.add("rhythm-monotony", None, "The plan names no look: pick one in references/looks.md and the 2-3 moves that set the store apart.")


def _check_font(ctx: Context) -> None:
    if ctx.page_type != "home":
        return
    font = typeset.describe_font(ctx.theme_settings)
    if font and font.get("risk"):
        ctx.add("theme-font", None, f"{typeset.font_line(font)}: {font['risk']}. {typeset.FONT_ADVICE}")


def hidden_text_paths(widget: str, settings: "dict[str, object]") -> "set[str]":
    """Text paths the storefront does not render: tile titles under `text.enableTitle: false`."""
    if widget not in (FC, CL) or get_path(settings, "text.enableTitle") is not False:
        return set()
    modules = settings.get("modules")
    return {f"$.modules[{i}].title" for i in range(len(modules))} if isinstance(modules, list) else set()


def _check_publishable(ctx: Context) -> None:
    pending = 0
    for s in ctx.sections:
        for path, text in find_markers(s.settings):
            if ctx.stage == "final":
                ctx.add("placeholder-marker", s, f"{path}: {text[:50]!r}")
            else:
                pending += 1
        hidden = hidden_text_paths(s.widget, s.settings)
        for path, text in find_platform_placeholders(s.settings):
            if path not in hidden:
                ctx.add("platform-placeholder", s, f"{path}: {text!r}")
        for path, media in find_media_objects(s.settings):
            # The footer image is optional and its documented empty value is an empty object.
            if s.widget == FOOTER:
                continue
            if media_is_empty(media) and not find_markers(media):
                if s.widget == MYSTIQUE and optional_media(s.settings, s.template_schema, path):
                    continue  # the template declares the image optional, or the block shows text in its place
                if ctx.stage == "final":
                    # Without the template's schema the lint cannot tell an optional image field; a custom section only warns.
                    level = WARN if s.widget == MYSTIQUE else None
                    ctx.add("media-missing", s, f"{path}: empty or data-URI image.", level)
                else:
                    pending += 1
        # Live data carries these empty strings widely (the cabinet writes them); the
        # constructor accepts them as drift already in the base and refuses a new one,
        # so here they are a warning, and `presets apply` writes 0 for backgroundDark.
        section = s.settings.get("section")
        if isinstance(section, dict):
            for key in ("hPaddingDesktop", "hPaddingMobile"):
                if section.get(key) == "":
                    ctx.add("empty-enum", s, f"section.{key} is an empty string; the schema expects a padding step.")
        for module in s.settings.get("modules") or [] if isinstance(s.settings.get("modules"), list) else []:
            if isinstance(module, dict) and get_path(module, "appearance.backgroundDark") == "":
                ctx.add("empty-enum", s, "appearance.backgroundDark is an empty string; it is a number 0-48.")
        if get_path(s.settings, "text.backgroundDark") == "":
            ctx.add("empty-enum", s, "text.backgroundDark is an empty string; it is a number 0-48.")
        if s.options:
            labels = ", ".join(str(o.get("preset") or o.get("pattern") or o.get("widget")) for o in s.options)
            if ctx.stage == "final":
                ctx.add("placeholder-marker", s, f"Slot {s.slot or s.role} is still a choice ({labels}): write the chosen widget and preset.")
            else:
                ctx.add("to-fill", s, f"Slot {s.slot or s.role}: choose one of {labels} by the brief and the look (references/looks.md).")
            continue
        if s.widget == MYSTIQUE and not s.template_id:
            if ctx.stage == "final":
                ctx.add("custom-without-template", s, "No template_id: nothing renders it.")
            else:
                ctx.add("to-fill", s, f"Custom section to build with kit-custom-sections{' (pattern ' + s.pattern + ')' if s.pattern else ''}.")
    if pending:
        ctx.add("to-fill", None, f"{pending} value(s) still marked <fill> or without media; `plan lint --final` refuses them.")


_PATH_TOKEN = re.compile(r"\.([^.\[]+)|\[(\d+)\]")


def _path_tokens(path: str) -> "list[object]":
    """`$.about.logoImage` → ["about", "logoImage"]; `$.modules[0].image` → ["modules", 0, "image"]."""
    return [int(index) if index else key for key, index in _PATH_TOKEN.findall(path)]


def optional_media(settings: "dict[str, object]", schema: "dict[str, object] | None", path: str) -> bool:
    """An empty image a custom template does not need: its key is not in the parent object's
    `required` in the template's json_schema, or the parent object shows text in its place
    (a sibling `mode` other than image, as a wordmark set as text)."""
    tokens = _path_tokens(path)
    if not tokens:
        return False
    parent: object = settings
    for token in tokens[:-1]:
        parent = parent[token] if isinstance(parent, list) and isinstance(token, int) and token < len(parent) else parent.get(token) if isinstance(parent, dict) else None  # type: ignore[union-attr]
    mode = parent.get("mode") if isinstance(parent, dict) else None
    if isinstance(mode, str) and mode and mode not in ("image", "media", "logo", "photo", "video"):
        return True
    if not isinstance(schema, dict):
        return False
    node: object = schema
    for token in tokens[:-1]:
        if not isinstance(node, dict):
            return False
        node = node.get("items") if isinstance(token, int) else (node.get("properties") or {}).get(token)  # type: ignore[union-attr]
    if not isinstance(node, dict) or not isinstance(node.get("properties"), dict) or tokens[-1] not in node["properties"]:
        return False  # the schema does not show this field: keep the warning
    required = node.get("required")
    return not (isinstance(required, list) and tokens[-1] in required)


def _source_type(s: Section) -> object:
    return get_path(s.settings, "dataSource.type") if s.widget == FP else None


def _check_cart_tail(ctx: Context) -> None:
    """The cart's shelves follow the product page's tail rule: recently viewed is never alone."""
    if ctx.page_type != "cart":
        return
    content = ctx.content
    shelves = [s for s in content if s.widget == FP]
    seen = [s for s in shelves if _source_type(s) == "last_seen"]
    if seen and len(shelves) == 1:
        ctx.add("pdp-tail", seen[0], "Recently viewed is the only shelf: a first visitor's empty cart shows a heading, a button and the footer.")
    for index, s in enumerate(content[:-1]):
        if s.widget == CART and content[index + 1].widget == FP and _source_type(content[index + 1]) == "last_seen" and len(shelves) > 1:
            ctx.add("pdp-tail", content[index + 1], "Recently viewed is the first section under the cart; put the bestsellers or new arrivals shelf there.")


def _linked(s: Section) -> bool:
    """A banner or tile block that leads somewhere (in a draft without modules: assumed)."""
    modules = [m for m in s.settings.get("modules") or [] if isinstance(m, dict)]
    if not modules:
        return s.widget in (BC, FC) and not isinstance(s.settings.get("modules"), list)
    return any(str(get_path(m, "linkSource.link") or "").strip() for m in modules)


DEAD_END_ADVICE = {
    "cart": "A first visitor's empty cart is a heading, a button and the footer: add a bestsellers or new arrivals shelf under the cart, recently viewed last (`cart` recipe).",
    "404": "Nothing on the 404 page leads to products: a banner button to a collection and a bestsellers shelf (`not-found` recipe).",
    "catalog": "The catalog shows categories and no product: add a bestsellers shelf under the tiles (`catalog` recipe); a hero button leads to a collection, not to /catalog.",
    "blog": "The blog page ends without a way to the products: add a shelf under the article list (`blog` recipe) or keep the article's own shelf (`from_news`).",
}


def _check_advantages(ctx: Context) -> None:
    for s in ctx.sections:
        concerns = claims.advantage_concerns(s.widget, s.settings)
        if concerns:
            ctx.add("advantages-unconfirmed", s, "; ".join(concerns) + ".")


def _check_dead_end(ctx: Context) -> None:
    if ctx.page_type not in DEAD_END_ADVICE or not ctx.content:
        return
    if ctx.alias == "blogEntry" or any(s.widget == FP and _source_type(s) != "last_seen" for s in ctx.content):
        return
    if ctx.page_type == "404" and any(s.widget in (BC, FC) and _linked(s) for s in ctx.content):
        return
    ctx.add("page-dead-end", None, DEAD_END_ADVICE[ctx.page_type])


# Pages whose own SEO fields the storefront reads (static pages and custom ones). Cart,
# search, favourites, account and confirmation pages show the store-wide template from the
# cabinet; product and category pages take theirs from the product or the category.
SEO_ALIASES = frozenset(
    {"main", "catalog", "404", "delivery", "payment", "return", "details", "oferta", "privacy", "giftCardQuestions", "loyaltyOffer", "loyaltyLanding", "blog"}
)


def _check_seo(ctx: Context) -> None:
    if ctx.meta is None or ctx.hidden:
        return
    if ctx.alias is not None and ctx.alias not in SEO_ALIASES:
        return
    empty = [key for key in ("seo_title", "seo_description") if not str(ctx.meta.get(key) or "").strip()]
    if not empty:
        return
    target = f"--id {ctx.page_id}" if ctx.page_id else "--id <page id>"
    what = {
        "seo_title": (
            "the title becomes a generic «Товары по выгодным ценам в интернет-магазине …»" if ctx.page_type == "home"
            else "the title becomes a generic «<page title> | Официальный интернет-магазин …»"
        ),
        "seo_description": "the description promises «Быстрая доставка, бонусы, скидки и акции, рассрочка и кешбэк», terms the store may not have",
    }
    flags = " ".join({"seo_title": "--seo-title …", "seo_description": "--seo-description …"}[key] for key in empty)
    ctx.add(
        "seo-meta-empty", None,
        f"{' and '.join(empty)} empty: the storefront fills in its template, so {'; '.join(what[key] for key in empty)}. "
        f"Write {'them' if len(empty) > 1 else 'it'} from the store's facts: `kit.py … page set --work work.json {target} {flags}`.",
    )


# Widgets whose prioritised heading wins the page's H1 over every earlier title (storefront
# `headingPriority: 1`): the listing's category name, the product name, the cart, the blog list.
H1_PRIORITY = frozenset(
    {PL, PD, CL, CART, "YandexKit.Blog", "YandexKit.BlogListing", "YandexKit.LoyaltyProgram", "YandexKit.GiftCard",
     "YandexKit.FavouriteProducts", "YandexKit.ProductReviewsListing", "YandexKit.DiscountLadder"}
)
# Headings the storefront prints whatever the settings say: the system widgets print a fixed
# heading of their own («Отзывы и оценки», «Подарочная карта», the bundle's name).
ALWAYS_TITLED = {
    PL: "(the category name)", PD: "(the product name)", CART: "(the cart heading)", ES: "(the subscription title)",
    "YandexKit.Blog": "(the article title)", "YandexKit.ProductReviewsListing": "(the reviews heading)",
    "YandexKit.GiftCard": "(the gift card heading)", "YandexKit.ProductsBundleConstructor": "(the bundle name)",
    "YandexKit.FavouriteProducts": "(the favourites heading)",
}
# A shelf title links to the shelf's category or collection, and a link never becomes the H1;
# for any other source (relevant, last_seen, new arrivals) the title is plain and can.
LINKED_SHELF_SOURCES = ("category", "collection")


def _heading(s: Section) -> "str | None":
    """The text of the section's own heading as the storefront prints it, or None."""
    if s.widget in ALWAYS_TITLED:
        return ALWAYS_TITLED[s.widget]
    if s.widget == MYSTIQUE:
        return "(a custom section title)" if s.heading == "title" else None
    if s.widget == FP and get_path(s.settings, "dataSource.type") in LINKED_SHELF_SOURCES:
        return None  # the title links to the category or collection and never becomes the H1
    title = typeset.section_title(s.widget, s.settings)
    return title or None


def page_h1(ctx: Context) -> "tuple[Section, str] | None":
    candidates = [(s, text) for s in ctx.content if (text := _heading(s))]
    prioritised = [c for c in candidates if c[0].widget in H1_PRIORITY]
    return prioritised[0] if prioritised else candidates[0] if candidates else None


def _check_h1(ctx: Context) -> None:
    if not (ctx.pulled or ctx.stage == "final") or ctx.page_type == "system" or not ctx.content:
        return
    literal = [s for s in ctx.content if s.heading == "h1"]
    found = page_h1(ctx)
    if found is None and not literal:
        ctx.add("page-h1", None, "The page has no H1: no section prints a title (a banner title never becomes one). Give the first section under the hero a title (RichText `lead-under-hero` or the category tiles' title).")
        return
    if found is None:
        return
    section, text = found
    if literal and literal[0] is not section:
        ctx.add("page-h1", literal[0], f"Two H1: the custom section's own h1 and the storefront's, «{text}» (#{section.index}). Keep one.")
    where = ctx.content.index(section)
    if section.widget == ES:
        ctx.add("page-h1", section, "The page's H1 is the subscription title: give a section above it a title (a BlogListing `title`, a RichText heading).")
    elif where >= 2 and not literal and section.widget in H1_PRIORITY:
        # The widget takes the H1 wherever it stands (storefront `headingPriority`): a title on a
        # section above it only becomes an h2, so «give the section under the hero a title» would not help.
        name = section.widget.split(".", 1)[-1]
        ctx.add(
            "page-h1", section,
            f"The page's H1 is «{text[:60]}» (#{section.index}): {name} takes it wherever it stands, and at content position {where + 1} "
            f"the page's main content starts low while the titles above it print as h2. Move the {name} into the first two content sections "
            "(at most a banner or subcategory chips above it); a title on a section above it does not take the H1.",
        )
    elif where >= 2 and not literal:
        ctx.add(
            "page-h1", section,
            f"The page's H1 is «{text[:60]}» (#{section.index}), from the middle of the page: give the first section under the hero a title that says what the page sells.",
        )


def _check_product_page(ctx: Context) -> None:
    """The product card is there; tail rules: the first shelf is never empty on a first visit."""
    if ctx.page_type != "product":
        return
    if not any(s.widget == PD or (s.widget == MYSTIQUE and s.role == "product") for s in ctx.sections):
        message = (
            "No product card: visitors get no photos, price or buy button. Add YandexKit.ProductDetails "
            "(`kit.py … section add --page-id <page id>`; `page add` copies only the header and the footer)."
        )
        # A pulled custom section may render the product through a template without the
        # ProductDetails alias; the lint cannot tell, so there it asks instead of blocking.
        unknown = [s for s in ctx.content if s.widget == MYSTIQUE and s.template_id and s.role not in ("product", "listing")]
        if ctx.pulled and unknown:
            ctx.add("product-card-missing", unknown[0], message + " If this custom section renders the product, check it on the preview.", WARN)
        else:
            ctx.add("product-card-missing", None, message)
    content = ctx.content
    shelves = [s for s in content if s.widget == FP]
    seen = [s for s in shelves if _source_type(s) == "last_seen"]
    if seen and len(shelves) == 1:
        ctx.add("pdp-tail", seen[0], "Recently viewed is the only shelf: a first visitor sees nothing, the next one the product itself.")
    for index, s in enumerate(content[:-1]):
        if s.widget == PD and content[index + 1].widget == FP and _source_type(content[index + 1]) == "last_seen" and len(shelves) > 1:
            ctx.add("pdp-tail", content[index + 1], "Recently viewed is the first section under the product card; put the similar-products shelf there.")


def _check_shelf_buttons(ctx: Context) -> None:
    """Card buttons on some shelves of a page and not on others read as two different stores."""
    first: "dict[bool, Section]" = {}
    for s in ctx.content:
        if s.widget != FP:
            continue
        value = get_path(s.settings, "snippet.enableButton") or "default"
        if value in ("enabled", "disabled"):
            shown = value == "enabled"
        elif ctx.theme.shelf_buttons_known:
            shown = ctx.theme.shelf_buttons
        else:
            continue  # follows a theme card the plan does not show
        first.setdefault(shown, s)
    if len(first) == 2:
        later = max(first.values(), key=lambda s: s.index)
        earlier = min(first.values(), key=lambda s: s.index)
        ctx.add(
            "shelf-buttons", later,
            f"Card buttons differ from the shelf at position {earlier.index}: write enableButton default on every shelf and let the theme card decide.",
        )


def _is_heading(s: Section) -> bool:
    if s.widget == RT:
        return True
    ratios = s.settings.get("aspectRatios")
    desktop = ratios.get("desktop") if isinstance(ratios, dict) else s.settings.get("aspectRatio")
    return s.widget == BC and desktop in ("10:1", "7:1")


def _check_auto_shelves(ctx: Context) -> None:
    content = ctx.content
    for above, s in zip(content, content[1:]):
        if _source_type(s) in AUTO_SOURCES and _is_heading(above):
            ctx.add(
                "shelf-heading-orphan", above,
                f"A heading section above a {_source_type(s)} shelf: put the title in the shelf's titleText, which hides with the shelf when it is empty.",
            )


LISTING_KEYS = {forms.FILTERS: "filters.position", forms.ROOT: "filtersPosition"}


def _listing_forms(ctx: Context, s: Section) -> "tuple[str, ...]":
    """The filter-position shapes the storefront reads for this listing (see designlib.forms)."""
    if forms.stored_version(s.settings) is not None or ctx.pulled:
        return (forms.listing_form(s.settings, new_section=False)[0],)
    if s.existing:
        return (forms.FILTERS, forms.ROOT)  # a kept section of a version the plan does not show
    return (forms.FILTERS,)  # `section add` stamps the storefront's current version


def _check_listing(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget != PL or not (ctx.pulled or ctx.stage == "final" or s.settings):
            continue
        readable = _listing_forms(ctx, s)
        if len(readable) == 1:
            read_key = LISTING_KEYS[readable[0]]
            read_value = get_path(s.settings, read_key)
            ignored = [k for f, k in LISTING_KEYS.items() if f != readable[0] and get_path(s.settings, k) not in (None, "", read_value)]
            if str(read_value or "").strip() and ignored:
                version = forms.stored_version(s.settings)
                ctx.add(
                    "listing-filters", s,
                    f"`{ignored[0]}` is {get_path(s.settings, ignored[0])!r}, but "
                    + (f"settings version {version}" if version is not None else "this section")
                    + f" reads `{read_key}` ({read_value!r}): the storefront ignores the {get_path(s.settings, ignored[0])!r} written."
                    f" Write it into `{read_key}` (`presets apply` over the pulled section does).",
                    PULLED_SEVERITY["listing-filters"] if s.existing and not ctx.pulled else None,
                )
                continue
        if any(str(get_path(s.settings, LISTING_KEYS[form]) or "").strip() for form in readable):
            if (
                (ctx.pulled or s.existing)
                and s.preset is None
                and readable == (forms.FILTERS,)
                and forms.stored_version(s.settings) is not None
                and get_path(s.settings, "filters.position") == "inline"
            ):
                # Current settings always carry the position (the schema requires it): `inline` is its
                # default, which the storefront's migration fills in, so it does not show a choice.
                ctx.add(
                    "listing-filters", s,
                    "`filters.position` is `inline`, the default every current listing carries: the inline sidebar may be"
                    " nobody's choice. Keep it only for the marketplace look; otherwise write drawer (`listing-drawer-4x6`).",
                    INFO,
                )
            continue
        key = LISTING_KEYS[readable[0]]
        other = [k for f, k in LISTING_KEYS.items() if f not in readable and get_path(s.settings, k) is not None]
        version = forms.stored_version(s.settings)
        why = f"settings version {version} reads `{key}`" if version is not None else f"the storefront reads `{key}` here"
        message = (
            f"`{other[0]}` is written but {why}, so the storefront draws the inline sidebar."
            if other
            else f"The filter position (`{key}`) is not written: the storefront draws the inline sidebar."
        )
        ctx.add(
            "listing-filters", s,
            message + " Write drawer (the usual choice) or inline on purpose.",
            PULLED_SEVERITY["listing-filters"] if s.existing and not ctx.pulled else None,
        )


def _header_main_menu(settings: "dict[str, object]") -> object:
    menu = get_path(settings, "modules.main.menu")
    return menu.get("menu") if isinstance(menu, dict) else menu


def _header_menu_variant(settings: "dict[str, object]") -> object:
    """How the desktop header shows its main menu (`icon` is the burger): flat or nested settings."""
    menu = get_path(settings, "modules.main.menu")
    return menu.get("menuVariant") if isinstance(menu, dict) else get_path(settings, "modules.main.menuVariant")


def _header_menu_unread(settings: "dict[str, object]") -> "int | None":
    """The stored version when a flat `modules.main.menu` sits in a header the storefront no
    longer regroups: it reads `modules.main.menu.menu` there and finds no menu."""
    version = forms.stored_version(settings)
    menu = get_path(settings, "modules.main.menu")
    if version is not None and version > forms.HEADER_FLAT_UNTIL and isinstance(menu, str) and menu.strip():
        return version
    return None


def _check_chrome(ctx: Context) -> None:
    for s in ctx.sections:
        if s.widget == HEADER and _header_menu_variant(s.settings) == "icon" and not _reported_on_home(ctx, s):
            ctx.add(
                "header-burger", s,
                "menuVariant icon: on desktop the categories hide behind the burger. With a flat menu of up to 6-8 short items use "
                "`centered-logo-links` (or `left-logo-inline-links`); keep the burger for a nested or long menu.",
            )
        if s.widget == HEADER and _header_main_menu(s.settings) == "":
            ctx.add("header-menu-empty", s, "The main menu id is empty: phones show no burger. Keep the menu the cabinet assigns.")
        elif s.widget == HEADER and s.preset is None and _header_menu_unread(s.settings) is not None:
            # With a preset named, `presets apply` regroups the stored keys (apply.resolve_header_layout).
            ctx.add(
                "header-menu-empty", s,
                f"The main menu is stored flat (`modules.main.menu`) at settings version {_header_menu_unread(s.settings)},"
                " which the storefront no longer regroups: it reads `modules.main.menu.menu`, finds no menu, and phones"
                " show no burger. Apply a Header preset over the pulled section: `presets apply` regroups the stored keys"
                " and keeps the menu id.",
            )
        if s.widget == FOOTER and not _reported_on_home(ctx, s):
            modules = [m for m in s.settings.get("modules") or [] if isinstance(m, dict)]
            untitled = [m for m in modules if str(m.get("menu") or "").strip() and not str(m.get("menuTitle") or "").strip()]
            if untitled:
                ctx.add("footer-untitled", s, f"{len(untitled)} of {len(modules)} menu columns have no menuTitle: on phones they print as flat lists instead of accordions.")


def _check_variant(ctx: Context) -> None:
    if ctx.variant in (None, "default") or ctx.page_type not in ("product", "category"):
        return
    what = "products" if ctx.page_type == "product" else "categories or collections"
    if ctx.assigned == 0:
        ctx.add("variant-dead", None, f"No {what} are assigned to this alternative page: nobody sees it. Assign them in the cabinet or drop the page.")
    elif ctx.assigned is None and ctx.assigned_unknown:
        ctx.add(
            "variant-dead", None,
            f"The workspace does not show which {what} this alternative page serves (a page made with `page add` carries no counts): "
            "assign them in the cabinet, then pull the workspace again to check.",
        )


# The widget that makes a system page work; without it the page shows the header and the footer only.
SYSTEM_WIDGETS = {
    "cart": CART,
    "catalog": CL,
    "search": PL,
    "blog": "YandexKit.BlogListing",
    "blogEntry": "YandexKit.Blog",
    "favouriteProducts": "YandexKit.FavouriteProducts",
    "giftCard": "YandexKit.GiftCard",
    "discountLadder": "YandexKit.DiscountLadder",
    "productReviews": "YandexKit.ProductReviewsListing",
    "productBundle": "YandexKit.ProductsBundleConstructor",
    "unsubscribe": "YandexKit.EmailUnsubscribe",
    "subscription": ES,
    "cartPreview": "YandexKit.PreviewShoppingCart",
    "accountLoyalty": "YandexKit.AccountLoyalty",
    "accountNewsletters": "YandexKit.AccountNewsletters",
    "accountOrderDetails": "YandexKit.AccountOrderDetails",
    "accountOrders": "YandexKit.AccountOrdersListing",
    "accountProfile": "YandexKit.AccountProfile",
    "accountRefunds": "YandexKit.AccountRefunds",
}


def _check_page_body(ctx: Context) -> None:
    """A published page shows something besides the header and the footer; a system page carries its widget."""
    if not (ctx.pulled or ctx.stage == "final"):
        return
    expected = SYSTEM_WIDGETS.get(ctx.alias or "")
    if expected:
        present = any(s.widget == expected or s.template_alias == expected for s in ctx.sections)
        if not present:
            ctx.add(
                "system-widget-missing", None,
                f"The page has no {expected.split('.')[-1]}: the storefront shows the header and the footer and nothing that makes "
                f"this page work. Add it with `kit.py … section add --page {ctx.alias} --widget {expected}`.",
            )
        return
    if not ctx.content and ctx.page_type not in ("product", "system"):
        ctx.add(
            "page-empty", None,
            "No content section: the published page is a header over a footer. Fill it, or hide it and take it out of the menus.",
        )


def _reported_on_home(ctx: Context, s: Section) -> bool:
    """A store-wide section on a pulled page other than home: its advice is reported on home."""
    return ctx.pulled and ctx.page_type != "home" and s.copies > 1 and s.widget in (HEADER, FOOTER, BAR, ES)


def _check_touch(ctx: Context) -> None:
    content = ctx.content
    for s in ctx.sections:
        layout = get_path(s.settings, "snippets.layout") or "grid"
        two_up = get_path(s.settings, "snippets.enableTwoColumnsOnTouch")  # missing = the schema default, true
        columns = get_path(s.settings, "snippets.columns")
        if s.widget == FC:
            modules = [m for m in s.settings.get("modules") or [] if isinstance(m, dict)]
            if layout == "grid" and two_up is False and len(modules) >= 3:
                ctx.add("touch-stack", s, f"{len(modules)} tiles stack one per row on phones; set enableTwoColumnsOnTouch true, or make it a carousel.")
            if layout == "carousel" and columns == 2 and two_up is False:
                ctx.add("touch-sliver", s, "A 2-column carousel shows one tile per phone view and the next as a sliver; use a grid of 2 that stacks.")
            if layout == "grid" and two_up is not False and len(modules) > 1 and len(modules) % 2:
                ctx.add("touch-odd", s, f"{len(modules)} tiles at 2 per phone row leave the last one alone.")
        if s.widget == FB:
            fb_layout = get_path(s.settings, "grid.layout") or "grid"
            fb_two_up = get_path(s.settings, "grid.enableTwoColumnsOnTouch")
            fb_count, fb_columns = get_path(s.settings, "grid.count"), get_path(s.settings, "grid.columns")
            if fb_layout == "grid" and isinstance(fb_count, int) and isinstance(fb_columns, int) and fb_columns and fb_count % fb_columns:
                ctx.add("grid-count", s, f"count {fb_count} is not a multiple of {fb_columns} columns.")
            if fb_layout == "grid" and fb_two_up is not False and isinstance(fb_count, int) and fb_count > 1 and fb_count % 2:
                ctx.add("touch-odd", s, f"{fb_count} posts at 2 per phone row leave the last one alone: a carousel, or an even count.")
        if s.widget == CL:
            cl_modules = [m for m in s.settings.get("modules") or [] if isinstance(m, dict)]
            if two_up is not False and len(cl_modules) > 1 and len(cl_modules) % 2:
                ctx.add("touch-odd", s, f"{len(cl_modules)} tiles at 2 per phone row leave the last one alone.")
            elif two_up is not False and not cl_modules and s.settings.get("source") == "categories":
                ctx.add("touch-odd", s, "Tiles come from the store's categories at 2 per phone row: with an odd number of categories the last one stands alone. Check the count.", INFO)
        if s.widget == FP:
            count = get_path(s.settings, "snippets.count")
            statement_pair = layout == "grid" and columns == 2 and count == 2
            if two_up is False and not statement_pair:
                ctx.add("shelf-touch", s, "One card per phone row: set enableTwoColumnsOnTouch true.")
            rows = touch.touch_rows(s.settings)
            if ctx.page_type == "home" and rows is not None and rows > HOME_MAX_GRID_ROWS:
                ctx.add("shelf-touch", s, f"The grid takes {rows} phone rows; use count 4 or 6, or a carousel.")
            if layout == "grid" and two_up is not False and isinstance(count, int) and count > 1 and count % 2:
                ctx.add("touch-odd", s, f"count {count} at 2 per phone row leaves the last card alone.")
        if s.widget in TEXT_INSET_WIDGETS and not _reported_on_home(ctx, s):
            inset = get_path(s.settings, "section.hPaddingMobile")
            try:
                value = int(str(inset))
            except ValueError:
                value = None
            if value is not None and value < TEXT_MIN_INSET:
                ctx.add("touch-inset", s, f"hPaddingMobile {inset}: text runs about {value * 8 // 100} px from the phone edge; 150-200 is usual.")
    if ctx.page_type == "home" and content:
        heights = [(s, touch.screens(_height(s, ctx.theme))) for s in ctx.sections]
        total = sum(h for _s, h in heights)
        if total > HOME_MAX_SCREENS:
            tallest = sorted(((h, s) for s, h in heights if s.widget not in CHROME and s.role != "hero"), key=lambda x: -x[0])[:2]
            named = ", ".join(f"#{s.index} {s.widget.split('.')[-1]} about {h:.1f}" for h, s in tallest)
            assumed = _touch_assumptions(ctx)
            ctx.add(
                "touch-length", None,
                f"About {total:.0f} phone screens of 390 × 664 px (estimate; the sample's median is 9). Tallest: {named}."
                + (f" Assumed: {'; '.join(assumed)}." if assumed else ""),
            )


def _height(s: Section, theme: "touch.Theme") -> float:
    """Phone height of a section; a custom header or footer is estimated as the native one."""
    if s.widget == MYSTIQUE and s.role in ("header", "footer"):
        return touch.section_height(HEADER if s.role == "header" else FOOTER, {}, theme)
    return touch.section_height(s.widget, s.settings, theme, s.pattern)


def _touch_assumptions(ctx: Context) -> "list[str]":
    found = []
    for s in ctx.sections:
        if s.widget == MYSTIQUE and s.role in ("header", "footer"):
            continue
        note = touch.assumption(s.widget, s.settings, s.pattern)
        if note:
            found.append(f"#{s.index} {note}")
    return found


CHECKS = (
    _check_identity,
    _check_hero,
    _check_banners,
    _check_shelves,
    _check_navigation,
    _check_closing,
    _check_length,
    _check_palettes,
    _check_header,
    _check_texts,
    _check_product_page,
    _check_shelf_buttons,
    _check_auto_shelves,
    _check_listing,
    _check_chrome,
    _check_variant,
    _check_page_body,
    _check_touch,
    _check_title_align,
    _check_measure,
    _check_font,
    _check_story,
    _check_cart_tail,
    _check_advantages,
    _check_dead_end,
    _check_seo,
    _check_h1,
    _check_publishable,
)


def _mark_declared(ctx: Context, recipe_id: object) -> None:
    """A warning the plan's recipe breaks on purpose says so, with the recipe's reason."""
    if not isinstance(recipe_id, str) or not recipe_id:
        return
    try:
        declared = find_recipe(recipe_id).get("deviations") or {}
    except DesignError:
        return
    for finding in ctx.findings:
        reason = declared.get(finding["rule"]) if isinstance(declared, dict) else None  # type: ignore[union-attr]
        if finding["severity"] == WARN and isinstance(reason, str):
            finding["message"] = f"{finding['message']} Declared deviation of {recipe_id}: {reason}"
            finding["declared"] = True


def _h1_report(ctx: Context) -> "dict[str, object] | None":
    if not (ctx.pulled or ctx.stage == "final"):
        return None
    found = page_h1(ctx)
    return {"position": found[0].index, "widget": found[0].widget, "text": found[1]} if found else None


def lint_plan(plan: "dict[str, object]", stage_override: "str | None" = None) -> "dict[str, object]":
    ctx = build_context(plan, stage_override)
    for check in CHECKS:
        check(ctx)
    _check_rhythm(ctx, plan)
    _mark_declared(ctx, plan.get("recipe"))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    ctx.findings.sort(key=lambda f: (order[f["severity"]], f["position"] or 0))  # type: ignore[index]
    height = sum(_height(s, ctx.theme) for s in ctx.sections)
    return {
        "page": ctx.page,
        "page_type": ctx.page_type,
        "stage": ctx.stage,
        "sections": len(ctx.sections),
        "content_sections": len(ctx.content),
        # A draft names widgets without their modules, so its height means little.
        "touch_screens": round(touch.screens(height), 1) if ctx.stage == "final" else None,
        "touch_assumes": _touch_assumptions(ctx) if ctx.stage == "final" else [],
        "font": typeset.font_line(typeset.describe_font(ctx.theme_settings)) if ctx.pulled else None,
        "h1": _h1_report(ctx),
        "errors": sum(1 for f in ctx.findings if f["severity"] == ERROR),
        "warnings": sum(1 for f in ctx.findings if f["severity"] == WARN),
        "findings": ctx.findings,
    }
