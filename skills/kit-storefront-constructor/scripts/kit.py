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
TEMPLATE_CREATE_FIELDS = {
    "alias",
    "commit_message",
    "css",
    "html",
    "json_schema",
    "title",
}
TEMPLATE_REQUIRED_FIELDS = {
    "css",
    "html",
    "json_schema",
    "title",
}
TEMPLATE_RESPONSE_FIELDS = {
    "alias",
    "created_at",
    "css",
    "html",
    "id",
    "json_schema",
    "title",
    "updated_at",
}
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


def encode_multipart_file(file_path: Path) -> tuple[bytes, str]:
    """Encode one local file as the API's required multipart `file` field."""
    boundary = uuid.uuid4().hex
    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
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
    if "alias" in request and not isinstance(request["alias"], str):
        raise UsageError("Section template request.alias must be a string when provided.")
    return {field: request[field] for field in TEMPLATE_CREATE_FIELDS if field in request}


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


def _validate_section_template(
    value: object,
    label: str,
    extra_fields: set[str] | None = None,
) -> dict[str, object]:
    template = require_mapping(value, label)
    allowed_fields = TEMPLATE_RESPONSE_FIELDS | (extra_fields or set())
    unknown_fields = set(template) - allowed_fields
    if unknown_fields:
        raise UsageError(f"{label} contains unsupported fields: {', '.join(sorted(unknown_fields))}.")
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
    if "alias" in template and not isinstance(template["alias"], str):
        raise UsageError(f"{label}.alias must be a string when provided.")
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
    template = _validate_section_template(value, "Created section template", {"version"})
    version = require_mapping(template.get("version"), "Created section template.version")
    unknown_version_fields = set(version) - TEMPLATE_VERSION_FIELDS
    if unknown_version_fields:
        raise UsageError(
            "Created section template.version contains unsupported fields: "
            f"{', '.join(sorted(unknown_version_fields))}."
        )
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


def project_page(page: dict[str, object]) -> dict[str, object]:
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
    return {
        "id": _required(page, "id", str, "Page"),
        "alias": page.get("alias"),
        "title": _required(page, "title", str, "Page"),
        "status": _required(page, "status", str, "Page"),
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
        alias = template.get("alias")
        if alias is not None and not isinstance(alias, str):
            raise UsageError(f"{label}.alias must be str when present.")
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
    page = select_page(_read_content(args), args.alias, args.id)
    emit_json(page if args.raw else project_page(page))
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


def run_templates_create(args: argparse.Namespace) -> int:
    request = load_template_create(Path(args.file).expanduser())
    emit_json(
        {
            "body": request,
            "operation": "POST /section-templates",
        }
    )
    if args.dry_run:
        return EXIT_OK
    if not args.confirm:
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
    emit_json(result)
    return EXIT_OK


# --- schema engine commands (BFF) -----------------------------------------------------


def _resolved_bff_url(args: argparse.Namespace) -> str:
    return schema_engine.resolve_bff_url(getattr(args, "bff_url", None))


def _cached_schema(args: argparse.Namespace) -> dict[str, object]:
    """Load the cached BFF schema offline, tolerating an unset BFF URL when unambiguous."""
    try:
        bff_url: str | None = _resolved_bff_url(args)
    except UsageError:
        bff_url = None
    return schema_engine.load_cached_schema(bff_url)


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
        "settings": section.get("settings"),
        "schema_source": schema_source,
        "copies": [
            {
                "page_id": page["id"],
                "page_alias": page.get("alias"),
                "page_title": page["title"],
                "section_type": copy_section.get("section_type"),
            }
            for page, _idx, copy_section in copies
        ],
    }
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
    emit_json({"template_id": args.id, "written": written})
    return EXIT_OK


# --- write core: workspace, diff, push ------------------------------------------------


def _work_path(args: argparse.Namespace) -> Path:
    return Path(args.work).expanduser()


