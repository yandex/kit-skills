"""Page-level catalog, derivation rules, and local validation for the write workspace.

A storefront page has five dimensions. Only three are chosen by a human:

* ``title``   — free text;
* ``pattern`` — the URL route; free for custom pages, fixed («canonical») for aliased ones;
* ``status``  — ``published`` / ``hidden``; ``hidden`` is honored only for a fixed
  subset of aliases (custom pages without an alias are in that subset).

The other two are derived and must not be invented by a client:

* ``exact``        — whether the pattern is static, i.e. carries no ``:param`` segments;
* ``variant_type`` — ``default`` / ``alternative``: the server recomputes it on write
  (an existing page keeps its stored value; a new page gets ``default`` unless another
  default already exists for its alias). Several pages per alias exist only for
  ``productCard`` and ``categoryAndCollection`` — those alternatives are the «page
  templates» a merchant assigns to individual products and collections in the B2B
  cabinet; the assignment itself is a separate entity and is not part of the
  constructor content.

The rules come in five kinds, and each is labelled where it is checked:

* **Server refusals, mirrored** — the write is answered with 400 and nothing is stored:
  an unknown alias, an empty or malformed custom address, a `~` route on a custom page,
  two pages on one pattern, two default pages per alias, `hidden` on an alias that may
  not be hidden, SEO meta on a dynamic pattern, the removal of a default page with an
  alias. Checking them here turns a bare 400 into an actionable local refusal.
* **Rules that differ between backend versions** — the pattern of an aliased page. An
  older backend refuses the write unless the pattern equals its own table entry; a newer
  one writes its own canonical pattern whatever is sent. Both accept the pattern as it
  was read, so the client sends it unchanged and refuses edits to it. A new aliased
  page takes the canonical pattern of the table below.
* **Server rewrites, refused** — a custom page on a whole canonical pattern: the server
  looks the pattern up in its table and makes the page that system page. When the store
  has none yet (``/account/refunds``: the refunds page is not created with a store), that
  happens silently; when it already has one (``/cart``: every store is created with it),
  the write is answered with 400 (two pages on one pattern). Refused here in both cases.
* **Client policy, stricter than the server** — a custom page on an address a system
  page of this store already serves (a ``~`` part such as ``/account``, or a ``:param``
  route such as ``/catalog/:categorySlug``): the server stores it, but the storefront
  then opens either page at that address. Also the derivation checks on
  ``variant_type`` and ``exact`` and the aliases ``page add`` does not create.
* **Warnings** — a stored custom address the newer server rule refuses, SEO meta on a
  page whose meta the storefront never reads, a custom address that a system page the
  store does not have yet would also serve, and a new system page over a stored custom
  address.

What cannot be checked locally — a pattern colliding with an SEO redirect — stays a
server-side 400 and is documented as such.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Callable  # noqa: F401  (named in a string annotation)

from kitlib.common import UsageError, validated_pages

PATTERN_DELIMITER = "~"

# Canonical route pattern per alias — a copy of the current server table. A newer server
# writes this pattern for an aliased page whatever the write carries; an older one
# refuses any pattern but its own entry, which may be older than this one
# (`accountProfile` was `/account/profile` there). So the client takes it only for a new
# page and sends a stored page's pattern as it was read. A pattern may join several
# routes with `~`; the page serves each of them. A page without an alias must not take
# one of these patterns whole — the server assigns it the alias, silently when the store
# lacks that system page and with 400 when it has one — nor any single route of a system
# page the store has (see `system_route_owner`).
ALIAS_PATTERNS = {
    "404": "/not-found",
    "accountLoyalty": "/account/loyalty-program",
    "accountNewsletters": "/account/newsletters",
    "accountOrderDetails": "/account/orders/:orderId",
    "accountOrders": "/account/orders",
    "accountProfile": "/account/profile~/account",
    "accountRefunds": "/account/refunds",
    "blog": "/blog",
    "blogEntry": "/blog/:blogEntrySlug",
    "cart": "/cart",
    "cartPreview": "/cart-preview",
    "catalog": "/catalog",
    "categoryAndCollection": "/catalog/:categorySlug~/collections/:collectionSlug",
    "delivery": "/delivery",
    "details": "/details",
    "discountLadder": "/promo/progressive-discount",
    "emailConfirmationFailed": "/email-confirmation-failed",
    "emailConfirmed": "/email-confirmed",
    "favouriteProducts": "/profile/favourites",
    "giftCard": "/gift-card",
    "giftCardQuestions": "/gift-card/questions",
    "loyaltyLanding": "/loyalty-program",
    "loyaltyOffer": "/loyalty-program-terms",
    "main": "/",
    "oferta": "/oferta",
    "payment": "/payment",
    "privacy": "/privacy",
    "productBundle": "/promo/bundles/:bundleSlug",
    "productCard": "/products/:productCardSlug",
    "productReviews": "/products/:productCardSlug/reviews",
    "return": "/return",
    "search": "/search",
    "subscription": "/subscribe",
    "unsubscribe": "/unsubscribe",
}
# The server looks a custom page's pattern up here as a whole string, `~` included.
ALIAS_BY_PATTERN = {pattern: alias for alias, pattern in ALIAS_PATTERNS.items()}

# Only these aliases may have several pages (one default + alternatives).
MULTI_VARIANT_ALIASES = {"categoryAndCollection", "productCard"}

# How the storefront turns one route into a matcher: the literal text, with every
# `:param` matching letters, digits, `_`, `-` and `%`; the category and collection slugs
# may also contain `/`. It tries the pages in no fixed order and opens the first match,
# so two pages matching one address take turns.
_ROUTE_PARAM = re.compile(r":[0-9a-zA-Z_-]+")
_SLASH_ROUTE_PARAMS = {":categorySlug", ":collectionSlug"}


def _route_matcher(route: str) -> "re.Pattern[str]":
    parts: "list[str]" = []
    position = 0
    for match in _ROUTE_PARAM.finditer(route):
        parts.append(re.escape(route[position : match.start()]))
        parts.append("[0-9a-zA-Z_\\-%/]+" if match.group() in _SLASH_ROUTE_PARAMS else "[0-9a-zA-Z_\\-%]+")
        position = match.end()
    parts.append(re.escape(route[position:]))
    return re.compile("".join(parts))


# Every single route a system page serves: (alias, route, matcher).
SYSTEM_ROUTES = [
    (alias, route, _route_matcher(route))
    for alias, pattern in sorted(ALIAS_PATTERNS.items())
    for route in pattern.split(PATTERN_DELIMITER)
]

# Known aliases `page add` does not create. Nothing on the platform adds these pages to
# a store on its own, and the widget their content needs is not in the storefront's
# widget schema (`schema widgets`), so a page made here could only be empty. A store
# that has one is read, validated and edited like any other page.
ALIASES_NOT_ADDABLE = {
    "emailConfirmationFailed": "YandexKit.EmailConfirmationFailed",
    "emailConfirmed": "YandexKit.EmailConfirmed",
}

# The content widget(s) of each system page with the display_sequence the server gives
# them when it creates the page itself — a copy of its default page table. Header (1) and
# footer (9999) are copied from the store separately. Without its content widget a
# system page renders only the header and footer: an account refunds page with no
# refunds list, a product template with no product on it. RichText pages get the page
# title as their heading, as the server does; the loyalty landing's own default also
# carries a demo banner and demo text, which are not reproduced.
_RICH_TEXT = (("YandexKit.RichText", 3),)
CONTENT_SECTIONS_BY_ALIAS = {
    "404": (),
    "accountLoyalty": (("YandexKit.AccountLoyalty", 3),),
    "accountNewsletters": (("YandexKit.AccountNewsletters", 3),),
    "accountOrderDetails": (("YandexKit.AccountOrderDetails", 3),),
    "accountOrders": (("YandexKit.AccountOrdersListing", 3),),
    "accountProfile": (("YandexKit.AccountProfile", 3),),
    "accountRefunds": (("YandexKit.AccountRefunds", 3),),
    "blog": (("YandexKit.BlogListing", 2),),
    "blogEntry": (("YandexKit.Blog", 2),),
    "cart": (("YandexKit.ShoppingCart", 3),),
    "cartPreview": (("YandexKit.PreviewShoppingCart", 1),),
    "catalog": (("YandexKit.CollectionsListing", 3),),
    "categoryAndCollection": (("YandexKit.ProductsListing", 3),),
    "delivery": _RICH_TEXT,
    "details": _RICH_TEXT,
    "discountLadder": (("YandexKit.DiscountLadder", 3),),
    "emailConfirmationFailed": (("YandexKit.EmailConfirmationFailed", 1),),
    "emailConfirmed": (("YandexKit.EmailConfirmed", 1),),
    "favouriteProducts": (("YandexKit.FavouriteProducts", 2),),
    "giftCard": (("YandexKit.GiftCard", 3),),
    "giftCardQuestions": _RICH_TEXT,
    "loyaltyLanding": _RICH_TEXT,
    "loyaltyOffer": _RICH_TEXT,
    "main": (),
    "oferta": _RICH_TEXT,
    "payment": _RICH_TEXT,
    "privacy": _RICH_TEXT,
    "productBundle": (("YandexKit.ProductsBundleConstructor", 3),),
    "productCard": (("YandexKit.ProductDetails", 2),),
    "productReviews": (("YandexKit.ProductReviewsListing", 2),),
    "return": _RICH_TEXT,
    "search": (("YandexKit.ProductsListing", 3),),
    "subscription": (("YandexKit.EmailSubscription", 3),),
    "unsubscribe": (("YandexKit.EmailUnsubscribe", 1),),
}

# The storefront ignores a page's own SEO title, H1 and description for these aliases
# and renders the store-wide template from the SEO settings instead. The server stores
# the meta all the same, so a write that sets it changes nothing visible.
ALIASES_IGNORING_PAGE_META = {
    "accountNewsletters",
    "accountOrders",
    "accountProfile",
    "accountRefunds",
    "cart",
    "cartPreview",
    "emailConfirmationFailed",
    "emailConfirmed",
    "favouriteProducts",
    "search",
    "unsubscribe",
}

# `hidden` is accepted only for these aliases; "" stands for a custom page without an
# alias. For any other alias the server refuses the whole write with 400.
HIDDEN_ALLOWED_ALIASES = {
    "",
    "accountLoyalty",
    "blog",
    "blogEntry",
    "catalog",
    "discountLadder",
    "favouriteProducts",
    "giftCard",
    "giftCardQuestions",
    "loyaltyLanding",
    "loyaltyOffer",
    "subscription",
}

PAGE_STATUSES = ("published", "hidden")
META_FIELDS = ("seo_title", "seo_h1", "seo_description")

# Section widgets the server lets a write store as `hidden`; any other hidden section
# fails the whole write with 400 («Секция … не может быть скрыта»). A copy of the server
# table, kept in sync by hand like the alias tables above.
HIDEABLE_SECTION_WIDGETS = frozenset(
    {
        "YandexKit.AnnouncementBar",
        "YandexKit.BannersCarousel",
        "YandexKit.EmailSubscription",
        "YandexKit.Faq",
        "YandexKit.FeaturedBlogs",
        "YandexKit.FeaturedCollections",
        "YandexKit.FeaturedProducts",
        "YandexKit.ImageWithText",
        "YandexKit.LoyaltyProgram",
        "YandexKit.Mystique",
        "YandexKit.RichText",
    }
)
SECTION_STATUSES = ("published", "hidden")

# The address of a custom page: one or more `/segment` parts, each made of Cyrillic
# and Latin letters, digits, spaces and `._!'+-`. Anything else — `:param`, `~`, `%`
# escapes, `?`, `#`, an empty segment — is refused by the server for the whole write.
CUSTOM_PATTERN_SEGMENT_CHARACTERS = "а-яА-ЯёЁa-zA-Z0-9 ._!'+-"
_CUSTOM_PATTERN = re.compile(f"(?:/[{CUSTOM_PATTERN_SEGMENT_CHARACTERS}]+)+")

# Composite chrome copied onto every page by the cabinet; a page created without
# them renders without the store's header and footer.
CHROME_WIDGETS = ("YandexKit.Header", "YandexKit.Footer")
# A custom header or footer is a YandexKit.Mystique section in the same slot whose
# template carries the widget's name as its alias. The alias is what the server
# looks at; nothing else marks a template as store chrome.
CHROME_SLOT_BY_ALIAS = {"YandexKit.Header": "header", "YandexKit.Footer": "footer"}
CHROME_WIDGET_BY_SLOT = {slot: widget for widget, slot in CHROME_SLOT_BY_ALIAS.items()}
MYSTIQUE_WIDGET = "YandexKit.Mystique"
HEADER_SEQUENCE = 1
FOOTER_SEQUENCE = 9999
SEQUENCE_BY_SLOT = {"header": HEADER_SEQUENCE, "footer": FOOTER_SEQUENCE}
# System pages the server keeps without the store header and footer: its fallback that
# restores a missing footer from the main page skips exactly these.
ALIASES_WITHOUT_CHROME = {"cartPreview", "emailConfirmationFailed", "emailConfirmed", "unsubscribe"}


def page_alias(page: "dict[str, object]") -> str:
    """The page alias normalized to '' for custom pages."""
    alias = page.get("alias")
    return alias if isinstance(alias, str) else ""


def normalize_pattern(pattern: str, alias: str = "", *, strip: bool = True) -> str:
    """Mirror the server's pattern normalization: leading slash on, one trailing slash off.

    `strip` trims surrounding whitespace first, a convenience for typed input; the server
    does not trim, so checks of a stored address pass `strip=False`.
    """
    if strip:
        pattern = pattern.strip()
    if pattern in ("", "/") or alias == "categoryAndCollection":
        return pattern
    if not pattern.startswith("/"):
        pattern = "/" + pattern
    if pattern.endswith("/"):
        pattern = pattern[:-1]
    return pattern


def is_static_pattern(pattern: str) -> bool:
    """A pattern without ':param' segments is static — the basis of the derived `exact`."""
    return ":" not in pattern


def is_valid_custom_pattern(pattern: str) -> bool:
    """Whether the server accepts this normalized pattern as the address of a custom page."""
    return _CUSTOM_PATTERN.fullmatch(pattern) is not None


def _custom_pattern_problem(pattern: str) -> str:
    return (
        f"address {pattern!r} has characters the server refuses for a custom page — it rejects "
        "the whole write. Allowed: Cyrillic and Latin letters, digits, spaces and . _ ! ' + -, "
        "in /-separated parts with nothing empty between slashes; no ':param', '~', '%', '?', '#', "
        "'&' or '='."
    )


def is_protected_page(page: "dict[str, object]") -> bool:
    """A default-variant page with an alias can never be dropped: the server 400s the write."""
    return bool(page_alias(page)) and page.get("variant_type") == "default"


def page_pattern(page: "dict[str, object]") -> str:
    """The page's pattern as it goes into the write, normalized the way the server does."""
    raw_pattern = page.get("pattern")
    return normalize_pattern(raw_pattern if isinstance(raw_pattern, str) else "", page_alias(page))


