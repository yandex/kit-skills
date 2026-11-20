#!/usr/bin/env python3
"""Fixed-route client for the Яндекс.Кит experimental constructor.

Reads, section-template creation, confirmed media uploads, the BFF schema engine,
workspace-based storefront editing, and the confirmed full-replace write.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Sequence, TextIO

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from kitlib import menu as menu_engine  # noqa: E402
from kitlib import pages as pages_engine  # noqa: E402
from kitlib import schema as schema_engine  # noqa: E402
from kitlib import selfupdate  # noqa: E402
from kitlib import tokenweb  # noqa: E402
from kitlib import workspace as workspace_engine  # noqa: E402
from kitlib import write as write_engine  # noqa: E402
from kitlib.common import (  # noqa: E402
    ApiError,
    TOKEN_PATTERN,
    TOKEN_PREFIX,
    UsageError,
    build_pinned_opener,
    canonical_json,
    content_version_id,
    emit_json,
    redact_secrets,
    redirect_refusal_message,
    require_mapping,
    skill_headers,
    validate_content,
    validated_pages,
)

DEFAULT_BASE_URL = "https://api.kit.yandex.net/api/experimental/v1"
DEFAULT_TOKEN_FILE = Path.home() / ".yandex-kit-skills" / "kit_api.token"
DEFAULT_VERSION_LIMIT = 10
REQUEST_TIMEOUT_SECONDS = 30
# Skill identification headers (`X-Skill`, `X-Skill-Prompt`, `X-Skill-Session`,
# `X-Skill-Model`, `X-Skill-Harness`, `X-Skill-OS`) and the prompt truncation
# limit live in kitlib.common: the storefront transport there tags its own
# requests, and two definitions of the same header would drift.
MAX_MEDIA_SIZE_BYTES = 104857600
IMAGE_EXCLUDED_MIME_TYPES = {
    "image/svg+xml",
    "image/vnd.microsoft.icon",
    "image/x-icon",
}
VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-flv",
    "video/x-msvideo",
}
# Font files a store theme can take as its custom typeface. The server stores anything
# that is not an Avatars-compatible image as a generic file (type OTHER), which is what the
# theme's font sources reference. The content type is set here, not guessed: Python's
# table has no entry for .woff2 before 3.11.
FONT_MIME_TYPES = {
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
TEMPLATE_CREATE_FIELDS = {
    "alias",
    "commit_message",
    "css",
    "html",
    "json_schema",
    "script",
    "title",
}
TEMPLATE_REQUIRED_FIELDS = {
    "css",
    "html",
    "json_schema",
    "title",
}
# The only template aliases with an effect on the storefront: a template carrying one
# of them belongs in the store-wide header or footer slot (see `kitlib.pages`).
CHROME_SLOT_BY_ALIAS = pages_engine.CHROME_SLOT_BY_ALIAS
CHROME_ALIAS_BY_SLOT = {slot: alias for alias, slot in CHROME_SLOT_BY_ALIAS.items()}
TEMPLATE_VERSION_FIELDS = {
    "commit_message",
    "created_at",
    "version_id",
}
# One validation rule, two audiences: the CLI answers in English like the rest of its
# output, the local entry page answers in the language of the cabinet the token comes from.
TOKEN_PROBLEM_MESSAGES_EN = {
    "empty": "Empty token; nothing saved.",
    "malformed": "Token must be a whitespace-free store-scoped yakit_ token; nothing saved.",
}
TOKEN_PROBLEM_MESSAGES_RU = {
    "empty": "Пустое поле — токен не сохранён.",
    "malformed": "Токен магазина начинается с yakit_ и не содержит пробелов — ничего не сохранено.",
}
EXIT_OK = 0
EXIT_API_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
# The write went through, but the server ignored `activate: false` and PUBLISHED the edit:
# the requested no-publish mode was not honored. Distinct so callers can never mistake it
# for a clean preview (0) or for a write that did not happen (1/2/3).
EXIT_PUBLISHED_NOT_PREVIEWED = 4
# A menu or logo write went through, but reading it back did not return what was sent.
# Distinct from 1/2/3, where nothing was written.
EXIT_WRITTEN_NOT_VERIFIED = 5
# The cabinet's limits for a store logo; the server itself checks only where the file is stored.
LOGO_MAX_SIZE_BYTES = 5 * 1024 * 1024
LOGO_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
LOGO_SNAPSHOT_KIND = "logo-snapshot"


def resolve_base_url(override: str | None) -> str:
    """Resolve the already-versioned experimental API base URL."""
    base_url = (override or os.environ.get("KIT_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url:
        raise UsageError("API base URL must not be empty.")
    return base_url


def _token_source() -> tuple[str, str]:
    """Resolve a token and its non-secret source description."""
    token = os.environ.get("KIT_TOKEN", "").strip()
    if token:
        return token, "KIT_TOKEN environment variable"

    configured_path = os.environ.get("KIT_TOKEN_FILE", "").strip()
    token_path = Path(configured_path).expanduser() if configured_path else DEFAULT_TOKEN_FILE
    source = f"KIT_TOKEN_FILE ({token_path})" if configured_path else f"default token file ({token_path})"
    if not token_path.is_file():
        return "", "not found"
    try:
        return token_path.read_text(encoding="utf-8").strip(), source
    except OSError as error:
        raise UsageError(f"Cannot read token file {token_path}: {redact_secrets(str(error))}") from None


def load_token() -> str:
    """Load and validate the store-scoped token without exposing it."""
    token, _source = _token_source()
    if not token:
        raise UsageError(
            "No API token found. Use 'token save', KIT_TOKEN, or KIT_TOKEN_FILE with a store-scoped yakit_ token."
        )
    if any(character.isspace() for character in token) or not token.startswith(TOKEN_PREFIX):
        raise UsageError("Configured token must be a whitespace-free store-scoped yakit_ token.")
    return token


def _token_problem(token: str) -> str | None:
    """Name what is wrong with a candidate token, or None when it is acceptable.

    One rule for every entry surface — the hidden prompt, a pipe, and the local page —
    so a token the CLI refuses can never slip in through the browser.
    """
    if not token:
        return "empty"
    if any(character.isspace() for character in token) or not token.startswith(TOKEN_PREFIX):
        return "malformed"
    return None


def _write_token_file(token: str) -> None:
    """Write the token file itself, owner-only from the moment it exists.

    The mode belongs to `os.open`. A file written first and `chmod`-ed second
    sits there readable by every process of this user until the second call, and
    a crash in that window leaves it that way. `O_CREAT` does not touch the mode
    of a file that already exists, so a `chmod` follows to cover that case.
    """
    DEFAULT_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(DEFAULT_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    try:
        os.chmod(DEFAULT_TOKEN_FILE, 0o600)  # no-op on Windows, fine
    except OSError:
        pass


def _token_save() -> int:
    """Save a token from hidden input or stdin to the default token file."""
    if sys.stdin.isatty():
        token = getpass.getpass("Token: ").strip()
    else:
        token = sys.stdin.read().strip()
    problem = _token_problem(token)
    if problem:
        print(TOKEN_PROBLEM_MESSAGES_EN[problem], file=sys.stderr)
        return EXIT_USAGE
    try:
        _write_token_file(token)
    except OSError as error:
        print(f"Cannot save token to {DEFAULT_TOKEN_FILE}: {redact_secrets(str(error))}", file=sys.stderr)
        return EXIT_USAGE
    print(f"Token saved to {DEFAULT_TOKEN_FILE}", file=sys.stderr)
    # Where the entry page cannot work, this save most likely followed the fallback route:
    # the user sent the token in the conversation. Nothing here can undo that, so say the one
    # thing that still helps — the token is theirs to revoke.
    if tokenweb.unreachable_reason():
        print(
            "This client is not on the user's machine. If the token arrived through the "
            "conversation, tell them it stays in that history and to revoke it in the cabinet "
            "under «Настройки → API» once the work is done.",
            file=sys.stderr,
        )
    return EXIT_OK


def _token_override_warning() -> str | None:
    """Warn when the environment will shadow the file this page writes.

    Worth saying on the page because it is the one case where a successful save
    changes nothing. Said without naming paths: the reader is a shop owner, and
    `token status` answers the technical version of the same question.
    """
    if os.environ.get("KIT_TOKEN", "").strip():
        return (
            "На этом компьютере токен уже задан в настройках окружения — использоваться будет он, "
            "а не тот, что вы введёте здесь."
        )
    if os.environ.get("KIT_TOKEN_FILE", "").strip():
        return (
            "Ассистент настроен брать токен из другого файла — введённый здесь токен "
            "использоваться не будет."
        )
    return None


def _token_web(args: argparse.Namespace) -> int:
    """Serve a one-shot local page for typing the token by hand."""
    saved = tokenweb.serve_token_page(
        token_path=DEFAULT_TOKEN_FILE,
        validate_token=lambda value: TOKEN_PROBLEM_MESSAGES_RU.get(_token_problem(value.strip())),
        save_token=_write_token_file,
        override_warning=_token_override_warning(),
        port=args.port,
        timeout_seconds=args.timeout,
        open_browser=not args.no_open,
        allow_unreachable=args.force,
    )
    return EXIT_OK if saved else EXIT_USAGE


def _token_status() -> int:
    """Report the selected token source without printing the token."""
    token, source = _token_source()
    if token:
        print(f"Token source: {source}")
        return EXIT_OK
    print(f"Token source: not found (checked KIT_TOKEN, KIT_TOKEN_FILE, {DEFAULT_TOKEN_FILE})")
    return EXIT_USAGE


def build_url(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    """Build a URL for a fixed constructor route."""
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def request_json(
    method: str,
    url: str,
    token: str,
    body: dict[str, object] | None = None,
    *,
    raw_data: bytes | None = None,
    content_type: str | None = None,
    skill_prompt: str | None = None,
    skill_session_id: str | None = None,
    skill_model: str | None = None,
    skill_harness: str | None = None,
) -> object:
    """Send one authenticated request and decode its JSON response.

    A non-2xx raises ApiError whose payload has been through `redact_secrets`:
    this request carries the bearer token, so its error body is the one that
    must never reach an exception message unredacted.
    """
    if body is not None and raw_data is not None:
        raise UsageError("Request cannot contain both JSON and raw data.")
    if content_type is not None and raw_data is None:
        raise UsageError("A raw request content type requires raw data.")

    data = raw_data
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    identification = skill_headers(skill_prompt, skill_session_id, skill_model, skill_harness)
    for header_name, header_value in identification.items():
        request.add_header(header_name, header_value)
    if content_type is not None:
        request.add_header("Content-Type", content_type)
    elif body is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with build_pinned_opener().open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            # The server names the current skill versions on every response; the
            # comparison and any reinstall happen once, after the command is done.
            selfupdate.note_response_headers(response.headers)
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # An outdated client meets an error more often than a 200, so the version
        # is read from the failure path too.
        selfupdate.note_response_headers(error.headers)
        if 300 <= error.code < 400:
            raise ApiError(error.code, redirect_refusal_message(error.code, error.headers.get("Location"))) from None
        raw = error.read().decode("utf-8", "replace")
        # Redact before parsing, not after — see `kitlib.common.http_json`.
        redacted = redact_secrets(raw)
        try:
            payload: object = json.loads(redacted) if redacted else {}
        except json.JSONDecodeError:
            payload = redacted
        raise ApiError(error.code, payload) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ApiError(None, redact_secrets(str(error))) from None

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(getattr(response, "status", None), "API returned invalid JSON.") from None


def encode_multipart_file(file_path: Path, media_type: str | None = None) -> tuple[bytes, str]:
    """Encode one local file as the API's required multipart `file` field."""
    boundary = uuid.uuid4().hex
    media_type = media_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    try:
        payload = file_path.read_bytes()
    except OSError as error:
        raise UsageError(f"Cannot read media file {file_path}: {redact_secrets(str(error))}") from None

    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + payload + footer, f"multipart/form-data; boundary={boundary}"


def validate_local_media_file(raw_path: str, media_kind: str) -> tuple[Path, str]:
    """Validate a local image or video without resolving remote sources."""
    file_path = Path(raw_path).expanduser()
    if not file_path.is_file():
        raise UsageError(f"Media source must be an existing local file: {file_path}")
    if any(character in file_path.name for character in ('"', "\r", "\n")):
        raise UsageError("Media filename contains unsupported characters.")

    try:
        size = file_path.stat().st_size
    except OSError as error:
        raise UsageError(f"Cannot inspect media file {file_path}: {redact_secrets(str(error))}") from None
    if size > MAX_MEDIA_SIZE_BYTES:
        raise UsageError(f"Media file exceeds the {MAX_MEDIA_SIZE_BYTES}-byte upload limit.")

    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    if media_kind == "font":
        suffix = file_path.suffix.lower()
        if suffix not in FONT_MIME_TYPES:
            supported = ", ".join(sorted(FONT_MIME_TYPES))
            raise UsageError(f"Font must be one of: {supported}.")
        return file_path, FONT_MIME_TYPES[suffix]
    if media_kind == "image":
        if not media_type.startswith("image/") or media_type in IMAGE_EXCLUDED_MIME_TYPES:
            raise UsageError("Image must use a supported image type; SVG and ICO are not supported.")
    elif media_kind == "video":
        if media_type not in VIDEO_MIME_TYPES:
            supported = ", ".join(sorted(VIDEO_MIME_TYPES))
            raise UsageError(f"Video MIME type must be one of: {supported}.")
    else:
        raise UsageError(f"Unknown media kind: {media_kind}.")

    return file_path, media_type


def require_image_upload_response(value: object) -> dict[str, object]:
    """Validate the cabinet-compatible File upload response."""
    response = require_mapping(value, "Image upload response")
    for field in ("id", "hash", "type", "public_path"):
        field_value = response.get(field)
        if not isinstance(field_value, str) or not field_value:
            raise UsageError(f"Image upload response.{field} must be a non-empty string.")
    if response["type"] != "IMAGE":
        raise UsageError("Image upload response.type must be IMAGE.")
    return response


