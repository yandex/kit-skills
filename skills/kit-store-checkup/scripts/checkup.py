#!/usr/bin/env python3
"""Яндекс.Кит store check-up — a read-only recurring review of a store.

Answers the questions a merchant asks again and again: what is published but
cannot be bought, what is about to run out, which orders have been waiting,
which listings are incomplete, and what the store template left behind — demo
blog articles, the seeded category, empty categories and collections.

The whole point is that the arithmetic is code, not prose. Availability is
`quantity - reserved`, summed across warehouses; order age is measured against
`created_at`; coverage is tracked while paging. A model asked to do this by hand
gets `reserved` wrong, reads one page, and reports «всё в порядке».

Checks are pure functions registered in CHECKS. A check declares which
collections it needs, and the runner reads exactly the union of those — so a
new check costs a line in the registry, and only the checks that were asked for
cost a request.

This client has no write verbs at all. It cannot change the store even if asked
to — fixes are emitted as exact commands for the `yandex-kit-cabinet` skill to
run under its own confirmation gate.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import platform
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_BASE_URL = "https://api.kit.yandex.net"
DEFAULT_TOKEN_FILE = Path.home() / ".yandex-kit-skills" / "kit_api.token"
SKILL_HEADER: str = "X-Skill"
SKILL_NAME: str = "kit-store-checkup"
SKILL_PROMPT_HEADER: str = "X-Skill-Prompt"
SKILL_PROMPT_MAX_LENGTH = 1000
SKILL_SESSION_HEADER: str = "X-Skill-Session"
SKILL_MODEL_HEADER: str = "X-Skill-Model"
SKILL_HARNESS_HEADER: str = "X-Skill-Harness"
SKILL_OS_HEADER: str = "X-Skill-OS"
# Model and harness arrive as free text from the caller, so they are trimmed to a
# length that fits a name and stripped of anything that could forge a header.
SKILL_META_MAX_LENGTH = 100
TELEMETRY_OPT_OUT_VARIABLE: str = "YANDEX_KIT_SKILLS_TELEMETRY_DISABLED"
PAGE_SIZE = 100
MAX_PAGES = 100
DEFAULT_STOCK_THRESHOLD = 10
DEFAULT_STALE_HOURS = 24
DEFAULT_STUCK_HOURS = 72
TIMEOUT = 60
# Public API limit is 10 rps per store. A review walks several collections back
# to back, so a 429 mid-walk is ordinary; it is retried rather than turned into
# partial coverage. Same budget as the cabinet client.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 10.0
# Same shape as every other skill in this repo. This client sends a bearer
# token, so anything derived from a response can echo one back.
# One prefix contract, shared by the entry surfaces and by redaction: a token is
# only accepted in the shape `redact_secrets` below can recognise, so a token
# this client holds can never be one it fails to hide.
TOKEN_PREFIX = "yakit_"
TOKEN_PATTERN = re.compile(TOKEN_PREFIX + r"[A-Za-z0-9._~-]+")

EXIT_OK = 0
EXIT_API_ERROR = 1
EXIT_USAGE = 2

# Orders in this status are waiting for the merchant, not for the buyer or a carrier.
NEEDS_MERCHANT_ACTION = "WAIT_FOR_CONFIRMATION"

# An order in one of these statuses is finished: nobody is waiting for anything.
TERMINAL_ORDER_STATUSES = frozenset(
    {"DELIVERED", "COMPLETED", "CANCELLED", "FULL_REFUND", "PARTIAL_REFUND"}
)
# States the platform passes through on its own, in minutes. An old order in one
# of them is not «in progress», it is stuck, so it outranks the rest.
TRANSIENT_ORDER_STATUSES = frozenset(
    {"CREATING_INITIAL_RECEIPT", "CREATING_FINAL_RECEIPTS", "CANCELLATION_IN_PROGRESS"}
)

# Collections a check can ask for. The runner reads the union of what the
# selected checks need, so an unused collection costs nothing.
COLLECTION_VARIANTS = "variants"
# Hidden variants are not on the storefront and no check reports on them; they
# are read only where a hidden variant changes the answer about something else.
COLLECTION_HIDDEN_VARIANTS = "hidden_variants"
COLLECTION_ORDERS = "orders"
COLLECTION_BLOGS = "blogs"
COLLECTION_CATEGORIES = "categories"
COLLECTION_PRODUCTS = "products"
COLLECTION_COLLECTIONS = "collections"

# A new store arrives with demo blog articles. They are recognised by how they
# were made, not by what they say: each has its own id for an address, none is
# attached to a category or a collection, and the whole batch shares one
# publication instant to the millisecond. This many articles on one instant —
# hidden and rewritten ones included — make a batch.
TEMPLATE_BLOG_BATCH = 3

# The category the platform seeds into every new store. It is not the merchant's,
# and template shelves that read from it come out empty or answer 404.
SEEDED_CATEGORY_TITLE = re.compile(r"^\s*категория\s+\d+-го\s+уровня\s*$", re.IGNORECASE)
SEEDED_CATEGORY_SLUG = re.compile(r"^kategoriya-\d+-go-urovnya(?:$|-)")

# What the storefront prints for a category whose SEO fields are empty. Quoted
# in evidence because the promises in it — delivery, payment, «большой
# ассортимент» — are the storefront's, not the merchant's, and hold whether or
# not delivery and payment are set up at all.
CATEGORY_SEO_DESCRIPTION_TEMPLATE = (
    "«{title} в официальном интернет-магазине … по низкой цене. Быстрая доставка. "
    "Акции и скидки. Удобная оплата. Большой ассортимент»"
)
CATEGORY_SEO_TITLE_TEMPLATE = "«{title} купить в официальном интернет-магазине … по выгодной цене с доставкой»"


class ApiError(RuntimeError):
    """A read failed; the caller decides whether that makes coverage partial."""


def redact_secrets(value: str) -> str:
    """Remove Kit token-shaped values from text.

    This client has no redacting output path of its own — an `ApiError` message
    goes straight to stderr — so redaction has to happen where the text is
    built, not where it is printed.
    """
    return TOKEN_PATTERN.sub("[REDACTED]", value)


# --------------------------------------------------------------------------- checks
# Pure functions. Everything below is testable without a network or a model.


def available_quantity(variant: dict) -> int:
    """Units a buyer can actually purchase: quantity minus reserved, never below zero.

    `reserved` is the trap. A variant with quantity 5 and reserved 5 is visible
    in the catalog and cannot be bought, and a check on `quantity` alone calls
    it healthy.
    """
    total = 0
    for stock in variant.get("stocks") or []:
        if not isinstance(stock, dict):
            continue
        quantity = stock.get("quantity")
        reserved = stock.get("reserved") or 0
        if not isinstance(quantity, int) or not isinstance(reserved, int):
            continue
        total += max(quantity - reserved, 0)
    return total


def is_published(variant: dict) -> bool:
    return variant.get("status") == "PUBLISHED"


def unavailable_published(variants: list) -> list[dict]:
    """Published but unbuyable — the costliest catalog defect, so it leads the report."""
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        if available_quantity(variant) > 0:
            continue
        findings.append(
            {
                "check": "unavailable_published",
                "severity": "high",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "evidence": _stock_evidence(variant),
                "suggested_fix": _hide_command(variant),
            }
        )
    return findings


def low_stock(variants: list, threshold: int) -> list[dict]:
    """Published items still buyable but under the threshold — reorder candidates."""
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        remaining = available_quantity(variant)
        if not 0 < remaining < threshold:
            continue
        findings.append(
            {
                "check": "low_stock",
                "severity": "medium",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "available": remaining,
                "threshold": threshold,
                "evidence": _stock_evidence(variant),
                "suggested_fix": None,
            }
        )
    return findings


def incomplete_listings(variants: list) -> list[dict]:
    """Published items missing an image or a description.

    The description doubles as the page's meta description: the storefront
    takes its opening for the snippet, so a card without one has none.
    """
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        missing = []
        if not (variant.get("media") or []):
            missing.append("media")
        if not str(variant.get("description") or "").strip():
            missing.append("description")
        if not missing:
            continue
        evidence = []
        if "media" in missing:
            evidence.append("нет фото")
        if "description" in missing:
            evidence.append("нет описания — и у страницы товара пустой meta description")
        findings.append(
            {
                "check": "incomplete_listing",
                "severity": "low",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "missing": missing,
                "evidence": "; ".join(evidence),
                "suggested_fix": None,
            }
        )
    return findings


def final_price(variant: dict) -> Decimal | None:
    """The price a buyer actually pays, or None when the card carries no price.

    Prices arrive as decimal strings, so they are compared as Decimal: `"0.00"`
    is falsy as a number and truthy as a string, and float() would round money.
    """
    pricing = variant.get("pricing")
    if not isinstance(pricing, dict):
        return None
    raw = pricing.get("final_price")
    if raw is None:
        raw = pricing.get("price")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def zero_price(variants: list) -> list[dict]:
    """Published with no price or a price of zero — the store is giving it away."""
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        price = final_price(variant)
        if price is not None and price > 0:
            continue
        findings.append(
            {
                "check": "zero_price",
                "severity": "high",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "evidence": "цена не указана" if price is None else f"итоговая цена {price}",
                "suggested_fix": None,
            }
        )
    return findings


def _duplicates(variants: list, field: str, check: str) -> list[dict]:
    """One finding per colliding value, not per variant.

    A SKU shared by three cards is one problem with three sides; three findings
    would triple the size of the report and hide how many distinct collisions
    there really are.
    """
    groups: dict[str, list[dict]] = {}
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        value = str(variant.get(field) or "").strip()
        if not value:
            continue
        groups.setdefault(value, []).append(variant)
    findings = []
    for value, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        findings.append(
            {
                "check": check,
                "severity": "medium",
                field: value,
                "variant_ids": [member.get("id") for member in members],
                "names": [member.get("name") for member in members],
                "evidence": f"{len(members)} товара с одинаковым значением «{value}»",
                "suggested_fix": None,
            }
        )
    return findings


def duplicate_sku(variants: list) -> list[dict]:
    """The same article on several cards — orders, stock and feeds start diverging."""
    return _duplicates(variants, "sku", "duplicate_sku")


def duplicate_barcode(variants: list) -> list[dict]:
    """The same barcode on several cards — the scanner cannot tell them apart."""
    return _duplicates(variants, "barcode", "duplicate_barcode")


def missing_dimensions(variants: list) -> list[dict]:
    """No weight or size — delivery is quoted on a guess, and the guess is billed."""
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        boxes = [box for box in (variant.get("cargo_boxes") or []) if isinstance(box, dict)]
        measured = [
            box
            for box in boxes
            if _positive(box.get("weight"))
            and _positive(box.get("length"))
            and _positive(box.get("width"))
            and _positive(box.get("height"))
        ]
        if measured:
            continue
        findings.append(
            {
                "check": "missing_dimensions",
                "severity": "medium",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "evidence": "нет габаритов и веса" if not boxes else "габариты заполнены не полностью",
                "suggested_fix": None,
            }
        )
    return findings


def empty_seo(variants: list) -> list[dict]:
    """Published cards with empty SEO fields — the page shows default text.

    Not invisibility: with the fields empty the storefront still builds the title
    from the product name and the description from the opening of the product
    description. The finding means «текст по умолчанию», which is why it is low
    and opt-in. Categories are another matter — see `category_template_seo`.
    """
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        missing = [
            field
            for field in ("seo_title", "seo_h1", "seo_description")
            if not str(variant.get(field) or "").strip()
        ]
        if not missing:
            continue
        findings.append(
            {
                "check": "empty_seo",
                "severity": "low",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "missing": missing,
                "suggested_fix": None,
            }
        )
    return findings


def missing_barcode(variants: list) -> list[dict]:
    """No barcode — external systems and shopping agents cannot identify the item."""
    return _missing_field(variants, "barcode", "missing_barcode", "нет штрихкода")


def missing_brand(variants: list) -> list[dict]:
    """No brand — the card loses every brand-based filter and recommendation."""
    return _missing_field(variants, "brand", "missing_brand", "не указан бренд")


def _missing_field(variants: list, field: str, check: str, evidence: str) -> list[dict]:
    findings = []
    for variant in variants:
        if not isinstance(variant, dict) or not is_published(variant):
            continue
        if str(variant.get(field) or "").strip():
            continue
        findings.append(
            {
                "check": check,
                "severity": "low",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "evidence": evidence,
                "suggested_fix": None,
            }
        )
    return findings


def stale_orders(orders: list, stale_hours: int, now: datetime | None = None) -> list[dict]:
    """Orders waiting for the merchant, oldest first.

    The public order list carries no status or date filter, so the selection
    happens here rather than in a query parameter the API would silently ignore.
    """
    moment = now or datetime.now(timezone.utc)
    findings = []
    for order in orders:
        if not isinstance(order, dict) or order.get("status") != NEEDS_MERCHANT_ACTION:
            continue
        created = _parse_time(order.get("created_at"))
        age_hours = None if created is None else (moment - created).total_seconds() / 3600
        findings.append(
            {
                "check": "order_waiting",
                "severity": "high" if (age_hours or 0) >= stale_hours else "medium",
                "order_id": order.get("id"),
                # The API field is `order_number`; there is no `number` on an order.
                "number": order.get("order_number"),
                "created_at": order.get("created_at"),
                "age_hours": None if age_hours is None else round(age_hours, 1),
                "total": order.get("total_final_price"),
                "suggested_fix": _confirm_command(order),
            }
        )
    return _oldest_first(findings)


def stuck_orders(orders: list, stuck_hours: int, now: datetime | None = None) -> list[dict]:
    """Old orders that are neither finished nor waiting for the merchant.

    `order_waiting` covers the one status the merchant can clear with a single
    call. Everything else non-terminal is invisible: nobody is asked to act, and
    the order simply stops moving. A transient platform state outranks the rest,
    because those resolve in minutes or not at all.
    """
    moment = now or datetime.now(timezone.utc)
    findings = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = order.get("status")
        if not isinstance(status, str) or status == NEEDS_MERCHANT_ACTION:
            continue
        if status in TERMINAL_ORDER_STATUSES:
            continue
        created = _parse_time(order.get("created_at"))
        if created is None:
            continue
        age_hours = (moment - created).total_seconds() / 3600
        if age_hours < stuck_hours:
            continue
        findings.append(
            {
                "check": "stuck_order",
                "severity": "high" if status in TRANSIENT_ORDER_STATUSES else "medium",
                "order_id": order.get("id"),
                # The API field is `order_number`; there is no `number` on an order.
                "number": order.get("order_number"),
                "status": status,
                "created_at": order.get("created_at"),
                "age_hours": round(age_hours, 1),
                "total": order.get("total_final_price"),
                "evidence": f"в статусе {status} уже {round(age_hours)} ч",
                # No public endpoint moves an order out of these statuses, so a
                # command here would be an invented fix.
                "suggested_fix": None,
            }
        )
    return _oldest_first(findings)


def is_active_blog(blog: object) -> bool:
    return isinstance(blog, dict) and blog.get("status") == "ACTIVE"


def _blog_start(blog: dict) -> tuple[str | None, datetime | None]:
    """The raw publication date and its parsed instant (None when unparsable)."""
    dates = blog.get("blog_dates")
    raw = dates.get("start_date") if isinstance(dates, dict) else None
    raw = raw if isinstance(raw, str) and raw.strip() else None
    return raw, _parse_time(raw)


def _instant_key(raw: str, moment: datetime | None) -> str:
    """One key per publication instant, whatever offset it was spelled in.

    Instants compare in UTC when the date parses, and as the literal string when
    it does not.
    """
    return moment.astimezone(timezone.utc).isoformat() if moment is not None else raw.strip()


def _untouched_template_shape(blog: dict) -> bool:
    """Still as the template made it: its id for an address and no attachment."""
    blog_id = str(blog.get("id") or "").strip()
    if not blog_id or str(blog.get("slug") or "").strip() != blog_id:
        return False
    return not blog.get("attachment")


def template_blog_posts(blogs: list) -> list[dict]:
    """Active articles the store template seeded, not ones the merchant wrote.

    A store made from a template keeps its demo articles — recipes, motorcycle
    helmets, laptops — on /blog, whatever the store sells. They are found by a
    fingerprint, not by topic. One signal is not enough: `slug` is optional when
    an article is created, so the merchant's own article saved without one also
    gets its id for an address. What the template adds is the batch: all of its
    articles share one publication instant to the millisecond.

    The batch is counted over every article on that instant, hidden or rewritten.
    A merchant who hid ten of twelve, or gave ten their own addresses, changed
    neither their date nor the fact that they came in one batch — counting only
    the untouched ones would let the last two pass. A finding still needs all of
    it on the article itself: on /blog (ACTIVE), its id for an address, no
    attachment, and a batch of at least TEMPLATE_BLOG_BATCH on its instant.
    """
    batch: dict[str, int] = {}
    reported = []
    for blog in blogs:
        if not isinstance(blog, dict):
            continue
        raw, moment = _blog_start(blog)
        if raw is None:
            continue
        key = _instant_key(raw, moment)
        batch[key] = batch.get(key, 0) + 1
        if is_active_blog(blog) and _untouched_template_shape(blog):
            reported.append((key, raw, moment, blog))
    left: dict[str, int] = {}
    for key, _raw, _moment, _blog in reported:
        left[key] = left.get(key, 0) + 1
    findings = []
    for key, raw, moment, blog in reported:
        size = batch[key]
        if size < TEMPLATE_BLOG_BATCH:
            continue
        findings.append(
            {
                "check": "template_blog_post",
                "severity": "medium",
                "blog_id": blog.get("id"),
                "name": blog.get("title"),
                "start_date": raw,
                "evidence": (
                    f"одна из {size} статей с датой публикации {_human_time(moment, raw)}; "
                    f"нетронутыми на /blog из них остались {left[key]}; "
                    "адрес = id; не привязана к категории или подборке"
                ),
                "suggested_fix": _deactivate_blog_command(blog),
            }
        )
    return findings


def blog_scheduled(blogs: list, now: datetime | None = None) -> list[dict]:
    """Active articles whose publication date is still ahead — not on /blog yet.

    The storefront shows an article only from its `start_date`, and says nothing
    about the ones it holds back: an article saved with a date in the future looks
    published in the cabinet and is missing from the blog. Soonest first.
    """
    moment = now or datetime.now(timezone.utc)
    pending = []
    for blog in blogs:
        if not is_active_blog(blog):
            continue
        raw, start = _blog_start(blog)
        if start is None or start <= moment:
            continue
        pending.append((start, raw, blog))
    pending.sort(key=lambda item: item[0])
    return [
        {
            "check": "blog_scheduled",
            "severity": "low",
            "blog_id": blog.get("id"),
            "name": blog.get("title"),
            "start_date": raw,
            "evidence": f"до {_human_time(start, raw)} статьи нет на /blog",
            # Whether the date is a plan or a typo is the merchant's call.
            "suggested_fix": None,
        }
        for start, raw, blog in pending
    ]


def _active_categories(categories: list) -> list[dict]:
    return [
        category
        for category in categories
        if isinstance(category, dict) and category.get("status") == "ACTIVE" and category.get("id")
    ]


def is_seeded_category(category: dict) -> bool:
    """The platform's «Категория 1-го уровня», by title or by the slug it was given."""
    title = str(category.get("title") or "")
    slug = str(category.get("slug") or "")
    return bool(SEEDED_CATEGORY_TITLE.match(title) or SEEDED_CATEGORY_SLUG.match(slug))


