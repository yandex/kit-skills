"""Shared primitives: errors, redaction, JSON output, HTTP, content validation."""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import TextIO

# One prefix contract, shared by the entry surfaces and by redaction: a token is
# only accepted in the shape `redact_secrets` below can recognise, so a token
# this client holds can never be one it fails to hide.
TOKEN_PREFIX = "yakit_"
TOKEN_PATTERN = re.compile(TOKEN_PREFIX + r"[A-Za-z0-9._~-]+")
REQUEST_TIMEOUT_SECONDS = 30
SKILL_HEADER: str = "X-Skill"
SKILL_NAME: str = "kit-storefront-constructor"
# Дата публикации, `YYYY.MM.DD[.N]`.
SKILL_VERSION: str = "2026.08.31"
SKILL_VERSION_HEADER: str = "X-Skill-Version"
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
REQUIRED_CONTENT_KEYS = {
    "version",
    "pages",
    "global_settings",
    "section_templates",
    "section_template_fragments",
}


class UsageError(Exception):
    """Invalid command input or local configuration."""


class ApiError(Exception):
    """Public API or network failure."""

    def __init__(self, status: "int | None", payload: object) -> None:
        super().__init__(str(payload))
        self.status = status
        self.payload = payload


def redact_secrets(value: str) -> str:
    """Remove Kit token-shaped values from text.

    Operates on raw text, which makes it safe to run on a JSON document *before*
    it is parsed: a bare token is not valid JSON, so `[REDACTED]` can only ever
    land inside a string literal and the document stays well-formed. Redacting
    first — rather than parsing first and redacting the leftovers — is what keeps
    a token out of an error body that happens to be well-formed, and it covers
    object keys as well as values.

    Known gap: a token spelled with JSON `\\uXXXX` escapes matches only up to the
    backslash and is redacted partially. That predates this function; widening
    the pattern to chase escapes would cost more than it buys.
    """
    return TOKEN_PATTERN.sub("[REDACTED]", value)


def telemetry_enabled() -> bool:
    """Telemetry is on unless the user explicitly opts out."""
    flag = os.environ.get(TELEMETRY_OPT_OUT_VARIABLE, "").strip().lower()
    return flag not in ("1", "true", "yes", "on")


def header_safe(value: str) -> str:
    """Reduce free text to something that can only ever be one header value.

    A value that reached the wire with a newline in it would let a caller append
    headers of their own, so everything outside printable ASCII is dropped rather
    than escaped, and the result is capped. Tokens are redacted first, on the same
    grounds as the prompt: this text ends up in the server's log.
    """
    cleaned = "".join(char for char in redact_secrets(value) if 32 <= ord(char) < 127)
    return cleaned.strip()[:SKILL_META_MAX_LENGTH]


def running_os() -> str:
    """This machine's OS, coarse enough to group by and no finer."""
    system = platform.system() or "unknown"
    release = platform.release() or ""
    return header_safe(f"{system} {release}".strip())


def skill_headers(
    prompt: "str | None" = None,
    session_id: "str | None" = None,
    model: "str | None" = None,
    harness: "str | None" = None,
) -> "dict[str, str]":
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
    if model and header_safe(model):
        headers[SKILL_MODEL_HEADER] = header_safe(model)
    if harness and header_safe(harness):
        headers[SKILL_HARNESS_HEADER] = header_safe(harness)
    return headers


def emit_json(value: object, *, stream: "TextIO | None" = None) -> None:
    """Print stable, sanitized JSON."""
    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    print(redact_secrets(serialized), file=stream if stream is not None else sys.stdout)


