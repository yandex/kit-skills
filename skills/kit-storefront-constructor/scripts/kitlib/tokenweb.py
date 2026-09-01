"""One-shot local page for typing the store API token by hand.

`token save` needs either a terminal (hidden prompt) or a pipe — and a pipe means the
secret travels through whatever produced it, an agent transcript included. This module
serves the same save through a browser instead: the user types the token into a form on
their own machine, and the value goes straight from the browser to the token file.

Everything here is local. No route, no network call, nothing sent anywhere:

* the socket binds to loopback only, never to `0.0.0.0`;
* the URL carries a one-time secret compared in constant time — a request without it,
  or with a wrong one, gets a bare 404;
* the `Host` header and the peer address must both be loopback, which is what stops a
  DNS-rebinding page in the user's browser from reaching this port;
* the token arrives in a POST body, never in a URL, and request logging is off, so it
  cannot land in a request line;
* the page loads no external resource, and its CSP says so;
* the server serves one successful save and exits, with a deadline in case nobody comes.
"""

from __future__ import annotations

import html
import http.server
import os
import secrets
import socket
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable, Optional, TextIO, Tuple, Type

from kitlib.common import UsageError, redact_secrets

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
LOOPBACK_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_TIMEOUT_SECONDS = 3600.0
# A token is ~64 characters; a form body far past that is not one, so refuse to read it.
MAX_BODY_BYTES = 8192
# The secret is 256 bits of urandom, so guessing is hopeless — this only keeps a local
# process that discovered the port from hammering it while the page waits.
MAX_WRONG_SECRETS = 20
POLL_INTERVAL_SECONDS = 0.5

SECURITY_HEADERS: Tuple[Tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    ),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)

PAGE_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
  padding: 24px; background: #f4f4f5; color: #1c1c1e;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { width: 100%; max-width: 520px; background: #fff; border-radius: 14px; padding: 28px; }