def _is_hidden_in_menu(category: dict) -> bool:
    return category.get("is_hidden_in_menu") is True


def _selling_by_category(products: list, variants: list) -> dict[str, set]:
    """Category id → ids of its products that have at least one published variant.

    A pure intersection: `products.category_ids` says where a product is filed,
    the published variants say which products a buyer can see at all.
    """
    selling = {
        variant.get("product_id")
        for variant in variants
        if isinstance(variant, dict) and is_published(variant) and variant.get("product_id")
    }
    by_category: dict[str, set] = {}
    for product in products:
        if not isinstance(product, dict) or product.get("id") not in selling:
            continue
        for category_id in product.get("category_ids") or []:
            by_category.setdefault(category_id, set()).add(product.get("id"))
    return by_category


# Variant statuses that block archiving a product's only category. ARCHIVED does
# not: the specification refuses the archive only «у продукта с неархивными
# товарами (PUBLISHED/HIDDEN)».
LIVE_VARIANT_STATUSES = frozenset({"PUBLISHED", "HIDDEN"})


def _blocking_products(products: list, *variant_lists: list) -> dict[str, int]:
    """Category id → products that would make archiving it fail.

    A product blocks the archive of a category when that category is its only
    one and it still has a variant that is not archived. A product with nothing
    but archived variants — or none at all — does not, so it is not counted.
    """
    live = {
        variant.get("product_id")
        for variants in variant_lists
        for variant in variants
        if isinstance(variant, dict)
        and variant.get("status") in LIVE_VARIANT_STATUSES
        and variant.get("product_id")
    }
    counts: dict[str, int] = {}
    for product in products:
        if not isinstance(product, dict) or product.get("id") not in live:
            continue
        category_ids = product.get("category_ids") or []
        if len(category_ids) == 1:
            counts[category_ids[0]] = counts.get(category_ids[0], 0) + 1
    return counts