def system_route_owner(pattern: str, aliases: "set[str] | None" = None) -> "tuple[str, str] | None":
    """(alias, route) of a system page that serves this custom address too, else None.

    A custom page equal to a single `~` route of a system page (`/account`, the second
    route of `accountProfile`), or matched by a `:param` route (`/catalog/sale` by
    `/catalog/:categorySlug`), is stored by the server without complaint. The storefront
    then has two pages for one address and opens whichever it tries first, in no fixed
    order. `aliases` limits the owners to those system pages (the ones a store has); None
    means every alias. A whole canonical pattern is a different case — the server makes
    that page the system page, or answers 400 when the store already has one — see
    ALIAS_BY_PATTERN.
    """
    for alias, route, matcher in SYSTEM_ROUTES:
        if aliases is not None and alias not in aliases:
            continue
        if matcher.fullmatch(pattern):
            return alias, route
    return None


def _store_aliases(workspace) -> "set[str]":
    """The aliases of the system pages this workspace has."""
    return {page_alias(page) for page in validated_pages(workspace.content) if page_alias(page)}


def _introduced_custom_address(page: "dict[str, object]", base_page: "dict[str, object] | None") -> "str | None":
    """The single-route address of a custom page when the edit gives it, else None.

    None for aliased pages, for an address the page already had on the storefront (stored,
    not re-litigated), and for addresses other rules refuse anyway (empty, a whole
    canonical pattern, a `~` route).
    """
    if page_alias(page):
        return None
    pattern = page_pattern(page)
    if not pattern or pattern in ALIAS_BY_PATTERN or PATTERN_DELIMITER in pattern:
        return None
    if base_page is not None and not page_alias(base_page) and page_pattern(base_page) == pattern:
        return None
    return pattern


def route_owner_warning(workspace, page: "dict[str, object]", base_page: "dict[str, object] | None") -> "str | None":
    """A warning when a system page the store does not have would also serve this address.

    The storefront matches only the pages a store has, so the address is free today. A
    store can lack a system page — one created before that page existed on the platform,
    or `accountRefunds`, which is not created with a store — and gain it later, through
    `page add --alias` or the cabinet; from then on either page may open at that address.
    """
    pattern = _introduced_custom_address(page, base_page)
    if pattern is None:
        return None
    present = _store_aliases(workspace)
    if system_route_owner(pattern, present) is not None:
        return None  # a refusal, not a warning — see `page_gate_errors`
    owner = system_route_owner(pattern, set(ALIAS_PATTERNS) - present)
    if owner is None:
        return None
    owner_alias, route = owner
    return (
        f"{_page_label(page)}: address {pattern!r} is free in this store today, but the system "
        f"page with alias {owner_alias!r} serves it too (its route {route!r}), and the store "
        "does not have that page now. If the page is added later (page add --alias, or the "
        "cabinet), the storefront would open either page at this address, in no fixed order. "
        "Tell the user, or choose another address."
    )