def require_font_upload_response(value: object) -> dict[str, object]:
    """Validate the File record a font upload must come back as: a generic file (OTHER)."""
    response = require_mapping(value, "Font upload response")
    for field in ("id", "hash", "type", "public_path"):
        field_value = response.get(field)
        if not isinstance(field_value, str) or not field_value:
            raise UsageError(f"Font upload response.{field} must be a non-empty string.")
    if response["type"] != "OTHER":
        raise UsageError(
            f"The server stored this file as {response['type']}, not as a generic file (OTHER); "
            "it cannot serve as a font source. Nothing in the theme references it."
        )
    return response


def require_video_upload_response(value: object) -> dict[str, object]:
    """Validate the asynchronous video upload response."""
    response = require_mapping(value, "Video upload response")
    video_id = response.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        raise UsageError("Video upload response.video_id must be a non-empty string.")
    return response


def parse_version_id(value: str) -> int:
    """Parse an OpenAPI-compatible positive constructor version ID."""
    if not value.isdecimal():
        raise argparse.ArgumentTypeError("version id must be a decimal integer greater than or equal to 1")
    identifier = int(value)
    if identifier < 1:
        raise argparse.ArgumentTypeError("version id must be greater than or equal to 1")
    return identifier


def parse_uuid(value: str) -> str:
    """Parse and normalize a UUID route selector."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError("id must be a valid UUID") from None


def parse_positive_limit(value: str) -> int:
    """Parse a positive local result limit."""
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError("limit must be a decimal integer greater than or equal to 1")
    return int(value)


def parse_local_port(value: str) -> int:
    """Parse the loopback port for the local token page."""
    if not value.isdecimal() or int(value) > 65535:
        raise argparse.ArgumentTypeError("port must be a decimal integer between 0 and 65535")
    return int(value)


def parse_page_timeout(value: str) -> float:
    """Parse how long the local token page may wait for input."""
    if not value.isdecimal() or not 1 <= int(value) <= int(tokenweb.MAX_TIMEOUT_SECONDS):
        raise argparse.ArgumentTypeError(
            f"timeout must be a decimal number of seconds between 1 and {int(tokenweb.MAX_TIMEOUT_SECONDS)}"
        )
    return float(value)


_ISO_DATETIME_PATTERN = re.compile(
    r"^(?P<head>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>[Zz]|[+-]\d{2}:?\d{2}|[+-]\d{2})?$"
)


def _normalize_iso8601(candidate: str) -> str:
    """Rewrite RFC-3339 date-times into the subset fromisoformat accepts before Python 3.11.

    The constructor backend serializes time without padding the fraction, so real responses carry
    1-9 fractional digits and an explicit offset. Python 3.10 and older accept only 3 or 6 digits.
    """
    match = _ISO_DATETIME_PATTERN.match(candidate)
    if match is None:
        return candidate
    head = match.group("head")
    fraction = match.group("fraction") or ""
    offset = match.group("offset") or ""
    if fraction:
        head = f"{head}.{fraction[:6].ljust(6, '0')}"
    if offset in ("Z", "z"):
        offset = "+00:00"
    elif len(offset) == 3:
        offset = f"{offset}:00"
    elif len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return head + offset


def parse_iso8601(value: str, label: str = "date-time") -> datetime.datetime:
    """Parse ISO-8601 dates and date-times and normalize them to UTC."""
    candidate = value.strip()
    if not candidate:
        raise argparse.ArgumentTypeError(f"{label} must be an ISO-8601 date or date-time")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            parsed = datetime.datetime.combine(datetime.date.fromisoformat(candidate), datetime.time())
        else:
            parsed = datetime.datetime.fromisoformat(_normalize_iso8601(candidate))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{label} must be an ISO-8601 date or date-time") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def parse_since(value: str) -> datetime.datetime:
    """Parse the inclusive versions lower bound."""
    return parse_iso8601(value, "since")


def _require_string(
    mapping: dict[str, object],
    field: str,
    label: str,
    *,
    non_empty: bool = False,
) -> str:
    value = mapping.get(field)
    if not isinstance(value, str):
        raise UsageError(f"{label}.{field} must be a string.")
    if non_empty and not value.strip():
        raise UsageError(f"{label}.{field} must not be empty.")
    return value


def validate_template_create(value: object) -> dict[str, object]:
    """Validate and normalize a section-template create request."""
    request = require_mapping(value, "Section template request")
    unsupported = sorted(request.keys() - TEMPLATE_CREATE_FIELDS)
    if unsupported:
        raise UsageError(f"Section template request has unsupported fields: {', '.join(unsupported)}.")

    for field in ("title", "html", "css", "commit_message"):
        _require_string(request, field, "Section template request", non_empty=True)
    schema = _require_string(request, "json_schema", "Section template request")
    try:
        decoded_schema = json.loads(schema)
    except json.JSONDecodeError as error:
        raise UsageError(
            "Section template request.json_schema must contain valid JSON: "
            f"line {error.lineno}, column {error.colno}."
        ) from None
    if not isinstance(decoded_schema, dict):
        raise UsageError("Section template request.json_schema must decode to a JSON object.")
    if "script" in request and not isinstance(request["script"], str):
        raise UsageError("Section template request.script must be a string when provided.")
    if "alias" in request:
        if not isinstance(request["alias"], str):
            raise UsageError("Section template request.alias must be a string when provided.")
        _check_template_alias(request["alias"])
    normalized = {field: request[field] for field in TEMPLATE_CREATE_FIELDS if field in request}
    if normalized.get("alias") == "":
        # "" is not «no alias» to the server: it is stored as a set, empty alias that
        # names nothing. Dropped, the alias is stored as null, the way the cabinet stores
        # it for its own custom sections.
        del normalized["alias"]
    # The API lists `script` among the required template fields; a template without JS sends "".
    normalized.setdefault("script", "")
    return normalized


def _check_template_alias(alias: str) -> None:
    """Refuse an alias this skill does not support; the server stores any string unchecked.

    Accepted: `""` (dropped from the body, see `validate_template_create`) and the two
    chrome values, which make the template a store-wide header or footer. Everything
    else is refused as a policy of this skill, not because the server would reject it:
    an alias naming a native widget is how the cabinet offers a template under
    «Заменить» for that widget, which this skill does not support (and the storefront
    renders sections on a `YandexKit.AnnouncementBar` template in the header area, out of
    the page content); any other string means nothing to the server or the storefront.
    A near-miss of a chrome value is a typo that would silently be neither header nor
    footer.
    """
    if alias == "" or alias in CHROME_SLOT_BY_ALIAS:
        return
    for known in CHROME_SLOT_BY_ALIAS:
        if alias.strip().casefold() == known.casefold():
            raise UsageError(
                f"Section template request.alias {alias!r} is not {known!r}: the server stores the "
                "value verbatim, and a template with a misspelt alias is neither header nor footer. "
                f"Leave alias out and pass --chrome-slot {CHROME_SLOT_BY_ALIAS[known]}, which sets it."
            )
    raise UsageError(
        f"Section template request.alias {alias!r} is not supported by this skill. Leave alias out "
        "for a regular custom section. A store-wide header or footer is made with --chrome-slot "
        "header|footer, which sets the alias itself. An alias naming a native widget (the cabinet "
        "offers such a template under «Заменить» for that widget) is not supported here; any other "
        "value means nothing to the server or the storefront."
    )


def load_template_create(path: Path) -> dict[str, object]:
    """Load and validate a section-template create request from disk."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"Cannot read section template file {path}: {redact_secrets(str(error))}") from None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise UsageError(
            f"Section template file {path} is not valid JSON: line {error.lineno}, column {error.colno}."
        ) from None
    return validate_template_create(value)


def _validate_section_template(value: object, label: str) -> dict[str, object]:
    """Validate one template record of a response.

    Unknown fields are tolerated on purpose. The template contract grows, and a
    validator demanding «no extra fields» turns a server rollout into a failure after
    a successful create: the template exists, the client reports an error, and a retry
    duplicates it, because there is no DELETE.
    """
    template = require_mapping(value, label)
    required_fields = TEMPLATE_REQUIRED_FIELDS | {"id", "created_at", "updated_at"}
    missing = sorted(required_fields - template.keys())
    if missing:
        raise UsageError(f"{label} is incomplete; missing fields: {', '.join(missing)}.")
    try:
        uuid.UUID(_require_string(template, "id", label))
    except (ValueError, AttributeError):
        raise UsageError(f"{label}.id must be a UUID.") from None
    for field in ("title", "html", "css", "created_at", "updated_at"):
        _require_string(template, field, label)
    schema = _require_string(template, "json_schema", label)
    try:
        json.loads(schema)
    except json.JSONDecodeError as error:
        raise UsageError(f"{label}.json_schema must contain valid JSON: {error.msg}.") from None
    for field in ("alias", "script"):
        if field in template and not isinstance(template[field], str):
            raise UsageError(f"{label}.{field} must be a string when provided.")
    return template


def validate_section_template_list(value: object) -> list[dict[str, object]]:
    """Validate the section-template catalog response."""
    if not isinstance(value, list):
        raise UsageError("Section template catalog must be a JSON array.")
    return [
        _validate_section_template(template, f"Section template catalog[{index}]")
        for index, template in enumerate(value)
    ]


def validate_created_section_template(value: object) -> dict[str, object]:
    """Validate the flat 201 response returned after template creation."""
    template = _validate_section_template(value, "Created section template")
    version = require_mapping(template.get("version"), "Created section template.version")
    missing_version_fields = TEMPLATE_VERSION_FIELDS - version.keys()
    if missing_version_fields:
        raise UsageError(
            "Created section template.version is incomplete; missing fields: "
            f"{', '.join(sorted(missing_version_fields))}."
        )
    for field in ("version_id", "commit_message", "created_at"):
        _require_string(version, field, "Created section template.version")
    try:
        uuid.UUID(str(version["version_id"]))
    except (ValueError, AttributeError):
        raise UsageError("Created section template.version.version_id must be a UUID.") from None
    return template


def _required(value: dict[str, object], key: str, expected: type, label: str) -> object:
    item = value.get(key)
    if expected is int:
        valid = isinstance(item, int) and not isinstance(item, bool)
    else:
        valid = isinstance(item, expected)
    if not valid:
        raise UsageError(f"{label}.{key} must be {expected.__name__}.")
    return item


def validate_versions_response(value: object) -> list[dict[str, object]]:
    """Validate a constructor versions response and every required field."""
    response = require_mapping(value, "Versions response")
    versions = response.get("versions")
    if not isinstance(versions, list):
        raise UsageError("Versions response.versions must be an array.")
    validated = []
    for index, item in enumerate(versions):
        version = require_mapping(item, f"Versions response.versions[{index}]")
        label = f"Versions response.versions[{index}]"
        _required(version, "id", int, label)
        _required(version, "author", str, label)
        _required(version, "commit_message", str, label)
        created_at = _required(version, "created_at", str, label)
        try:
            parse_iso8601(str(created_at), f"{label}.created_at")
        except argparse.ArgumentTypeError as error:
            raise UsageError(str(error)) from None
        validated.append(version)
    return validated


def _version_created_at(version: dict[str, object]) -> datetime.datetime:
    try:
        return parse_iso8601(str(version["created_at"]), "created_at")
    except argparse.ArgumentTypeError as error:
        raise UsageError(str(error)) from None


def project_versions(
    versions: list[dict[str, object]],
    since: datetime.datetime | None,
    limit: int | None,
) -> dict[str, object]:
    """Sort versions newest-first, filter inclusively, and bound output."""
    # Decorate once: a live store holds thousands of versions and parsing each timestamp for the
    # sort key and again for the --since filter is the dominant cost of this command.
    decorated = [(_version_created_at(version), int(version["id"]), version) for version in versions]
    decorated.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if since is not None:
        decorated = [item for item in decorated if item[0] >= since]
    if limit is not None:
        decorated = decorated[:limit]
    return {"versions": [item[2] for item in decorated]}


_validated_pages = validated_pages


def project_content_summary(content: dict[str, object]) -> dict[str, object]:
    """Project complete constructor content to aggregate storefront facts."""
    pages = _validated_pages(content)
    templates = content.get("section_templates")
    if not isinstance(templates, list):
        raise UsageError("Constructor content.section_templates must be an array.")
    statuses: dict[str, int] = {}
    variants: dict[str, int] = {}
    for page in pages:
        status = str(page["status"])
        variant = str(page["variant_type"])
        statuses[status] = statuses.get(status, 0) + 1
        variants[variant] = variants.get(variant, 0) + 1
    return {
        "page_count": len(pages),
        "pages_by_status": {key: statuses[key] for key in sorted(statuses)},
        "pages_by_variant_type": {key: variants[key] for key in sorted(variants)},
        "section_template_count": len(templates),
        "version_id": version_id(content),
    }


def project_pages(content: dict[str, object]) -> dict[str, object]:
    """Project page inventory without layout contents."""
    pages = []
    for page in _validated_pages(content):
        pages.append(
            {
                "id": page["id"],
                "alias": page.get("alias"),
                "title": page["title"],
                "pattern": page.get("pattern"),
                "variant_type": page["variant_type"],
                "status": page["status"],
                "layout_count": len(page["layout"]),
            }
        )
    return {"pages": pages}


def select_page(
    content: dict[str, object],
    alias: str | None,
    page_id: str | None,
) -> dict[str, object]:
    """Select exactly one page by exact alias or UUID."""
    pages = _validated_pages(content)
    matches = (
        [page for page in pages if page.get("alias") == alias]
        if alias is not None
        else [page for page in pages if page["id"] == page_id]
    )
    selector = f"alias {alias!r}" if alias is not None else f"id {page_id!r}"
    if not matches:
        raise UsageError(f"Page with {selector} was not found.")
    if len(matches) > 1:
        raise UsageError(f"Page selector {selector} is ambiguous.")
    return matches[0]


def section_status(section: dict[str, object], label: str) -> str | None:
    """The section's `published`/`hidden` status as read, or None when the server sent none.

    A hidden section is stored with its settings but not rendered on the storefront, so a
    projection without this field makes it look live. None is reported as is rather than
    guessed: it means the response carried no status, not that the section is visible.
    """
    status = section.get("status")
    if status is not None and not isinstance(status, str):
        raise UsageError(f"{label}.status must be str when present.")
    return status