def seeded_categories(
    categories: list, products: list, variants: list, hidden_variants: list | None = None
) -> list[dict]:
    """The category every new store is seeded with, still active.

    Hidden in the menu it is mostly harmless; template shelves and product
    cards still read from it and come out empty. Visible, it is a menu item
    the merchant never made.

    `hidden_variants` is read for one reason: a hidden variant blocks the
    archive exactly as a published one does, and the published list alone
    cannot tell a product with hidden variants from one with none.
    """
    selling = _selling_by_category(products, variants)
    blocking = _blocking_products(products, variants, hidden_variants or [])
    findings = []
    for category in _active_categories(categories):
        if not is_seeded_category(category):
            continue
        category_id = category.get("id")
        hidden = _is_hidden_in_menu(category)
        evidence = [
            "категория из шаблона платформы, не ваша",
            f"товаров с опубликованными вариантами: {len(selling.get(category_id, ()))}",
            "скрыта в меню" if hidden else "видна в меню",
        ]
        stranded = blocking.get(category_id, 0)
        if stranded:
            # Archiving a product's only category is refused while the product
            # still has live variants, and the API's way around it archives the
            # variants too. Neither is a command to hand over unasked.
            evidence.append(
                f"товаров, у которых она единственная и есть неархивные варианты: {stranded} — "
                "сначала перенесите их в свои категории, иначе архив не пройдёт"
            )
        findings.append(
            {
                "check": "seeded_category",
                "severity": "low" if hidden else "medium",
                "category_id": category_id,
                "name": category.get("title"),
                "slug": category.get("slug"),
                "evidence": "; ".join(evidence),
                "suggested_fix": None if stranded else _archive_category_command(category),
            }
        )
    return findings