def new_system_page_route_warning(workspace, page: "dict[str, object]", base_page: "dict[str, object] | None") -> "str | None":
    """A warning when a system page the edit adds serves the address of a custom page.

    The reverse of `route_owner_warning`: a store that lacked a system page may already
    have a custom page at an address one of its routes matches. The system page is what
    the store asked for, so it is not refused — but from the push on either page may open
    there.
    """
    alias = page_alias(page)
    if not alias or base_page is not None or alias not in ALIAS_PATTERNS:
        return None
    matchers = [(route, matcher) for owner, route, matcher in SYSTEM_ROUTES if owner == alias]
    taken: "list[str]" = []
    for other in validated_pages(workspace.content):
        if page_alias(other):
            continue
        pattern = page_pattern(other)
        if any(matcher.fullmatch(pattern) for _route, matcher in matchers):
            taken.append(f"{other.get('title')!r} at {pattern!r}")
    if not taken:
        return None
    return (
        f"{_page_label(page)}: its routes also match the custom page(s) {', '.join(taken)}. "
        "After the push the storefront would open either page at those addresses, in no fixed "
        "order. Tell the user; moving the custom page is 'page set --pattern', and links to its "
        "old address stop working."
    )


def ignored_meta_warning(page: "dict[str, object]") -> "str | None":
    """A warning when this page carries SEO meta the storefront never reads, else None."""
    alias = page_alias(page)
    meta = page.get("meta")
    if alias not in ALIASES_IGNORING_PAGE_META or not isinstance(meta, dict):
        return None
    if not any(isinstance(meta.get(field), str) and meta.get(field) for field in META_FIELDS):
        return None
    return (
        f"{_page_label(page)}: the storefront does not read the SEO title, H1 or description of "
        f"a page with alias {alias!r} — it renders the store-wide template from the SEO settings "
        "instead. The meta is stored but changes nothing customers or search engines see; tell "
        "the user so rather than reporting the SEO as updated."
    )


def _seo_h1(page: "dict[str, object]") -> object:
    meta = page.get("meta")
    return meta.get("seo_h1") if isinstance(meta, dict) else None


def seo_h1_warning(page: "dict[str, object]") -> "str | None":
    """A warning when a static page carries an seo_h1 the storefront never prints.

    The storefront picks the H1 from the sections (see `h1_hint`); it does not print the
    page's seo_h1 (the core computes it, the storefront ignores it). Pages whose meta is
    ignored altogether get the broader `ignored_meta_warning` instead.
    """
    alias = page_alias(page)
    if alias in ALIASES_IGNORING_PAGE_META:
        return None
    meta = page.get("meta")
    h1 = meta.get("seo_h1") if isinstance(meta, dict) else None
    if not isinstance(h1, str) or not h1.strip() or not is_static_pattern(page_pattern(page)):
        return None
    return (
        f"{_page_label(page)}: the storefront does not print seo_h1 — seo_h1 is stored and changes "
        "nothing customers or search engines see. The page's H1 is the first section title on it "
        "that is not a link; a banner carousel never takes it (its slide titles are h2), nor does "
        "a product shelf on a category or collection (its title links there), and on a product, "
        "listing, blog, cart or account page the page's own block takes it. To set the H1, give "
        "that first titled section the intended title; 'page show' names it as h1_hint."
    )


# --- the storefront's H1 --------------------------------------------------------------
#
# How the storefront picks the H1 (its page-heading registry; checked against live pages):
# it renders the header area first (Header, AnnouncementBar, templates with those
# aliases), then the content, then the footer. Only a *section title* takes part — the
# title row of a widget built with one — and only when it has text and is not a link. The
# first such title on the page becomes the H1 and every later one an h2. A page block whose
# own heading outranks section titles (the product name, a listing, an article, the cart,
# an account page) takes the H1 whenever it is on the page. A banner carousel has no
# section title: its slide titles are always h2. Custom markup that writes an h1 itself
# (`ya-kit-text as="h1"`) is outside the registry and prints one more H1 next to it.
#
# The rank is page-wide: one such block on the page — any of PAGE_HEADING_WIDGETS, or a
# widget of THRESHOLD_ONLY_WIDGETS — raises it, and while the server renders the page a
# section title cannot take the H1 under that rank. When the block prints no heading of
# its own (the loyalty block's title is a plain h2, a blog listing without a title prints
# none), the server HTML has no H1 at all; the browser promotes the first section title
# only after the page has loaded its scripts.

# Widget -> path of its section title in the settings; None when the text comes from data
# (the subscription form titles itself «Подпишитесь на рассылку» or with its promo code).
SECTION_TITLE_SETTINGS: "dict[str, tuple[str, ...] | None]" = {
    "YandexKit.FeaturedProducts": ("titleText",),
    "YandexKit.FeaturedCollections": ("title",),
    "YandexKit.FeaturedBlogs": ("titleText",),
    "YandexKit.Faq": ("title",),
    "YandexKit.ImageWithText": ("titleText",),
    "YandexKit.RichText": ("titleText",),
    "YandexKit.EmailSubscription": None,
    "YandexKit.ProductsBundleConstructor": None,
}
# A product shelf on a category or a collection renders its title as a link to it.
LINKED_SHELF_SOURCES = frozenset({"category", "collection"})
# Page blocks whose heading outranks every section title on the page, with the settings
# path of that heading; None when it comes from data (product name, category, article,
# «Корзина», «Подарочная карта»...). A blog listing without a title prints none, and the
# H1 goes on to the section titles.
PAGE_HEADING_WIDGETS: "dict[str, tuple[str, ...] | None]" = {
    "YandexKit.AccountLoyalty": None,
    "YandexKit.AccountNewsletters": None,
    "YandexKit.AccountOrderDetails": None,
    "YandexKit.AccountOrdersListing": None,
    "YandexKit.AccountProfile": None,
    "YandexKit.AccountRefunds": None,
    "YandexKit.Blog": None,
    "YandexKit.BlogListing": ("title",),
    "YandexKit.CollectionsListing": None,
    "YandexKit.DiscountLadder": None,
    "YandexKit.FavouriteProducts": None,
    "YandexKit.GiftCard": None,
    "YandexKit.ProductDetails": None,
    "YandexKit.ProductReviewsListing": None,
    "YandexKit.ProductsListing": None,
    "YandexKit.ShoppingCart": None,
}
# Blocks that raise the page-wide rank like PAGE_HEADING_WIDGETS but never print a heading
# of that rank: the loyalty programme writes its title as an ordinary h2.
THRESHOLD_ONLY_WIDGETS = frozenset({"YandexKit.LoyaltyProgram"})
HEADER_AREA_TYPES = ("YandexKit.Header", "YandexKit.AnnouncementBar")
FOOTER_AREA_TYPES = ("YandexKit.Footer",)
# Setting keys a custom section's own heading usually sits in (kit-custom-sections
# patterns), for the text of a template that writes its h1 itself.
HEADING_SETTING_KEYS = ("titleText", "title", "headline", "heading")

_SECTION_TITLE_TAG = re.compile(r"<ya-kit-section-title\b((?:[^>\"]|\"[^\"]*\")*)>")
_TAG_ATTRIBUTE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_SETTINGS_REFERENCE = re.compile(r"settings\.([A-Za-z_][\w.]*)")
_LITERAL_H1 = re.compile(r'<h1[\s>]|\bas\s*=\s*"h1"')


def _template_html(section: "dict[str, object]", content: "dict[str, object] | None") -> "str | None":
    if content is None:
        return None
    template_id = section.get("template_id")
    templates = content.get("section_templates")
    for template in templates if isinstance(templates, list) else []:
        if isinstance(template, dict) and template.get("id") == template_id:
            html = template.get("html")
            return html if isinstance(html, str) else None
    return None


def _setting(settings: object, path: "tuple[str, ...] | list[str]") -> object:
    value = settings
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _text_or_none(value: object) -> "str | None":
    return value.strip() if isinstance(value, str) and value.strip() else None


def _attribute_value(raw: str, settings: object) -> "tuple[bool, str | None]":
    """(non-empty, text) of a template attribute: literal, or a settings reference resolved."""
    if "{{" not in raw:
        return bool(raw.strip()), _text_or_none(raw)
    reference = _SETTINGS_REFERENCE.search(raw)
    if reference is None:
        return True, None  # an expression of something else: assume it renders
    text = _text_or_none(_setting(settings, reference.group(1).split(".")))
    return text is not None, text


def _mystique_section_title(section: "dict[str, object]", html: str) -> "tuple[bool, str | None]":
    """Whether a custom section's template has a section title that can take the H1, and its text."""
    settings = section.get("settings")
    for match in _SECTION_TITLE_TAG.finditer(html):
        attributes = dict(_TAG_ATTRIBUTE.findall(match.group(1)))
        if "to" in attributes and _attribute_value(attributes["to"], settings)[0]:
            continue  # a link, not a heading
        if "text" not in attributes:
            return True, None  # the title is the tag's content
        present, text = _attribute_value(attributes["text"], settings)
        if present:
            return True, text
    return False, None


def _template_writes_h1(section: "dict[str, object]", html: str) -> bool:
    if not _LITERAL_H1.search(html):
        return False
    # kit-custom-sections patterns with a heading switch keep it in `headingLevel`.
    level = _setting(section.get("settings"), ("headingLevel",))
    return not isinstance(level, str) or level == "h1"