def project_page(page: dict[str, object], content: dict[str, object] | None = None) -> dict[str, object]:
    """Project one page layout while hiding section settings values."""
    layout = _required(page, "layout", list, "Page")
    projected_layout = []
    for index, item in enumerate(layout):
        section = require_mapping(item, f"Page.layout[{index}]")
        label = f"Page.layout[{index}]"
        projected = {
            "display_sequence": _required(section, "display_sequence", int, label),
            "id": _required(section, "id", str, label),
            "widget": _required(section, "widget", str, label),
            "section_type": _required(section, "section_type", str, label),
            "status": section_status(section, label),
            "has_settings": "settings" in section,
        }
        instance_id = section.get("instance_id")
        if isinstance(instance_id, str):
            # Derived by the backend; carried through on writes but never edited locally.
            projected["instance_id"] = instance_id
        template_id = section.get("template_id")
        if template_id is not None:
            if not isinstance(template_id, str):
                raise UsageError(f"{label}.template_id must be str when present.")
            projected["template_id"] = template_id
        projected_layout.append(projected)
    meta = page.get("meta")
    return {
        "id": _required(page, "id", str, "Page"),
        "alias": page.get("alias"),
        "title": _required(page, "title", str, "Page"),
        "pattern": page.get("pattern"),
        "status": _required(page, "status", str, "Page"),
        # The three SEO fields a write carries; robots/social meta stay server-side.
        "meta": (
            {field: meta.get(field) for field in pages_engine.META_FIELDS}
            if isinstance(meta, dict)
            else None
        ),
        # The storefront prints as H1 the first section title that is not a link (a page
        # block such as the product or the listing outranks them), never meta.seo_h1; this
        # names that section by the same rule (best effort, from settings and templates).
        "h1_hint": pages_engine.h1_hint(page, content),
        "layout": projected_layout,
    }


def validate_section_templates_response(value: object) -> list[dict[str, object]]:
    """Validate the canonical section-template array response."""
    if not isinstance(value, list):
        raise UsageError("Section templates response must be an array.")
    templates = []
    for index, item in enumerate(value):
        template = require_mapping(item, f"Section templates response[{index}]")
        label = f"Section templates response[{index}]"
        _required(template, "id", str, label)
        _required(template, "title", str, label)
        _required(template, "json_schema", str, label)
        for field in ("alias", "script"):
            value = template.get(field)
            if value is not None and not isinstance(value, str):
                raise UsageError(f"{label}.{field} must be str when present.")
        templates.append(template)
    return templates


def _project_template(template: dict[str, object], include_schema: bool) -> dict[str, object]:
    schema = str(template["json_schema"])
    projected = {
        "alias": template.get("alias"),
        "title": template["title"],
        "id": template["id"],
        "json_schema_bytes": len(schema.encode("utf-8")),
    }
    script = template.get("script")
    if isinstance(script, str):
        # The size only: whether a template carries JS is worth knowing at a glance,
        # the code itself belongs in `templates export`, not in the context.
        projected["script_bytes"] = len(script.encode("utf-8"))
    if include_schema:
        projected["json_schema"] = schema
    return projected


def project_templates(templates: list[dict[str, object]]) -> dict[str, object]:
    """Project the section-template catalog without source bodies."""
    return {"templates": [_project_template(template, False) for template in templates]}


def select_template(templates: list[dict[str, object]], template_id: str) -> dict[str, object]:
    """Select one section template by exact UUID and include its schema."""
    matches = [template for template in templates if template["id"] == template_id]
    if not matches:
        raise UsageError(f"Section template with id {template_id!r} was not found.")
    if len(matches) > 1:
        raise UsageError(f"Section template id {template_id!r} is ambiguous.")
    return _project_template(matches[0], True)


version_id = content_version_id


