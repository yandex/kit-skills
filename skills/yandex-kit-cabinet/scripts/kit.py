#!/usr/bin/env python3
"""yandex-kit-cabinet: safe, read-only-by-default client for the Яндекс.Кит public API.

Cross-platform (Windows / macOS / desktop Linux), Python 3 стандартная библиотека
only. The API token is resolved from ``KIT_TOKEN``, then the file named by
``KIT_TOKEN_FILE``, then the default token file ``~/.yandex-kit-skills/kit_api.token``
(written by ``kit.py token save``). It is never printed or passed as a process
argument. Mutating verbs (POST/PUT/PATCH/DELETE) are refused unless ``--confirm``
is passed.

Base URL: https://api.kit.yandex.net (override with KIT_API_BASE_URL / --base-url).
Docs: https://yandex.ru/dev/kit/ru/ · Endpoint map: ../references/api.md

Usage examples:
  kit.py env                                     # active contour: base URL + token source
  kit.py token save                              # hidden prompt -> default token file
  kit.py token status                            # where the token comes from (no secrets)
  kit.py whoami                                  # GET /v1/users/current
  kit.py store                                   # GET /v1/store
  kit.py api GET /v1/products -q limit=10
  kit.py api GET /v1/orders -q status=created -q limit=20
  kit.py validate POST /v1/variants --data @body.json    # offline schema check, no network
  kit.py list /v1/orders                         # every page + explicit coverage report
  kit.py api POST /v1/categories --data '{"title": "Новинки"}' --confirm
  kit.py api PATCH /v1/variants/<id> --data @body.json --confirm
  kit.py api POST /v1/variants/<id>/archive --confirm
  kit.py upload photo.jpg --confirm              # multipart POST /v1/files
  kit.py ask analyst --prompt "Продажи за неделю?"   # AI-ассистент: аналитика магазина
  kit.py ask support --prompt "Как настроить доставку?"  # AI-ассистент: справка по кабинету

Exit codes:
  0 — success
  1 — API/network error, or a request body that violates the documented schema
  2 — usage error or missing token
  3 — mutating operation refused (no --confirm)
  4 — write outcome ambiguous: the request may or may not have been applied.
      Verify with a read; never resend the same mutation blindly.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import mimetypes
import os
import platform
import re
import ssl
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

DEFAULT_BASE_URL = "https://api.kit.yandex.net"
DEFAULT_TOKEN_FILE = Path.home() / ".yandex-kit-skills" / "kit_api.token"
SKILL_HEADER: str = "X-Skill"
SKILL_NAME: str = "yandex-kit-cabinet"
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
# Vendored public API specification: the offline source of truth for request
# bodies. The API does not serve its own spec in production, so `describe` and
# `schema` read this file instead of guessing or calling the network.
SPEC_FILE = Path(__file__).resolve().parent.parent / "references" / "openapi.json"
MAX_SCHEMA_DEPTH = 3
REQUEST_TIMEOUT_SECONDS = 30
# Public API limit is 10 rps per store; retry a few times on 429.
MAX_RATE_LIMIT_RETRIES = 3
# `list` walks a paginated collection to completion. per_page is capped at 100 by
# the API; the page ceiling only guards against an endless loop and is reported
# as partial coverage when it is hit.
DEFAULT_PAGE_SIZE = 100
MAX_LIST_PAGES = 50

# AI-assistant agents live on the experimental API, mounted next to the external
# one. The public host serves the external API at the root; other deployments
# serve it under EXTERNAL_PATH_PREFIX, so swapping the suffix keeps a single
# KIT_API_BASE_URL working for both.
EXTERNAL_PATH_PREFIX = "/api/external"
EXPERIMENTAL_PATH_PREFIX = "/api/experimental/v1"
AI_AGENTS = {
    "analyst": "/ai-assistant/data-analyst",
    "support": "/ai-assistant/support",
}
# Agents generate text, so they answer in tens of seconds rather than instantly.
AI_AGENT_TIMEOUT_SECONDS = 180
# One prefix contract, shared by the entry surfaces and by redaction: a token is
# only accepted in the shape `redact_secrets` below can recognise, so a token
# this client holds can never be one it fails to hide.
TOKEN_PREFIX = "yakit_"
TOKEN_PATTERN = re.compile(TOKEN_PREFIX + r"[A-Za-z0-9._~-]+")
# Personal data leaves the machine when a prompt goes to the AI service, so the
# same two shapes are refused there that --redact hides in output.
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Deliberately narrow: a country prefix followed by ten digits in the usual
# groupings. A UUID, a price and an order number do not match it.
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+\d{1,3}|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")

# Personal data, plus the free text written about or by a buyer. Every name here
# was checked against the vendored spec — which schema carries it and who fills
# it in — rather than matched by how the field is named.
#
# Deliberately absent:
#   * `name` and `description` — the merchant's own catalog copy. Hiding them
#     would break the ordinary listing work this client exists for.
#   * `comment` — in this API it belongs to `ContextCollection`, i.e. the
#     merchant's own note about a collection. It is neither personal data nor
#     third-party text, and masking it would make the flag look arbitrary.
# The buyer's own free text on an order is `delivery_notes` in
# `OrderDeliveryInfo`; `note` is the merchant's note about a customer and is
# masked because it is *about* a person, not because of who typed it.
#
# Treating store text as untrusted input is a rule in SKILL.md — a mask cannot
# enforce it, because an instruction is indistinguishable from a string.
REDACTED_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "patronymic",
        "phone",
        "email",
        "buyer_name",
        "buyer_phone",
        "buyer_email",
        "holder_email",
        "address",
        "courier_address",
        "pickup_point_address",
        "self_pick_up_address",
        "entrance",
        "floor",
        "intercom",
        "note",
        "delivery_notes",
    }
)


def _mask(value: object) -> object:
    """Keep the shape, drop the content — a reader can still see the field exists."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if not value else f"«скрыто, {len(value)} симв.»"
    return "«скрыто»"