def _section_title(section: "dict[str, object]", content: "dict[str, object] | None") -> "tuple[bool, str | None]":
    """(takes part in the H1 choice, its text — None when it comes from data) for one section."""
    widget = section.get("widget")
    settings = section.get("settings")
    if widget in SECTION_TITLE_SETTINGS:
        path = SECTION_TITLE_SETTINGS[str(widget)]
        if path is None:
            return True, None
        text = _text_or_none(_setting(settings, path))
        if text is None:
            return False, None
        if widget == "YandexKit.FeaturedProducts" and _setting(settings, ("dataSource", "type")) in LINKED_SHELF_SOURCES:
            return False, None
        return True, text
    if widget == MYSTIQUE_WIDGET:
        html = _template_html(section, content)
        return _mystique_section_title(section, html) if html is not None else (False, None)
    return False, None


def _render_area(section: "dict[str, object]", content: "dict[str, object] | None") -> int:
    """0 header area, 1 content, 2 footer — the storefront renders the three groups in that order."""
    kind = section.get("widget")
    if kind == MYSTIQUE_WIDGET and content is not None:
        kind = template_alias(content, section.get("template_id")) or kind
    if kind in HEADER_AREA_TYPES:
        return 0
    if kind in FOOTER_AREA_TYPES:
        return 2
    return 1


def h1_hint(page: "dict[str, object]", content: "dict[str, object] | None" = None) -> "dict[str, object] | None":
    """The section the storefront prints as the page H1, by its own rule; None when none does.

    `source` says which rule picked it: `page_block` (a product, listing, blog, cart or
    account block outranks section titles; its text comes from data), `section_title` (the
    first section title on the page that is not a link), `template_markup` (no section
    title takes it, but a custom template writes an h1 itself) or `none_in_server_html`: a
    block that outranks section titles is on the page without a heading of that rank
    (`raised_by` — the loyalty block, a blog listing without a title), so the server HTML
    has no H1 and `after_hydration` names the section title the browser promotes once the
    scripts run. `also_h1` lists custom sections whose markup writes an h1 of its own next
    to the chosen one — the page then has more than one H1. A best-effort reading of
    settings and templates: a shelf with no products renders nothing, and a listing whose
    collection has no title prints no heading, which only the storefront knows.
    """
    visible = [
        item
        for item in page.get("layout") or []
        if isinstance(item, dict) and item.get("status") != "hidden"
    ]
    ordered = sorted(
        visible,
        key=lambda item: (
            _render_area(item, content),
            item.get("display_sequence") if isinstance(item.get("display_sequence"), int) else 0,
        ),
    )
    main: "dict[str, object] | None" = None
    raised_by: "dict[str, object] | None" = None
    for item in ordered:
        widget = item.get("widget")
        if widget in THRESHOLD_ONLY_WIDGETS:
            raised_by = raised_by or {"section_id": item.get("id"), "widget": widget}
            continue
        if widget not in PAGE_HEADING_WIDGETS:
            continue
        path = PAGE_HEADING_WIDGETS[str(widget)]
        text = None if path is None else _text_or_none(_setting(item.get("settings"), path))
        if path is None or text is not None:
            main = {"section_id": item.get("id"), "widget": widget, "text": text, "source": "page_block"}
            break
        raised_by = raised_by or {"section_id": item.get("id"), "widget": widget}
    after_hydration: "dict[str, object] | None" = None
    if main is None:
        for item in ordered:
            takes_part, text = _section_title(item, content)
            if takes_part:
                title = {"section_id": item.get("id"), "widget": item.get("widget"), "text": text}
                if raised_by is None:
                    main = dict(title, source="section_title")
                else:
                    after_hydration = title
                break
    own: "list[dict[str, object]]" = []
    for item in ordered:
        html = _template_html(item, content) if item.get("widget") == MYSTIQUE_WIDGET else None
        if html is not None and _template_writes_h1(item, html):
            text = next(
                (t for t in (_text_or_none(_setting(item.get("settings"), (key,))) for key in HEADING_SETTING_KEYS) if t),
                None,
            )
            own.append({"section_id": item.get("id"), "widget": item.get("widget"), "text": text})
    if main is None:
        if own:
            main = dict(own.pop(0), source="template_markup")
        elif raised_by is not None:
            main = {"section_id": None, "widget": None, "text": None, "source": "none_in_server_html"}
        else:
            return None
        if raised_by is not None:
            main["raised_by"] = raised_by
            if after_hydration is not None:
                # One more H1 once the scripts run, next to a template's own.
                main["after_hydration"] = after_hydration
    if own:
        main["also_h1"] = own
    return main


def _page_field_errors(page: "dict[str, object]", label: str) -> "list[str]":
    """Rules for one page: server refusals mirrored, plus the client policy marked as such."""
    errors: "list[str]" = []
    title = page.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{label}: title must be a non-empty string.")

    alias = page_alias(page)
    normalized = page_pattern(page)
    if alias:
        # Server refusal: an alias outside its catalog fails the whole write. The pattern
        # of an aliased page is checked in `page_gate_errors`, against what was read.
        if alias not in ALIAS_PATTERNS:
            known = ", ".join(sorted(ALIAS_PATTERNS))
            errors.append(f"{label}: unknown alias {alias!r}; known aliases: {known}.")
    elif not normalized:
        errors.append(f"{label}: pattern must not be empty.")
    else:
        if normalized in ALIAS_BY_PATTERN:
            # Server rewrite, refused here: the server makes the page the system page —
            # silently when the store has none, with 400 when it already has one.
            reserved_for = ALIAS_BY_PATTERN[normalized]
            errors.append(
                f"{label}: pattern {normalized!r} is reserved for the system page with alias "
                f"{reserved_for!r}; the server never keeps a custom page there: when the store "
                "already has that system page it refuses the whole write with 400, otherwise it "
                "silently turns this page into it. Choose another pattern."
            )
        elif PATTERN_DELIMITER in normalized:
            errors.append(
                f"{label}: multi-pattern routes ('{PATTERN_DELIMITER}') exist only on system "
                "pages; a custom page gets exactly one pattern."
            )
        # A route a system page already serves is checked in `page_gate_errors`, against
        # the system pages this store has. The address characters are checked there too,
        # over every page: the server applies that rule to untouched pages, so no baseline
        # excuses it.

    status = page.get("status")
    if status not in PAGE_STATUSES:
        errors.append(f"{label}: status must be one of: {', '.join(PAGE_STATUSES)}.")
    elif status == "hidden" and alias not in HIDDEN_ALLOWED_ALIASES:
        allowed = ", ".join(sorted(a for a in HIDDEN_ALLOWED_ALIASES if a))
        errors.append(
            f"{label}: pages with alias {alias!r} cannot be hidden — the server refuses the whole "
            f"write with 400; hidden is supported only for custom pages (no alias) and aliases: "
            f"{allowed}."
        )

    variant = page.get("variant_type")
    if variant not in ("default", "alternative"):
        errors.append(f"{label}: variant_type must be 'default' or 'alternative'.")
    elif not alias and variant != "default":
        errors.append(
            f"{label}: variant_type is derived by the server; a custom page without an alias "
            "is always 'default'."
        )

    if normalized and "exact" in page and page.get("exact") != is_static_pattern(normalized):
        errors.append(
            f"{label}: exact must be {is_static_pattern(normalized)} — it is derived from the "
            "pattern (True when the pattern has no ':param' segments); do not set it by hand."
        )

    meta = page.get("meta")
    if meta is not None:
        if not isinstance(meta, dict):
            errors.append(f"{label}: meta must be a JSON object.")
        else:
            for field in META_FIELDS:
                if field in meta and not isinstance(meta[field], str):
                    errors.append(f"{label}: meta.{field} must be a string.")
            unknown = sorted(set(meta) - set(META_FIELDS))
            if unknown:
                errors.append(f"{label}: meta has unsupported fields: {', '.join(unknown)}.")
        if normalized and not is_static_pattern(normalized):
            errors.append(
                f"{label}: SEO meta is allowed only on static pages; pattern {normalized!r} "
                "carries ':param' segments, so the server rejects meta for it. Remove the meta."
            )
    return errors


def _page_label(page: "dict[str, object]") -> str:
    alias = page_alias(page)
    identity = alias or page.get("id") or "?"
    return f"page {page.get('title')!r} ({identity})"


def _refused_custom_addresses(workspace) -> "list[tuple[dict[str, object], str, bool]]":
    """Custom pages whose address the server rule refuses: (page, address, stored).

    `stored` means the address is the one already on the storefront, not introduced by
    the edit. Normalized the way the server does it — without trimming whitespace, so
    a stored '/a\\n' is not mistaken for '/a'.
    """
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    refused: "list[tuple[dict[str, object], str, bool]]" = []
    for page in validated_pages(workspace.content):
        if page_alias(page):
            continue
        raw_pattern = page.get("pattern")
        pattern = normalize_pattern(raw_pattern if isinstance(raw_pattern, str) else "", strip=False)
        if not pattern or pattern in ALIAS_BY_PATTERN or is_valid_custom_pattern(pattern):
            continue
        base_page = base_pages.get(page["id"])
        base_raw = base_page.get("pattern") if base_page is not None else None
        stored = isinstance(base_raw, str) and normalize_pattern(base_raw, strip=False) == pattern
        refused.append((page, pattern, stored))
    return refused