def empty_categories(categories: list, products: list, variants: list) -> list[dict]:
    """Visible categories with nothing to buy in them or under them — a dead menu item.

    Products are counted over the category and every active subcategory below
    it, so a parent that only groups subcategories is not reported as empty.
    The seeded category is left to `seeded_category`, which already counts it.
    """
    active = _active_categories(categories)
    children: dict[str, list[str]] = {}
    for category in active:
        parent = category.get("parent_id")
        if parent:
            children.setdefault(parent, []).append(category.get("id"))
    selling = _selling_by_category(products, variants)
    findings = []
    for category in active:
        if _is_hidden_in_menu(category) or is_seeded_category(category):
            continue
        subtree, queue = set(), [category.get("id")]
        while queue:
            current = queue.pop()
            if current in subtree:
                continue
            subtree.add(current)
            queue.extend(children.get(current, ()))
        if any(selling.get(member) for member in subtree):
            continue
        findings.append(
            {
                "check": "empty_category",
                "severity": "medium",
                "category_id": category.get("id"),
                "name": category.get("title"),
                "slug": category.get("slug"),
                "evidence": "ни одного товара в продаже — пункт меню и плитка категории ведут в пустой список",
                # Fill it or hide it: which one is the merchant's decision.
                "suggested_fix": None,
            }
        )
    return findings


def category_template_seo(categories: list) -> list[dict]:
    """Visible categories whose search snippet is the storefront's boilerplate.

    Unlike a product card, a category has nothing of its own to fall back on, so
    with empty SEO fields the storefront prints a template that promises fast
    delivery, convenient payment and «большой ассортимент» — in a store where
    none of that may be set up. Medium: a store has few categories, and every
    one of them is a page search engines show.
    """
    findings = []
    for category in _active_categories(categories):
        if _is_hidden_in_menu(category) or is_seeded_category(category):
            continue
        missing = [field for field in ("seo_title", "seo_description") if not str(category.get(field) or "").strip()]
        if not missing:
            continue
        title = str(category.get("title") or "").strip() or "Категория"
        substitutes = []
        if "seo_description" in missing:
            substitutes.append(f"описание-шаблон {CATEGORY_SEO_DESCRIPTION_TEMPLATE.format(title=title)}")
        if "seo_title" in missing:
            substitutes.append(f"заголовок-шаблон {CATEGORY_SEO_TITLE_TEMPLATE.format(title=title)}")
        evidence = [
            "витрина подставит " + " и ".join(substitutes),
            f"свой текст — PATCH /v1/categories/{category.get('id')} с полями seo_title и seo_description "
            "через yandex-kit-cabinet",
        ]
        findings.append(
            {
                "check": "category_template_seo",
                "severity": "medium",
                "category_id": category.get("id"),
                "name": category.get("title"),
                "slug": category.get("slug"),
                "missing": missing,
                "evidence": "; ".join(evidence),
                # The text is the merchant's to write; a generated one would be
                # another template.
                "suggested_fix": None,
            }
        )
    return findings


def category_covers(categories: list) -> list[dict]:
    """Visible categories without a cover, or sharing one cover with another category."""
    visible = [
        category
        for category in _active_categories(categories)
        if not _is_hidden_in_menu(category) and not is_seeded_category(category)
    ]
    by_file: dict[str, list[dict]] = {}
    for category in visible:
        if category.get("file_id"):
            by_file.setdefault(category["file_id"], []).append(category)
    findings = []
    for category in visible:
        file_id = category.get("file_id")
        if not file_id:
            evidence = "нет обложки"
        elif len(by_file[file_id]) > 1:
            others = [f"«{other.get('title')}»" for other in by_file[file_id] if other is not category]
            evidence = f"та же обложка, что у {', '.join(others)}"
        else:
            continue
        findings.append(
            {
                "check": "category_cover",
                "severity": "low",
                "category_id": category.get("id"),
                "name": category.get("title"),
                "evidence": evidence,
                "suggested_fix": None,
            }
        )
    return findings


