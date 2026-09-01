#!/usr/bin/env python3
"""Яндекс.Кит store check-up — a read-only recurring review of a store.

Answers the questions a merchant asks again and again: what is published but
cannot be bought, what is about to run out, which orders have been waiting, and
which listings are incomplete.

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
COLLECTION_ORDERS = "orders"


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
    """Published items missing an image or a description."""
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
        findings.append(
            {
                "check": "incomplete_listing",
                "severity": "low",
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "name": variant.get("name"),
                "missing": missing,
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
    """Published cards with empty SEO fields — invisible to search and to AI agents."""
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
    """

    def __init__(self, name: str, area: str, needs: frozenset[str], run, *, default: bool = True) -> None:
        self.name = name
        self.area = area
        self.needs = needs
        self.run = run
        self.default = default


VARIANTS = frozenset({COLLECTION_VARIANTS})
ORDERS = frozenset({COLLECTION_ORDERS})

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
    """
    subject = (
        finding.get("variant_id")
        or finding.get("order_id")
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
        # Redact before truncating: a token cut in half by the 300-character
        # limit would no longer match the pattern, but the visible half is still
        # a leak.
        body = redact_secrets(error.read().decode("utf-8", "replace"))[:300]
        raise ApiError(f"GET {path} → {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ApiError(f"GET {path} failed: {redact_secrets(str(error))}") from error


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
COLLECTION_SOURCES = {
    COLLECTION_VARIANTS: ("/v1/variants", {"status": "PUBLISHED"}),
    COLLECTION_ORDERS: ("/v1/orders", None),
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

    findings: list[dict] = []
    for check in checks:
        findings += check.run(data, args)
    findings = normalize(findings)

    partial = [name for name, item in coverage.items() if item["coverage"] == "partial"]
    report = {
        "checks": [check.name for check in checks],
        "coverage": coverage,
        "complete": not partial,
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
    "sku",
    "barcode",
    "name",
    "number",
    "status",
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
        help="Limit to one area (repeatable). Default: every area except readiness.",
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