def page_gate_warnings(workspace) -> "list[str]":
    """What the push plan reports without blocking.

    * Stored custom-page addresses the newer server rule refuses. A backend with the rule
      answers the whole write with 400 (nothing is written); one without it accepts the
      write. The client cannot tell which one serves the store, so it says so before the
      confirmation instead of blocking a write that may succeed.
    * SEO meta the edit sets on a page whose meta the storefront never reads.
    * A custom address the edit gives that a system page the store does not have yet would
      also serve (`route_owner_warning`), and the reverse: a system page the edit adds over
      a stored custom address (`new_system_page_route_warning`).
    """
    warnings = [
        (
            f"{_page_label(page)}: this page is already on the storefront, but its "
            f"{_custom_pattern_problem(pattern)} A backend that has this rule refuses every write "
            "with 400 while such a page exists; one without it accepts the write. If the push is "
            f"refused for this address, the way out is 'page set --id {page['id']} --pattern ...' — "
            "and tell the user that links to the old address stop working."
        )
        for page, pattern, stored in _refused_custom_addresses(workspace)
        if stored
    ]
    base_meta = {page["id"]: page.get("meta") for page in validated_pages(workspace.base)}
    for page in validated_pages(workspace.content):
        if page["id"] in base_meta and page.get("meta") == base_meta[page["id"]]:
            continue
        warning = ignored_meta_warning(page)
        if warning is not None:
            warnings.append(warning)
        if _seo_h1({"meta": base_meta.get(page["id"])}) != _seo_h1(page):
            h1_warning = seo_h1_warning(page)
            if h1_warning is not None:
                warnings.append(h1_warning)
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    for page in validated_pages(workspace.content):
        base_page = base_pages.get(page["id"])
        for warning in (
            route_owner_warning(workspace, page, base_page),
            new_system_page_route_warning(workspace, page, base_page),
        ):
            if warning is not None:
                warnings.append(warning)
    return warnings


def page_gate_errors(workspace) -> "list[str]":
    """The page-level server refusals and client policy, checked offline before the write.

    Drift tolerance: rules run only against pages the edit added or changed — a page
    carried through byte-for-byte came from the server and is never re-litigated.
    The address characters of a custom page are a special case: the newer server rule
    checks every page of every write, so a stored address it refuses fails each push.
    Whether the serving backend already has that rule is not known from here, so a
    stored address only warns (`page_gate_warnings`) and an introduced one blocks.
    """
    errors: "list[str]" = []
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    work_pages = validated_pages(workspace.content)

    seen_ids: "set[str]" = set()
    touched: "list[dict[str, object]]" = []
    touched_ids: "set[str]" = set()
    for page in work_pages:
        pid = str(page["id"])
        if pid in seen_ids:
            errors.append(f"{_page_label(page)}: duplicate page id {pid}; the server rejects the write.")
        seen_ids.add(pid)
        base_page = base_pages.get(pid)
        if base_page is None or base_page != page:
            touched.append(page)
            touched_ids.add(pid)

    store_aliases = _store_aliases(workspace)
    for page in touched:
        label = _page_label(page)
        field_errors = _page_field_errors(page, label)
        base_page = base_pages.get(str(page["id"]))
        if base_page is not None:
            # Baseline tolerance, as with section settings: server-origin drift already
            # present in the pulled page never blocks; only errors the edit introduces do.
            baseline = set(_page_field_errors(base_page, label))
            field_errors = [error for error in field_errors if error not in baseline]
        errors.extend(field_errors)
        if base_page is not None and page.get("variant_type") != base_page.get("variant_type"):
            errors.append(
                f"{_page_label(page)}: variant_type is derived by the server and cannot be "
                f"changed (stays {base_page.get('variant_type')!r})."
            )
        alias = page_alias(page)
        if alias in ALIAS_PATTERNS:
            if base_page is not None and page_alias(base_page) == alias:
                # Sent as read: an older backend accepts only its own pattern, a newer one
                # writes its canonical pattern anyway — so a stored one is never changed.
                if page.get("pattern") != base_page.get("pattern"):
                    errors.append(
                        f"{label}: the pattern of an aliased system page belongs to the server "
                        f"and cannot be changed (stays {base_page.get('pattern')!r}): an older "
                        "backend refuses any other one with 400, a newer one writes its own."
                    )
            elif page_pattern(page) != ALIAS_PATTERNS[alias]:
                errors.append(
                    f"{label}: a new page with alias {alias!r} takes the canonical pattern "
                    f"{ALIAS_PATTERNS[alias]!r}, not {page.get('pattern')!r}."
                )
        # Client policy: a custom address a system page of this store already serves.
        address = _introduced_custom_address(page, base_page)
        owner = system_route_owner(address, store_aliases) if address is not None else None
        if owner is not None:
            owner_alias, route = owner
            errors.append(
                f"{label}: address {address!r} is already served by this store's system page "
                f"with alias {owner_alias!r} (its route {route!r}). The server would store this "
                "page, but the storefront would then open either page at that address, in no "
                "fixed order. Choose another address."
            )

    # Address characters of custom pages: an address the edit introduces blocks. A stored
    # one only warns — see `page_gate_warnings`.
    for page, pattern, stored in _refused_custom_addresses(workspace):
        if stored:
            continue
        if str(page["id"]) in touched_ids and PATTERN_DELIMITER in pattern:
            continue  # already reported as a multi-pattern route above
        errors.append(f"{_page_label(page)}: {_custom_pattern_problem(pattern)}")

    # Pattern uniqueness: one page per pattern, except the two multi-variant aliases.
    # Only groups touched by the edit block — pre-existing drift does not.
    by_pattern: "dict[str, list[dict[str, object]]]" = {}
    for page in work_pages:
        by_pattern.setdefault(page_pattern(page), []).append(page)
    for pattern, group in by_pattern.items():
        if len(group) < 2 or not pattern:
            continue
        aliases = {page_alias(page) for page in group}
        if aliases <= MULTI_VARIANT_ALIASES and len(aliases) == 1:
            continue
        if not any(str(page["id"]) in touched_ids for page in group):
            continue
        titles = ", ".join(repr(page.get("title")) for page in group)
        errors.append(
            f"pattern {pattern!r} is used by {len(group)} pages ({titles}); every page needs a "
            "unique pattern — several pages per pattern exist only for productCard and "
            "categoryAndCollection variants."
        )

    # One default variant per alias.
    defaults: "dict[str, list[dict[str, object]]]" = {}
    for page in work_pages:
        alias = page_alias(page)
        if alias and page.get("variant_type") == "default":
            defaults.setdefault(alias, []).append(page)
    for alias, group in defaults.items():
        if len(group) < 2:
            continue
        if not any(str(page["id"]) in touched_ids for page in group):
            continue
        errors.append(
            f"alias {alias!r} has {len(group)} pages with variant_type 'default'; exactly one "
            "default page per alias is allowed."
        )
    return errors


def template_alias(content: "dict[str, object]", template_id: object) -> "str | None":
    """The alias of one catalogued section template, or None when it has none or is unknown."""
    if not isinstance(template_id, str):
        return None
    templates = content.get("section_templates")
    for template in templates if isinstance(templates, list) else []:
        if isinstance(template, dict) and template.get("id") == template_id:
            alias = template.get("alias")
            return alias if isinstance(alias, str) else None
    return None


def template_chrome_slot(content: "dict[str, object]", template_id: object) -> "str | None":
    """'header' / 'footer' for a template whose alias makes it store chrome, else None."""
    return CHROME_SLOT_BY_ALIAS.get(str(template_alias(content, template_id)))


# Template aliases the storefront places in the header area, above the page content: it
# reads a custom section's type from its template's alias, whatever the section's place in
# the layout (`YandexKit.Footer` goes to the footer area the same way). The chrome aliases
# are refused for regular sections outright — they are the store's header and footer — so
# what is left to warn about is the announcement bar.
HEADER_AREA_TEMPLATE_ALIASES = ("YandexKit.Header", "YandexKit.AnnouncementBar")


def template_placement_warning(content: "dict[str, object]", template_id: object) -> "str | None":
    """A warning when a non-chrome template makes the storefront lift its sections out of the page.

    `YandexKit.AnnouncementBar` is how the cabinet offers a custom template under «Заменить»
    for the announcement bar, so a store may have such a template. A section on it renders in
    the header area, above the content, not at the position it has in the page layout.
    """
    alias = template_alias(content, template_id)
    if alias not in HEADER_AREA_TEMPLATE_ALIASES or alias in CHROME_SLOT_BY_ALIAS:
        return None
    return (
        f"template {template_id} carries alias {alias}, so the storefront renders every section "
        "on it in the header area, above the page content — not at the position this section "
        "has in the page layout. Tell the user; for a block inside the page, use a template "
        "without that alias."
    )