def load_settings_file(path: Path, label: str) -> dict[str, object]:
    """Load one JSON object of settings from disk."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"Cannot read {label} file {path}: {redact_secrets(str(error))}") from None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise UsageError(f"{label} file {path} is not valid JSON: line {error.lineno}, column {error.colno}.") from None
    return require_mapping(value, label)


def telemetry(args: argparse.Namespace) -> dict[str, str | None]:
    """The identification the caller passed, as keyword arguments for `request_json`.

    Every call site spreads this instead of listing the flags one by one, so a
    new call site cannot quietly send an unattributed request.
    """
    return {
        "skill_prompt": getattr(args, "skill_prompt", None),
        "skill_session_id": getattr(args, "skill_session_id", None),
        "skill_model": getattr(args, "skill_model", None),
        "skill_harness": getattr(args, "skill_harness", None),
    }


def _request_for_args(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> object:
    url = build_url(resolve_base_url(args.base_url), path, query)
    return request_json(
        method,
        url,
        load_token(),
        body,
        **telemetry(args),
    )


def run_env(args: argparse.Namespace) -> int:
    """Report which addresses the client will talk to — never the token itself."""
    _token, source = _token_source()
    print(f"Store API: {resolve_base_url(args.base_url)}")
    print(f"Token source: {source}")
    try:
        bff_url = schema_engine.resolve_bff_url(getattr(args, "bff_url", None))
        print(f"Storefront: {bff_url}")
        schema_path, _meta = schema_engine.cache_paths(bff_url)
        state = "present" if schema_path.is_file() else "absent"
        print(f"Widget schema cache: {state} ({schema_path})")
    except UsageError:
        print("Storefront: not set (pass --bff-url or set KIT_BFF_BASE_URL)")
    return EXIT_OK


def _read_content(args: argparse.Namespace) -> dict[str, object]:
    result = _request_for_args(args, "GET", "/constructor/content")
    return validate_content(result, "Constructor content")


def run_versions_list(args: argparse.Namespace) -> int:
    result = _request_for_args(args, "GET", "/constructor/versions")
    versions = validate_versions_response(result)
    limit = None if args.all else args.limit
    emit_json(project_versions(versions, args.since, limit))
    return EXIT_OK


def run_version_content(args: argparse.Namespace) -> int:
    result = _request_for_args(args, "GET", f"/constructor/versions/{args.id}/content")
    emit_json(result)
    return EXIT_OK


def run_content_summary(args: argparse.Namespace) -> int:
    emit_json(project_content_summary(_read_content(args)))
    return EXIT_OK


def run_latest_content(args: argparse.Namespace) -> int:
    content = _read_content(args)
    emit_json(content if args.raw else project_content_summary(content))
    return EXIT_OK


def run_pages_list(args: argparse.Namespace) -> int:
    emit_json(project_pages(_read_content(args)))
    return EXIT_OK


def run_page_show(args: argparse.Namespace) -> int:
    content = _read_content(args)
    page = select_page(content, args.alias, args.id)
    emit_json(page if args.raw else project_page(page, content))
    return EXIT_OK


def run_templates_list(args: argparse.Namespace) -> int:
    result = _request_for_args(args, "GET", "/section-templates")
    emit_json(project_templates(validate_section_templates_response(result)))
    return EXIT_OK


def run_template_show(args: argparse.Namespace) -> int:
    result = _request_for_args(args, "GET", "/section-templates")
    templates = validate_section_templates_response(result)
    emit_json(select_template(templates, args.id))
    return EXIT_OK


def run_template_preview(args: argparse.Namespace) -> int:
    result = _request_for_args(
        args,
        "GET",
        "/constructor/content/template",
        query={"template_id": args.template_id},
    )
    emit_json(result)
    return EXIT_OK


def chrome_slot_warning(slot: str) -> str:
    """What a header/footer template means for the store, said before it is created."""
    alias = CHROME_ALIAS_BY_SLOT[slot]
    return (
        f"STORE-WIDE {slot.upper()}: this template carries alias {alias}. Placed into the {slot} "
        f"slot, it becomes the {slot} of EVERY page of the storefront, including pages the server "
        "adds on its own later. Creating it changes "
        "nothing on the storefront yet: a template renders nowhere until it is placed. It cannot "
        "be edited or deleted through this API afterwards — every correction is one more template. "
        f"Placing it into the {slot} slot is a separate step; see «Custom header or footer» in "
        "SKILL.md before promising the user anything about the storefront."
    )


def _apply_chrome_slot(
    request: dict[str, object], requested_slot: str | None
) -> tuple[dict[str, object], str | None]:
    """Turn --chrome-slot into the template alias; the flag is the stated intent.

    The flag sets the slot's alias in the body, so a regular section file without an
    alias becomes a header or footer template without editing it by hand; a file built
    by `kit-custom-sections --slot` already carries the matching alias. A chrome alias already in the file must
    agree with the flag, and without the flag it is refused: the alias is what makes the
    server treat the template as store chrome, the flag is what shows that this was
    meant rather than copied along from an export.
    """
    alias = request.get("alias")
    file_slot = CHROME_SLOT_BY_ALIAS.get(alias) if isinstance(alias, str) else None
    if requested_slot is None:
        if file_slot is not None:
            raise UsageError(
                f"alias {alias} makes this template a store-wide {file_slot}, not a page section. If "
                f"that is the intent, rerun with --chrome-slot {file_slot} and show the user the warning "
                "in the plan before confirming; otherwise leave alias out."
            )
        return request, None
    if file_slot is not None and file_slot != requested_slot:
        raise UsageError(
            f"alias {alias} is a {file_slot} template, but --chrome-slot says {requested_slot}. "
            "Leave alias out of the file: --chrome-slot sets it."
        )
    return {**request, "alias": CHROME_ALIAS_BY_SLOT[requested_slot]}, requested_slot


def project_created_template(template: dict[str, object]) -> dict[str, object]:
    """The created template without its source: what to record, not what was just sent."""
    projected = _project_template(template, False)
    projected["version"] = template["version"]
    return projected


MAX_PLAN_SCHEMA_FIELDS = 120


def _utf8_bytes(value: object) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def schema_fields(schema: object, limit: int = MAX_PLAN_SCHEMA_FIELDS) -> list[dict[str, object]]:
    """The editable fields of a template schema: path, type (or $ref name), title.

    What a person checks before confirming is which fields the section gets, not the
    schema text. Refs into the template's own definitions are followed; editor models
    and partials are named, not expanded.
    """
    definitions = schema.get("definitions") if isinstance(schema, dict) else None
    definitions = definitions if isinstance(definitions, dict) else {}
    fields: list[dict[str, object]] = []

    def walk(node: object, path: str, seen: frozenset) -> None:
        if len(fields) >= limit or not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/definitions/"):
            name = reference[len("#/definitions/") :]
            target = definitions.get(name)
            if isinstance(target, dict) and name not in seen and isinstance(target.get("properties"), dict):
                walk(target, path, seen | {name})
                return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for key, child in properties.items():
                child_path = f"{path}.{key}" if path else str(key)
                if not isinstance(child, dict):
                    continue
                child_ref = child.get("$ref")
                kind = child.get("type")
                if kind is None and isinstance(child_ref, str):
                    kind = child_ref.rsplit("/", 1)[-1]
                fields.append({"path": child_path, "type": kind, "title": child.get("title")})
                if len(fields) >= limit:
                    return
                walk(child, child_path, seen)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]", seen)

    walk(schema, "", frozenset())
    return fields


def template_create_plan(
    request: dict[str, object], slot: str | None, *, show_body: bool = False
) -> dict[str, object]:
    """The compact create plan a person confirms: sizes, digest, fields — not the source.

    The full body runs to tens of kilobytes for a rich section, and a preview that long
    is cut off before anyone reads the warning at its end. `--show-body` adds it back.
    """
    decoded = json.loads(str(request["json_schema"]))
    plan: dict[str, object] = {
        "operation": "POST /section-templates",
        "title": request.get("title"),
        "alias": request.get("alias"),
        "html_bytes": _utf8_bytes(request.get("html")),
        "css_bytes": _utf8_bytes(request.get("css")),
        "json_schema_bytes": _utf8_bytes(request.get("json_schema")),
        "script_bytes": _utf8_bytes(request.get("script")),
        # Over the body with sorted keys, so the dry run and the confirmed run agree on it.
        "body_sha256": hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "schema_fields": schema_fields(decoded),
    }
    if len(plan["schema_fields"]) >= MAX_PLAN_SCHEMA_FIELDS:  # type: ignore[arg-type]
        plan["schema_fields_truncated"] = True
    if slot is not None:
        plan["chrome_slot"] = slot
        plan["store_wide_warning"] = chrome_slot_warning(slot)
    if request.get("script"):
        plan["script_warning"] = (
            "The template carries JavaScript. The storefront runs it only for a store on its own "
            "domain, not on the default Кит address; this skill passes it through unchanged."
        )
    if show_body:
        plan["body"] = request
    return plan


def run_templates_create(args: argparse.Namespace) -> int:
    request = load_template_create(Path(args.file).expanduser())
    request, slot = _apply_chrome_slot(request, getattr(args, "chrome_slot", None))
    plan = template_create_plan(request, slot, show_body=bool(getattr(args, "show_body", False)))
    # Loaded before anything is sent: a broken workspace must not turn into a template
    # that exists on the server but never reached the snapshot.
    work = getattr(args, "work", None)
    workspace = workspace_engine.load_workspace(Path(work).expanduser()) if work else None
    if workspace is not None:
        plan["workspace"] = str(workspace.work_path)
    if args.dry_run or not args.confirm:
        emit_json(plan)
        if args.dry_run:
            return EXIT_OK
        print(
            "Template creation refused: get explicit confirmation, then rerun this operation with --confirm.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    result = validate_created_section_template(
        _request_for_args(
            args,
            "POST",
            "/section-templates",
            body=request,
        )
    )
    projected = project_created_template(result)
    if workspace is not None:
        # The create already happened; a workspace problem now is reported, not raised,
        # so the new id is never lost from the output.
        try:
            projected["workspace_sync"] = workspace_engine.sync_section_templates(workspace, [result])
        except (UsageError, OSError) as error:
            projected["workspace_sync_error"] = (
                f"{error} The template exists on the server; add it later with "
                "'content templates-sync --work WORK'."
            )
    # One JSON document on stdout: what was confirmed and what the server made of it.
    # The echo of html/css/script adds nothing to the plan.
    emit_json({"plan": plan, "result": projected})
    return EXIT_OK


def run_content_templates_sync(args: argparse.Namespace) -> int:
    """Bring templates created after the pull into the workspace without losing its edits."""
    workspace = workspace_engine.load_workspace(_work_path(args))
    templates = validate_section_template_list(_request_for_args(args, "GET", "/section-templates"))
    emit_json(workspace_engine.sync_section_templates(workspace, templates))
    return EXIT_OK


# --- schema engine commands (BFF) -----------------------------------------------------


def _resolved_bff_url(args: argparse.Namespace) -> str:
    return schema_engine.resolve_bff_url(getattr(args, "bff_url", None))


def _bff_url_passed(args: argparse.Namespace) -> bool:
    """Whether a storefront address was named: the flag or KIT_BFF_BASE_URL."""
    return bool((getattr(args, "bff_url", None) or os.environ.get("KIT_BFF_BASE_URL", "")).strip())


def _optional_bff_url(args: argparse.Namespace) -> str | None:
    """The storefront address, or None when none is named and none can be worked out.

    A named address that is malformed (no http:// or https://) is an error, not an absent
    address: dropping it quietly would put another one in its place — the address the
    workspace recorded, or the single schema in the cache.
    """
    try:
        return _resolved_bff_url(args)
    except UsageError:
        if _bff_url_passed(args):
            raise
        return None


def _cached_schema(args: argparse.Namespace) -> dict[str, object]:
    """Load the cached BFF schema offline, tolerating an unset BFF URL when unambiguous."""
    return schema_engine.load_cached_schema(_optional_bff_url(args))


def run_schema_fetch(args: argparse.Namespace) -> int:
    result = schema_engine.fetch_schema(_resolved_bff_url(args), force=args.force)
    emit_json(result)
    return EXIT_OK


def run_schema_widgets(args: argparse.Namespace) -> int:
    emit_json(schema_engine.project_widgets(_cached_schema(args)))
    return EXIT_OK


def run_schema_widget(args: argparse.Namespace) -> int:
    document = _cached_schema(args)
    if args.skeleton:
        emit_json(
            {
                "widget": args.name,
                "build_version": document.get("buildVersion"),
                "skeleton": schema_engine.build_widget_skeleton(document, args.name),
            }
        )
        return EXIT_OK
    emit_json(schema_engine.project_widget(document, args.name))
    return EXIT_OK


def run_schema_theme(args: argparse.Namespace) -> int:
    emit_json(schema_engine.project_theme(_cached_schema(args)))
    return EXIT_OK


def run_schema_widget_version(args: argparse.Namespace) -> int:
    version = schema_engine.fetch_widget_version(_resolved_bff_url(args), args.name)
    emit_json({"widget": args.name, "actual_version": version})
    return EXIT_OK


# --- navigation commands --------------------------------------------------------------


def run_sections_list(args: argparse.Namespace) -> int:
    content = _read_content(args)
    page = select_page(content, args.page, args.page_id)
    copy_counts: dict[str, int] = {}
    for other in _validated_pages(content):
        for item in other.get("layout") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                copy_counts[item["id"]] = copy_counts.get(item["id"], 0) + 1
    sections = []
    for index, item in enumerate(page["layout"]):
        section = require_mapping(item, f"Page.layout[{index}]")
        label = f"Page.layout[{index}]"
        entry = {
            "id": _required(section, "id", str, label),
            "widget": _required(section, "widget", str, label),
            "display_sequence": _required(section, "display_sequence", int, label),
            "section_type": _required(section, "section_type", str, label),
            "status": section_status(section, label),
            "has_settings": "settings" in section,
            "page_copies": copy_counts.get(str(section["id"]), 1),
        }
        template_id = section.get("template_id")
        if template_id is not None:
            if not isinstance(template_id, str):
                raise UsageError(f"{label}.template_id must be str when present.")
            entry["template_id"] = template_id
        sections.append(entry)
    emit_json(
        {
            "page_id": page["id"],
            "page_alias": page.get("alias"),
            "page_title": page["title"],
            "sections": sections,
        }
    )
    return EXIT_OK


def run_section_show(args: argparse.Namespace) -> int:
    content = _read_content(args)
    copies = workspace_engine.find_section_copies(content, args.id)
    if not copies:
        raise UsageError(f"Section with id {args.id!r} was not found on any page.")
    parent_copies = [entry for entry in copies if entry[2].get("section_type") == "parent"]
    _page, _index, section = parent_copies[0] if parent_copies else copies[0]
    widget = section.get("widget")
    if widget == schema_engine.MYSTIQUE_WIDGET:
        schema_source: dict[str, object] = {
            "kind": "template_json_schema",
            "template_id": section.get("template_id"),
        }
    else:
        schema_source = {"kind": "bff_widget", "widget": widget}
    result: dict[str, object] = {
        "id": args.id,
        "widget": widget,
        "template_id": section.get("template_id"),
        "status": section_status(section, "Section"),
        "settings": section.get("settings"),
        "schema_source": schema_source,
        "copies": [
            {
                "page_id": page["id"],
                "page_alias": page.get("alias"),
                "page_title": page["title"],
                "section_type": copy_section.get("section_type"),
                "status": section_status(copy_section, f"Section copy on page {page['id']}"),
            }
            for page, _idx, copy_section in copies
        ],
    }
    if widget == schema_engine.MYSTIQUE_WIDGET:
        result["template_alias"] = pages_engine.template_alias(content, section.get("template_id"))
    slot = pages_engine.chrome_slot_of(section, content)
    if slot is not None:
        result["chrome_slot"] = slot
    menu_ids = (
        pages_engine.chrome_menu_ids(widget, section.get("settings"), content, section.get("template_id"))
        if slot is not None or widget != schema_engine.MYSTIQUE_WIDGET
        else None
    )
    if menu_ids is not None:
        # Read through every stored form of the setting, so the id is never an object.
        result["menu_ids"] = menu_ids
        if widget == schema_engine.MYSTIQUE_WIDGET and not menu_ids:
            result["menu_ids_note"] = (
                "This slot's template has no field referencing #/definitions/MenuId, so it points "
                "at no store menu; its links are in its own settings."
            )
    if len(copies) > 1:
        result["composite_note"] = (
            "This section is stored once and copied to every page; the backend keeps the "
            "parent copy's settings, so edits must go through 'section set', which syncs all copies."
        )
    emit_json(result)
    return EXIT_OK


def run_templates_versions(args: argparse.Namespace) -> int:
    if args.version_id:
        result = _request_for_args(args, "GET", f"/section-templates/{args.id}/versions/{args.version_id}")
        template = _validate_section_template(result, "Section template version")
        emit_json(_project_template(template, True))
        return EXIT_OK
    result = _request_for_args(args, "GET", f"/section-templates/{args.id}/versions")
    response = require_mapping(result, "Template versions response")
    raw_versions = response.get("versions")
    if not isinstance(raw_versions, list):
        raise UsageError("Template versions response.versions must be an array.")
    versions = []
    for index, item in enumerate(raw_versions):
        version = require_mapping(item, f"Template versions response.versions[{index}]")
        label = f"Template versions response.versions[{index}]"
        versions.append(
            {
                "version_id": _required(version, "version_id", str, label),
                "author": _required(version, "author", str, label),
                "commit_message": _required(version, "commit_message", str, label),
                "created_at": _required(version, "created_at", str, label),
            }
        )
    emit_json({"template_id": args.id, "versions": versions})
    return EXIT_OK


def run_templates_export(args: argparse.Namespace) -> int:
    result = _request_for_args(args, "GET", "/section-templates")
    templates = validate_section_templates_response(result)
    matches = [template for template in templates if template["id"] == args.id]
    if not matches:
        raise UsageError(f"Section template with id {args.id!r} was not found.")
    template = matches[0]
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, field in (("template.html", "html"), ("template.css", "css"), ("template.schema.json", "json_schema")):
        value = template.get(field)
        if not isinstance(value, str):
            raise UsageError(f"Section template.{field} must be a string.")
        path = out_dir / name
        path.write_text(value, encoding="utf-8")
        written[field] = str(path)
    # The template's JS travels with the markup: a copy made from an export that left it
    # behind would lose it silently on `templates create`. Written whenever the server sends
    # the field, even empty, so the directory says whether the template had any.
    script = template.get("script")
    if isinstance(script, str):
        script_path = out_dir / "template.js"
        script_path.write_text(script, encoding="utf-8")
        written["script"] = str(script_path)
    meta_path = out_dir / "template.meta.json"
    meta_path.write_text(
        canonical_json(
            {
                "id": template["id"],
                "title": template["title"],
                "alias": template.get("alias"),
            }
        ),
        encoding="utf-8",
    )
    written["meta"] = str(meta_path)
    output: dict[str, object] = {"template_id": args.id, "written": written}
    if isinstance(script, str):
        # 0 means the template has no JS: the size answers that without opening the file.
        output["script_bytes"] = len(script.encode("utf-8"))
    emit_json(output)
    return EXIT_OK


# --- write core: workspace, diff, push ------------------------------------------------


def _work_path(args: argparse.Namespace) -> Path:
    return Path(args.work).expanduser()


def _workspace_bff_url(
    args: argparse.Namespace, workspace: workspace_engine.Workspace | None = None
) -> str | None:
    """The storefront address for a workspace command: the one its manifest records.

    The manifest records the address the workspace was pulled for, so its edits are
    checked against that storefront's schema even when the flag is not repeated. A flag or
    `KIT_BFF_BASE_URL` naming another host is refused rather than obeyed: a variable left
    set for another store would otherwise check these edits against that store's schema
    without a word. A workspace without a recorded address takes the flag or the variable.
    The same host named again (in another letter case, with a path) gives the recorded
    address itself, the one the schema cache is found by. A malformed named address is
    refused, not replaced by the recorded one.
    """
    explicit = _bff_url_passed(args)
    resolved = _optional_bff_url(args)
    recorded = workspace.manifest.get("bff_url") if workspace is not None else None
    if not isinstance(recorded, str) or not recorded:
        return resolved
    if resolved is None or _storefront_host(resolved) == _storefront_host(recorded):
        return recorded
    if not explicit:
        return recorded  # an address worked out from the API host never outranks the recorded one
    source = "--bff-url" if getattr(args, "bff_url", None) else "KIT_BFF_BASE_URL"
    raise UsageError(
        f"This workspace was pulled for the storefront {recorded}, and {source} names {resolved}: "
        "another host, whose widget schema may be another store's. Drop "
        f"{source} — the workspace remembers its address. If the recorded one is wrong, pull "
        "again with the right one (a new pull starts over from the server state)."
    )


def _storefront_host(url: str) -> str:
    """Host (and port) of a storefront address — what its schema cache is keyed by."""
    return re.sub(r"^https?://", "", url.strip()).split("/", 1)[0].lower()


def _schema_for_gates(
    args: argparse.Namespace, workspace: workspace_engine.Workspace | None = None
) -> dict[str, object] | None:
    """The cached BFF schema for validation, or None when no cache is reachable.

    Native-widget edits fail closed later without it. A custom-template edit needs it as
    well when its settings use an editor model (ImageModel, LinkSource, Palette, …) the
    template takes from the storefront schema, as the templates kit-custom-sections builds
    do; settings that use none are checked against the pulled catalog alone.
    A workspace pulled without a storefront address (`bff_url: null` in its manifest)
    never borrows the single schema in the cache: that one may belong to another store.
    Only a workspace from an older client, whose manifest has no `bff_url` key at all,
    keeps the old fallback.
    """
    bff_url = _workspace_bff_url(args, workspace)
    try:
        if bff_url is not None:
            return schema_engine.load_cached_schema(bff_url)
        if workspace is not None and "bff_url" in workspace.manifest:
            return None
        return schema_engine.load_cached_schema(None)
    except UsageError:
        return None


def _no_workspace_schema(lead: str = "") -> UsageError:
    return UsageError(
        f"{lead}No widget schema is known for this workspace's storefront. Pass the store's own "
        "address with --bff-url (or set KIT_BFF_BASE_URL) and run 'schema fetch' first; "
        "a schema cached for another store is never used."
    )


def _require_workspace_schema(
    args: argparse.Namespace, workspace: workspace_engine.Workspace
) -> dict[str, object]:
    document = _schema_for_gates(args, workspace)
    if document is None:
        raise _no_workspace_schema()
    return document


def _require_rebindable(
    section: dict[str, object],
    workspace: workspace_engine.Workspace,
    template_id: str,
) -> None:
    """Refuse a template rebind that the storefront could not carry out."""
    widget = section.get("widget")
    if widget != schema_engine.MYSTIQUE_WIDGET:
        raise UsageError(
            f"Only {schema_engine.MYSTIQUE_WIDGET} sections carry a template; this one is "
            f"{widget!r}. A built-in widget has no template to rebind."
        )
    workspace_engine.section_template(workspace.base, template_id)  # names templates-sync when missing
    current_slot = pages_engine.chrome_slot_of(section, workspace.base)
    target_slot = pages_engine.template_chrome_slot(workspace.base, template_id)
    if current_slot == target_slot:
        return
    if current_slot is not None:
        raise UsageError(
            f"This section is the store's custom {current_slot} (its template carries alias "
            f"{CHROME_ALIAS_BY_SLOT[current_slot]}). The template it moves to must carry the same "
            f"alias: without it the server stops treating the section as the {current_slot}, and "
            f"pages it adds later get no {current_slot} at all."
        )
    raise UsageError(
        f"Template {template_id} carries alias {CHROME_ALIAS_BY_SLOT[str(target_slot)]}: it is a "
        f"store-wide {target_slot}, not a page section. Moving a regular section onto it puts a "
        f"second {target_slot} on the page. See «Custom header or footer» in SKILL.md."
    )


def _refuse_chrome_template_as_section(workspace: workspace_engine.Workspace, template_id: str) -> None:
    """A header/footer template goes into its slot, never in as one more page section.

    As a regular section it renders a second header in the middle of the page, and the
    server may pick that copy as the store header for pages it adds on its own later.
    """
    slot = pages_engine.template_chrome_slot(workspace.base, template_id)
    if slot is None:
        return
    raise UsageError(
        f"Template {template_id} carries alias {CHROME_ALIAS_BY_SLOT[slot]}: it is a store-wide "
        f"{slot}, not a page section. Added as a section it would put a second {slot} on this page, "
        f"and the server may copy it as the {slot} onto pages it adds later. It belongs in the "
        f"{slot} slot — see «Custom header or footer» in SKILL.md."
    )


def _validate_edited_section(
    args: argparse.Namespace,
    workspace: workspace_engine.Workspace,
    section: dict[str, object],
    settings: dict[str, object],
    *,
    with_baseline: bool = False,
    template_id: str | None = None,
) -> None:
    """Validate new settings at edit time; pre-existing drift in the old settings never blocks."""
    schema_document = _schema_for_gates(args, workspace)
    candidate = dict(section)
    candidate["settings"] = settings
    # Judging the new settings against the template they are moving to, not the one
    # they are leaving: that check is the whole point of a rebind.
    if template_id is not None:
        candidate["template_id"] = template_id
    if schema_document is None:
        models = write_engine.bff_models_needed(candidate, workspace)
        if models:
            raise _no_workspace_schema(
                f"Template {candidate.get('template_id')} takes editor models ({', '.join(models[:5])}) "
                "from the storefront's widget schema, and these settings use them, so they cannot be "
                "checked without that schema. "
            )
    baseline: tuple[str, ...] = ()
    if with_baseline and schema_document is not None:
        baseline = tuple(write_engine._validate_section_settings(section, workspace, schema_document))
    errors = write_engine.introduced_errors(candidate, workspace, schema_document, baseline)
    if errors:
        raise UsageError("Settings are invalid: " + "; ".join(errors[:5]))


def run_content_pull(args: argparse.Namespace) -> int:
    base_url = resolve_base_url(args.base_url)
    content = _read_content(args)
    work_path = Path(args.out).expanduser() if args.out else workspace_engine.default_work_path(base_url)
    # The schema build is matched to this store's storefront only: the single schema in
    # the cache may belong to another store, so it is never taken as a stand-in.
    bff_url = _optional_bff_url(args)
    schema_build = None
    schema_note = None
    if bff_url is None:
        schema_note = (
            "The storefront address is not known (no --bff-url or KIT_BFF_BASE_URL), so no widget "
            "schema was matched to this store; edits of native widgets, and custom-section settings "
            "that use the editor models of that schema (images, links, palettes), will ask for it."
        )
    else:
        try:
            schema_build = str(schema_engine.load_cached_schema(bff_url).get("buildVersion"))
        except UsageError:
            schema_note = (
                f"'schema fetch' has not been run for {bff_url}; run it before editing native widgets, "
                "or custom-section settings that use the editor models of the storefront schema."
            )
    manifest = workspace_engine.write_workspace(
        work_path, content, base_url, schema_build, bff_url=bff_url, schema_note=schema_note
    )
    emit_json({"work_path": str(work_path), "manifest": manifest})
    return EXIT_OK


def _switch_slot(
    args: argparse.Namespace,
    workspace: workspace_engine.Workspace,
    section: dict[str, object],
    widget: str,
    template_id: str | None,
    settings: dict[str, object],
) -> dict[str, object]:
    """`section set --widget` on a header or footer slot: built-in ↔ custom template.

    The new settings are judged as a new section's would be — against the template's schema,
    or against the built-in widget's schema with the widget's version stamped from migrate
    (empty settings only), exactly as `section add` does it. Whether the section is a slot
    at all, and whether the slot can hold the target, is decided first and offline: a
    refusal there never waits on the schema, the storefront or the settings.
    """
    workspace_engine.check_slot_switch(workspace, str(section.get("id")), widget, template_id)
    if widget != schema_engine.MYSTIQUE_WIDGET and "version" not in settings:
        document = _require_workspace_schema(args, workspace)
        schema_engine.widget_schema(document, widget)
        bff_url = _workspace_bff_url(args, workspace)
        if bff_url is None:
            raise UsageError(
                "The widget version is read from the store's storefront, and its address is not "
                "known for this workspace: pass --bff-url or set KIT_BFF_BASE_URL."
            )
        version = schema_engine.fetch_widget_version(bff_url, widget)
        if version is not None:
            settings = dict(settings)
            settings["version"] = version
    candidate: dict[str, object] = {"id": section.get("id"), "widget": widget}
    if template_id:
        candidate["template_id"] = template_id
    _validate_edited_section(args, workspace, candidate, settings)
    return workspace_engine.switch_slot_widget(workspace, str(section.get("id")), widget, template_id, settings)


def run_section_set(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    settings = load_settings_file(Path(args.settings_file).expanduser(), "Settings")
    copies = workspace_engine.require_section_copies(workspace.content, args.id)
    template_id = getattr(args, "template_id", None)
    widget = getattr(args, "widget", None)
    if widget and widget != copies[0][2].get("widget"):
        report = _switch_slot(args, workspace, copies[0][2], widget, template_id, settings)
        workspace.save()
        emit_json(report)
        return EXIT_OK
    if template_id:
        _require_rebindable(copies[0][2], workspace, template_id)
    _validate_edited_section(
        args, workspace, copies[0][2], settings, with_baseline=True, template_id=template_id
    )
    previous_template_id = copies[0][2].get("template_id")
    report = workspace_engine.set_section_settings(workspace, args.id, settings, template_id)
    if template_id and template_id != previous_template_id:
        placement = pages_engine.template_placement_warning(workspace.base, template_id)
        if placement is not None:
            report["warnings"] = [placement]
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_section_add(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    settings = load_settings_file(Path(args.settings_file).expanduser(), "Settings")
    page = workspace_engine.select_page(workspace.content, args.page, args.page_id)
    if args.widget == schema_engine.MYSTIQUE_WIDGET:
        if not args.template_id:
            raise UsageError(f"{schema_engine.MYSTIQUE_WIDGET} sections require --template-id.")
        workspace_engine.section_template(workspace.base, args.template_id)
        _refuse_chrome_template_as_section(workspace, args.template_id)
    elif args.template_id:
        raise UsageError("--template-id applies only to YandexKit.Mystique sections.")
    else:
        # A new native section gets the widget's actual version from migrate; a widget
        # without migrations has no version and the field is not written at all.
        document = _require_workspace_schema(args, workspace)
        schema_engine.widget_schema(document, args.widget)
        if "version" not in settings:
            bff_url = _workspace_bff_url(args, workspace)
            if bff_url is None:
                raise UsageError(
                    "The widget version is read from the store's storefront, and its address is not "
                    "known for this workspace: pass --bff-url or set KIT_BFF_BASE_URL."
                )
            actual_version = schema_engine.fetch_widget_version(bff_url, args.widget)
            if actual_version is not None:
                settings = dict(settings)
                settings["version"] = actual_version
    candidate_section: dict[str, object] = {"widget": args.widget, "id": "new"}
    if args.template_id:
        candidate_section["template_id"] = args.template_id
    _validate_edited_section(args, workspace, candidate_section, settings)
    report = workspace_engine.add_section(
        workspace,
        page,
        args.widget,
        settings,
        args.template_id,
        args.position,
    )
    if args.template_id:
        placement = pages_engine.template_placement_warning(workspace.base, args.template_id)
        if placement is not None:
            report["warnings"] = [placement]
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_section_remove(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    report = workspace_engine.remove_section(
        workspace, args.id, getattr(args, "page", None), getattr(args, "page_id", None)
    )
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_section_status(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    report = workspace_engine.set_section_status(
        workspace, args.id, args.status, getattr(args, "page", None), getattr(args, "page_id", None)
    )
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_section_move(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    report = workspace_engine.move_section(workspace, args.id, args.position, args.page, args.page_id)
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_theme_set(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    settings = load_settings_file(Path(args.settings_file).expanduser(), "Global settings")
    schema_document = _schema_for_gates(args, workspace)
    if schema_document is None:
        raise UsageError(
            "Global settings are checked against the storefront theme schema; pass --bff-url for this "
            "store and run 'schema fetch' first."
        )
    definitions = schema_document["definitions"]
    assert isinstance(definitions, dict)
    theme_node = schema_engine.theme_schema(schema_document)
    current = workspace.content.get("global_settings")
    baseline = set(
        schema_engine.validate_instance(current if isinstance(current, dict) else {}, theme_node, definitions)
    )
    errors = [
        error for error in schema_engine.validate_instance(settings, theme_node, definitions) if error not in baseline
    ]
    if errors:
        raise UsageError("Global settings are invalid: " + "; ".join(errors[:5]))
    report = workspace_engine.set_theme(workspace, settings)
    workspace.save()
    emit_json(report)
    return EXIT_OK


def _seo_from_args(args: argparse.Namespace) -> dict[str, str] | None:
    seo = {}
    if args.seo_title is not None:
        seo["seo_title"] = args.seo_title
    if args.seo_h1 is not None:
        seo["seo_h1"] = args.seo_h1
    if args.seo_description is not None:
        seo["seo_description"] = args.seo_description
    return seo or None


def _content_settings_builder(args: argparse.Namespace, workspace: workspace_engine.Workspace):
    """Settings for a system page's content widget: the schema skeleton plus the widget version.

    The same path `section add` takes for a native widget, so the page is created the way
    the cabinet would fill it. Returns (settings, None), or (None, why) when the schema or
    the version is out of reach — the page is then added empty and the report says so.
    """
    document = _schema_for_gates(args, workspace)
    bff_url = _workspace_bff_url(args, workspace)

    def build(widget: str) -> tuple[dict[str, object] | None, str | None]:
        if document is None:
            return None, "no widget schema is known for this storefront: pass --bff-url and run 'schema fetch'"
        try:
            skeleton = schema_engine.build_widget_skeleton(document, widget)
        except UsageError as error:
            return None, str(error)
        if "version" not in skeleton:
            if bff_url is None:
                return None, "its version is read from the storefront, whose address is not known (--bff-url)"
            try:
                version = schema_engine.fetch_widget_version(bff_url, widget)
            except (UsageError, ApiError) as error:
                return None, f"its version could not be read from the storefront: {redact_secrets(str(error))}"
            if version is not None:
                skeleton = dict(skeleton)
                skeleton["version"] = version
        return skeleton, None

    return build


def run_page_add(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    alias = (args.alias or "").strip()
    copy_layout_from = getattr(args, "copy_layout_from", None)
    report = pages_engine.add_page(
        workspace,
        title=args.title,
        pattern=args.pattern,
        alias=args.alias,
        status=args.status,
        seo=_seo_from_args(args),
        with_chrome=not args.no_chrome,
        # Only a system page has content widgets of its own; a custom page starts empty.
        content_settings=_content_settings_builder(args, workspace) if alias and not copy_layout_from else None,
        copy_layout_from=copy_layout_from,
    )
    if "layout_copied_from" in report:
        _report_inherited_drift(args, workspace, report)
    workspace.save()
    emit_json(report)
    return EXIT_OK


def _report_inherited_drift(
    args: argparse.Namespace, workspace: workspace_engine.Workspace, report: dict[str, object]
) -> None:
    """Name the copied sections whose settings today's schema flags, and what that means."""
    copied_ids = [
        str(entry["section_id"])
        for entry in report.get("content_widgets_added") or []
        if isinstance(entry, dict)
    ]
    drift = write_engine.inherited_drift(workspace, _schema_for_gates(args, workspace), copied_ids)
    if not drift:
        return
    report["inherited_drift"] = drift
    report["inherited_drift_note"] = (
        "These copies carry the default template's settings byte for byte, including what "
        "today's widget schema flags in them: the server stores such settings as they are, and "
        "the default template already renders with them. The push gate judges each copy against "
        "the section it came from, so this drift does not block the write, and a 'section set' "
        "edit is checked only for what it changes. To clean a copy up, set the named fields "
        "with 'section set': each copy is a section of the new template alone, so the default "
        "template keeps its own settings."
    )


def run_page_set(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    page = workspace_engine.select_page(workspace.content, args.alias, args.id)
    report = pages_engine.set_page(
        workspace,
        page,
        title=args.title,
        pattern=args.pattern,
        status=args.status,
        seo=_seo_from_args(args),
        clear_meta=args.clear_meta,
    )
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_page_remove(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    page = workspace_engine.select_page(workspace.content, args.alias, args.id)
    report = pages_engine.remove_page(workspace, page)
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_content_diff(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    emit_json(workspace_engine.diff_summary(workspace))
    return EXIT_OK


def run_content_push(args: argparse.Namespace) -> int:
    """Confirmed full-replace write with every integrity gate run offline first."""
    workspace = workspace_engine.load_workspace(_work_path(args))
    commit_message = args.commit_message.strip()
    if not commit_message:
        raise UsageError("--commit-message must not be empty.")
    publish = not args.no_publish
    gate_errors = write_engine.check_gates(workspace, _schema_for_gates(args, workspace), args.allow_page_removal)
    if gate_errors:
        emit_json({"error": "push_gates_failed", "gates": gate_errors}, stream=sys.stderr)
        return EXIT_USAGE
    plan = write_engine.build_push_plan(workspace, commit_message, activate=publish)
    if args.dry_run or not args.confirm:
        emit_json(plan)
        if args.dry_run:
            return EXIT_OK
        print(
            "Push refused: get explicit confirmation, then rerun this operation with --confirm.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    # From here on stdout carries exactly one JSON document, {"plan": ..., "result": ...},
    # printed once the server has answered; failures go to stderr as before.

    body = write_engine.build_put_body(workspace, commit_message, activate=publish)
    try:
        result = _request_for_args(args, "PUT", "/constructor/content", body=body)
    except ApiError as error:
        if error.status != 409:
            raise
        latest = validate_content(
            _request_for_args(args, "GET", "/constructor/content"),
            "Latest API content after conflict",
        )
        emit_json(
            {
                "error": "version_conflict",
                "status": 409,
                "base_version_id": workspace.base_version_id,
                "latest_version": latest["version"],
                "message": (
                    "The storefront moved past the pulled base. No retry was attempted. "
                    "Run 'content pull' again, reapply the edit, and request fresh confirmation."
                ),
            },
            stream=sys.stderr,
        )
        return EXIT_API_ERROR

    response = write_engine.validate_put_response(result)
    version = require_mapping(response["version"], "Update response.version")
    written_version_id = int(version["id"])  # validated above
    active_version_id = write_engine.response_active_version_id(response)
    mismatches = write_engine.count_mismatches(plan, response)
    if mismatches:
        emit_json(
            {
                "warning": "written_counts_diverge_from_local_expectation",
                "mismatches": mismatches,
                "message": "The server wrote different totals than computed locally; inspect the storefront now.",
            },
            stream=sys.stderr,
        )

    if not publish:
        if active_version_id is None or active_version_id == written_version_id:
            # An old server silently ignores the unknown `activate` field and publishes.
            # The only reliable signal is the response: no distinct active_version_id
            # means the edit is LIVE right now, whatever the request asked for.
            workspace_engine.rebase_after_push(workspace, written_version_id, published=True, version_info=version)
            emit_json({"plan": plan, "result": response})
            emit_json(
                {
                    "error": "no_publish_not_supported_edit_published",
                    "written_version_id": written_version_id,
                    "previous_version_id": plan["base_version_id"],
                    "message": (
                        f"ПРАВКА ОПУБЛИКОВАНА (версия {written_version_id}), режим предпросмотра "
                        "не поддержан этим стендом: сервер проигнорировал activate=false и не вернул "
                        "active_version_id. Изменения уже видны на витрине. Что делать: проверьте "
                        "витрину; если правку нужно убрать — верните прежний контент новой "
                        f"подтверждённой записью (база до правки: версия {plan['base_version_id']}); "
                        "повторяйте предпросмотр только после раскатки сервера с поддержкой activate."
                    ),
                },
                stream=sys.stderr,
            )
            return EXIT_PUBLISHED_NOT_PREVIEWED
        workspace_engine.rebase_after_push(workspace, active_version_id, published=False)
        emit_json(
            {
                "plan": plan,
                "result": {
                    "mode": "proposal_stored_not_published",
                    "proposal_version_id": written_version_id,
                    "active_version_id": active_version_id,
                    "pages_count": response.get("pages_count"),
                    "sections_count": response.get("sections_count"),
                    "message": (
                        f"Правка сохранена как версия-предложение {written_version_id} и НЕ опубликована. "
                        f"На витрине сейчас версия {active_version_id} — копия прежнего контента. "
                        f"Манифест воркспейса переведён на версию {active_version_id}. "
                        "Отрисовать предложение агент не может: посмотреть его на десктопе и телефоне "
                        f"может только пользователь — в истории конструктора кабинета, версия {written_version_id}. "
                        f"Агенту доступна офлайн-проверка: versions content --id {written_version_id} и "
                        "дизайн-линт этого снимка. Опубликовать, посмотреть и вернуть прежний контент "
                        "новой записью — только с явного согласия пользователя."
                    ),
                },
            }
        )
        return EXIT_OK

    if active_version_id is not None and active_version_id != written_version_id:
        emit_json(
            {
                "warning": "active_version_diverges_from_written",
                "written_version_id": written_version_id,
                "active_version_id": active_version_id,
                "message": "The server reports a different active version than the one written; inspect the storefront now.",
            },
            stream=sys.stderr,
        )
    workspace_engine.rebase_after_push(
        workspace,
        active_version_id if active_version_id is not None else written_version_id,
        published=True,
        version_info=version,
    )
    emit_json({"plan": plan, "result": response})
    return EXIT_OK


def refuse_mutation(action: str) -> int:
    """Refuse an unconfirmed media mutation before local or external access."""
    print(
        f"{action.capitalize()} refused: get explicit confirmation, then rerun with --confirm.",
        file=sys.stderr,
    )
    return EXIT_REFUSED


def run_media_upload(args: argparse.Namespace) -> int:
    """Upload one confirmed local image and print its reusable public URL."""
    if not args.confirm:
        return refuse_mutation("image upload")

    file_path, _ = validate_local_media_file(args.file, "image")
    body, content_type = encode_multipart_file(file_path)
    response = request_json(
        "POST",
        build_url(resolve_base_url(args.base_url), "/files"),
        load_token(),
        raw_data=body,
        content_type=content_type,
        **telemetry(args),
    )
    uploaded = require_image_upload_response(response)
    output = dict(uploaded)
    output["url"] = uploaded["public_path"]
    emit_json(output)
    return EXIT_OK


def run_media_font_upload(args: argparse.Namespace) -> int:
    """Upload one confirmed local font file and print the theme's font-source fragment."""
    if not args.confirm:
        return refuse_mutation("font upload")

    file_path, media_type = validate_local_media_file(args.file, "font")
    body, content_type = encode_multipart_file(file_path, media_type)
    response = request_json(
        "POST",
        build_url(resolve_base_url(args.base_url), "/files"),
        load_token(),
        raw_data=body,
        content_type=content_type,
        **telemetry(args),
    )
    uploaded = require_font_upload_response(response)
    emit_json(
        {
            "file_id": uploaded["id"],
            "file_path": uploaded["public_path"],
            "type": uploaded["type"],
            # Exactly what typography.fontFamily.custom.sources.<style>.<weight> takes.
            "sources_fragment": {"fileId": uploaded["id"], "filePath": uploaded["public_path"]},
            "next_step": (
                "Reference it from the theme: theme set with typography.fontFamily.custom = {isSelected: "
                "true, name, sources: {normal: {regular: <fragment>, bold: <fragment>}}} (medium and "
                "semibold optional) and typography.fontFamily.predefined.isSelected = false. Upload "
                "both the regular and the bold file. Only a font the user holds a web licence for."
            ),
        }
    )
    return EXIT_OK


def run_media_video_upload(args: argparse.Namespace) -> int:
    """Upload one confirmed local video for asynchronous processing."""
    if not args.confirm:
        return refuse_mutation("video upload")

    file_path, _ = validate_local_media_file(args.file, "video")
    body, content_type = encode_multipart_file(file_path)
    response = request_json(
        "POST",
        build_url(resolve_base_url(args.base_url), "/videos"),
        load_token(),
        raw_data=body,
        content_type=content_type,
        **telemetry(args),
    )
    emit_json(require_video_upload_response(response))
    return EXIT_OK


def run_media_video_status(args: argparse.Namespace) -> int:
    """Return the cabinet-compatible asynchronous video state."""
    video_id = args.id.strip()
    if not video_id:
        raise UsageError("Video ID must not be empty.")

    encoded_id = urllib.parse.quote(video_id, safe="")
    response = request_json(
        "GET",
        build_url(resolve_base_url(args.base_url), f"/videos/{encoded_id}"),
        load_token(),
        **telemetry(args),
    )
    emit_json(require_mapping(response, "Video status response"))
    return EXIT_OK


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _save_json_file(path: Path, value: object, label: str) -> None:
    try:
        path.write_text(canonical_json(value), encoding="utf-8")
    except OSError as error:
        raise UsageError(f"Cannot write {label} file {path}: {redact_secrets(str(error))}") from None


def _request_feature(
    args: argparse.Namespace,
    feature: str,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
) -> object:
    """A request to a route that an older server does not have yet.

    There a missing route answers 404 like a missing object would, so the reads that
    address no object say what the 404 means.
    """
    try:
        return _request_for_args(args, method, path, body=body)
    except ApiError as error:
        if error.status == 404 and method == "GET":
            raise ApiError(
                404,
                {"error": "not_supported", "detail": f"This server does not expose {feature} operations."},
            ) from None
        raise


def _read_menus(args: argparse.Namespace) -> list[dict[str, object]]:
    return menu_engine.validate_menus(_request_feature(args, "menu", "GET", "/menus"))


def _read_menu(args: argparse.Namespace, menu_id: str) -> dict[str, object]:
    return menu_engine.validate_menu(_request_for_args(args, "GET", f"/menus/{menu_id}"))


def _read_menu_targets(args: argparse.Namespace) -> object:
    return _request_feature(args, "menu", "GET", "/menu-links")


def run_menu_list(args: argparse.Namespace) -> int:
    menus = [menu_engine.project_menu(menu, with_tree=False) for menu in _read_menus(args)]
    menus.sort(key=lambda menu: (menu["alias"] is None, str(menu["alias"]), str(menu["title"])))
    emit_json({"menus": menus})
    return EXIT_OK


def run_menu_show(args: argparse.Namespace) -> int:
    menu = menu_engine.select_menu(_read_menus(args), args.id, args.alias)
    emit_json(menu_engine.project_menu(menu, with_tree=True))
    return EXIT_OK


def run_menu_links(args: argparse.Namespace) -> int:
    emit_json(menu_engine.project_links(_read_menu_targets(args)))
    return EXIT_OK


def run_menu_pull(args: argparse.Namespace) -> int:
    work_path = Path(args.work).expanduser()
    if work_path.exists() and not args.force:
        raise UsageError(f"{work_path} already exists; pass --force to overwrite it.")
    menu = menu_engine.select_menu(_read_menus(args), args.id, args.alias)
    _save_json_file(work_path, menu_engine.new_workspace(menu), "menu workspace")
    emit_json({"work_path": str(work_path), "menu": menu_engine.project_menu(menu, with_tree=False)})
    return EXIT_OK


def _load_menu_workspace(raw_path: str) -> tuple[Path, dict[str, object]]:
    work_path = Path(raw_path).expanduser()
    return work_path, menu_engine.load_workspace(load_settings_file(work_path, "Menu workspace"))


def _menu_plan(
    menu_id: object,
    alias: object,
    title: str,
    tree: list[dict[str, object]],
    base_title: str,
    base_tree: list[dict[str, object]],
    targets: dict[str, set[str]] | None = None,
) -> dict[str, object]:
    """What a write would change, with the gate results; `targets` adds the link check."""
    changes = menu_engine.diff_trees(base_tree, tree)
    lint = menu_engine.lint_tree(title, tree, base_tree, targets)
    plan: dict[str, object] = {
        "menu_id": menu_id,
        "alias": alias or None,
        "title": title,
        "items": {"before": menu_engine.tree_size(base_tree), "after": menu_engine.tree_size(tree)},
        "depth": menu_engine.tree_depth(tree),
        "changes": changes,
        "unchanged": title == base_title and menu_engine.diff_is_empty(changes),
        "errors": lint["errors"],
        "warnings": lint["warnings"],
    }
    if title != base_title:
        plan["title_before"] = base_title
    return plan


def _workspace_plan(
    workspace: dict[str, object], targets: dict[str, set[str]] | None = None
) -> dict[str, object]:
    base = workspace["base"]
    return _menu_plan(
        workspace["menu_id"],
        workspace["alias"],
        str(workspace["title"]),
        workspace["tree"],  # type: ignore[arg-type]
        str(base["title"]),  # type: ignore[index]
        base["tree"],  # type: ignore[index]
        targets,
    )


def run_menu_diff(args: argparse.Namespace) -> int:
    _, workspace = _load_menu_workspace(args.work)
    emit_json(_workspace_plan(workspace))
    return EXIT_OK


def _replace_menu(
    args: argparse.Namespace,
    current: dict[str, object],
    title: str,
    alias: str,
    tree: list[dict[str, object]],
    snapshot_path: Path,
) -> tuple[dict[str, object], bool]:
    """Save what the menu is now, replace it, and read the result back."""
    menu_id = str(current["id"])
    _save_json_file(
        snapshot_path,
        menu_engine.new_snapshot(current, _utc_now().isoformat()),
        "menu snapshot",
    )
    _request_for_args(args, "PUT", f"/menus/{menu_id}", body=menu_engine.draft_menu(title, alias, tree))
    stored = _read_menu(args, menu_id)
    verified = menu_engine.menu_state(stored)["fingerprint"] == menu_engine.fingerprint(title, alias, tree)
    return stored, verified


def _menu_write_result(stored: dict[str, object], snapshot_path: Path, verified: bool) -> dict[str, object]:
    return {
        "menu": menu_engine.project_menu(stored, with_tree=False),
        "snapshot_path": str(snapshot_path),
        "undo": f"menu restore --snapshot {snapshot_path} --confirm",
        "verified": verified,
    }


def _report_unverified(what: str) -> int:
    print(
        f"The {what} was written, but reading it back returned something else. "
        "Read it again before any further edit.",
        file=sys.stderr,
    )
    return EXIT_WRITTEN_NOT_VERIFIED


def run_menu_push(args: argparse.Namespace) -> int:
    work_path, workspace = _load_menu_workspace(args.work)
    plan = _workspace_plan(workspace)
    if plan["errors"]:
        emit_json({"plan": plan}, stream=sys.stderr)
        return EXIT_USAGE
    if args.dry_run or not args.confirm:
        emit_json({"plan": plan})
        if args.dry_run:
            return EXIT_OK
        return refuse_mutation("menu write")
    if plan["unchanged"]:
        raise UsageError("The workspace equals the menu it was pulled from; there is nothing to write.")

    plan = _workspace_plan(workspace, menu_engine.targets_index(_read_menu_targets(args)))
    if plan["errors"]:
        emit_json({"plan": plan}, stream=sys.stderr)
        return EXIT_USAGE

    menu_id = str(workspace["menu_id"])
    alias = str(workspace["alias"])
    base = workspace["base"]
    # The server has no version check, so the menu is read once more right before the write.
    current = _read_menu(args, menu_id)
    now = menu_engine.menu_state(current)
    if now["fingerprint"] != base["fingerprint"]:  # type: ignore[index]
        emit_json(
            {
                "error": "menu_changed",
                "detail": "The menu was changed after 'menu pull'. Nothing was written; pull it again.",
                "changes_since_pull": menu_engine.diff_trees(base["tree"], now["tree"]),  # type: ignore[index, arg-type]
            },
            stream=sys.stderr,
        )
        return EXIT_API_ERROR

    snapshot_path = work_path.with_name(f"{work_path.name}.snapshot-{_utc_now():%Y%m%dT%H%M%SZ}.json")
    title = str(workspace["title"])
    tree = workspace["tree"]
    stored, verified = _replace_menu(args, current, title, alias, tree, snapshot_path)  # type: ignore[arg-type]
    if verified:
        _save_json_file(work_path, menu_engine.new_workspace(stored), "menu workspace")
    emit_json({"plan": plan, "result": _menu_write_result(stored, snapshot_path, verified)})
    return EXIT_OK if verified else _report_unverified("menu")


def run_menu_restore(args: argparse.Namespace) -> int:
    snapshot_file = Path(args.snapshot).expanduser()
    snapshot = menu_engine.load_snapshot(load_settings_file(snapshot_file, "Menu snapshot"))
    title = str(snapshot["title"])
    alias = str(snapshot["alias"])
    tree: list[dict[str, object]] = snapshot["tree"]  # type: ignore[assignment]
    if args.dry_run or not args.confirm:
        emit_json(
            {
                "plan": {
                    "menu_id": snapshot["menu_id"],
                    "alias": alias or None,
                    "title": title,
                    "items": menu_engine.tree_size(tree),
                    "taken_at": snapshot["taken_at"],
                }
            }
        )
        if args.dry_run:
            return EXIT_OK
        return refuse_mutation("menu restore")

    targets = menu_engine.targets_index(_read_menu_targets(args))
    dropped: list[str] = []
    if args.drop_missing:
        tree, dropped = menu_engine.drop_unknown_targets(tree, targets)
    current = _read_menu(args, str(snapshot["menu_id"]))
    now = menu_engine.menu_state(current)
    plan = _menu_plan(snapshot["menu_id"], alias, title, tree, str(now["title"]), now["tree"], targets)  # type: ignore[arg-type]
    plan["dropped"] = dropped
    if plan["errors"]:
        emit_json({"plan": plan}, stream=sys.stderr)
        return EXIT_USAGE
    if plan["unchanged"]:
        raise UsageError("The menu already equals the snapshot; there is nothing to write.")

    snapshot_path = snapshot_file.with_name(f"{snapshot_file.name}.before-restore-{_utc_now():%Y%m%dT%H%M%SZ}.json")
    stored, verified = _replace_menu(args, current, title, alias, tree, snapshot_path)
    emit_json({"plan": plan, "result": _menu_write_result(stored, snapshot_path, verified)})
    return EXIT_OK if verified else _report_unverified("menu")


def _read_logo(args: argparse.Namespace) -> dict[str, object]:
    return require_mapping(_request_feature(args, "logo", "GET", "/company/logo"), "Company logo response")


def _logo_value(logo: dict[str, object]) -> dict[str, object] | None:
    """The stored logo as a write body, or None when the store has no logo."""
    file_id, file_path = logo.get("file_id"), logo.get("file_path")
    if not isinstance(file_id, str) or not isinstance(file_path, str) or not file_id or not file_path:
        return None
    sizes = logo.get("sizes")
    return {
        "file_id": file_id,
        "file_path": file_path,
        "sizes": sizes if isinstance(sizes, list) else [],
        "display_sequence": 1,
    }


def _project_logo(logo: dict[str, object]) -> dict[str, object]:
    value = _logo_value(logo)
    if value is None:
        return {"set": False}
    return {"set": True, "file_id": value["file_id"], "file_path": value["file_path"], "sizes": value["sizes"]}


def run_logo_show(args: argparse.Namespace) -> int:
    emit_json(_project_logo(_read_logo(args)))
    return EXIT_OK


def validate_logo_file(raw_path: str) -> Path:
    """Apply the cabinet's logo limits to a local image."""
    file_path, media_type = validate_local_media_file(raw_path, "image")
    if media_type not in LOGO_MIME_TYPES:
        raise UsageError("A logo must be a JPEG, PNG or WEBP image.")
    if file_path.stat().st_size > LOGO_MAX_SIZE_BYTES:
        raise UsageError(f"A logo file must not exceed {LOGO_MAX_SIZE_BYTES} bytes.")
    return file_path


def _logo_snapshot_path(args: argparse.Namespace) -> Path:
    if args.snapshot_out:
        return Path(args.snapshot_out).expanduser()
    return Path(f"logo-snapshot-{_utc_now():%Y%m%dT%H%M%SZ}.json")


def _replace_logo(
    args: argparse.Namespace,
    previous: dict[str, object],
    body: dict[str, object],
    snapshot_path: Path,
) -> int:
    """Save what the logo is now, write the new value, and read the result back."""
    _save_json_file(
        snapshot_path,
        {
            "kind": LOGO_SNAPSHOT_KIND,
            "version": 1,
            "taken_at": _utc_now().isoformat(),
            "logo": _logo_value(previous),
        },
        "logo snapshot",
    )
    write_error: object = None
    try:
        _request_for_args(args, "PUT", "/company/logo", body=body)
    except ApiError as error:
        # The server stores the logo before its follow-up steps, so a failed request
        # can still have changed it: the read below decides.
        write_error = {"status": error.status, "detail": error.payload}
    stored = _read_logo(args)
    verified = _logo_value(stored) is not None and stored.get("file_id") == body["file_id"]
    if write_error is not None and not verified:
        raise ApiError(write_error["status"], write_error["detail"])  # type: ignore[index]
    result: dict[str, object] = {
        "logo": _project_logo(stored),
        "previous": _project_logo(previous),
        "snapshot_path": str(snapshot_path),
        "verified": verified,
    }
    if _logo_value(previous) is not None:
        result["undo"] = f"logo restore --snapshot {snapshot_path} --confirm"
    if write_error is not None:
        result["write_error"] = write_error
    emit_json(result)
    return EXIT_OK if verified else _report_unverified("logo")


def run_logo_set(args: argparse.Namespace) -> int:
    if not args.confirm and not args.dry_run:
        return refuse_mutation("logo change")
    file_path = validate_logo_file(args.file)
    if args.dry_run:
        emit_json(
            {
                "plan": {
                    "file": str(file_path),
                    "bytes": file_path.stat().st_size,
                    "requests": ["GET /company/logo", "POST /files", "PUT /company/logo", "GET /company/logo"],
                }
            }
        )
        return EXIT_OK

    previous = _read_logo(args)
    body, content_type = encode_multipart_file(file_path)
    uploaded = require_image_upload_response(
        request_json(
            "POST",
            build_url(resolve_base_url(args.base_url), "/files"),
            load_token(),
            raw_data=body,
            content_type=content_type,
            **telemetry(args),
        )
    )
    sizes = uploaded.get("sizes")
    logo = {
        "file_id": uploaded["id"],
        "file_path": uploaded["public_path"],
        "sizes": sizes if isinstance(sizes, list) else [],
        "display_sequence": 1,
    }
    return _replace_logo(args, previous, logo, _logo_snapshot_path(args))


def run_logo_restore(args: argparse.Namespace) -> int:
    snapshot_file = Path(args.snapshot).expanduser()
    snapshot = load_settings_file(snapshot_file, "Logo snapshot")
    if snapshot.get("kind") != LOGO_SNAPSHOT_KIND:
        raise UsageError("This file is not a logo snapshot written by 'logo set'.")
    saved = snapshot.get("logo")
    logo = _logo_value(saved) if isinstance(saved, dict) else None
    if logo is None:
        raise UsageError(
            "The snapshot holds no logo: the store had none, and this API cannot remove a logo."
        )
    if args.dry_run or not args.confirm:
        emit_json({"plan": {"logo": _project_logo(logo), "taken_at": snapshot.get("taken_at")}})
        if args.dry_run:
            return EXIT_OK
        return refuse_mutation("logo restore")
    return _replace_logo(args, _read_logo(args), logo, _logo_snapshot_path(args))


def _add_menu_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", type=parse_uuid, help="Menu UUID from 'menu list'.")
    parser.add_argument(
        "--alias",
        choices=menu_engine.SYSTEM_ALIASES,
        help="A system menu by its alias instead of --id.",
    )


def _add_write_flags(parser: argparse.ArgumentParser, action: str) -> None:
    parser.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help=f"Confirm this {action}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print the plan without token or network access.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yandex Kit experimental constructor fixed-route client.")
    parser.add_argument("--base-url", help="Override API base URL (else KIT_API_BASE_URL, else configured default).")
    parser.add_argument(
        "--bff-url",
        help="Address of the store's own site, used to read widget schemas (else KIT_BFF_BASE_URL, else worked out when possible).",
    )
    parser.add_argument("--confirm", action="store_true", help="Confirm one supported mutation.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show a mutation plan without writing.")
    parser.add_argument("--skill-prompt", help="User prompt that initiated the skill call.")
    parser.add_argument("--skill-session-id", type=parse_uuid, help="Stable UUID for the current user thread.")
    parser.add_argument(
        "--skill-model",
        help="Optional: the model running this skill, e.g. claude-opus-5. Omitted if not passed.",
    )
    parser.add_argument(
        "--skill-harness",
        help="Optional: the agent running this skill, e.g. claude-code or codex. "
        "Omitted if not passed.",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "env",
        help="Show what the client is pointed at: addresses and token source (never the token).",
    ).set_defaults(handler=run_env)

    token = commands.add_parser("token", help="Manage the local API token without network access.")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    token_commands.add_parser("save", help="Save a token to the default token file.").set_defaults(
        handler=lambda _args: _token_save(),
    )
    token_commands.add_parser("status", help="Show the selected token source.").set_defaults(
        handler=lambda _args: _token_status(),
    )
    token_web = token_commands.add_parser(
        "web",
        help="Open a one-shot local page (loopback only) for typing the token by hand.",
    )
    token_web.add_argument(
        "--port",
        type=parse_local_port,
        default=0,
        help="Loopback port to listen on; 0 (default) picks a free one.",
    )
    token_web.add_argument(
        "--timeout",
        type=parse_page_timeout,
        default=tokenweb.DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for the token before giving up (default {int(tokenweb.DEFAULT_TIMEOUT_SECONDS)}).",
    )
    token_web.add_argument(
        "--no-open",
        action="store_true",
        help="Only print the URL instead of opening a browser.",
    )
    token_web.add_argument(
        "--force",
        action="store_true",
        help="Serve even when this host looks unreachable from the user's browser (port forward).",
    )
    token_web.set_defaults(handler=_token_web)

    versions = commands.add_parser("versions", help="Read constructor content versions.")
    version_commands = versions.add_subparsers(dest="versions_command", required=True)
    versions_list = version_commands.add_parser("list", help="List newest content versions with a local bound.")
    limit_group = versions_list.add_mutually_exclusive_group()
    limit_group.add_argument("--limit", type=parse_positive_limit, default=DEFAULT_VERSION_LIMIT)
    limit_group.add_argument("--all", action="store_true", help="Return the complete history explicitly.")
    versions_list.add_argument("--since", type=parse_since, help="Inclusive ISO-8601 lower bound.")
    versions_list.set_defaults(handler=run_versions_list)
    version_content = version_commands.add_parser("content", help="Read complete content for one version.")
    version_content.add_argument(
        "--id",
        required=True,
        type=parse_version_id,
        help="Positive integer content version id.",
    )
    version_content.set_defaults(handler=run_version_content)

    content = commands.add_parser("content", help="Read constructor content projections.")
    content_commands = content.add_subparsers(dest="content_command", required=True)
    content_commands.add_parser("summary", help="Read compact current storefront totals.").set_defaults(
        handler=run_content_summary,
    )
    latest = content_commands.add_parser("latest", help="Read compact current totals; use --raw explicitly.")
    latest.add_argument("--raw", action="store_true", help="Return complete ConstructorContent.")
    latest.set_defaults(handler=run_latest_content)
    preview = content_commands.add_parser("preview", help="Preview storefront content with a store theme.")
    preview.add_argument("--template-id", required=True, type=parse_uuid, help="Store theme/template UUID.")
    preview.set_defaults(handler=run_template_preview)

    pull = content_commands.add_parser("pull", help="Snapshot full content into a local write workspace.")
    pull.add_argument("--out", help="Workspace file path (default: per-store workspace directory).")
    pull.set_defaults(handler=run_content_pull)
    templates_sync = content_commands.add_parser(
        "templates-sync",
        help="Add section templates created after the pull to the workspace, keeping every edit in it.",
    )
    templates_sync.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    templates_sync.set_defaults(handler=run_content_templates_sync)
    content_diff = content_commands.add_parser("diff", help="Offline diff of the workspace against its base.")
    content_diff.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    content_diff.set_defaults(handler=run_content_diff)
    push = content_commands.add_parser(
        "push",
        help="Confirmed full-replace write of the workspace through PUT /constructor/content.",
    )
    push.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    push.add_argument("--commit-message", required=True, help="Human-readable change description.")
    push.add_argument(
        "--allow-page-removal",
        action="store_true",
        help="Permit deletion of non-default pages missing from the workspace.",
    )
    push.add_argument(
        "--no-publish",
        action="store_true",
        help=(
            "Write the edit as a stored proposal version WITHOUT publishing it: the server keeps "
            "the storefront on a copy of the current content in the same transaction (activate: false). "
            "Requires server support — when the response carries no active_version_id, the client "
            "reports loudly that the edit WAS published and exits 4."
        ),
    )
    push.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Confirm this full-replace write.",
    )
    push.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run every gate and print the plan without token or network access.",
    )
    push.set_defaults(handler=run_content_push)

    pages = commands.add_parser("pages", help="Read a compact storefront page inventory.")
    page_commands = pages.add_subparsers(dest="pages_command", required=True)
    page_commands.add_parser("list", help="List pages without layouts.").set_defaults(handler=run_pages_list)

    page = commands.add_parser("page", help="Read one storefront page, or edit pages in the workspace.")
    page_commands = page.add_subparsers(dest="page_command", required=True)
    page_show = page_commands.add_parser("show", help="Show one compact page layout.")
    page_selector = page_show.add_mutually_exclusive_group(required=True)
    page_selector.add_argument("--alias", help="Exact system page alias, for example main.")
    page_selector.add_argument("--id", type=parse_uuid, help="Exact page UUID.")
    page_show.add_argument("--raw", action="store_true", help="Return the complete selected page.")
    page_show.set_defaults(handler=run_page_show)

    page_add = page_commands.add_parser("add", help="Insert one new page into the workspace.")
    page_add.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    page_add.add_argument("--title", required=True, help="Page title.")
    page_add.add_argument("--pattern", help="URL route, for example /promo-august (custom pages).")
    page_add.add_argument(
        "--alias",
        help="System alias; only the missing default of an alias or an extra productCard/categoryAndCollection variant.",
    )
    page_add.add_argument("--status", choices=("published", "hidden"), default="published")
    page_add.add_argument("--seo-title", help="SEO title (static pages only).")
    page_add.add_argument("--seo-h1", help="SEO H1 (static pages only).")
    page_add.add_argument("--seo-description", help="SEO description (static pages only).")
    page_add.add_argument(
        "--no-chrome",
        action="store_true",
        help="Do not copy the store header and footer onto the new page.",
    )
    page_add.add_argument(
        "--copy-layout-from",
        choices=("default",),
        help="For a new productCard/categoryAndCollection template: start from the layout of the "
        "default template (its sections copied as new ones; a block it shares with other pages "
        "stays shared and is listed as shared_sections).",
    )
    page_add.set_defaults(handler=run_page_add)

    page_set = page_commands.add_parser("set", help="Change title, pattern, status, or SEO of one page.")
    page_set.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    set_selector = page_set.add_mutually_exclusive_group(required=True)
    set_selector.add_argument("--alias", help="Exact system page alias, for example main.")
    set_selector.add_argument("--id", type=parse_uuid, help="Exact page UUID.")
    page_set.add_argument("--title", help="New page title.")
    page_set.add_argument("--pattern", help="New URL route (custom pages only).")
    page_set.add_argument("--status", choices=("published", "hidden"), help="New page status.")
    page_set.add_argument("--seo-title", help="SEO title (static pages only).")
    page_set.add_argument("--seo-h1", help="SEO H1 (static pages only).")
    page_set.add_argument("--seo-description", help="SEO description (static pages only).")
    page_set.add_argument("--clear-meta", action="store_true", help="Remove the page SEO meta entirely.")
    page_set.set_defaults(handler=run_page_set)

    page_remove = page_commands.add_parser(
        "remove",
        help="Remove one page from the workspace; the push will demand --allow-page-removal.",
    )
    page_remove.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    remove_selector = page_remove.add_mutually_exclusive_group(required=True)
    remove_selector.add_argument("--alias", help="Exact system page alias.")
    remove_selector.add_argument("--id", type=parse_uuid, help="Exact page UUID.")
    page_remove.set_defaults(handler=run_page_remove)

    sections = commands.add_parser("sections", help="Read a flat section inventory of one page.")
    sections_commands = sections.add_subparsers(dest="sections_command", required=True)
    sections_list = sections_commands.add_parser("list", help="List sections of one page with ids.")
    sections_selector = sections_list.add_mutually_exclusive_group(required=True)
    sections_selector.add_argument("--page", help="Exact system page alias, for example main.")
    sections_selector.add_argument("--page-id", type=parse_uuid, help="Exact page UUID.")
    sections_list.set_defaults(handler=run_sections_list)

    section = commands.add_parser("section", help="Inspect or edit one storefront section.")
    section_commands = section.add_subparsers(dest="section_command", required=True)
    section_show = section_commands.add_parser("show", help="Show one section's settings and page copies.")
    section_show.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    section_show.set_defaults(handler=run_section_show)
    section_set = section_commands.add_parser(
        "set", help="Replace one section's settings, and optionally move it to another template."
    )
    section_set.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    section_set.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    section_set.add_argument("--settings-file", required=True, help="Path to the new settings JSON object.")
    section_set.add_argument(
        "--template-id",
        type=parse_uuid,
        help="Rebind this custom section to another section template, on every page it appears on. "
        "The settings are checked against the new template, not the old one.",
    )
    section_set.add_argument(
        "--widget",
        help="Header or footer slot only: switch it to YandexKit.Mystique (with --template-id of a "
        "template carrying the slot's alias) or back to the built-in YandexKit.Header / "
        "YandexKit.Footer, on every page at once.",
    )
    section_set.set_defaults(handler=run_section_set)
    section_add = section_commands.add_parser("add", help="Insert one new section into a page in the workspace.")
    section_add.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    add_selector = section_add.add_mutually_exclusive_group(required=True)
    add_selector.add_argument("--page", help="Exact system page alias, for example main.")
    add_selector.add_argument("--page-id", type=parse_uuid, help="Exact page UUID.")
    section_add.add_argument("--widget", required=True, help="Widget name, for example YandexKit.Faq.")
    section_add.add_argument("--template-id", type=parse_uuid, help="Section-template UUID for YandexKit.Mystique.")
    section_add.add_argument("--settings-file", required=True, help="Path to the settings JSON object.")
    section_add.add_argument("--position", type=parse_positive_limit, help="1-based position in the layout.")
    section_add.set_defaults(handler=run_section_add)
    section_remove = section_commands.add_parser(
        "remove", help="Remove one section from the workspace: every copy, or the copy on one page."
    )
    section_remove.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    section_remove.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    remove_page_selector = section_remove.add_mutually_exclusive_group()
    remove_page_selector.add_argument(
        "--page", help="Remove only the copy on this page (alias); the other pages keep theirs."
    )
    remove_page_selector.add_argument("--page-id", type=parse_uuid, help="Remove only the copy on this page (UUID).")
    section_remove.set_defaults(handler=run_section_remove)
    section_status_parser = section_commands.add_parser(
        "status", help="Hide or show one section: every copy, or the copy on one page."
    )
    section_status_parser.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    section_status_parser.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    section_status_parser.add_argument(
        "--status", required=True, choices=pages_engine.SECTION_STATUSES, help="New section status."
    )
    status_page_selector = section_status_parser.add_mutually_exclusive_group()
    status_page_selector.add_argument("--page", help="Change only the copy on this page (alias).")
    status_page_selector.add_argument("--page-id", type=parse_uuid, help="Change only the copy on this page (UUID).")
    section_status_parser.set_defaults(handler=run_section_status)
    section_move = section_commands.add_parser("move", help="Move one section inside its page layout.")
    section_move.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    section_move.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    section_move.add_argument("--position", required=True, type=parse_positive_limit, help="1-based target position.")
    move_selector = section_move.add_mutually_exclusive_group()
    move_selector.add_argument("--page", help="Page alias when the section is copied to several pages.")
    move_selector.add_argument("--page-id", type=parse_uuid, help="Page UUID when copies are ambiguous.")
    section_move.set_defaults(handler=run_section_move)

    theme = commands.add_parser("theme", help="Edit storefront global settings in the workspace.")
    theme_commands = theme.add_subparsers(dest="theme_command", required=True)
    theme_set = theme_commands.add_parser("set", help="Replace global settings after theme-schema validation.")
    theme_set.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    theme_set.add_argument("--settings-file", required=True, help="Path to the new global settings JSON object.")
    theme_set.set_defaults(handler=run_theme_set)

    schema = commands.add_parser("schema", help="Schemas of the built-in widgets, read from the store's own site.")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    schema_fetch = schema_commands.add_parser("fetch", help="Fetch the widget schemas and cache them, refetching only when changed.")
    schema_fetch.add_argument("--force", action="store_true", help="Ignore the cached ETag and refetch.")
    schema_fetch.set_defaults(handler=run_schema_fetch)
    schema_commands.add_parser("widgets", help="List cached native widgets compactly.").set_defaults(
        handler=run_schema_widgets,
    )
    schema_widget = schema_commands.add_parser("widget", help="Show one widget's compact resolved schema.")
    schema_widget.add_argument("--name", required=True, help="Widget name, for example YandexKit.Faq.")
    schema_widget.add_argument(
        "--skeleton",
        action="store_true",
        help="Build a locally validated settings skeleton from schema defaults.",
    )
    schema_widget.set_defaults(handler=run_schema_widget)
    schema_commands.add_parser("theme", help="Show the compact globalSettings schema.").set_defaults(
        handler=run_schema_theme,
    )
    schema_version = schema_commands.add_parser(
        "widget-version",
        help="Read one widget's actual version through migrate with strictly empty settings.",
    )
    schema_version.add_argument("--name", required=True, help="Widget name, for example YandexKit.Faq.")
    schema_version.set_defaults(handler=run_schema_widget_version)

    templates = commands.add_parser("templates", help="Read custom section templates.")
    template_commands = templates.add_subparsers(dest="templates_command", required=True)
    template_commands.add_parser("list", help="List template metadata and schema sizes.").set_defaults(
        handler=run_templates_list,
    )
    template_show = template_commands.add_parser("show", help="Show one template schema by UUID.")
    template_show.add_argument("--id", required=True, type=parse_uuid, help="Section-template UUID.")
    template_show.set_defaults(handler=run_template_show)
    template_versions = template_commands.add_parser("versions", help="Read one template's version history.")
    template_versions.add_argument("--id", required=True, type=parse_uuid, help="Section-template UUID.")
    template_versions.add_argument("--version-id", type=parse_uuid, help="One template version UUID.")
    template_versions.set_defaults(handler=run_templates_versions)
    template_export = template_commands.add_parser(
        "export", help="Write one template's html/css/schema/script to files."
    )
    template_export.add_argument("--id", required=True, type=parse_uuid, help="Section-template UUID.")
    template_export.add_argument("--out", required=True, help="Output directory for the exported files.")
    template_export.set_defaults(handler=run_templates_export)
    create = template_commands.add_parser("create", help="Validate, preview, and create a section template.")
    create.add_argument("--file", required=True, help="Path to section-template request JSON.")
    create.add_argument(
        "--chrome-slot",
        choices=sorted(CHROME_ALIAS_BY_SLOT),
        help="Make the template a store-wide header or footer: sets alias YandexKit.Header or "
        "YandexKit.Footer in the body (a chrome alias already in the file must match; without this "
        "flag it is refused). The plan then carries a store-wide warning.",
    )
    create.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Confirm this template creation.",
    )
    create.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Validate and print the compact create plan without token or network access.",
    )
    create.add_argument(
        "--work",
        help="Workspace file: after a confirmed create the new template is added to its snapshot "
        "(as 'content templates-sync' would), so it can be placed without pulling again.",
    )
    create.add_argument(
        "--show-body",
        action="store_true",
        help="Add the exact request body (html, css, schema, script) to the plan.",
    )
    create.set_defaults(handler=run_templates_create)

    media = commands.add_parser("media", help="Upload local storefront media and inspect video status.")
    media_commands = media.add_subparsers(dest="media_command", required=True)
    image_upload = media_commands.add_parser("upload", help="Upload a confirmed local image.")
    image_upload.add_argument("--file", required=True, help="Path to a local image file.")
    image_upload.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Confirm this image upload.",
    )
    image_upload.set_defaults(handler=run_media_upload)

    font_upload = media_commands.add_parser(
        "font-upload", help="Upload a confirmed local font file (.woff2, .woff, .ttf, .otf) for the theme."
    )
    font_upload.add_argument("--file", required=True, help="Path to a local font file.")
    font_upload.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Confirm this font upload.",
    )
    font_upload.set_defaults(handler=run_media_font_upload)

    video_upload = media_commands.add_parser("video-upload", help="Upload a confirmed local video.")
    video_upload.add_argument("--file", required=True, help="Path to a local video file.")
    video_upload.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Confirm this video upload.",
    )
    video_upload.set_defaults(handler=run_media_video_upload)

    video_status = media_commands.add_parser("video-status", help="Get asynchronous video processing status.")
    video_status.add_argument("--id", required=True, help="Video ID returned by video-upload.")
    video_status.set_defaults(handler=run_media_video_status)

    menu = commands.add_parser("menu", help="Read storefront menus, or replace one through a local workspace.")
    menu_commands = menu.add_subparsers(dest="menu_command", required=True)
    menu_commands.add_parser("list", help="List the store's menus with their totals.").set_defaults(
        handler=run_menu_list
    )
    menu_show = menu_commands.add_parser("show", help="Show one menu as a tree with its link targets.")
    _add_menu_selector(menu_show)
    menu_show.set_defaults(handler=run_menu_show)
    menu_commands.add_parser(
        "links", help="List the pages, categories and collections a menu item can link to."
    ).set_defaults(handler=run_menu_links)
    menu_pull = menu_commands.add_parser("pull", help="Write one menu into a local workspace file.")
    _add_menu_selector(menu_pull)
    menu_pull.add_argument("--work", required=True, help="Path of the workspace file to write.")
    menu_pull.add_argument("--force", action="store_true", help="Overwrite an existing workspace file.")
    menu_pull.set_defaults(handler=run_menu_pull)
    menu_diff = menu_commands.add_parser("diff", help="Offline diff and checks of a menu workspace.")
    menu_diff.add_argument("--work", required=True, help="Workspace file path from 'menu pull'.")
    menu_diff.set_defaults(handler=run_menu_diff)
    menu_push = menu_commands.add_parser(
        "push", help="Confirmed full replace of one menu from its workspace through PUT /menus/{id}."
    )
    menu_push.add_argument("--work", required=True, help="Workspace file path from 'menu pull'.")
    _add_write_flags(menu_push, "menu write")
    menu_push.set_defaults(handler=run_menu_push)
    menu_restore = menu_commands.add_parser("restore", help="Write a menu back from a snapshot of 'menu push'.")
    menu_restore.add_argument("--snapshot", required=True, help="Snapshot file path printed by 'menu push'.")
    menu_restore.add_argument(
        "--drop-missing",
        action="store_true",
        help="Leave out items whose page, category or collection no longer exists, with everything under them.",
    )
    _add_write_flags(menu_restore, "menu restore")
    menu_restore.set_defaults(handler=run_menu_restore)

    logo = commands.add_parser("logo", help="Read or replace the store logo.")
    logo_commands = logo.add_subparsers(dest="logo_command", required=True)
    logo_commands.add_parser("show", help="Show the current store logo.").set_defaults(handler=run_logo_show)
    logo_set = logo_commands.add_parser("set", help="Upload a local image and make it the store logo.")
    logo_set.add_argument("--file", required=True, help="Path to a local JPEG, PNG or WEBP file, at most 5 MB.")
    logo_set.add_argument("--snapshot-out", help="Where to save the previous logo (default: ./logo-snapshot-<time>.json).")
    _add_write_flags(logo_set, "logo change")
    logo_set.set_defaults(handler=run_logo_set)
    logo_restore = logo_commands.add_parser("restore", help="Put back the logo saved by 'logo set'.")
    logo_restore.add_argument("--snapshot", required=True, help="Snapshot file path printed by 'logo set'.")
    logo_restore.add_argument("--snapshot-out", help="Where to save the logo being replaced.")
    _add_write_flags(logo_restore, "logo restore")
    logo_restore.set_defaults(handler=run_logo_restore)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except UsageError as error:
        print(f"Usage error: {redact_secrets(str(error))}", file=sys.stderr)
        return EXIT_USAGE
    except ApiError as error:
        emit_json(
            {
                "error": "api_error" if error.status is not None else "network_error",
                "status": error.status,
                "detail": error.payload,
            },
            stream=sys.stderr,
        )
        return EXIT_API_ERROR
    finally:
        # After the command, never instead of it: the update runs once the user
        # already has their result, and it cannot change the exit code. A command
        # that worked must not start failing because a mirror was unreachable.
        selfupdate.maybe_update()


if __name__ == "__main__":
    sys.exit(main())
