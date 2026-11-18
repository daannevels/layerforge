"""Local HTTP server for the browser UI.

Security rules (see CLAUDE.md):
- binds to 127.0.0.1 only;
- a random token is created at start, put in the URL that opens the browser,
  and required on every /api/ call (header X-Layerforge-Token, or ?token= for links);
- every request whose Host header is not exactly 127.0.0.1:<port> is rejected;
- static files come from a fixed whitelist, never from paths built from client input;
- uploads (milestone M2) accept only wav/aiff/flac up to MAX_UPLOAD_BYTES.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from layerforge import __version__

HOST = "127.0.0.1"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024          # bytes: largest accepted upload (50 MB)
ALLOWED_UPLOAD_EXTENSIONS = (".wav", ".aif", ".aiff", ".flac")
TOKEN_HEADER = "X-Layerforge-Token"

# URL path -> (file in layerforge/web, content type). The only files the server will ever send.
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _read_static(name: str) -> bytes:
    return resources.files("layerforge.web").joinpath(name).read_bytes()


class App:
    """State shared by all request handlers of one server."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.port = 0  # set once the socket is bound

    def token_ok(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate.encode(), self.token.encode())

    def host_ok(self, host_header: str | None) -> bool:
        return host_header == f"{HOST}:{self.port}"


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Layerforge"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:  # keep the console quiet
            pass

        # --- helpers -----------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: object) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _error(self, status: int, msg: str) -> None:
            self._json(status, {"error": msg})

        def _guard(self) -> tuple[str, dict[str, list[str]]] | None:
            """Host check for every request, token check for /api/. Returns (path, query) or None."""
            if not app.host_ok(self.headers.get("Host")):
                self._error(HTTPStatus.FORBIDDEN, "bad host")
                return None
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path.startswith("/api/"):
                token = self.headers.get(TOKEN_HEADER) or (query.get("token") or [None])[0]
                if not app.token_ok(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong token")
                    return None
            return parts.path, query

        # --- routes ------------------------------------------------------
        def do_GET(self) -> None:
            guarded = self._guard()
            if guarded is None:
                return
            path, _query = guarded
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
                return
            if path in STATIC_FILES:
                name, ctype = STATIC_FILES[path]
                self._send(HTTPStatus.OK, _read_static(name), ctype)
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")

        do_HEAD = do_GET

        def do_POST(self) -> None:
            guarded = self._guard()
            if guarded is None:
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "file is larger than 50 MB")
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")

    return Handler


def make_server(app: App, port: int = 8766, tries: int = 20) -> ThreadingHTTPServer:
    """Bind to 127.0.0.1 on `port` or the next free one (port 0 = any free port)."""
    last_err: OSError | None = None
    for p in ([0] if port == 0 else range(port, port + tries)):
        try:
            httpd = ThreadingHTTPServer((HOST, p), make_handler(app))
        except OSError as exc:
            last_err = exc
            continue
        app.port = httpd.server_address[1]
        return httpd
    raise OSError(f"no free port between {port} and {port + tries - 1}") from last_err


def serve(port: int = 8766, open_browser: bool = True) -> None:
    app = App()
    httpd = make_server(app, port)
    url = f"http://{HOST}:{app.port}/?token={app.token}"
    print(f"Layerforge {__version__} is running.")
    print(f"Open this address in your browser if it did not open by itself:\n  {url}")
    print("Keep this window open while you use Layerforge. Close it to quit.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