def chrome_slot_of(section: "dict[str, object]", content: "dict[str, object]") -> "str | None":
    """Which chrome slot a section fills: the built-in widget, or a custom template in its place.

    A custom header is a composite (`parent`/`child`) YandexKit.Mystique section whose
    template carries the alias YandexKit.Header, likewise the footer. A regular section on
    such a template exists on some storefronts, but it is not the slot: it is one more
    section on its page.
    """
    widget = section.get("widget")
    if widget in CHROME_SLOT_BY_ALIAS:
        return CHROME_SLOT_BY_ALIAS[str(widget)]
    if widget == MYSTIQUE_WIDGET and section.get("section_type") in ("parent", "child"):
        return template_chrome_slot(content, section.get("template_id"))
    return None


def unwrap_menu_id(value: object) -> "str | None":
    """The menu id from any form a stored setting has, or None when no menu is chosen.

    The current built-in header keeps the main menu at `modules.main.menu.menu`, next to
    `menuVariant` and `menuDisplayType`. Settings saved before that keep the id as a plain
    string at `modules.main.menu`, and a former migration wrapped the object into itself
    more than once (`menu.menu.menu`). The store keeps settings as they were last saved —
    the storefront and the cabinet editor migrate them on the fly — so `section show` can
    return any of the three. Following `menu` down to the string reads all of them.
    """
    for _depth in range(16):
        if not isinstance(value, dict):
            break
        value = value.get("menu")
    return value if isinstance(value, str) and value else None


def template_menu_paths(content: "dict[str, object]", template_id: object) -> "list[str] | None":
    """Setting paths of a template's menu fields (`$ref: #/definitions/MenuId`), or None.

    A custom header or footer names its menu fields freely (`mainMenu`, `columns[].menu`);
    what marks them is the MenuId editor model they reference. Refs into the template's
    own definitions are followed; None when the template is not in the snapshot or its
    schema is not JSON.
    """
    import json

    if not isinstance(template_id, str):
        return None
    templates = content.get("section_templates")
    record = next(
        (
            template
            for template in (templates if isinstance(templates, list) else [])
            if isinstance(template, dict) and template.get("id") == template_id
        ),
        None,
    )
    if record is None or not isinstance(record.get("json_schema"), str):
        return None
    try:
        schema = json.loads(record["json_schema"])
    except json.JSONDecodeError:
        return None
    definitions = schema.get("definitions") if isinstance(schema, dict) else None
    definitions = definitions if isinstance(definitions, dict) else {}
    paths: "list[str]" = []

    def walk(node: object, path: str, seen: "frozenset[str]") -> None:
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            if name == "MenuId":
                if path:
                    paths.append(path)
                return
            target = definitions.get(name) if reference.startswith("#/definitions/") else None
            if isinstance(target, dict) and name not in seen:
                walk(target, path, seen | {name})
        properties = node.get("properties")
        if isinstance(properties, dict):
            for key, child in properties.items():
                walk(child, f"{path}.{key}" if path else str(key), seen)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]", seen)

    walk(schema, "", frozenset())
    return paths


def _read_setting_path(settings: object, path: str) -> object:
    """The value at a dotted path, `[]` fanning out over a list; menu ids unwrapped."""
    head, _, rest = path.partition(".")
    fan_out = head.endswith("[]")
    key = head[:-2] if fan_out else head
    value = settings.get(key) if isinstance(settings, dict) else None
    if fan_out:
        items = value if isinstance(value, list) else []
        return [(_read_setting_path(item, rest) if rest else unwrap_menu_id(item)) for item in items]
    return _read_setting_path(value, rest) if rest else unwrap_menu_id(value)


def chrome_menu_ids(
    widget: object,
    settings: object,
    content: "dict[str, object] | None" = None,
    template_id: object = None,
) -> "dict[str, object] | None":
    """The menu ids a header or footer points at, or None for any other section.

    Built-in header: `main` (`modules.main.menu`, unwrapped) and `additional`
    (`modules.additional.menu`). Built-in footer: `legal` (`legalInfo.menu`) and
    `columns` (`modules[].menu`, one per column). A custom template in the slot
    (`YandexKit.Mystique`, given `content` and `template_id`): one entry per MenuId field
    of its schema, keyed by the setting path — `{}` when it has none. The menus
    themselves are not reachable from here.
    """
    if widget == MYSTIQUE_WIDGET:
        if content is None:
            return None
        paths = template_menu_paths(content, template_id)
        if paths is None:
            return None
        return {path: _read_setting_path(settings, path) for path in paths}
    if not isinstance(settings, dict):
        settings = {}
    if widget == "YandexKit.Header":
        modules = settings.get("modules")
        modules = modules if isinstance(modules, dict) else {}
        main = modules.get("main")
        additional = modules.get("additional")
        return {
            "main": unwrap_menu_id(main.get("menu") if isinstance(main, dict) else None),
            "additional": unwrap_menu_id(additional.get("menu") if isinstance(additional, dict) else None),
        }
    if widget == "YandexKit.Footer":
        legal = settings.get("legalInfo")
        columns = settings.get("modules")
        return {
            "legal": unwrap_menu_id(legal.get("menu") if isinstance(legal, dict) else None),
            "columns": [
                unwrap_menu_id(column.get("menu"))
                for column in (columns if isinstance(columns, list) else [])
                if isinstance(column, dict)
            ],
        }
    return None


def find_chrome_parents(content: "dict[str, object]") -> "dict[str, dict[str, object]]":
    """The parent copy of each composite chrome slot (`header`, `footer`), if present.

    Both the built-in widget and a custom template in the slot count: a store whose
    header is its own template must get that template on a new page, not no header.
    The first `parent` in workspace page order wins. This is the client's own choice for
    `page add`, not a copy of the server's pick for the pages it adds itself: that one
    walks the pages in its own sort order, takes a section of any type, and a later
    match replaces an earlier one until a page leaves both slots filled. On a store
    with one header section and one footer section the two agree.
    """
    parents: "dict[str, dict[str, object]]" = {}
    for page in validated_pages(content):
        for item in page.get("layout") or []:
            if not isinstance(item, dict) or item.get("section_type") != "parent":
                continue
            slot = chrome_slot_of(item, content)
            if slot is not None and slot not in parents:
                parents[slot] = item
    return parents


def _chrome_copy(parent: "dict[str, object]", slot: str) -> "dict[str, object]":
    """A child copy of the slot's parent section: same id, widget, template and settings.

    The server keeps one record per section id and takes it from the parent copy, so a
    child that differed in widget, template or settings would be silently overwritten.
    """
    section = {
        "id": parent["id"],
        "instance_id": str(uuid.uuid4()),
        "widget": parent["widget"],
        "section_type": "child",
        "display_sequence": SEQUENCE_BY_SLOT[slot],
    }
    if "settings" in parent:
        section["settings"] = copy.deepcopy(parent["settings"])
    if parent.get("template_id") is not None:
        section["template_id"] = parent["template_id"]
    # The write schema requires a status on every section; a parent pulled without one
    # (an older record) gives the copy the default a new section gets.
    status = parent.get("status")
    section["status"] = status if isinstance(status, str) else "published"
    return section


def _raise_if_errors(errors: "list[str]") -> None:
    if errors:
        raise UsageError("Page is invalid: " + " ".join(errors[:5]))


def _build_meta(seo: "dict[str, str] | None", existing: object = None) -> "dict[str, object] | None":
    if seo is None:
        return copy.deepcopy(existing) if isinstance(existing, dict) else None
    merged = {field: "" for field in META_FIELDS}
    if isinstance(existing, dict):
        for field in META_FIELDS:
            value = existing.get(field)
            if isinstance(value, str):
                merged[field] = value
    merged.update(seo)
    return merged


ContentSettingsBuilder = "Callable[[str], tuple[dict[str, object] | None, str | None]]"


def _pages_by_section(content: "dict[str, object]") -> "dict[str, list[dict[str, object]]]":
    """Section id → the pages carrying a copy of it, in workspace order."""
    found: "dict[str, list[dict[str, object]]]" = {}
    for page in validated_pages(content):
        for item in page.get("layout") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.setdefault(item["id"], []).append(page)
    return found


def _copy_default_layout(
    default_page: "dict[str, object]", with_chrome: bool, content: "dict[str, object]", alias: str = ""
) -> "tuple[list[dict[str, object]], dict[str, str], list[dict[str, object]]]":
    """The default template's layout as the start of a new alternative template.

    Returns the copied layout, the new id → origin id map, and the blocks kept shared.
    Every section becomes a new one (new id), so the two templates can differ from then
    on — the page's own content widget (the product block, the listing) included, even
    when the server stores it as a composite: a `parent` with no other copy, or a listing
    the default category template shares with the search page. A template sharing it
    could never get a product block or a listing of its own, and 'section set' on it would
    rewrite the default template and the search page too; every alternative template in
    the stores measured has its own. Only a block the default template really shares with
    other pages (a subscription, an announcement bar, a FAQ copied from the main page)
    stays a child copy of that section, and is named in the third value. The header and
    footer slots are copied by the caller.
    """
    copied: "list[dict[str, object]]" = []
    origins: "dict[str, str]" = {}
    shared: "list[dict[str, object]]" = []
    content_widgets = {widget for widget, _sequence in CONTENT_SECTIONS_BY_ALIAS.get(alias, ())}
    pages_by_section = _pages_by_section(content)
    for item in default_page.get("layout") or []:
        if not isinstance(item, dict):
            continue
        slot = chrome_slot_of(item, content)
        if slot is not None:
            if with_chrome:
                copied.append(_chrome_copy(item, slot))
            continue
        entry = copy.deepcopy(item)
        entry["instance_id"] = str(uuid.uuid4())
        others = [
            page
            for page in pages_by_section.get(str(item.get("id")), [])
            if page.get("id") != default_page.get("id")
        ]
        if (
            item.get("section_type") in ("parent", "child")
            and item.get("widget") not in content_widgets
            and others
        ):
            entry["section_type"] = "child"
            shared.append(
                {
                    "section_id": item.get("id"),
                    "widget": item.get("widget"),
                    # By alias where it names one page; an alternative template by its title.
                    "also_on": [
                        page_alias(page) if page_alias(page) and page.get("variant_type") == "default"
                        else page.get("title") or page.get("id")
                        for page in others
                    ],
                }
            )
        else:
            entry["id"] = str(uuid.uuid4())
            entry["section_type"] = "usual"
            if isinstance(item.get("id"), str):
                origins[entry["id"]] = item["id"]
        if not isinstance(entry.get("status"), str):
            entry["status"] = "published"
        copied.append(entry)
    return copied, origins, shared