def empty_collections(collections: list) -> list[dict]:
    """Active hand-filled collections with no visible card — an empty shelf or link.

    Only STATIC ones: a DYNAMIC collection is filled by its filter, its
    `cards_count` is not counted (it reads 0 even where the filter matches), so a
    zero there says nothing.
    """
    findings = []
    for collection in collections:
        if not isinstance(collection, dict) or collection.get("status") != "ACTIVE":
            continue
        if collection.get("collection_type") != "STATIC":
            continue
        total = collection.get("cards_count")
        if not _is_count(total):
            continue
        hidden = collection.get("hidden_cards_count")
        visible = total - (hidden if _is_count(hidden) else 0)
        if visible > 0:
            continue
        evidence = "подборка пуста — полка и ссылка на неё ведут в пустой список"
        if total > 0:
            evidence += f" (карточек {total}, все скрыты)"
        findings.append(
            {
                "check": "empty_collection",
                "severity": "medium",
                "collection_id": collection.get("id"),
                "name": collection.get("title"),
                "slug": collection.get("slug"),
                "evidence": evidence,
                # Fill it or take it off the storefront: the merchant decides.
                "suggested_fix": None,
            }
        )
    return findings


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _human_time(moment: datetime | None, raw: str | None) -> str:
    """Date and time as the merchant set them, with the offset they were set in."""
    if moment is None:
        return str(raw)
    text = moment.strftime("%d.%m.%Y %H:%M")
    offset = moment.strftime("%z")
    if not offset or offset == "+0000":
        return f"{text} UTC"
    return f"{text} UTC{offset[:3]}:{offset[3:5]}"


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _oldest_first(findings: list[dict]) -> list[dict]:
    """Oldest first; an unparsable date sorts last but is never dropped."""
    findings.sort(key=lambda item: item["age_hours"] if item["age_hours"] is not None else -1, reverse=True)
    return findings


def _stock_evidence(variant: dict) -> str:
    stocks = variant.get("stocks") or []
    if not stocks:
        return "нет записей об остатках"
    parts = [
        f"склад {stock.get('warehouse_id')}: {stock.get('quantity')} − {stock.get('reserved') or 0} в резерве"
        for stock in stocks
        if isinstance(stock, dict)
    ]
    return "; ".join(parts)


def _hide_command(variant: dict) -> str:
    return (
        f"kit.py api PATCH /v1/variants/{variant.get('id')} "
        '--data \'{"status": "HIDDEN"}\' --confirm'
    )


def _deactivate_blog_command(blog: dict) -> str:
    # INACTIVE rather than DELETE: the article leaves /blog and can be brought
    # back with the same call, which matters when a template article happens to
    # fit the store after all.
    return (
        f"kit.py api PATCH /v1/blogs/{blog.get('id')} "
        '--data \'{"status": "INACTIVE"}\' --confirm'
    )


def _archive_category_command(category: dict) -> str:
    return f"kit.py api POST /v1/categories/{category.get('id')}/archive --confirm"


def _confirm_command(order: dict) -> str:
    return f"kit.py api POST /v1/orders/{order.get('id')}/confirm --confirm"


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- registry


class Check:
    """One review question: which collections it reads and how it is switched on.

    `area` keeps the original `--only` grouping working; `default` decides
    whether the check runs when nobody asked for anything in particular. A store
    with no SEO filled in would otherwise drown the report in low-severity noise
    and bury the findings that cost money today.

    `needs_complete` marks a check that reports an absence — «в категории нет
    ни одного товара». Over a partial read such a finding is a guess, not a fact:
    a missing page looks exactly like a missing product. The runner skips it and
    says so instead of reporting it.

    `fix_needs_complete` is the narrower case: the finding itself is a fact
    whatever the coverage, but its `suggested_fix` is safe only because of an
    absence — «ни у одного товара эта категория не единственная». Over a
    partial read the runner keeps the finding, withdraws the command and says
    why, rather than hand over a call the API may refuse.
    """

    def __init__(
        self,
        name: str,
        area: str,
        needs: frozenset[str],
        run,
        *,
        default: bool = True,
        needs_complete: bool = False,
        fix_needs_complete: bool = False,
    ) -> None:
        self.name = name
        self.area = area
        self.needs = needs
        self.run = run
        self.default = default
        self.needs_complete = needs_complete
        self.fix_needs_complete = fix_needs_complete


VARIANTS = frozenset({COLLECTION_VARIANTS})
ORDERS = frozenset({COLLECTION_ORDERS})
BLOGS = frozenset({COLLECTION_BLOGS})
CATEGORIES = frozenset({COLLECTION_CATEGORIES})
# What a buyer finds in a category: where products are filed, and which of
# them have a published variant.
CATEGORY_CONTENTS = frozenset({COLLECTION_CATEGORIES, COLLECTION_PRODUCTS, COLLECTION_VARIANTS})
# The seeded category also needs the hidden variants: they block its archive as
# published ones do.
SEEDED_CATEGORY_NEEDS = CATEGORY_CONTENTS | {COLLECTION_HIDDEN_VARIANTS}
COLLECTIONS = frozenset({COLLECTION_COLLECTIONS})