h1 { margin: 0 0 4px; font-size: 20px; }
p { margin: 0 0 14px; }
.sub { color: #6b6b70; }
.warn { background: #fff4d6; border-radius: 10px; padding: 12px 14px; margin-bottom: 18px; font-size: 14px; }
.error { background: #ffe3e0; border-radius: 10px; padding: 12px 14px; margin-bottom: 18px; font-size: 14px; }
.ok { background: #e3f7e8; border-radius: 10px; padding: 12px 14px; margin-bottom: 18px; font-size: 14px; }
label { display: block; margin-bottom: 6px; font-weight: 600; font-size: 14px; }
input[type=password] {
  width: 100%; padding: 12px 14px; font-size: 15px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  border: 1px solid #d3d3d8; border-radius: 10px; background: #fff; color: inherit;
}
input[type=password]:focus { outline: 2px solid #fc3f1d; outline-offset: 1px; border-color: #fc3f1d; }
button {
  margin-top: 16px; width: 100%; padding: 12px 16px; font-size: 15px; font-weight: 600;
  border: 0; border-radius: 10px; background: #fc3f1d; color: #fff; cursor: pointer;
}
button:hover { background: #e0330f; }
footer { margin-top: 20px; color: #6b6b70; font-size: 13px; }
@media (prefers-color-scheme: dark) {
  body { background: #131316; color: #f2f2f5; }
  main { background: #1d1d21; }
  .warn { background: #3a3116; }
  .error { background: #3a1f1c; }
  .ok { background: #17301d; }
  .sub, footer { color: #a1a1a8; }
  input[type=password] { background: #131316; border-color: #3a3a41; }
}
"""


class _Session:
    """Mutable state shared by the handler and the serving loop."""

    def __init__(
        self,
        secret: str,
        token_path: Path,
        validate_token: Callable[[str], Optional[str]],
        save_token: Callable[[str], None],
        override_warning: Optional[str],
    ) -> None:
        self.secret = secret
        self.token_path = token_path
        self.validate_token = validate_token
        self.save_token = save_token
        self.override_warning = override_warning
        self.saved = False
        self.wrong_secrets = 0
        self.aborted_reason = ""


def _document(title: str, body: str) -> bytes:
    """Wrap page content in a self-contained document with no external resource."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        "<title>{title}</title>\n"
        "<style>{style}</style>\n"
        "</head>\n"
        "<body><main>{body}</main></body>\n"
        "</html>\n"
    ).format(title=html.escape(title), style=PAGE_STYLE, body=body).encode("utf-8")


def _form_page(session: _Session, error: str = "") -> bytes:
    """The token entry form, optionally showing a rejection from the previous attempt.

    The page is read by a shop owner, not by an engineer: it says where to get the
    token and what happens to it, and nothing about file paths, permissions or
    commands. Anything a technical user needs is available from `token status`.
    """
    blocks = [
        "<h1>Подключение магазина</h1>",
        '<p class="sub">Вставьте токен магазина Яндекс.Кит — после этого ассистент сможет '
        "работать с вашим магазином.</p>",
    ]
    if session.override_warning:
        blocks.append('<div class="warn">{text}</div>'.format(text=html.escape(session.override_warning)))
    if error:
        blocks.append('<div class="error">{message}</div>'.format(message=html.escape(error)))
    blocks.append(
        '<form method="post" action="/save" autocomplete="off">'
        '<input type="hidden" name="secret" value="{secret}">'
        '<label for="token">Токен магазина</label>'
        '<input id="token" name="token" type="password" autocomplete="off" autofocus spellcheck="false" '
        'placeholder="yakit_…">'
        '<button type="submit">Подключить</button>'
        "</form>".format(secret=html.escape(session.secret))
    )
    blocks.append(
        "<footer>Где взять токен: кабинет Яндекс.Кит, <b>Настройки → API → «Сгенерировать токен»</b>.<br>"
        "Токен остаётся на этом компьютере и никуда не отправляется. "
        "Страница одноразовая — никому не передавайте ссылку на неё.</footer>"
    )
    return _document("Подключение магазина Яндекс.Кит", "".join(blocks))


def _saved_page(session: _Session) -> bytes:
    """The terminal page shown once the token file has been written."""
    blocks = [
        "<h1>Токен сохранён</h1>",
        '<div class="ok">Магазин подключён. Можно закрыть эту вкладку и вернуться в чат.</div>',
    ]
    if session.override_warning:
        blocks.append('<div class="warn">{text}</div>'.format(text=html.escape(session.override_warning)))
    return _document("Токен сохранён", "".join(blocks))


def _build_handler(session: _Session) -> Type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler bound to one session."""

    class TokenPageHandler(http.server.BaseHTTPRequestHandler):
        server_version = "kit-token-page"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            """Log nothing: a request line or an error string could carry user input."""

        def _reply(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._reply(404, b"not found\n", "text/plain; charset=utf-8")

        def _local_request(self) -> bool:
            """Both the peer and the Host header must be loopback."""
            peer = self.client_address[0] if self.client_address else ""
            if peer not in LOOPBACK_PEERS:
                return False
            host_header = self.headers.get("Host", "")
            hostname = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
            return hostname in LOOPBACK_HOST_NAMES

        def _secret_ok(self, candidate: str) -> bool:
            """Constant-time check that also bounds how many wrong guesses we serve."""
            if not candidate:
                return False
            if secrets.compare_digest(candidate, session.secret):
                return True
            session.wrong_secrets += 1
            if session.wrong_secrets >= MAX_WRONG_SECRETS:
                session.aborted_reason = "too many requests with a wrong link secret"
            return False

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            if not self._local_request():
                self._not_found()
                return
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/":
                self._not_found()
                return
            query = urllib.parse.parse_qs(parsed.query)
            candidates = query.get("secret") or []
            if not self._secret_ok(candidates[0] if candidates else ""):
                self._not_found()
                return
            if session.saved:
                self._reply(200, _saved_page(session))
                return
            self._reply(200, _form_page(session))

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            if not self._local_request():
                self._not_found()
                return
            if urllib.parse.urlsplit(self.path).path != "/save":
                self._not_found()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, b"bad request\n", "text/plain; charset=utf-8")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._reply(413, b"request too large\n", "text/plain; charset=utf-8")
                return
            raw = self.rfile.read(length)
            fields = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
            submitted = fields.get("secret") or []
            if not self._secret_ok(submitted[0] if submitted else ""):
                self._not_found()
                return
            values = fields.get("token") or []
            token = values[0].strip() if values else ""
            problem = session.validate_token(token)
            if problem:
                self._reply(400, _form_page(session, problem))
                return
            try:
                session.save_token(token)
            except Exception as error:  # noqa: BLE001 - any save failure belongs on the page
                self._reply(500, _form_page(session, redact_secrets(str(error))))
                return
            session.saved = True
            self._reply(200, _saved_page(session))

    return TokenPageHandler


def _open_url(url: str) -> bool:
    """Try to open the page in the user's browser; the printed URL is the fallback."""
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box has no browser, and that is fine
        return False


def _container_cgroup() -> bool:
    """Report whether PID 1 lives in a container control group (Linux only)."""
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "lxc", "libpod"))


def unreachable_reason() -> Optional[str]:
    """Say why the user's browser probably cannot reach this machine, or None.

    The page is only useful when the client runs where the user's browser runs.
    In a hosted agent session — a container, a remote shell, a headless box — the
    URL points at *that* host, so the user gets a link that resolves to their own
    machine, where nothing is listening. That failure is silent and confusing, so
    it is worth refusing before binding a socket rather than after.
    """
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return "this client runs over SSH, so 127.0.0.1 is the remote host, not the user's desktop"
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return "this client runs inside a container the user's browser cannot reach"
    if _container_cgroup():
        return "this client runs inside a container the user's browser cannot reach"
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return "this Linux session has no display, so the browser is not on this machine"
    try:
        webbrowser.get()
    except webbrowser.Error:
        return "this machine has no browser to open the page with"
    return None


def serve_token_page(
    token_path: Path,
    validate_token: Callable[[str], Optional[str]],
    save_token: Callable[[str], None],
    override_warning: Optional[str] = None,
    port: int = 0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
    allow_unreachable: bool = False,
    stream: TextIO = sys.stderr,
) -> bool:
    """Serve the local entry page until the token is saved or the deadline passes.

    Returns True when a token was saved. Prints the URL and the outcome to `stream`,
    never the token itself.
    """
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise UsageError(f"Timeout must be between 1 and {int(MAX_TIMEOUT_SECONDS)} seconds.")
    if port < 0 or port > 65535:
        raise UsageError("Port must be between 0 and 65535.")
    obstacle = None if allow_unreachable else unreachable_reason()
    if obstacle:
        raise UsageError(
            "The token page cannot work here: "
            f"{obstacle}. A link printed here would resolve to the user's own machine, where "
            "nothing is listening. Take the token another way, best first:\n"
            "  1. Ask the user to set KIT_TOKEN (or KIT_TOKEN_FILE) in this session's environment "
            "or secret settings. The value never enters the conversation. Prefer this.\n"
            "  2. Otherwise ask them to send the token here — as text or as a file — and save it "
            "with 'token save' reading stdin. This is a supported route for a hosted session, not "
            "a workaround. Say plainly that the value then lives in the conversation history, and "
            "tell them they can revoke it in the cabinet under «Настройки → API» when the work is "
            "done.\n"
            "  3. --force, only when the user already forwards this host's port to their browser."
        )

    session = _Session(
        secret=secrets.token_urlsafe(32),
        token_path=token_path,
        validate_token=validate_token,
        save_token=save_token,
        override_warning=override_warning,
    )
    try:
        server = http.server.HTTPServer((LOOPBACK_HOST, port), _build_handler(session))
    except OSError as error:
        raise UsageError(f"Cannot open the local token page: {error}") from None

    server.timeout = POLL_INTERVAL_SECONDS
    bound_port = server.server_address[1]
    url = "http://{host}:{port}/?secret={secret}".format(
        host=LOOPBACK_HOST,
        port=bound_port,
        secret=urllib.parse.quote(session.secret, safe=""),
    )
    print(f"Token page: {url}", file=stream)
    print(
        f"Open it, paste the token, and press save. The page is local, single-use, and expires "
        f"in {int(timeout_seconds)} seconds.",
        file=stream,
    )
    stream.flush()
    if open_browser and not _open_url(url):
        print("Could not open a browser automatically — use the URL above.", file=stream)
        stream.flush()

    deadline = time.monotonic() + timeout_seconds
    try:
        while not session.saved and not session.aborted_reason and time.monotonic() < deadline:
            try:
                server.handle_request()
            except (socket.timeout, TimeoutError):
                continue
    except KeyboardInterrupt:
        print("Cancelled; nothing saved.", file=stream)
        return False
    finally:
        server.server_close()

    if session.saved:
        print(f"Token saved to {token_path}", file=stream)
        return True
    if session.aborted_reason:
        print(f"Token page stopped: {session.aborted_reason}; nothing saved.", file=stream)
        return False
    print("Token page timed out; nothing saved.", file=stream)
    return False