def _content_sections(
    alias: str, title: str, build: "ContentSettingsBuilder | None"
) -> "tuple[list[dict[str, object]], list[tuple[str, str]]]":
    """The system page's own content widgets, as new sections; and what could not be built."""
    sections: "list[dict[str, object]]" = []
    missing: "list[tuple[str, str]]" = []
    for widget, sequence in CONTENT_SECTIONS_BY_ALIAS.get(alias, ()):
        if build is None:
            missing.append((widget, "the storefront widget schema was not consulted"))
            continue
        settings, problem = build(widget)  # type: ignore[operator]
        if settings is None:
            missing.append((widget, problem or "its settings could not be built"))
            continue
        settings = copy.deepcopy(settings)
        if widget == "YandexKit.RichText" and "titleText" in settings:
            settings["titleText"] = title  # the server seeds these pages with their title
        sections.append(
            {
                "id": str(uuid.uuid4()),
                "instance_id": str(uuid.uuid4()),
                "widget": widget,
                "section_type": "usual",
                "settings": settings,
                "display_sequence": sequence,
                "status": "published",
            }
        )
    return sections, missing


def add_page(
    workspace,
    title: str,
    pattern: "str | None",
    alias: "str | None",
    status: str,
    seo: "dict[str, str] | None",
    with_chrome: bool,
    *,
    content_settings: "ContentSettingsBuilder | None" = None,
    copy_layout_from: "str | None" = None,
) -> "dict[str, object]":
    """Insert one new page into the workspace with server-mirroring derivation.

    ``variant_type`` and ``exact`` are computed, never taken from the caller. An alias
    page is created only when it can exist: as the missing default of its alias, or as
    an extra variant of the two multi-variant aliases — and never for the aliases in
    ALIASES_NOT_ADDABLE.

    A system page gets its content widget(s) from CONTENT_SECTIONS_BY_ALIAS, built by
    ``content_settings`` (widget name → settings, or None and the reason); without it,
    or when a widget cannot be built, the page stays empty between header and footer and
    the report says so. ``copy_layout_from="default"`` starts a new productCard /
    categoryAndCollection template from the layout of the default one instead.
    """
    alias = (alias or "").strip()
    if alias:
        if alias not in ALIAS_PATTERNS:
            known = ", ".join(sorted(ALIAS_PATTERNS))
            raise UsageError(f"Unknown alias {alias!r}; known aliases: {known}.")
        if alias in ALIASES_NOT_ADDABLE:
            raise UsageError(
                f"'page add' does not create the {alias!r} page: nothing on the platform adds it "
                f"to a store, and its content widget {ALIASES_NOT_ADDABLE[alias]} is not in the "
                "storefront widget schema, so the page could only be made empty. A store that "
                "already has it can edit it with 'page set' like any other page."
            )
        canonical = ALIAS_PATTERNS[alias]
        if pattern is not None and normalize_pattern(pattern, alias) != canonical:
            raise UsageError(
                f"A page with alias {alias!r} always gets its canonical pattern {canonical!r} — "
                "a newer server writes that one whatever is sent, an older one refuses any other "
                "one; omit --pattern."
            )
        pattern = canonical
        has_default = any(
            page_alias(page) == alias and page.get("variant_type") == "default"
            for page in validated_pages(workspace.content)
        )
        if has_default and alias not in MULTI_VARIANT_ALIASES:
            raise UsageError(
                f"A page with alias {alias!r} already exists and only one is allowed. "
                "Extra variants exist only for productCard and categoryAndCollection — "
                "those are the page templates assigned to products and collections."
            )
        variant_type = "alternative" if has_default else "default"
    else:
        if pattern is None or not pattern.strip():
            raise UsageError("A custom page requires --pattern (for example /promo-august).")
        pattern = normalize_pattern(pattern)
        variant_type = "default"

    default_page = None
    if copy_layout_from is not None:
        if copy_layout_from != "default":
            raise UsageError("--copy-layout-from takes only 'default': the store's default template of this alias.")
        if alias not in MULTI_VARIANT_ALIASES or variant_type != "alternative":
            raise UsageError(
                "--copy-layout-from default applies only to a new productCard or categoryAndCollection "
                "template — an extra variant next to the default one."
            )
        default_page = next(
            page
            for page in validated_pages(workspace.content)
            if page_alias(page) == alias and page.get("variant_type") == "default"
        )

    page: "dict[str, object]" = {
        "id": str(uuid.uuid4()),
        "title": title,
        "pattern": pattern,
        "exact": is_static_pattern(pattern),
        "variant_type": variant_type,
        "status": status,
        "layout": [],
    }
    if alias:
        page["alias"] = alias
    meta = _build_meta(seo)
    if meta is not None:
        page["meta"] = meta

    chrome_copied: "list[str]" = []
    chrome_templates: "dict[str, object]" = {}
    chrome_note = None
    if with_chrome and alias in ALIASES_WITHOUT_CHROME:
        with_chrome = False
        chrome_note = (
            f"Header and footer were not copied: the server keeps the {alias!r} page without them."
        )
    layout = page["layout"]
    assert isinstance(layout, list)
    chrome: "dict[str, dict[str, object]]" = {}
    if with_chrome:
        parents = find_chrome_parents(workspace.content)
        for slot in ("header", "footer"):
            parent = parents.get(slot)
            if parent is not None:
                chrome[slot] = _chrome_copy(parent, slot)
                # Reported by slot, so a custom header reads as the header it is.
                chrome_copied.append(CHROME_WIDGET_BY_SLOT[slot])
                if parent.get("widget") == MYSTIQUE_WIDGET:
                    chrome_templates[CHROME_WIDGET_BY_SLOT[slot]] = parent.get("template_id")

    content_added: "list[dict[str, object]]" = []
    missing: "list[tuple[str, str]]" = []
    origins: "dict[str, str]" = {}
    shared: "list[dict[str, object]]" = []
    if default_page is not None:
        copied, origins, shared = _copy_default_layout(default_page, False, workspace.content, alias)
        shared_ids = {entry["section_id"] for entry in shared}
        content_added = [
            {"section_id": entry["id"], "widget": entry["widget"]} for entry in copied if entry["id"] not in shared_ids
        ]
        body = copied
    else:
        body, missing = _content_sections(alias, title, content_settings) if alias else ([], [])
        content_added = [{"section_id": entry["id"], "widget": entry["widget"]} for entry in body]
    # In sequence order, as the server returns layouts: the header at 1, the footer at 9999.
    layout.extend(
        sorted(
            [*([chrome["header"]] if "header" in chrome else []), *body, *([chrome["footer"]] if "footer" in chrome else [])],
            key=lambda entry: entry.get("display_sequence") if isinstance(entry.get("display_sequence"), int) else 0,
        )
    )

    pages = workspace.content["pages"]
    assert isinstance(pages, list)
    pages.append(page)
    errors = page_gate_errors(workspace)
    if errors:
        pages.pop()
        _raise_if_errors(errors)
    if origins:
        from kitlib import workspace as workspace_engine  # imports this module; resolved at call time

        # The copies start as the default template's server bytes: the push gate judges them
        # against that section, so its old drift is not taken for the edit's.
        workspace_engine.record_copied_sections(workspace, origins)
    report: "dict[str, object]" = {
        "page_id": page["id"],
        "title": title,
        "pattern": pattern,
        "alias": alias or None,
        "variant_type": variant_type,
        "status": status,
        "exact": page["exact"],
        "chrome_copied": chrome_copied,
        "content_widgets_added": content_added,
        "layout_count": len(layout),
        "pages_count": len(pages),
        "note": (
            "The page exists only in the workspace until a confirmed 'content push'. "
            "Select it in section commands by --page-id."
        ),
    }
    if not alias:
        report["reachability_note"] = reachability_note(page)
    if default_page is not None:
        report["layout_copied_from"] = {"page_id": default_page["id"], "title": default_page.get("title")}
        if shared:
            report["shared_sections"] = shared
            report["shared_sections_note"] = (
                "These blocks are not copies of their own: each is one section the default template "
                "shares with the pages in also_on, and the new template now shows it too. 'section set' "
                "on one changes it on all of those pages. To give the new template a block of its own, "
                f"take the shared one off it ('section remove --id <section_id> --page-id {page['id']}') "
                "and 'section add' a new one."
            )
    if chrome_templates:
        # The store's header or footer is its own template; the new page shows that one.
        report["chrome_custom_templates"] = chrome_templates
    if chrome_note:
        report["chrome_note"] = chrome_note
    warnings = [
        warning
        for warning in (
            ignored_meta_warning(page),
            seo_h1_warning(page),
            route_owner_warning(workspace, page, None),
            new_system_page_route_warning(workspace, page, None),
        )
        if warning is not None
    ]
    if missing:
        names = ", ".join(f"{widget} ({reason})" for widget, reason in missing)
        if alias in MULTI_VARIANT_ALIASES and variant_type == "alternative":
            assigned = _ASSIGNED_ITEMS[alias]
            warnings.append(
                f"The template is empty: without {', '.join(widget for widget, _ in missing)} the "
                f"{assigned} assigned to it open as an empty page. Not added: {names}. Use "
                "--copy-layout-from default, or add the widget with 'section add' once the "
                "storefront schema is available."
            )
        else:
            warnings.append(
                f"The {alias!r} page will be empty between its header and footer: its content "
                f"widget could not be added — {names}. Pass --bff-url for this store and run "
                "'schema fetch', then add it with 'section add'."
            )
    elif alias in MULTI_VARIANT_ALIASES and variant_type == "alternative" and default_page is None:
        report["layout_note"] = (
            "Only the base content widget was placed. To start this template from the store's "
            "current default template instead, add it with --copy-layout-from default."
        )
    if warnings:
        report["warnings"] = warnings
    return report