def canonical_json(value: object) -> str:
    """Serialize deterministically for on-disk snapshots and digests."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def require_mapping(value: object, label: str) -> "dict[str, object]":
    """Require a JSON object and return it with a precise type."""
    if not isinstance(value, dict):
        raise UsageError(f"{label} must be a JSON object.")
    return value


def required_typed(value: "dict[str, object]", key: str, expected: type, label: str) -> object:
    """Require one typed field, rejecting bool-as-int."""
    item = value.get(key)
    if expected is int:
        valid = isinstance(item, int) and not isinstance(item, bool)
    else:
        valid = isinstance(item, expected)
    if not valid:
        raise UsageError(f"{label}.{key} must be {expected.__name__}.")
    return item


def validate_content(value: object, label: str) -> "dict[str, object]":
    """Validate a complete ConstructorContent while preserving extra fields."""
    content = require_mapping(value, label)
    missing = sorted(REQUIRED_CONTENT_KEYS - content.keys())
    if missing:
        raise UsageError(f"{label} is incomplete; missing fields: {', '.join(missing)}.")

    version = content["version"]
    if not isinstance(version, dict):
        raise UsageError(f"{label}.version must be a JSON object.")
    identifier = version.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        raise UsageError(f"{label}.version.id must be an integer.")
    if not isinstance(content["pages"], list):
        raise UsageError(f"{label}.pages must be an array.")
    if not isinstance(content["global_settings"], dict):
        raise UsageError(f"{label}.global_settings must be a JSON object.")
    if not isinstance(content["section_templates"], list):
        raise UsageError(f"{label}.section_templates must be an array.")
    if not isinstance(content["section_template_fragments"], list):
        raise UsageError(f"{label}.section_template_fragments must be an array.")
    return content


def validated_pages(content: "dict[str, object]") -> "list[dict[str, object]]":
    """Validate the pages array of ConstructorContent."""
    pages = content.get("pages")
    if not isinstance(pages, list):
        raise UsageError("Constructor content.pages must be an array.")
    validated = []
    for index, item in enumerate(pages):
        page = require_mapping(item, f"Constructor content.pages[{index}]")
        label = f"Constructor content.pages[{index}]"
        required_typed(page, "id", str, label)
        required_typed(page, "title", str, label)
        required_typed(page, "status", str, label)
        required_typed(page, "variant_type", str, label)
        required_typed(page, "layout", list, label)
        alias = page.get("alias")
        if alias is not None and not isinstance(alias, str):
            raise UsageError(f"{label}.alias must be str when present.")
        validated.append(page)
    return validated


def content_version_id(content: "dict[str, object]") -> int:
    """Return the validated ConstructorContent version id."""
    version = content["version"]
    assert isinstance(version, dict)
    identifier = version["id"]
    assert isinstance(identifier, int) and not isinstance(identifier, bool)
    return identifier


# One rule, three copies — this handler is duplicated verbatim in
# `kit-store-checkup/scripts/checkup.py` and `yandex-kit-cabinet/scripts/kit.py`,
# because every skill installs as a standalone directory and cannot import a
# sibling. Change one, change all three.
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
    leaves urllib to raise the 3xx as an `HTTPError`, which the callers below
    turn into an explicit refusal rather than a puzzling error body.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - stdlib signature
        if redirect_stays_on_origin(req.full_url, newurl):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def build_pinned_opener(context: "ssl.SSLContext | None" = None) -> urllib.request.OpenerDirector:
    """An opener that behaves like `urlopen` but refuses off-origin redirects."""
    handlers: "list[urllib.request.BaseHandler]" = [OriginPinnedRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def redirect_refusal_message(code: int, location: "str | None") -> str:
    """The message a declined redirect gets, in place of the 3xx body."""
    target = f" to {location}" if location else ""
    return (
        f"Server answered {code} with a redirect{target} off the configured host. "
        "The request was not followed: it carries the store token, and a redirect "
        "would hand that token to another host. Check the configured API URL."
    )


def http_json(
    method: str,
    url: str,
    *,
    headers: "dict[str, str] | None" = None,
    body: "dict[str, object] | None" = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> "tuple[int, dict[str, str], object]":
    """Send one unauthenticated JSON request; return status, headers, decoded body.

    304 responses return an empty body instead of raising. Any other non-2xx
    raises ApiError, whose payload has been through `redact_secrets` — see the
    error path below for why that happens before the body is parsed.
    """
    data = None
    # The storefront needs no token, but it is still this skill's traffic: it
    # carries `X-Skill` like every other request the client sends, and drops it on
    # the same opt-out. Everything else — prompt, session, model, harness, OS —
    # stays on the authenticated API. The prompt and the session are user content
    # and this is a different service with a different log; model, harness and OS
    # are not, but without the session there is nothing here to join them to, so
    # sending them would add a column nobody can read. Deliberate, not forgotten.
    request_headers = {"Accept": "application/json"}
    if telemetry_enabled():
        request_headers[SKILL_HEADER] = SKILL_NAME
    if headers:
        request_headers.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with build_pinned_opener().open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return 304, {key.lower(): value for key, value in error.headers.items()}, {}
        if 300 <= error.code < 400:
            raise ApiError(error.code, redirect_refusal_message(error.code, error.headers.get("Location"))) from None
        raw = error.read().decode("utf-8", "replace")
        # Redact before parsing, not after: a well-formed error body is the
        # common case, so redacting only the unparseable one would leave the
        # usual path unprotected. `ApiError` puts the payload into the exception
        # message too, which is read by handlers that never go through
        # `emit_json`.
        redacted = redact_secrets(raw)
        try:
            payload: object = json.loads(redacted) if redacted else {}
        except json.JSONDecodeError:
            payload = redacted
        raise ApiError(error.code, payload) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ApiError(None, redact_secrets(str(error))) from None

    if not raw:
        return status, response_headers, {}
    try:
        return status, response_headers, json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(status, "API returned invalid JSON.") from None