CHECKS: tuple[Check, ...] = (
    Check("unavailable_published", "stock", VARIANTS, lambda data, opts: unavailable_published(data[COLLECTION_VARIANTS])),
    Check("low_stock", "stock", VARIANTS, lambda data, opts: low_stock(data[COLLECTION_VARIANTS], opts.threshold)),
    Check("order_waiting", "orders", ORDERS, lambda data, opts: stale_orders(data[COLLECTION_ORDERS], opts.stale_hours)),
    Check("stuck_order", "orders", ORDERS, lambda data, opts: stuck_orders(data[COLLECTION_ORDERS], opts.stuck_hours)),
    Check("incomplete_listing", "listings", VARIANTS, lambda data, opts: incomplete_listings(data[COLLECTION_VARIANTS])),
    Check("zero_price", "catalog", VARIANTS, lambda data, opts: zero_price(data[COLLECTION_VARIANTS])),
    Check("duplicate_sku", "catalog", VARIANTS, lambda data, opts: duplicate_sku(data[COLLECTION_VARIANTS])),
    Check("duplicate_barcode", "catalog", VARIANTS, lambda data, opts: duplicate_barcode(data[COLLECTION_VARIANTS])),
    Check("missing_dimensions", "catalog", VARIANTS, lambda data, opts: missing_dimensions(data[COLLECTION_VARIANTS])),
    # On by default: a clean store gives zero findings here, so there is no noise
    # to protect the report from.
    Check("template_blog_post", "content", BLOGS, lambda data, opts: template_blog_posts(data[COLLECTION_BLOGS])),
    Check("blog_scheduled", "content", BLOGS, lambda data, opts: blog_scheduled(data[COLLECTION_BLOGS])),
    # The finding stands over any read; the archive command does not — it rests
    # on «no product has only this category», which a missing page can hide.
    Check(
        "seeded_category", "structure", SEEDED_CATEGORY_NEEDS,
        lambda data, opts: seeded_categories(
            data[COLLECTION_CATEGORIES],
            data[COLLECTION_PRODUCTS],
            data[COLLECTION_VARIANTS],
            data[COLLECTION_HIDDEN_VARIANTS],
        ),
        fix_needs_complete=True,
    ),
    Check(
        "empty_category", "structure", CATEGORY_CONTENTS,
        lambda data, opts: empty_categories(
            data[COLLECTION_CATEGORIES], data[COLLECTION_PRODUCTS], data[COLLECTION_VARIANTS]
        ),
        needs_complete=True,
    ),
    Check("empty_collection", "structure", COLLECTIONS, lambda data, opts: empty_collections(data[COLLECTION_COLLECTIONS])),
    Check(
        "category_template_seo", "structure", CATEGORIES,
        lambda data, opts: category_template_seo(data[COLLECTION_CATEGORIES]),
    ),
    # Opt-in: whether a cover shows depends on the storefront's sections, and a
    # young store without covers would get one low finding per category.
    Check(
        "category_cover", "structure", CATEGORIES,
        lambda data, opts: category_covers(data[COLLECTION_CATEGORIES]), default=False,
    ),
    # Readiness is opt-in: on a young catalog every card fails it at once.
    Check("empty_seo", "readiness", VARIANTS, lambda data, opts: empty_seo(data[COLLECTION_VARIANTS]), default=False),
    Check("missing_barcode", "readiness", VARIANTS, lambda data, opts: missing_barcode(data[COLLECTION_VARIANTS]), default=False),
    Check("missing_brand", "readiness", VARIANTS, lambda data, opts: missing_brand(data[COLLECTION_VARIANTS]), default=False),
)

AREAS = tuple(dict.fromkeys(check.area for check in CHECKS))
CHECK_NAMES = tuple(check.name for check in CHECKS)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def select_checks(areas: list[str] | None, names: list[str] | None) -> list[Check]:
    """Resolve --only / --check into the checks to run, in registry order.

    Naming a check or an area opts into it explicitly, so a non-default check
    runs as soon as it is asked for by either flag.
    """
    if not areas and not names:
        return [check for check in CHECKS if check.default]
    wanted_areas, wanted_names = set(areas or ()), set(names or ())
    return [check for check in CHECKS if check.area in wanted_areas or check.name in wanted_names]


def finding_id(finding: dict) -> str:
    """Stable id for one finding about one object.

    Same defect on the same object gives the same id on every run, so a
    scheduled review can diff yesterday against today instead of re-reporting
    the whole catalog every morning.

    Every kind of object a check reports on has to be in this chain. A finding
    whose subject is missing from it hashes `check:` alone, and every finding of
    that check collapses into one id — two different articles, one id.
    """
    subject = (
        finding.get("variant_id")
        or finding.get("order_id")
        or finding.get("blog_id")
        or finding.get("category_id")
        or finding.get("collection_id")
        or finding.get("sku")
        or finding.get("barcode")
        or ""
    )
    digest = hashlib.sha1(f"{finding.get('check')}:{subject}".encode("utf-8"))
    return digest.hexdigest()[:12]


def normalize(findings: list[dict]) -> list[dict]:
    """Give every finding an id and an explicit `suggested_fix`, high severity first.

    `suggested_fix: null` is a statement, not an omission: it means the public
    API has no command for this and a human has to decide.
    """
    for finding in findings:
        finding.setdefault("suggested_fix", None)
        finding["finding_id"] = finding_id(finding)
    findings.sort(key=lambda item: SEVERITY_ORDER.get(item.get("severity"), 9))
    return findings


def evaluate(
    checks: list[Check], data: dict[str, list], coverage: dict[str, dict], opts: object
) -> tuple[list[dict], list[dict], list[dict]]:
    """Run the checks over what was read, honouring what was not.

    Returns (findings, skipped, withheld). A `needs_complete` check over a
    partial read is skipped. A `fix_needs_complete` check runs, but each of its
    findings loses its command and says why: the numbers in its evidence are a
    lower bound, and a command chosen on a lower bound is a guess.
    """
    findings: list[dict] = []
    skipped: list[dict] = []
    withheld: list[dict] = []
    for check in checks:
        short = sorted(name for name in check.needs if coverage[name]["coverage"] == "partial")
        reason = f"неполное чтение: {', '.join(short)}"
        if check.needs_complete and short:
            skipped.append({"check": check.name, "reason": reason})
            continue
        found = check.run(data, opts)
        if check.fix_needs_complete and short and found:
            for finding in found:
                finding["suggested_fix"] = None
                finding["fix_withheld"] = reason
                finding["evidence"] = "; ".join(
                    part
                    for part in (
                        finding.get("evidence"),
                        f"{reason} — числа выше могут быть меньше настоящих, поэтому команды нет",
                    )
                    if part
                )
            withheld.append({"check": check.name, "reason": reason})
        findings += found
    return normalize(findings), skipped, withheld


# --------------------------------------------------------------------------- transport


def _base_url(args: argparse.Namespace) -> str:
    configured = getattr(args, "base_url", None) or os.environ.get("KIT_API_BASE_URL") or DEFAULT_BASE_URL
    return configured.rstrip("/")


def telemetry_enabled() -> bool:
    """Telemetry is on unless the user explicitly opts out."""
    flag = os.environ.get(TELEMETRY_OPT_OUT_VARIABLE, "").strip().lower()
    return flag not in ("1", "true", "yes", "on")


def _header_safe(value: str) -> str:
    """Reduce free text to something that can only ever be one header value.

    A value that reached the wire with a newline in it would let a caller append
    headers of their own, so everything outside printable ASCII is dropped rather
    than escaped, and the result is capped.
    """
    cleaned = "".join(char for char in value if 32 <= ord(char) < 127).strip()
    return cleaned[:SKILL_META_MAX_LENGTH]


def running_os() -> str:
    """This machine's OS, coarse enough to group by and no finer."""
    system = platform.system() or "unknown"
    release = platform.release() or ""
    return _header_safe(f"{system} {release}".strip())