def redact_payload(value: object) -> object:
    """Mask known personal-data fields anywhere in a decoded response.

    Output only. The request body is never redacted: what is sent has to stay
    byte-for-byte what the user confirmed.
    """
    if isinstance(value, dict):
        return {
            key: (_mask(item) if key.lower() in REDACTED_FIELDS else redact_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def redact_secrets(value: str) -> str:
    """Remove Kit token-shaped values from text.

    Distinct from `redact_payload`, which masks personal-data *fields* in a
    decoded response. This one works on raw text, for the places where a
    response becomes part of an error message and never passes through a
    decoded-payload path at all.
    """
    return TOKEN_PATTERN.sub("[REDACTED]", value)


EXIT_OK = 0
EXIT_API_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_AMBIGUOUS = 4

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# A write that fails this way may still have reached the store: the request was
# accepted or its fate is unknown, so the outcome is ambiguous rather than failed.
AMBIGUOUS_STATUSES = frozenset({408, 500, 502, 503, 504})


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


def _telemetry(args: argparse.Namespace) -> dict[str, str | None]:
    """The identification the caller passed, as keyword arguments for `_request`.

    Every call site spreads this instead of listing the flags one by one, so a
    new call site cannot quietly send an unattributed request.
    """
    session_id = getattr(args, "skill_session_id", None)
    return {
        "skill_prompt": getattr(args, "skill_prompt", None),
        "skill_session_id": str(session_id) if session_id else None,
        "skill_model": getattr(args, "skill_model", None),
        "skill_harness": getattr(args, "skill_harness", None),
    }


def _base_url(args: argparse.Namespace) -> str:
    return (args.base_url or os.environ.get("KIT_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _experimental_base_url(args: argparse.Namespace) -> str:
    """Base URL of the experimental API, derived from the configured one."""
    base_url = _base_url(args)
    if base_url.endswith(EXTERNAL_PATH_PREFIX):
        base_url = base_url[: -len(EXTERNAL_PATH_PREFIX)]
    return base_url + EXPERIMENTAL_PATH_PREFIX


def _token_source() -> tuple[str, str]:
    """Resolve the token: KIT_TOKEN env → KIT_TOKEN_FILE → default token file.

    Returns (token, human-readable source). Token may be empty if nothing found.
    """
    token = os.environ.get("KIT_TOKEN", "").strip()
    if token:
        return token, "KIT_TOKEN environment variable"
    token_file = os.environ.get("KIT_TOKEN_FILE", "").strip()
    if token_file:
        try:
            return (
                Path(token_file).expanduser().read_text(encoding="utf-8").strip(),
                f"KIT_TOKEN_FILE ({token_file})",
            )
        except OSError as error:
            print(f"Cannot read KIT_TOKEN_FILE ({token_file}): {error}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
    if DEFAULT_TOKEN_FILE.is_file():
        try:
            return (
                DEFAULT_TOKEN_FILE.read_text(encoding="utf-8").strip(),
                f"default token file ({DEFAULT_TOKEN_FILE})",
            )
        except OSError as error:
            print(f"Cannot read {DEFAULT_TOKEN_FILE}: {error}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
    return "", "not found"


def _token() -> str:
    token, _ = _token_source()
    if not token:
        print(
            "No API token found. Save one with:  kit.py token save\n"
            "(or export KIT_TOKEN / point KIT_TOKEN_FILE at a file with the token).\n"
            "Generate a token in the cabinet: https://<магазин>.b2b.kit.yandex.ru/settings/api\n"
            "(Настройки → API → «Сгенерировать токен»).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)
    if any(c.isspace() for c in token) or not token.startswith(TOKEN_PREFIX):
        print(
            f"The configured token is not a store-scoped {TOKEN_PREFIX} token. "
            "Fix KIT_TOKEN / KIT_TOKEN_FILE, or save a new one with:  kit.py token save",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)
    return token


def _token_save() -> int:
    """Read the token from a hidden prompt (or piped stdin) and store it in the default file."""
    if sys.stdin.isatty():
        import getpass

        print(
            "Paste the API token from the cabinet (Настройки → API → «Сгенерировать токен»).\n"
            "Input is hidden and the token is stored only on this machine.",
            file=sys.stderr,
        )
        token = getpass.getpass("Token: ").strip()
    else:
        token = sys.stdin.read().strip()
    if not token:
        print("Empty token — nothing saved.", file=sys.stderr)
        return EXIT_USAGE
    # The same shape every entry surface enforces, and the same shape
    # `redact_secrets` looks for. Accepting a token of another shape here would
    # accept one redaction cannot recognise later.
    if any(c.isspace() for c in token) or not token.startswith(TOKEN_PREFIX):
        print(
            f"Token must be a whitespace-free store-scoped {TOKEN_PREFIX} token — nothing saved.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    DEFAULT_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only from the moment the file exists. Writing first and `chmod`-ing
    # second leaves a window where the token is readable by every process of
    # this user.
    descriptor = os.open(DEFAULT_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    try:
        os.chmod(DEFAULT_TOKEN_FILE, 0o600)  # no-op on Windows, fine
    except OSError:
        pass
    print(f"Token saved to {DEFAULT_TOKEN_FILE}", file=sys.stderr)
    print("Verify it with:  kit.py whoami && kit.py store", file=sys.stderr)
    return EXIT_OK


def _token_status() -> int:
    """Report where the token would come from — never the token itself."""
    token, source = _token_source()
    if token:
        print(f"Token source: {source}")
        print("Verify it with:  kit.py whoami")
        return EXIT_OK
    print("Token source: not found (checked KIT_TOKEN, KIT_TOKEN_FILE, " f"{DEFAULT_TOKEN_FILE})")
    print("Save one with:  kit.py token save")
    return EXIT_USAGE


def _env_status(args: argparse.Namespace) -> int:
    """Report which contour the client will talk to — never the token itself."""
    _, source = _token_source()
    print(f"Base URL: {_base_url(args)}")
    print(f"Token source: {source}")
    return EXIT_OK


def _multipart_body(file_path: Path) -> tuple[bytes, str]:
    """Encode a single-file multipart/form-data body (field name: ``file``)."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    try:
        payload = file_path.read_bytes()
    except OSError as error:
        print(f"Cannot read file: {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + payload + tail, f"multipart/form-data; boundary={boundary}"


class ApiError(Exception):
    """A failed call, classified by whether the store may have changed anyway.

    ``ambiguous`` marks the transport-level failures of a *mutating* request —
    timeout, dropped connection, 408, 5xx — where the write may already have been
    applied even though no answer came back. Such an outcome is never retried
    blindly: it is resolved by reading the object back.
    """

    def __init__(self, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous

    @property
    def exit_code(self) -> int:
        return EXIT_AMBIGUOUS if self.ambiguous else EXIT_API_ERROR


# One rule, three copies — this handler is duplicated verbatim in
# `kit-storefront-constructor/scripts/kitlib/common.py` and
# `kit-store-checkup/scripts/checkup.py`, because every skill installs as a
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


def _request(
    method: str,
    url: str,
    *,
    body: dict | list | None,
    raw_data: bytes | None = None,
    content_type: str | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    mutating: bool = False,
    skill_prompt: str | None = None,
    skill_session_id: str | None = None,
    skill_model: str | None = None,
    skill_harness: str | None = None,
) -> tuple[int, object]:
    """Send one request and return (status, decoded body), or raise ApiError."""
    data = raw_data if raw_data is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        OriginPinnedRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {_token()}")
        request.add_header("Content-Type", content_type or "application/json")
        identification = skill_headers(skill_prompt, skill_session_id, skill_model, skill_harness)
        for header_name, header_value in identification.items():
            request.add_header(header_name, header_value)
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                location = error.headers.get("Location")
                # A refused redirect is not ambiguous: nothing reached the handler.
                raise ApiError(
                    f"API error {error.code}: redirect"
                    f"{' to ' + location if location else ''} off the configured host was not followed; "
                    "the request carries the store token. Check the configured API URL.",
                    ambiguous=False,
                ) from error
            if error.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                retry_after = error.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 10.0) if retry_after else 1.0 + attempt
                except ValueError:
                    delay = 1.0 + attempt
                time.sleep(delay)
                continue
            # The message is the only form this body ever takes, so redaction
            # happens here: there is no later output path to catch a token.
            detail = redact_secrets(error.read().decode("utf-8", "replace"))
            # A 4xx other than 408 is a decision: the request was rejected before
            # it touched anything, so even a write failed cleanly. 429 included —
            # the rate limiter answers instead of the handler.
            raise ApiError(
                f"API error {error.code}: {detail}",
                ambiguous=mutating and error.code in AMBIGUOUS_STATUSES,
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ApiError(
                f"Network error after up to {timeout}s: {redact_secrets(str(error))}",
                ambiguous=mutating,
            ) from error
    raise ApiError("Rate limit did not clear after retries")  # unreachable, keeps type-checkers happy


def _parse_body(raw: str | None) -> dict | list | None:
    """--data accepts an inline JSON string, @path/to/file.json, or '-' for stdin."""
    if raw is None:
        return None
    if raw == "-":
        raw = sys.stdin.read()
    elif raw.startswith("@"):
        try:
            raw = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            print(f"Cannot read body file: {error}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"--data is not valid JSON: {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _parse_query(pairs: list[str] | None) -> str:
    if not pairs:
        return ""
    items = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            print(f"Bad --query value (expected key=value): {pair}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
        items.append((key, value))
    return "?" + urllib.parse.urlencode(items)


AMBIGUOUS_NOTE = (
    "outcome: ambiguous — the write may or may not have been applied.\n"
    "Do NOT resend this request. Read the object back to establish the real state;\n"
    "if the read cannot settle it, report «результат неизвестен, нужна проверка»."
)


def _report_body_validation(method: str, path: str, body: object) -> tuple[list[str], list[str]]:
    """Print schema violations and unknown-field warnings; return both."""
    errors = validate_request_body(method, path, body)
    warnings = unknown_fields(request_schema(method, path) or {}, body)
    if errors:
        print(f"INVALID — {len(errors)} violation(s) against the documented schema:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    if warnings:
        print(
            "Warning — fields the spec does not document (the API ignores them "
            "silently, so the write would look successful while doing nothing):",
            file=sys.stderr,
        )
        for warning in warnings:
            print(f"  - {warning}", file=sys.stderr)
    if errors or warnings:
        print(f"Build the body from:  kit.py describe {method} {path}", file=sys.stderr)
    return errors, warnings


def _call(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    body: dict | list | None,
    upload_file: Path | None = None,
) -> int:
    if not path.startswith("/"):
        path = "/" + path
    if any(c.isspace() for c in path):
        print(
            f"Bad endpoint path (contains whitespace): {path!r}. "
            "Pass query parameters via -q key=value, not inside the path.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    url = f"{_base_url(args)}{path}{_parse_query(getattr(args, 'query', None))}"
    mutating = method in MUTATING_METHODS
    if mutating and not args.confirm:
        print(
            f"Refusing mutating operation ({method} {url}) without --confirm.\n"
            "Show the request to the user, get confirmation, then re-run with --confirm.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    raw_data = content_type = None
    if upload_file is not None:
        raw_data, content_type = _multipart_body(upload_file)
    elif mutating and not args.skip_validation:
        # Pre-flight: a body that violates the documented schema never leaves the
        # machine. --dry-run validates too, so the check costs no API call.
        if match_route(path) is None:
            print(
                f"No such operation in the bundled spec: {method} {path}.\n"
                f"Find the real one with:  kit.py endpoints {path.strip('/').split('/')[-1]}\n"
                "Pass --skip-validation only for an endpoint the spec does not cover yet.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if _report_body_validation(method, path, body)[0]:
            print("outcome: failed before sending — nothing was sent, the store is unchanged.", file=sys.stderr)
            return EXIT_USAGE

    if body is not None and content_type is None:
        # Five operations take JSON Merge Patch; sending them application/json
        # risks a 415 even with a perfectly valid body.
        spec_type = request_content_type(method, path)
        content_type = spec_type if spec_type in JSON_CONTENT_TYPES else "application/json"

    if args.dry_run:
        preview = {"method": method, "url": url, "body": body}
        if content_type is not None:
            preview["content_type"] = content_type
        if upload_file is not None:
            preview["upload_file"] = str(upload_file)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return EXIT_OK

    try:
        status, result = _request(
            method,
            url,
            body=body,
            raw_data=raw_data,
            content_type=content_type,
            mutating=mutating,
            **_telemetry(args),
        )
    except ApiError as error:
        print(str(error), file=sys.stderr)
        if error.ambiguous:
            print(AMBIGUOUS_NOTE, file=sys.stderr)
        elif mutating:
            print("outcome: failed — the API rejected the request, the store is unchanged.", file=sys.stderr)
        return error.exit_code

    payload = redact_payload(result) if getattr(args, "redact", False) else result
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    if mutating:
        print(
            f"outcome: applied (HTTP {status}) — not verified yet. Read the object back and "
            "compare the full expected state before reporting «выполнено».",
            file=sys.stderr,
        )
    return EXIT_OK


@lru_cache(maxsize=1)
def _load_spec() -> dict:
    """Load the vendored specification (once per process)."""
    try:
        return json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    except OSError as error:
        print(
            f"Cannot read the bundled API spec ({SPEC_FILE}): {error}\n"
            "Reinstall the skill, or run tools/sync_openapi.py from the repo.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)
    except json.JSONDecodeError as error:
        print(f"Bundled API spec is not valid JSON: {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _spec_operations(spec: dict) -> list[tuple[str, str, dict]]:
    return [
        (method.upper(), path, operation)
        for path, item in spec.get("paths", {}).items()
        for method, operation in item.items()
        if method.upper() in {"GET", *MUTATING_METHODS}
    ]


# ---------------------------------------------------------------------------
# Spec routing and offline request validation.
#
# These are the offline half of the client: they answer "does this operation
# exist, what may I send it, and is this body legal" without a token or a
# network call. The evaluation suite imports them directly, so the mock that scores
# it validates requests with exactly the code that ships in the skill. Treat them as
# a public surface of this module: keep the names stable.
# ---------------------------------------------------------------------------

JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}
# Five operations take JSON Merge Patch instead of plain JSON (UpdateVariant,
# UpdateCategory, UpdateCharacteristic, UpdateVariantAttachment, UpdateWarehouse).
# Sending them application/json risks a 415, and reading only application/json
# from the spec would claim they take no body at all.
JSON_CONTENT_TYPES = ("application/json", "application/merge-patch+json")


@lru_cache(maxsize=1)
def _route_table() -> tuple[tuple[re.Pattern[str], str], ...]:
    """Compile every spec path template into a matcher, concrete templates first."""
    routes = []
    for template in _load_spec().get("paths", {}):
        escaped = re.escape(template).replace(r"\{", "{").replace(r"\}", "}")
        routes.append((re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", escaped) + "$"), template))
    # A literal segment must win over a templated one: /v1/variants/stocks/bulk_update
    # has to match before /v1/variants/{id}.
    routes.sort(key=lambda item: item[1].count("{"))
    return tuple(routes)


def match_route(path: str) -> str | None:
    """Return the spec path template serving this concrete path."""
    for pattern, template in _route_table():
        if pattern.match(path):
            return template
    return None


def operation_for(method: str, path: str) -> tuple[str | None, dict | None]:
    """Return (path template, operation object) for a concrete or templated path."""
    template = match_route(path)
    if template is None:
        return None, None
    return template, _load_spec()["paths"][template].get(method.lower())


def operation_id(method: str, path: str) -> str | None:
    """Return the operationId serving this request, if any."""
    _template, operation = operation_for(method, path)
    return operation.get("operationId") if isinstance(operation, dict) else None


def allowed_methods(path: str) -> list[str]:
    """Return the HTTP methods the spec defines for this path."""
    template = match_route(path)
    if template is None:
        return []
    return [
        method.upper() for method in _load_spec()["paths"][template] if method.upper() in {"GET", *MUTATING_METHODS}
    ]


def resolve(node: object, seen: tuple[str, ...] = ()) -> object:
    """Inline every $ref, stopping only at cycles.

    Unlike the display resolver, this one does not stop at a depth limit: a
    validator has to see the whole schema.
    """
    if isinstance(node, list):
        return [resolve(item, seen) for item in node]
    if not isinstance(node, dict):
        return node
    name = _schema_name(node)
    if name:
        if name in seen:
            return {"type": "object"}
        target = _load_spec().get("components", {}).get("schemas", {}).get(name)
        return {} if target is None else resolve(target, seen + (name,))
    return {key: resolve(value, seen) for key, value in node.items()}


def request_body_content(method: str, path: str) -> tuple[str | None, dict | None]:
    """Return (content type, unresolved body schema) for an operation."""
    _template, operation = operation_for(method, path)
    if not isinstance(operation, dict):
        return None, None
    content = operation.get("requestBody", {}).get("content", {})
    for content_type in JSON_CONTENT_TYPES:
        schema = content.get(content_type, {}).get("schema")
        if schema:
            return content_type, schema
    for content_type, media in content.items():
        return content_type, media.get("schema")
    return None, None


def request_content_type(method: str, path: str) -> str | None:
    """Return the Content-Type an operation expects, per the spec."""
    return request_body_content(method, path)[0]


def request_schema(method: str, path: str) -> dict | None:
    """Return the fully resolved JSON request schema for an operation."""
    content_type, schema = request_body_content(method, path)
    if schema is None or content_type not in JSON_CONTENT_TYPES:
        return None
    resolved = resolve(schema)
    return resolved if isinstance(resolved, dict) else None


def required_query_params(method: str, path: str) -> list[str]:
    """Return the query parameters the spec marks required."""
    _template, operation = operation_for(method, path)
    if not isinstance(operation, dict):
        return []
    return [
        parameter["name"]
        for parameter in (resolve(item) for item in operation.get("parameters", []))
        if isinstance(parameter, dict) and parameter.get("in") == "query" and parameter.get("required")
    ]


def validate_value(schema: dict, value: object, where: str = "body") -> list[str]:
    """Validate a value against the OpenAPI subset this specification uses."""
    if not isinstance(schema, dict) or not schema:
        return []

    for variant in ("oneOf", "anyOf"):
        options = schema.get(variant)
        if isinstance(options, list) and options:
            if any(not validate_value(option, value, where) for option in options):
                return []
            return [f"{where}: does not match any {variant} variant"]
    if isinstance(schema.get("allOf"), list):
        errors: list[str] = []
        for option in schema["allOf"]:
            errors.extend(validate_value(option, value, where))
        return errors

    if value is None:
        return [] if schema.get("nullable") else [f"{where}: must not be null"]

    kind = schema.get("type")
    if kind in JSON_TYPES:
        # JSON has no integer type of its own, and booleans are not numbers.
        if kind in ("integer", "number") and isinstance(value, bool):
            return [f"{where}: must be {kind}, got boolean"]
        if not isinstance(value, JSON_TYPES[kind]):
            return [f"{where}: must be {kind}, got {type(value).__name__}"]

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return [f"{where}: {value!r} is not one of {enum}"]

    errors = []
    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return [f"{where}: must be object, got {type(value).__name__}"]
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{where}: missing required field {name!r}")
        for name, item in value.items():
            if name in properties:
                errors.extend(validate_value(properties[name], item, f"{where}.{name}"))
    if kind == "array" and isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate_value(items, item, f"{where}[{index}]"))
    return errors


def validate_request_body(method: str, path: str, body: object) -> list[str]:
    """Validate a request body against the operation's documented schema."""
    schema = request_schema(method, path)
    if schema is None:
        return [] if body is None else ["body: operation does not accept a JSON body"]
    if body is None:
        return ["body: operation requires a JSON body"]
    return validate_value(schema, body)


def unknown_fields(schema: object, value: object, where: str = "body") -> list[str]:
    """Return fields the schema does not document, as dotted paths.

    Not an error: the API ignores unknown fields silently. That silence is the
    danger — an invented field name looks like a successful write — so these are
    reported as warnings next to the real violations.
    """
    if not isinstance(schema, dict) or not schema:
        return []
    for variant in ("oneOf", "anyOf"):
        options = schema.get(variant)
        if isinstance(options, list) and options:
            # Report only what every branch rejects, so a valid alternative is never blamed.
            per_option = [unknown_fields(option, value, where) for option in options]
            return sorted(set.intersection(*(set(item) for item in per_option))) if per_option else []
    if isinstance(schema.get("allOf"), list):
        documented: dict[str, object] = {}
        for option in schema["allOf"]:
            resolved_option = option if isinstance(option, dict) else {}
            documented.update(resolved_option.get("properties") or {})
        schema = {"type": "object", "properties": documented}

    found: list[str] = []
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        if properties:
            for name, item in value.items():
                if name not in properties:
                    found.append(f"{where}.{name}")
                else:
                    found.extend(unknown_fields(properties[name], item, f"{where}.{name}"))
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            found.extend(unknown_fields(schema["items"], item, f"{where}[{index}]"))
    return found


def _schema_name(node: object) -> str:
    """Name of a referenced schema, or '' for an inline one."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        return node["$ref"].rsplit("/", 1)[-1]
    return ""


def _resolve(node: object, spec: dict, depth: int = 0, seen: tuple[str, ...] = ()) -> object:
    """Inline $ref targets, stopping at cycles and at MAX_SCHEMA_DEPTH."""
    if isinstance(node, list):
        return [_resolve(item, spec, depth, seen) for item in node]
    if not isinstance(node, dict):
        return node

    name = _schema_name(node)
    if name:
        if name in seen or depth >= MAX_SCHEMA_DEPTH:
            return {"$ref": name, "hint": f"run: kit.py schema {name}"}
        target = spec.get("components", {}).get("schemas", {}).get(name)
        if target is None:
            return {"$ref": name, "error": "not found in spec"}
        resolved = _resolve(target, spec, depth + 1, seen + (name,))
        if isinstance(resolved, dict):
            resolved = {"$schema_name": name, **resolved}
        return resolved
    return {key: _resolve(value, spec, depth, seen) for key, value in node.items()}


def _type_of(schema: dict) -> str:
    """One-token type label for a property."""
    name = schema.get("$schema_name") or schema.get("$ref")
    enum = schema.get("enum")
    if enum:
        return "enum[" + ",".join(str(value) for value in enum) + "]"
    kind = schema.get("type", "")
    if kind == "array":
        return f"array<{_type_of(schema.get('items') or {})}>"
    if kind == "object" and name:
        return str(name)
    if schema.get("format"):
        return f"{kind}({schema['format']})"
    return str(kind or name or "any")


def _render_schema(schema: dict, indent: str = "  ") -> list[str]:
    """Render an object schema as compact 'name type description' lines."""
    if not isinstance(schema, dict):
        return []
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    if not properties:
        return [f"{indent}(no object properties: {_type_of(schema)})"]

    lines = []
    width = min(max((len(name) for name in properties), default=4) + 1, 26)
    for name, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        mark = "*" if name in required else " "
        note = str(prop.get("description", "")).replace("\n", " ").strip()
        example = prop.get("example")
        if example is not None and not isinstance(example, (dict, list)):
            # Several spec examples contradict their own type: `price` is a
            # string carrying the numeric example 1000.0. Show the value the way
            # it has to be sent, or the example itself teaches a 400.
            if prop.get("type") == "string" and not isinstance(example, str):
                example = json.dumps(str(example), ensure_ascii=False)
            note = f"{note}  e.g. {example}".strip()
        lines.append(f"{indent}{mark} {name:<{width}} {_type_of(prop):<22} {note}".rstrip())
    return lines


def _describe(args: argparse.Namespace) -> int:
    """Print one operation: parameters and the exact request body to build."""
    spec = _load_spec()
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path
    # A concrete path (/v1/variants/<uuid>) resolves to its template, so the
    # contract can be looked up for the request actually about to be sent.
    template, operation = operation_for(method, path)
    if operation is None:
        if template is not None:
            print(
                f"{path} exists but has no {method}. Allowed: {', '.join(allowed_methods(path)) or 'none'}.",
                file=sys.stderr,
            )
        else:
            print(
                f"No such operation: {method} {path}. List them with:  kit.py endpoints {path.split('/')[2] if path.count('/') > 2 else ''}",
                file=sys.stderr,
            )
        return EXIT_USAGE

    content_type, body_ref = request_body_content(method, path)
    body_ref = body_ref or {}
    if args.raw:
        print(json.dumps(_resolve(operation, spec), ensure_ascii=False, indent=2))
        return EXIT_OK

    print(f"{method} {template}  — {operation.get('summary', '')}")
    if operation.get("operationId"):
        print(f"operationId: {operation['operationId']}")

    parameters = [_resolve(item, spec) for item in operation.get("parameters", [])]
    if parameters:
        print("\nparameters:")
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            mark = "*" if parameter.get("required") else " "
            schema = parameter.get("schema") or {}
            print(
                f"  {mark} {parameter.get('name', ''):<20} {parameter.get('in', ''):<6} "
                f"{_type_of(schema):<24} {str(parameter.get('description', '')).strip()}"
            )

    name = _schema_name(body_ref)
    if body_ref:
        label = f" ({name})" if name else ""
        merge_patch = content_type == "application/merge-patch+json"
        print(f"\nrequest body{label}, Content-Type: {content_type}:")
        for line in _render_schema(_resolve(body_ref, spec)):
            print(line)
        print("\n* = required. Nested objects show as a schema name — expand with:  kit.py schema <Name>")
        if merge_patch:
            print(
                "JSON Merge Patch: send only the fields you change. `null` clears a field "
                "only where the schema marks it nullable."
            )
        print(f"Check a drafted body offline with:  kit.py validate {method} {path} --data @body.json")
    else:
        print("\nrequest body: none")

    responses = operation.get("responses", {})
    ok = responses.get("200") or responses.get("201") or {}
    response_name = _schema_name(ok.get("content", {}).get("application/json", {}).get("schema", {}))
    if response_name:
        print(f"\nresponse: {response_name}  (kit.py schema {response_name})")
    return EXIT_OK


def _schema(args: argparse.Namespace) -> int:
    """Print one named schema from the specification."""
    spec = _load_spec()
    schemas = spec.get("components", {}).get("schemas", {})
    if args.name not in schemas:
        matches = [name for name in schemas if args.name.lower() in name.lower()]
        print(
            f"No schema named {args.name!r}."
            + (f" Did you mean: {', '.join(sorted(matches)[:10])}?" if matches else ""),
            file=sys.stderr,
        )
        return EXIT_USAGE
    resolved = _resolve({"$ref": f"#/components/schemas/{args.name}"}, spec)
    if args.raw:
        print(json.dumps(resolved, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{args.name}  — {resolved.get('description', '') if isinstance(resolved, dict) else ''}".rstrip(" —"))
    for line in _render_schema(resolved if isinstance(resolved, dict) else {}):
        print(line)
    return EXIT_OK


def _endpoints(args: argparse.Namespace) -> int:
    """List operations, optionally filtered by a substring."""
    spec = _load_spec()
    needle = (args.pattern or "").lower()
    rows = [
        (method, path, operation)
        for method, path, operation in _spec_operations(spec)
        if not needle
        or needle in path.lower()
        or needle in str(operation.get("summary", "")).lower()
        or needle in str(operation.get("operationId", "")).lower()
    ]
    for method, path, operation in sorted(rows, key=lambda row: (row[1], row[0])):
        body = _schema_name(
            operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        )
        print(f"{method:6} {path:58} {operation.get('summary', '')}" + (f"  [{body}]" if body else ""))
    print(f"\n{len(rows)} operation(s). Details:  kit.py describe <METHOD> <path>", file=sys.stderr)
    return EXIT_OK


def _validate(args: argparse.Namespace) -> int:
    """Check a drafted request body against the documented schema — offline."""
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path
    template, operation = operation_for(method, path)
    if operation is None:
        if template is not None:
            print(
                f"{path} exists but has no {method}. Allowed: {', '.join(allowed_methods(path)) or 'none'}.",
                file=sys.stderr,
            )
        else:
            print(f"No such operation: {method} {path}. Find it with:  kit.py endpoints", file=sys.stderr)
        return EXIT_USAGE

    body = _parse_body(args.data)
    content_type, body_ref = request_body_content(method, path)
    name = _schema_name(body_ref or {})
    print(f"{method} {template}" + (f"  ({name}, {content_type})" if name else ""), flush=True)
    errors, warnings = _report_body_validation(method, path, body)
    if errors:
        print("INVALID — fix the body and validate again; nothing was sent.")
        return EXIT_USAGE
    if warnings:
        print("VALID — required fields, types and enums check out, but see the warning(s) above.")
        return EXIT_OK
    print("VALID — the body satisfies the documented schema.")
    return EXIT_OK


def _list_payload(payload: object) -> tuple[list, int | None]:
    """Unwrap a list response: {"<resource>": [...], "total_count": N}."""
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return [], None
    total = payload.get("total_count")
    for value in payload.values():
        if isinstance(value, list):
            return value, total if isinstance(total, int) else None
    return [], total if isinstance(total, int) else None


_FIELD_SEGMENT = re.compile(r"^([^\[\]]+)((?:\[\d*\])*)$")
_FIELD_BRACKET = re.compile(r"\[(\d*)\]")
# Tells "this record has no such path" apart from "the path is there and empty".
# The first is a broken column, the second is data — and only the first is a bug.
_MISSING = object()


def _parse_field_path(column: str) -> list[tuple[str, object]] | None:
    """Parse `a.b`, `a[].b`, `a[0].b` into resolver steps; None if unparseable."""
    steps: list[tuple[str, object]] = []
    for segment in column.split("."):
        match = _FIELD_SEGMENT.match(segment)
        if not match:
            return None
        steps.append(("key", match.group(1)))
        for index in _FIELD_BRACKET.findall(match.group(2)):
            steps.append(("each", None) if index == "" else ("index", int(index)))
    return steps


def _resolve_field(value: object, steps: list[tuple[str, object]]) -> object:
    """Return the values at `steps` as a list, or `_MISSING` if the path is absent."""
    if not steps:
        return [value]
    (kind, arg), rest = steps[0], steps[1:]
    if kind == "key":
        if not isinstance(value, dict) or arg not in value:
            return _MISSING
        return _resolve_field(value[arg], rest)
    if not isinstance(value, list):
        return _MISSING
    if kind == "index":
        if not -len(value) <= arg < len(value):
            return _MISSING
        return _resolve_field(value[arg], rest)
    collected: list = []
    resolved_any = False
    for element in value:
        found = _resolve_field(element, rest)
        if found is not _MISSING:
            resolved_any = True
            collected.extend(found)
    # An empty list is data — the collection is there and holds nothing. A list of
    # elements where not one has the rest of the path is a typo in the path.
    if value and not resolved_any:
        return _MISSING
    return collected


def _csv_cell(value: object) -> str:
    """Scalars as themselves; anything nested as compact JSON, never a Python repr."""
    if value is None:
        return ""
    # Before the int branch: bool is a subclass of int, so the order is the check.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _csv_field_cell(values: object) -> str:
    """Render one resolved path: nothing found, a single value, or several joined."""
    if values is _MISSING or not values:
        return ""
    if len(values) == 1:
        return _csv_cell(values[0])
    return "; ".join(_csv_cell(value) for value in values)


def _top_level_fields(items: list) -> list[str]:
    """Every top-level field present in the collection, in first-seen order."""
    fields: list[str] = []
    for item in items:
        if isinstance(item, dict):
            fields.extend(key for key in item if key not in fields)
    return fields


def _csv_columns(items: list, requested: str | None) -> list[str]:
    """Explicit columns if asked for, else every top-level field in first-seen order."""
    if requested:
        return [name.strip() for name in requested.split(",") if name.strip()]
    return _top_level_fields(items)


def _column_paths(columns: list[str], *, literal: bool) -> list:
    """Resolver steps for each column.

    Default columns are literal key names read off the data, so a key that happens
    to contain a dot or a bracket stays one column instead of being split into a
    path. Only names the caller typed in `--fields` are read as paths.
    """
    if literal:
        return [[("key", column)] for column in columns]
    return [_parse_field_path(column) for column in columns]


def _unknown_columns(items: list, columns: list[str], paths: list) -> list[str]:
    """Return the requested columns that no item in the collection has at all."""
    unknown: list[str] = []
    for column, steps in zip(columns, paths):
        if steps is None or not any(_resolve_field(item, steps) is not _MISSING for item in items):
            unknown.append(column)
    return unknown


def _write_csv(items: list, columns: list[str], paths: list) -> None:
    """Write the collection to stdout as CSV, leaving stderr for the coverage verdict."""
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(
            [_csv_field_cell(_resolve_field(item, steps)) if steps else "" for steps in paths]
        )
    sys.stdout.flush()


def _emit_csv(items: list, requested: str | None, path: str) -> int:
    """Write the CSV, refusing first if a requested column exists in no item.

    A column the data cannot fill is the quiet way to a wrong report: the export
    comes out with the header, N empty cells, `coverage: complete` and exit `0`,
    and the empty column then reads as a statement about the store ("these orders
    have no delivery service") instead of a typo in `--fields`. The same class of
    silence is refused in a request body by `validate`, so it is refused here.
    """
    columns = _csv_columns(items, requested)
    paths = _column_paths(columns, literal=requested is None)
    if requested and items:
        unknown = _unknown_columns(items, columns, paths)
        if unknown:
            available = _top_level_fields(items)
            print(
                f"No such field(s) in {path}: {', '.join(unknown)}. Nothing was written: "
                "an empty column would be read as a fact about the store, not as a wrong "
                "field name.\n"
                f"Available top-level fields: {', '.join(available) if available else '(none)'}.\n"
                "A nested value needs an explicit path — `chunks[].type` takes it from every "
                "element, `chunks[0].type` from the first one. Quote the whole --fields value: "
                "an unquoted [ ] is a filename pattern to the shell.",
                file=sys.stderr,
            )
            return EXIT_USAGE
    _write_csv(items, columns, paths)
    if requested and not items:
        print(
            "The collection is empty, so the requested columns were not checked against real "
            "data — a header alone does not confirm that the field names exist.",
            file=sys.stderr,
        )
    return EXIT_OK


def _list(args: argparse.Namespace) -> int:
    """Read every page of a collection and state exactly how much was covered.

    A single unpaginated read is the quiet way to a wrong report: 100 of 137
    orders look like the whole store. This walks the collection to the end and
    labels the result `complete` or `partial`, so a partial read can never be
    presented as the full picture.
    """
    path = args.path if args.path.startswith("/") else "/" + args.path
    if any(c.isspace() for c in path):
        print(f"Bad endpoint path (contains whitespace): {path!r}.", file=sys.stderr)
        return EXIT_USAGE
    # page/per_page are owned by the walk itself.
    extra = [pair for pair in (args.query or []) if pair.split("=", 1)[0] not in ("page", "per_page")]

    items: list = []
    total_count: int | None = None
    pages_read = 0
    coverage = "complete"
    note: str | None = None

    for page in range(1, args.max_pages + 1):
        query = _parse_query(extra + [f"page={page}", f"per_page={args.per_page}"])
        try:
            _status, payload = _request(
                "GET",
                f"{_base_url(args)}{path}{query}",
                body=None,
                **_telemetry(args),
            )
        except ApiError as error:
            coverage, note = "partial", f"page {page} failed: {error}"
            break
        chunk, total = _list_payload(payload)
        pages_read += 1
        if total is not None:
            total_count = total
        items.extend(chunk)
        if not chunk or len(chunk) < args.per_page:
            break
        if total_count is not None and len(items) >= total_count:
            break
    else:
        if total_count is None or len(items) < total_count:
            coverage = "partial"
            note = f"stopped at the --max-pages ceiling ({args.max_pages})"

    if coverage == "complete" and total_count is not None and len(items) < total_count:
        coverage = "partial"
        note = f"received {len(items)} of {total_count} reported by the API"

    if getattr(args, "redact", False):
        items = redact_payload(items)
    envelope = {
        "path": path,
        "coverage": coverage,
        "received": len(items),
        "total_count": total_count,
        "pages_read": pages_read,
        "per_page": args.per_page,
        "items": items,
    }
    if note:
        envelope["coverage_note"] = note
    if getattr(args, "format", "json") == "csv":
        # Before the coverage verdict: a refused export must never be followed by
        # `coverage: complete`, which would read as a verdict on the whole thing.
        status = _emit_csv(items, getattr(args, "fields", None), path)
        if status != EXIT_OK:
            return status
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2), flush=True)

    if coverage == "complete":
        print(f"coverage: complete — {len(items)} item(s) over {pages_read} page(s).", file=sys.stderr)
        return EXIT_OK
    print(
        f"coverage: partial — {len(items)} of "
        f"{total_count if total_count is not None else 'unknown'} item(s) over {pages_read} page(s): {note}.\n"
        "State this coverage in the report. A partial read never supports «всё в порядке».",
        file=sys.stderr,
    )
    return EXIT_OK if pages_read else EXIT_API_ERROR


def _read_prompt(args: argparse.Namespace) -> str:
    """Take the prompt from --prompt, --prompt-file or stdin and vet it locally."""
    if args.prompt is not None:
        prompt = args.prompt
    elif args.prompt_file is not None:
        try:
            prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            print(f"Cannot read prompt file: {error}", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        print(
            "No prompt provided. Use --prompt, --prompt-file, or pipe the prompt through stdin.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)

    prompt = prompt.strip()
    if not prompt:
        print("Prompt must not be blank — the API rejects blank prompts with 400.", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    if TOKEN_PATTERN.search(prompt):
        print(
            "Prompt contains a token-shaped value. Prompts are sent to an AI service — remove the secret.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)
    # SKILL.md promises no customer personal data ever reaches the AI service.
    # Until this check existed that promise was prose; now it is enforced.
    for pattern, what in ((EMAIL_PATTERN, "an email address"), (PHONE_PATTERN, "a phone number")):
        if pattern.search(prompt):
            print(
                f"Prompt contains {what}. Prompts are sent to an AI service, so customer personal "
                "data must not travel in them. Ask about the order or the customer by id or number "
                "instead, and look the person up through the API.",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_USAGE)
    return prompt


def _ask(args: argparse.Namespace) -> int:
    """Ask one AI-assistant agent about this store and print its answer.

    The verb is POST, but the call only reads: the agent replies with generated
    text and changes nothing in the store, so it stays outside the --confirm
    gate that guards real mutations.
    """
    url = f"{_experimental_base_url(args)}{AI_AGENTS[args.agent]}"
    body = {"prompt": _read_prompt(args)}

    if args.dry_run:
        print(json.dumps({"method": "POST", "url": url, "body": body}, ensure_ascii=False, indent=2))
        return EXIT_OK

    print(
        f"Asking the {args.agent} agent — this usually takes 10-60 seconds "
        f"(giving up after {AI_AGENT_TIMEOUT_SECONDS}s).",
        file=sys.stderr,
    )
    try:
        _status, result = _request(
            "POST",
            url,
            body=body,
            timeout=AI_AGENT_TIMEOUT_SECONDS,
            **_telemetry(args),
        )
    except ApiError as error:
        # The agents only generate text, so a failed ask is never ambiguous:
        # nothing in the store could have changed. Asking again is safe.
        print(str(error), file=sys.stderr)
        return EXIT_API_ERROR
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Flags that work both before and after the subcommand."""
    parser.add_argument("--base-url", help="Override API base URL (else KIT_API_BASE_URL, else default).")
    parser.add_argument("--confirm", action="store_true", help="Required to run mutating operations.")
    parser.add_argument("--dry-run", action="store_true", help="Print the request without sending it.")
    parser.add_argument(
        "--redact",
        action="store_true",
        help="Mask customer personal data and free-text notes in the printed response "
        "(output only — the request is unchanged).",
    )
    parser.add_argument("--skill-prompt", help="User prompt that initiated the skill call.")
    parser.add_argument("--skill-session-id", type=uuid.UUID, help="Stable UUID for the current user thread.")
    parser.add_argument(
        "--skill-model",
        help="Optional: the model running this skill, e.g. claude-opus-5. Omitted if not passed.",
    )
    parser.add_argument(
        "--skill-harness",
        help="Optional: the agent running this skill, e.g. claude-code or codex. "
        "Omitted if not passed.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Send a mutating request even if its body violates the bundled spec "
        "(only for an endpoint the spec does not cover yet).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Яндекс.Кит public-API client (https://api.kit.yandex.net). "
        "One token = one store. Endpoint map: references/api.md."
    )
    _add_common_flags(parser)

    # The same flags on every subcommand, so `api POST … --confirm` works as
    # documented and not only `--confirm api POST …`. SUPPRESS keeps an unused
    # subcommand flag from overwriting the value parsed before the subcommand.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    _add_common_flags(common)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "env",
        parents=[common],
        help="Show the active contour: base URL and token source (never the token).",
    )
    sub.add_parser("whoami", parents=[common], help="Current API user (GET /v1/users/current).")

    endpoints = sub.add_parser(
        "endpoints",
        parents=[common],
        help="List API operations from the bundled spec (offline, no token needed).",
    )
    endpoints.add_argument("pattern", nargs="?", help="Filter by substring of path, summary or operationId.")

    describe = sub.add_parser(
        "describe",
        parents=[common],
        help="Show one operation's parameters and exact request body (offline).",
        description="Read the request contract from the bundled spec before building a body. "
        "Example: kit.py describe POST /v1/variants",
    )
    describe.add_argument("method", type=str.upper, choices=sorted({"GET", *MUTATING_METHODS}))
    describe.add_argument("path", help="Endpoint path, e.g. /v1/variants.")
    describe.add_argument("--raw", action="store_true", help="Print the resolved spec fragment as JSON.")

    validate = sub.add_parser(
        "validate",
        parents=[common],
        help="Check a request body against the bundled spec (offline, no token, nothing sent).",
        description="Validate a drafted body before showing it to the user. "
        "Example: kit.py validate POST /v1/variants --data @body.json",
    )
    validate.add_argument("method", type=str.upper, choices=sorted({"GET", *MUTATING_METHODS}))
    validate.add_argument("path", help="Endpoint path, concrete or templated, e.g. /v1/variants/<id>.")
    validate.add_argument("--data", help="JSON body: inline string, @path/to/file.json, or '-' to read stdin.")

    schema = sub.add_parser("schema", parents=[common], help="Show one named schema from the bundled spec (offline).")
    schema.add_argument("name", help="Schema name, e.g. CreateVariantRequest.")
    schema.add_argument("--raw", action="store_true", help="Print the resolved schema as JSON.")
    sub.add_parser("store", parents=[common], help="Store info: id, slug, storefront URL (GET /v1/store).")

    token = sub.add_parser("token", help="Local token management (no API calls).").add_subparsers(
        dest="token_command", required=True
    )
    token.add_parser("save", help="Prompt for the token (hidden input) and save it to the default file.")
    token.add_parser("status", help="Show where the token comes from — never prints the token.")

    api = sub.add_parser(
        "api",
        parents=[common],
        help="Call any public-API endpoint.",
        description="Call any endpoint from references/api.md, e.g.: "
        "api GET /v1/products -q limit=10 · api POST /v1/categories --data '{...}' --confirm",
    )
    api.add_argument("method", choices=sorted({"GET", *MUTATING_METHODS}), type=str.upper, help="HTTP method.")
    api.add_argument("path", help="Endpoint path, e.g. /v1/products.")
    api.add_argument(
        "-q",
        "--query",
        action="append",
        metavar="KEY=VALUE",
        help="Query parameter (repeatable), e.g. -q limit=10 -q status=created.",
    )
    api.add_argument(
        "--data",
        help="JSON body: inline string, @path/to/file.json, or '-' to read stdin.",
    )

    listing = sub.add_parser(
        "list",
        parents=[common],
        help="Read every page of a collection and report exact coverage.",
        description="Walk a paginated collection to the end and print "
        "{coverage, received, total_count, pages_read, items}. Use it instead of a single "
        "api GET whenever the answer depends on how many objects there are.",
    )
    listing.add_argument("path", help="List endpoint path, e.g. /v1/orders.")
    listing.add_argument(
        "-q",
        "--query",
        action="append",
        metavar="KEY=VALUE",
        help="Query parameter (repeatable), e.g. -q status=ACTIVE. page/per_page are managed by the walk.",
    )
    listing.add_argument(
        "--per-page", type=int, default=DEFAULT_PAGE_SIZE, help=f"Page size (API max 100, default {DEFAULT_PAGE_SIZE})."
    )
    listing.add_argument(
        "--max-pages",
        type=int,
        default=MAX_LIST_PAGES,
        help=f"Stop after this many pages and report partial coverage (default {MAX_LIST_PAGES}).",
    )
    listing.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output shape. csv writes one row per item to stdout; coverage still goes to stderr.",
    )
    listing.add_argument(
        "--fields",
        help="Comma-separated columns for --format csv, e.g. --fields order_number,created_at,status. "
        "Nested values take a path: chunks[].type (from every element, joined by '; ') or "
        "chunks[0].type (from the first one) — quote the value, since an unquoted [ ] is a "
        "filename pattern to the shell. A column no item has is refused, not left empty. "
        "Default: every top-level field seen, in first-seen order.",
    )

    upload = sub.add_parser(
        "upload", parents=[common], help="Upload a file (multipart POST /v1/files, needs --confirm)."
    )
    upload.add_argument("file", help="Path to the file to upload (max 100 MB).")

    ask = sub.add_parser(
        "ask",
        parents=[common],
        help="Ask an AI-assistant agent about this store (slow: tens of seconds).",
        description="Send one natural-language prompt to a Кит AI-assistant agent and print its answer. "
        "Read-only: agents answer in text and never change the store.",
    )
    ask_agents = ask.add_subparsers(dest="agent", required=True)
    for agent, help_text in (
        (
            "analyst",
            "Store analytics: sales, orders, traffic, catalog — arbitrary reports from a prompt.",
        ),
        (
            "support",
            "How-to questions about the cabinet and its features, answered from the documentation.",
        ),
    ):
        agent_parser = ask_agents.add_parser(agent, help=help_text)
        agent_parser.add_argument("--prompt", help="Prompt text.")
        agent_parser.add_argument("--prompt-file", help="Path to a file holding the prompt.")
        agent_parser.set_defaults(agent=agent)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "token":
        if args.token_command == "save":
            return _token_save()
        if args.token_command == "status":
            return _token_status()

    if args.command == "env":
        return _env_status(args)

    if args.command == "endpoints":
        return _endpoints(args)

    if args.command == "describe":
        return _describe(args)

    if args.command == "validate":
        return _validate(args)

    if args.command == "schema":
        return _schema(args)

    if args.command == "whoami":
        return _call(args, "GET", "/v1/users/current", body=None)

    if args.command == "store":
        return _call(args, "GET", "/v1/store", body=None)

    if args.command == "api":
        return _call(args, args.method, args.path, body=_parse_body(args.data))

    if args.command == "list":
        return _list(args)

    if args.command == "ask":
        return _ask(args)

    if args.command == "upload":
        file_path = Path(args.file).expanduser()
        if not file_path.is_file():
            print(f"File not found: {file_path}", file=sys.stderr)
            return EXIT_USAGE
        return _call(args, "POST", "/v1/files", body=None, upload_file=file_path)

    print("Unknown command.", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