def set_page(
    workspace,
    page: "dict[str, object]",
    title: "str | None",
    pattern: "str | None",
    status: "str | None",
    seo: "dict[str, str] | None",
    clear_meta: bool,
) -> "dict[str, object]":
    """Change the human-owned fields of one page: title, pattern (custom pages), status, SEO."""
    if seo is not None and clear_meta:
        raise UsageError("--clear-meta cannot be combined with SEO fields.")
    before = copy.deepcopy(page)
    changed: "list[str]" = []
    if title is not None and title != page.get("title"):
        page["title"] = title
        changed.append("title")
    if pattern is not None:
        alias = page_alias(page)
        if alias:
            raise UsageError(
                f"The pattern of the aliased system page {alias!r} is fixed "
                f"({page.get('pattern')!r}) and belongs to the server: an older backend refuses "
                "any other one with 400, a newer one writes its own whatever is sent."
            )
        normalized = normalize_pattern(pattern)
        if normalized != page.get("pattern"):
            page["pattern"] = normalized
            page["exact"] = is_static_pattern(normalized)
            changed.append("pattern")
    if status is not None and status != page.get("status"):
        page["status"] = status
        changed.append("status")
    if clear_meta:
        if "meta" in page:
            del page["meta"]
            changed.append("meta")
    elif seo is not None:
        meta = _build_meta(seo, page.get("meta"))
        if meta != page.get("meta"):
            page["meta"] = meta
            changed.append("meta")

    if not changed:
        return {"page_id": page["id"], "changed": [], "message": "Nothing to change."}
    errors = page_gate_errors(workspace)
    if errors:
        page.clear()
        page.update(before)
        _raise_if_errors(errors)
    report: "dict[str, object]" = {
        "page_id": page["id"],
        "alias": page_alias(page) or None,
        "title": page.get("title"),
        "pattern": page.get("pattern"),
        "status": page.get("status"),
        "changed": changed,
    }
    base_page = next(
        (candidate for candidate in validated_pages(workspace.base) if candidate["id"] == page["id"]),
        None,
    )
    warnings = [
        warning
        for warning in (
            ignored_meta_warning(page) if "meta" in changed else None,
            seo_h1_warning(page) if "meta" in changed and _seo_h1(before) != _seo_h1(page) else None,
            route_owner_warning(workspace, page, base_page) if "pattern" in changed else None,
        )
        if warning is not None
    ]
    if warnings:
        report["warnings"] = warnings
    return report


_ASSIGNED_ITEMS = {"productCard": "products", "categoryAndCollection": "categories and collections"}


def reachability_note(page: "dict[str, object]") -> str:
    """What makes a new custom page reachable: nothing yet, and this skill cannot add a menu item."""
    return (
        f"{_page_label(page)} is new and nothing links to it: it is in no menu, and the storefront "
        "only reaches it by typing its address. Menus are not editable from here. Either put a "
        "link to it into a section in this same edit (a button or a tile), or have the user add a "
        "menu item in the cabinet (section «Меню») right after the push — not before it: the page "
        "does not exist until the push, and a menu edit is live at once."
    )


def alternative_removal_warning(page: "dict[str, object]", *, no_publish: bool = False) -> "str | None":
    """What removing a product/collection page template does to its assignments, else None.

    An `alternative` productCard / categoryAndCollection page is a template merchants
    assign to individual products or collections. An assignment points at the page
    variant and is cleared when the variant goes, so the write that drops the page clears them, and a
    page restored later (a version revert, or the copy the no-publish write re-activates)
    comes back without them. The assignment is a separate entity this skill cannot read
    or write, so the only honest move is to say it before the confirmation.
    """
    alias = page_alias(page)
    if alias not in _ASSIGNED_ITEMS or page.get("variant_type") != "alternative":
        return None
    items = _ASSIGNED_ITEMS[alias]
    warning = (
        f"{_page_label(page)} is a page template merchants assign to individual {items}. "
        f"Removing it clears those assignments: every one of the {items} assigned to it falls "
        "back to the default template. Neither reverting a content version nor --no-publish "
        "brings an assignment back — the server clears it in the same write, and a restored "
        f"page returns unassigned. Reassigning is possible only in the cabinet, one by one. "
        "Tell the user before confirming."
    )
    if no_publish:
        warning += (
            " This write is therefore NOT a safe preview: the proposal is not published, but the "
            "assignments are cleared all the same."
        )
    return warning


def remove_page(workspace, page: "dict[str, object]") -> "dict[str, object]":
    """Remove one page from the workspace, spelling out exactly what disappears.

    A default page with an alias is refused outright — the server rejects the whole
    write with 400. Everything else is removed together with its section copies. A page
    a menu item links to is not deleted by the server at all: it refuses the whole write
    with 400 and the item has to go first, in the cabinet. Which pages the menu links to
    is not readable from here, so the report says so. The push still requires
    --allow-page-removal plus explicit confirmation.
    """
    if is_protected_page(page):
        alias = page_alias(page)
        hint = (
            f" To take it off the storefront, hide it instead: page set --alias {alias} --status hidden."
            if alias in HIDDEN_ALLOWED_ALIASES
            else ""
        )
        raise UsageError(
            f"{_page_label(page)} is a default system page and can never be removed — "
            f"the server rejects the whole write with 400.{hint}"
        )

    counts: "dict[str, int]" = {}
    for other in validated_pages(workspace.content):
        for item in other.get("layout") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                counts[item["id"]] = counts.get(item["id"], 0) + 1
    gone_entirely: "list[dict[str, object]]" = []
    copies_removed: "list[dict[str, object]]" = []
    for item in page.get("layout") or []:
        if not isinstance(item, dict):
            continue
        entry = {"section_id": item.get("id"), "widget": item.get("widget")}
        if counts.get(str(item.get("id")), 1) > 1:
            copies_removed.append(entry)
        else:
            gone_entirely.append(entry)

    pages = workspace.content["pages"]
    assert isinstance(pages, list)
    pages[:] = [candidate for candidate in pages if candidate is not page]
    report: "dict[str, object]" = {
        "page_id": page["id"],
        "title": page.get("title"),
        "alias": page_alias(page) or None,
        "pattern": page.get("pattern"),
        "sections_gone_entirely": gone_entirely,
        "section_copies_removed": copies_removed,
        "pages_count": len(pages),
        "warning": (
            "Removal happens on push as part of the full replace: the page disappears with "
            "its sections. A page without an alias is dropped silently — the only guard is "
            "the local one. If a menu item links to this page, the server refuses the whole "
            "write with 400 and writes nothing. "
            "The push will require --allow-page-removal and explicit confirmation."
        ),
        "menu_note": (
            "Whether a menu item links to this page cannot be checked from here: the storefront "
            "menu is not part of the constructor content and this API exposes no menu "
            "operations. Say so plainly before confirming. If the push answers 400 naming "
            "this page as linked to a menu item, nothing was written: the user removes that "
            "item in the cabinet (section «Меню»), and the same push is repeated — a menu "
            "edit does not change the content version, so the workspace stays valid. Warn the "
            "user before that menu edit: a menu is not versioned, so the item disappears from "
            "the live storefront at once, and neither --no-publish nor reverting a content "
            "version brings it back."
        ),
    }
    assignment_warning = alternative_removal_warning(page)
    if assignment_warning is not None:
        report["assignment_warning"] = assignment_warning
    return report