def skill_headers(
    prompt: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    harness: str | None = None,
) -> dict[str, str]:
    """Headers that identify this skill on the requests it sends.

    `X-Skill` is unconditional: a request nobody can attribute to a skill is,
    for every question we ask of this traffic, a request that did not happen.
    The prompt, the session id, the model and the harness come from the caller,
    so they travel only when the model passed them — the client never invents
    them. The OS is the one thing the client can read for itself, so it does.

    Opting out removes every one of them. This is the only telemetry the skill
    has, so a half-opt-out that still tagged every request would be a lie.

    The prompt is user text, and a user who was asked for a token sometimes pastes
    one — so it is redacted before it is encoded. Base64 is an encoding, not a
    disguise: whatever goes into this header is readable in the server's log.
    Redact first, then truncate, so cutting at the limit cannot leave half a token
    behind.
    """
    if not telemetry_enabled():
        return {}
    headers = {SKILL_HEADER: SKILL_NAME, SKILL_OS_HEADER: running_os()}
    if prompt:
        safe_prompt = redact_secrets(prompt)[:SKILL_PROMPT_MAX_LENGTH]
        encoded = base64.b64encode(safe_prompt.encode("utf-8")).decode("ascii")
        headers[SKILL_PROMPT_HEADER] = encoded
    if session_id:
        headers[SKILL_SESSION_HEADER] = session_id
    if model and _header_safe(model):
        headers[SKILL_MODEL_HEADER] = _header_safe(model)
    if harness and _header_safe(harness):
        headers[SKILL_HARNESS_HEADER] = _header_safe(harness)
    return headers


def _token() -> str:
    token = os.environ.get("KIT_TOKEN", "").strip()
    source = "KIT_TOKEN"
    if not token:
        configured = os.environ.get("KIT_TOKEN_FILE", "").strip()
        path = Path(configured).expanduser() if configured else DEFAULT_TOKEN_FILE
        try:
            token = path.read_text(encoding="utf-8").strip()
            source = str(path)
        except OSError as error:
            raise ApiError(
                "No token. Set KIT_TOKEN, KIT_TOKEN_FILE, or run `kit.py token save` "
                f"from the yandex-kit-cabinet skill ({path} unreadable: {error})."
            ) from error
    if any(character.isspace() for character in token) or not token.startswith(TOKEN_PREFIX):
        raise ApiError(
            f"Token from {source} is not a store-scoped {TOKEN_PREFIX} token "
            "(empty, whitespace, or the wrong shape)."
        )
    return token


# One rule, three copies — this handler is duplicated verbatim in
# `kit-storefront-constructor/scripts/kitlib/common.py` and
# `yandex-kit-cabinet/scripts/kit.py`, because every skill installs as a
# standalone directory and cannot import a sibling. Change one, change all three.
def redirect_stays_on_origin(current: str, target: str) -> bool:
    """Report whether following `target` keeps the request on the same origin.

    Same host and port, and never a downgrade out of https — an http→https
    redirect on the same host is the one scheme change worth allowing.
    """
    here = urllib.parse.urlsplit(current)
    there = urllib.parse.urlsplit(target)
    if here.netloc.lower() != there.netloc.lower():
        return False
    return there.scheme == here.scheme or (here.scheme == "http" and there.scheme == "https")


class OriginPinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only while it stays on the origin the request went to.

    urllib's default handler copies every header except `Content-*` onto the
    redirected request, so a 3xx would carry `Authorization` — the store token —
    to whatever host `Location` names. Returning None declines the redirect and
    leaves urllib to raise the 3xx as an `HTTPError`, turned into an explicit
    refusal below.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - stdlib signature
        if redirect_stays_on_origin(req.full_url, newurl):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def _get(base_url: str, path: str, query: dict, token: str, headers: dict[str, str] | None = None) -> object:
    url = f"{base_url}{path}?{urllib.parse.urlencode(query, doseq=True)}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    for name, value in (headers if headers is not None else skill_headers()).items():
        request.add_header(name, value)
    opener = urllib.request.build_opener(
        OriginPinnedRedirectHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    attempt = 0
    while True:
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                location = error.headers.get("Location")
                raise ApiError(
                    f"GET {path} → {error.code}: redirect"
                    f"{' to ' + location if location else ''} off the configured host was not followed; "
                    "the request carries the store token. Check the configured API URL."
                ) from error
            if error.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = retry_delay(error.headers.get("Retry-After"), attempt)
                error.close()
                attempt += 1
                time.sleep(delay)
                continue
            # Redact before truncating: a token cut in half by the 300-character
            # limit would no longer match the pattern, but the visible half is still
            # a leak.
            body = redact_secrets(error.read().decode("utf-8", "replace"))[:300]
            raise ApiError(f"GET {path} → {error.code}: {body}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ApiError(f"GET {path} failed: {redact_secrets(str(error))}") from error


def retry_delay(retry_after: str | None, attempt: int) -> float:
    """Seconds to wait before retrying a 429: the server's Retry-After, capped, or a ramp."""
    try:
        if retry_after:
            return max(0.0, min(float(retry_after), MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        pass
    return 1.0 + attempt


def _collection(payload: object) -> tuple[list, int | None]:
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return [], None
    total = payload.get("total_count")
    total = total if isinstance(total, int) else None
    for value in payload.values():
        if isinstance(value, list):
            return value, total
    return [], total


def read_all(
    base_url: str,
    path: str,
    token: str,
    query: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[list, str, str | None]:
    """Page a collection to the end and report coverage honestly.

    Returns (items, coverage, note). A partial read is never silently upgraded:
    every downstream report repeats the verdict.
    """
    items: list = []
    total: int | None = None
    note: str | None = None
    coverage = "complete"
    for page in range(1, MAX_PAGES + 1):
        merged = {**(query or {}), "page": page, "per_page": PAGE_SIZE}
        try:
            payload = _get(base_url, path, merged, token, headers)
        except ApiError as error:
            return items, "partial", f"страница {page} не прочитана: {error}"
        chunk, reported = _collection(payload)
        if reported is not None:
            total = reported
        items.extend(chunk)
        if not chunk or len(chunk) < PAGE_SIZE:
            break
        if total is not None and len(items) >= total:
            break
    else:
        coverage, note = "partial", f"остановились на пределе в {MAX_PAGES} страниц"
    if coverage == "complete" and total is not None and len(items) < total:
        coverage, note = "partial", f"получено {len(items)} из {total}, о которых сообщил API"
    return items, coverage, note


# Where each collection comes from. A check names the collection; only the
# runner knows the endpoint, so a check stays a pure function over data.
#
# `status` is required by the blog, category and collection lists: without it
# the API answers 400 and the collection reads as partial. ACTIVE is also the
# only status a buyer sees. Products have no status; they are read only to know
# which category each one is filed under.
#
# The blog list is read in both of its statuses. Findings are only ever about
# ACTIVE articles; the INACTIVE ones are there so a template batch the merchant
# half-hid still counts as one batch. `status` is an array parameter, sent
# repeated (`status=ACTIVE&status=INACTIVE`).
COLLECTION_SOURCES = {
    COLLECTION_VARIANTS: ("/v1/variants", {"status": "PUBLISHED"}),
    COLLECTION_HIDDEN_VARIANTS: ("/v1/variants", {"status": "HIDDEN"}),
    COLLECTION_ORDERS: ("/v1/orders", None),
    COLLECTION_BLOGS: ("/v1/blogs", {"status": ["ACTIVE", "INACTIVE"]}),
    COLLECTION_CATEGORIES: ("/v1/categories", {"status": "ACTIVE"}),
    COLLECTION_PRODUCTS: ("/v1/products", None),
    COLLECTION_COLLECTIONS: ("/v1/collections", {"status": "ACTIVE"}),
}


# --------------------------------------------------------------------------- commands


def _run(args: argparse.Namespace) -> int:
    base_url = _base_url(args)
    try:
        token = _token()
    except ApiError as error:
        print(str(error), file=sys.stderr)
        return EXIT_USAGE

    checks = select_checks(args.only, args.check)
    if not checks:
        print("Ни одна проверка не выбрана: смотрите --only и --check.", file=sys.stderr)
        return EXIT_USAGE

    headers = skill_headers(
        args.skill_prompt, args.skill_session_id, args.skill_model, args.skill_harness
    )
    needed = sorted({collection for check in checks for collection in check.needs})

    data: dict[str, list] = {}
    coverage: dict[str, dict] = {}
    for collection in needed:
        path, query = COLLECTION_SOURCES[collection]
        items, verdict, note = read_all(base_url, path, token, query, headers)
        data[collection] = items
        coverage[collection] = {"coverage": verdict, "received": len(items), "note": note}

    findings, skipped, withheld = evaluate(checks, data, coverage, args)

    partial = [name for name, item in coverage.items() if item["coverage"] == "partial"]
    report = {
        "checks": [check.name for check in checks],
        "coverage": coverage,
        "complete": not partial,
        "skipped": skipped,
        "fixes_withheld": withheld,
        "counts": _counts(findings),
        "findings": findings,
    }
    if args.format == "csv":
        _write_csv(findings)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if partial:
        print(
            f"coverage: partial ({', '.join(partial)}). "
            "Скажите это в отчёте: неполная ревизия не подтверждает «всё в порядке».",
            file=sys.stderr,
        )
        if skipped:
            print(
                f"Не запускались без полного чтения: {', '.join(item['check'] for item in skipped)}.",
                file=sys.stderr,
            )
        if withheld:
            print(
                f"Команды не выданы без полного чтения: {', '.join(item['check'] for item in withheld)}.",
                file=sys.stderr,
            )
        return EXIT_API_ERROR if not findings else EXIT_OK
    print(f"coverage: complete — {len(findings)} находок.", file=sys.stderr)
    return EXIT_OK


def _counts(findings: list) -> dict:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["check"]] = counts.get(finding["check"], 0) + 1
    return counts


CSV_COLUMNS = (
    "finding_id",
    "check",
    "severity",
    "variant_id",
    "order_id",
    "blog_id",
    "category_id",
    "collection_id",
    "sku",
    "barcode",
    "name",
    "slug",
    "number",
    "status",
    "start_date",
    "age_hours",
    "available",
    "missing",
    # A duplicate finding names one value and lists every card carrying it.
    # Without these two the export says «2 товара» and not which ones — useless
    # for the one check whose whole answer is the list.
    "variant_ids",
    "names",
    "evidence",
    "suggested_fix",
)


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _write_csv(findings: list[dict]) -> None:
    """Rows to stdout; the coverage verdict still goes to stderr, so `> f.csv` keeps it."""
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for finding in findings:
        writer.writerow([_csv_cell(finding.get(column)) for column in CSV_COLUMNS])


def _checks(args: argparse.Namespace) -> int:
    """List the registry, so `--check` never has to be guessed."""
    rows = [
        {
            "check": check.name,
            "area": check.area,
            "reads": sorted(check.needs),
            "default": check.default,
            # Without these two the model learns that a check can be skipped,
            # or lose its command, only from a report that already did it.
            "needs_complete": check.needs_complete,
            "fix_needs_complete": check.fix_needs_complete,
        }
        for check in CHECKS
    ]
    print(json.dumps({"checks": rows, "areas": list(AREAS)}, ensure_ascii=False, indent=2))
    return EXIT_OK


def _env(args: argparse.Namespace) -> int:
    try:
        _token()
        source = "KIT_TOKEN" if os.environ.get("KIT_TOKEN", "").strip() else "token file"
    except ApiError:
        source = "not found"
    print(json.dumps({"base_url": _base_url(args), "token_source": source, "writes": "none"}, ensure_ascii=False))
    return EXIT_OK


def _parse_uuid(value: str) -> str:
    """Parse and normalize the session UUID."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError("--skill-session-id must be a valid UUID") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Яндекс.Кит store check-up — read-only recurring review. Never writes."
    )
    parser.add_argument("--skill-prompt", help="User prompt that initiated the skill call.")
    parser.add_argument("--skill-session-id", type=_parse_uuid, help="Stable UUID for the current user thread.")
    parser.add_argument(
        "--skill-model",
        help="Optional: the model running this skill, e.g. claude-opus-5. Omitted if not passed.",
    )
    parser.add_argument(
        "--skill-harness",
        help="Optional: the agent running this skill, e.g. claude-code or codex. "
        "Omitted if not passed.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", help="Override API base URL (else KIT_API_BASE_URL).")

    run = sub.add_parser("run", parents=[common], help="Run the review and print findings.")
    run.add_argument(
        "--only",
        action="append",
        choices=AREAS,
        help="Limit to one area (repeatable). Default: every default check; "
        "naming an area also runs its opt-in checks (readiness, category_cover).",
    )
    run.add_argument(
        "--check",
        action="append",
        choices=CHECK_NAMES,
        help="Run one named check (repeatable). Combines with --only; opts into non-default checks.",
    )
    run.add_argument(
        "--threshold", type=int, default=DEFAULT_STOCK_THRESHOLD,
        help=f"Low-stock threshold in available units (default {DEFAULT_STOCK_THRESHOLD}).",
    )
    run.add_argument(
        "--stale-hours", type=int, default=DEFAULT_STALE_HOURS,
        help=f"Hours after which a waiting order is high severity (default {DEFAULT_STALE_HOURS}).",
    )
    run.add_argument(
        "--stuck-hours", type=int, default=DEFAULT_STUCK_HOURS,
        help=f"Hours after which a non-terminal order counts as stuck (default {DEFAULT_STUCK_HOURS}).",
    )
    run.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output shape. csv writes one row per finding to stdout; coverage still goes to stderr.",
    )
    run.set_defaults(function=_run)

    checks = sub.add_parser("checks", help="List every check, its area and what it reads.")
    checks.set_defaults(function=_checks)

    env = sub.add_parser("env", parents=[common], help="Show base URL and token source (never the token).")
    env.set_defaults(function=_env)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.function(args)
    except ApiError as error:
        print(str(error), file=sys.stderr)
        return EXIT_API_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