def _schema_for_gates(args: argparse.Namespace) -> dict[str, object] | None:
    """The cached BFF schema for validation, or None when no cache is reachable.

    Native-widget edits fail closed later without it; custom-template edits validate
    against the pulled catalog and never need the BFF.
    """
    try:
        return _cached_schema(args)
    except UsageError:
        return None


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
    try:
        workspace_engine.section_template(workspace.base, template_id)
    except UsageError:
        raise UsageError(
            f"Section template {template_id!r} is not in this workspace. A template created "
            "after 'content pull' is not in the snapshot yet — run 'content pull' again, then "
            "rebind."
        ) from None


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
    schema_document = _schema_for_gates(args)
    candidate = dict(section)
    candidate["settings"] = settings
    # Judging the new settings against the template they are moving to, not the one
    # they are leaving: that check is the whole point of a rebind.
    if template_id is not None:
        candidate["template_id"] = template_id
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
    schema_build = None
    try:
        schema_build = str(_cached_schema(args).get("buildVersion"))
    except UsageError:
        schema_build = None
    manifest = workspace_engine.write_workspace(work_path, content, base_url, schema_build)
    emit_json({"work_path": str(work_path), "manifest": manifest})
    return EXIT_OK


def run_section_set(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    settings = load_settings_file(Path(args.settings_file).expanduser(), "Settings")
    copies = workspace_engine.require_section_copies(workspace.content, args.id)
    template_id = getattr(args, "template_id", None)
    if template_id:
        _require_rebindable(copies[0][2], workspace, template_id)
    _validate_edited_section(
        args, workspace, copies[0][2], settings, with_baseline=True, template_id=template_id
    )
    report = workspace_engine.set_section_settings(workspace, args.id, settings, template_id)
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
    elif args.template_id:
        raise UsageError("--template-id applies only to YandexKit.Mystique sections.")
    else:
        # A new native section gets the widget's actual version from migrate; a widget
        # without migrations has no version and the field is not written at all.
        document = _cached_schema(args)
        schema_engine.widget_schema(document, args.widget)
        if "version" not in settings:
            actual_version = schema_engine.fetch_widget_version(_resolved_bff_url(args), args.widget)
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
    workspace.save()
    emit_json(report)
    return EXIT_OK


def run_section_remove(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    report = workspace_engine.remove_section(workspace, args.id)
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
    schema_document = _schema_for_gates(args)
    if schema_document is None:
        raise UsageError("Global settings are checked against the storefront theme schema; run 'schema fetch' first.")
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


def run_page_add(args: argparse.Namespace) -> int:
    workspace = workspace_engine.load_workspace(_work_path(args))
    report = pages_engine.add_page(
        workspace,
        title=args.title,
        pattern=args.pattern,
        alias=args.alias,
        status=args.status,
        seo=_seo_from_args(args),
        with_chrome=not args.no_chrome,
    )
    workspace.save()
    emit_json(report)
    return EXIT_OK


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
    gate_errors = write_engine.check_gates(workspace, _schema_for_gates(args), args.allow_page_removal)
    if gate_errors:
        emit_json({"error": "push_gates_failed", "gates": gate_errors}, stream=sys.stderr)
        return EXIT_USAGE
    plan = write_engine.build_push_plan(workspace, commit_message, activate=publish)
    emit_json(plan)
    if args.dry_run:
        return EXIT_OK
    if not args.confirm:
        print(
            "Push refused: get explicit confirmation, then rerun this operation with --confirm.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

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
                "mode": "proposal_stored_not_published",
                "proposal_version_id": written_version_id,
                "active_version_id": active_version_id,
                "pages_count": response.get("pages_count"),
                "sections_count": response.get("sections_count"),
                "message": (
                    f"Правка сохранена как версия-предложение {written_version_id} и НЕ опубликована. "
                    f"На витрине сейчас версия {active_version_id} — копия прежнего контента. "
                    f"Предложение открывается в истории по номеру {written_version_id}. "
                    f"Манифест воркспейса переведён на версию {active_version_id}."
                ),
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
    emit_json(response)
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
    section_remove = section_commands.add_parser("remove", help="Remove one section from the workspace.")
    section_remove.add_argument("--work", required=True, help="Workspace file path from 'content pull'.")
    section_remove.add_argument("--id", required=True, type=parse_uuid, help="Section UUID.")
    section_remove.set_defaults(handler=run_section_remove)
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
    template_export = template_commands.add_parser("export", help="Write one template's html/css/schema to files.")
    template_export.add_argument("--id", required=True, type=parse_uuid, help="Section-template UUID.")
    template_export.add_argument("--out", required=True, help="Output directory for the exported files.")
    template_export.set_defaults(handler=run_templates_export)
    create = template_commands.add_parser("create", help="Validate, preview, and create a section template.")
    create.add_argument("--file", required=True, help="Path to section-template request JSON.")
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
        help="Validate and print the exact request without token or network access.",
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
