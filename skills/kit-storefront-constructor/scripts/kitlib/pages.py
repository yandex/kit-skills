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

Everything here mirrors the server-side validation chain (``validatePages`` and
``ReplaceAllPages`` in the Core backend) so a human gets an actionable local refusal
instead of a bare 400. What cannot be checked locally — a pattern colliding with an
SEO redirect — stays a server-side 400 and is documented as such.
"""

from __future__ import annotations

import copy
import uuid

from kitlib.common import UsageError, validated_pages

# Canonical route pattern per alias. A page with an alias must use exactly this
# pattern; a page without an alias must not squat on one of these patterns —
# the server would silently assign it the alias.
ALIAS_PATTERNS = {
    "404": "/not-found",
    "accountLoyalty": "/account/loyalty-program",
    "accountNewsletters": "/account/newsletters",
    "accountOrderDetails": "/account/orders/:orderId",
    "accountOrders": "/account/orders",
    "accountProfile": "/account/profile",
    "blog": "/blog",
    "blogEntry": "/blog/:blogEntrySlug",
    "cart": "/cart",
    "catalog": "/catalog",
    "categoryAndCollection": "/catalog/:categorySlug~/collections/:collectionSlug",
    "delivery": "/delivery",
    "details": "/details",
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
}
ALIAS_BY_PATTERN = {pattern: alias for alias, pattern in ALIAS_PATTERNS.items()}

# Only these aliases may have several pages (one default + alternatives).
MULTI_VARIANT_ALIASES = {"categoryAndCollection", "productCard"}

# `hidden` is honored only for these aliases; "" stands for a custom page without
# an alias. Every other page is always effectively published.
HIDDEN_ALLOWED_ALIASES = {
    "",
    "accountLoyalty",
    "blog",
    "blogEntry",
    "catalog",
    "favouriteProducts",
    "giftCard",
    "giftCardQuestions",
    "loyaltyLanding",
    "loyaltyOffer",
    "subscription",
}

PAGE_STATUSES = ("published", "hidden")
PATTERN_DELIMITER = "~"
META_FIELDS = ("seo_title", "seo_h1", "seo_description")

# Composite chrome copied onto every page by the cabinet; a page created without
# them renders without the store's header and footer.
CHROME_WIDGETS = ("YandexKit.Header", "YandexKit.Footer")
HEADER_SEQUENCE = 1
FOOTER_SEQUENCE = 9999


def page_alias(page: "dict[str, object]") -> str:
    """The page alias normalized to '' for custom pages."""
    alias = page.get("alias")
    return alias if isinstance(alias, str) else ""


def normalize_pattern(pattern: str, alias: str = "") -> str:
    """Mirror the server's pattern normalization: leading slash on, one trailing slash off."""
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


def is_protected_page(page: "dict[str, object]") -> bool:
    """A default-variant page with an alias can never be dropped: the server 400s the write."""
    return bool(page_alias(page)) and page.get("variant_type") == "default"


def _page_field_errors(page: "dict[str, object]", label: str) -> "list[str]":
    """Structural rules for one page, mirrored from the server validators."""
    errors: "list[str]" = []
    title = page.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{label}: title must be a non-empty string.")

    alias = page_alias(page)
    raw_pattern = page.get("pattern")
    pattern = raw_pattern if isinstance(raw_pattern, str) else ""
    normalized = normalize_pattern(pattern, alias)
    if not normalized:
        errors.append(f"{label}: pattern must not be empty.")
    elif alias:
        if alias not in ALIAS_PATTERNS:
            known = ", ".join(sorted(ALIAS_PATTERNS))
            errors.append(f"{label}: unknown alias {alias!r}; known aliases: {known}.")
        elif normalized != ALIAS_PATTERNS[alias]:
            errors.append(
                f"{label}: a page with alias {alias!r} must use its canonical pattern "
                f"{ALIAS_PATTERNS[alias]!r}, not {pattern!r}."
            )
    else:
        if normalized in ALIAS_BY_PATTERN:
            reserved_for = ALIAS_BY_PATTERN[normalized]
            errors.append(
                f"{label}: pattern {normalized!r} is reserved for the system page with alias "
                f"{reserved_for!r}; the server would silently turn this page into it. "
                "Choose another pattern."
            )
        if PATTERN_DELIMITER in normalized:
            errors.append(
                f"{label}: multi-pattern routes ('{PATTERN_DELIMITER}') exist only on the "
                "system categoryAndCollection page; a custom page gets exactly one pattern."
            )

    status = page.get("status")
    if status not in PAGE_STATUSES:
        errors.append(f"{label}: status must be one of: {', '.join(PAGE_STATUSES)}.")
    elif status == "hidden" and alias not in HIDDEN_ALLOWED_ALIASES:
        allowed = ", ".join(sorted(a for a in HIDDEN_ALLOWED_ALIASES if a))
        errors.append(
            f"{label}: pages with alias {alias!r} cannot be hidden; hidden is supported only "
            f"for custom pages (no alias) and aliases: {allowed}. Others are always published."
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


def page_gate_errors(workspace) -> "list[str]":
    """Every page-level rule the server enforces, checked offline before the write.

    Drift tolerance: rules run only against pages the edit added or changed — a page
    carried through byte-for-byte came from the server and is never re-litigated.
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
        if base_page is not None and page_alias(page):
            base_pattern = base_page.get("pattern")
            if page.get("pattern") != base_pattern:
                errors.append(
                    f"{_page_label(page)}: the pattern of an aliased system page is fixed "
                    f"({base_pattern!r}) and cannot be changed."
                )

    # Pattern uniqueness: one page per pattern, except the two multi-variant aliases.
    # Only groups touched by the edit block — pre-existing drift does not.
    by_pattern: "dict[str, list[dict[str, object]]]" = {}
    for page in work_pages:
        raw_pattern = page.get("pattern")
        pattern = normalize_pattern(raw_pattern if isinstance(raw_pattern, str) else "", page_alias(page))
        by_pattern.setdefault(pattern, []).append(page)
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


def find_chrome_parents(content: "dict[str, object]") -> "dict[str, dict[str, object]]":
    """The parent copy of each composite chrome section (header, footer), if present."""
    parents: "dict[str, dict[str, object]]" = {}
    for page in validated_pages(content):
        for item in page.get("layout") or []:
            if not isinstance(item, dict):
                continue
            widget = item.get("widget")
            if widget in CHROME_WIDGETS and item.get("section_type") == "parent" and widget not in parents:
                parents[widget] = item
    return parents


def _chrome_copy(parent: "dict[str, object]") -> "dict[str, object]":
    section = {
        "id": parent["id"],
        "instance_id": str(uuid.uuid4()),
        "widget": parent["widget"],
        "section_type": "child",
        "display_sequence": FOOTER_SEQUENCE if parent.get("widget") == "YandexKit.Footer" else HEADER_SEQUENCE,
    }
    if "settings" in parent:
        section["settings"] = copy.deepcopy(parent["settings"])
    if parent.get("template_id") is not None:
        section["template_id"] = parent["template_id"]
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


def add_page(
    workspace,
    title: str,
    pattern: "str | None",
    alias: "str | None",
    status: str,
    seo: "dict[str, str] | None",
    with_chrome: bool,
) -> "dict[str, object]":
    """Insert one new page into the workspace with server-mirroring derivation.

    ``variant_type`` and ``exact`` are computed, never taken from the caller. An alias
    page is created only when it can exist: as the missing default of its alias, or as
    an extra variant of the two multi-variant aliases.
    """
    alias = (alias or "").strip()
    if alias:
        if alias not in ALIAS_PATTERNS:
            known = ", ".join(sorted(ALIAS_PATTERNS))
            raise UsageError(f"Unknown alias {alias!r}; known aliases: {known}.")
        canonical = ALIAS_PATTERNS[alias]
        if pattern is not None and normalize_pattern(pattern, alias) != canonical:
            raise UsageError(
                f"A page with alias {alias!r} must use its canonical pattern {canonical!r}; "
                "omit --pattern to use it."
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
    if with_chrome:
        parents = find_chrome_parents(workspace.content)
        layout = page["layout"]
        assert isinstance(layout, list)
        for widget in CHROME_WIDGETS:
            parent = parents.get(widget)
            if parent is not None:
                layout.append(_chrome_copy(parent))
                chrome_copied.append(widget)

    pages = workspace.content["pages"]
    assert isinstance(pages, list)
    pages.append(page)
    errors = page_gate_errors(workspace)
    if errors:
        pages.pop()
        _raise_if_errors(errors)
    return {
        "page_id": page["id"],
        "title": title,
        "pattern": pattern,
        "alias": alias or None,
        "variant_type": variant_type,
        "status": status,
        "exact": page["exact"],
        "chrome_copied": chrome_copied,
        "layout_count": len(page["layout"]),  # type: ignore[arg-type]
        "pages_count": len(pages),
        "note": (
            "The page exists only in the workspace until a confirmed 'content push'. "
            "Select it in section commands by --page-id."
        ),
    }


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
                f"({ALIAS_PATTERNS.get(alias, page.get('pattern'))!r}) and cannot be changed."
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
    return {
        "page_id": page["id"],
        "alias": page_alias(page) or None,
        "title": page.get("title"),
        "pattern": page.get("pattern"),
        "status": page.get("status"),
        "changed": changed,
    }


def remove_page(workspace, page: "dict[str, object]") -> "dict[str, object]":
    """Remove one page from the workspace, spelling out exactly what disappears.

    A default page with an alias is refused outright — the server rejects the whole
    write with 400. Everything else is removed together with its section copies; the
    server also deletes menu items pointing at the page. The push still requires
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
    return {
        "page_id": page["id"],
        "title": page.get("title"),
        "alias": page_alias(page) or None,
        "pattern": page.get("pattern"),
        "sections_gone_entirely": gone_entirely,
        "section_copies_removed": copies_removed,
        "pages_count": len(pages),
        "warning": (
            "Removal happens on push as part of the full replace: the page disappears with "
            "its sections, and the server also deletes menu items pointing at it. A page "
            "without an alias is dropped silently — the only guard is the local one. "
            "The push will require --allow-page-removal and explicit confirmation."
        ),
        "menu_note": (
            "Which menu items point at this page cannot be listed from here: the storefront "
            "menu is not part of the constructor content and this API exposes no menu "
            "operations. Say so plainly and have the user check the menu in the cabinet "
            "before confirming — the deletion is silent and there is no rollback."
        ),
    }
