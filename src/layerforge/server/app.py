"""Local HTTP server for the browser UI.

Security rules (see CLAUDE.md):
- binds to 127.0.0.1 only;
- a random token is created at start, put in the URL that opens the browser,
  and required on every /api/ call (header X-Layerforge-Token, or ?token= for links);
- every request whose Host header is not exactly 127.0.0.1:<port> is rejected;
- static files come from a fixed whitelist, never from paths built from client input;
  generated files are addressed by random server-made ids;
- uploads accept only wav/aiff/flac up to MAX_UPLOAD_BYTES.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from layerforge import __version__
from layerforge.audio_io import AudioError, read_audio
from layerforge.engine import constants as C
from layerforge.engine.analysis import estimate_f0, find_onset
from layerforge.engine.presets import GROUP_NAMES, PRESETS, ZONE_NAMES
from layerforge.server.jobs import Job, RequestError, Upload, new_job_id, parse_request, run_generate
from layerforge.server import feedback
from layerforge.server.workspace import cleanup_projects, new_project_dir, workspace_root

HOST = "127.0.0.1"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024          # bytes: largest accepted upload (50 MB)
MAX_JSON_BYTES = 1024 * 1024                 # bytes: largest accepted JSON request
ALLOWED_UPLOAD_EXTENSIONS = (".wav", ".aif", ".aiff", ".flac")
TOKEN_HEADER = "X-Layerforge-Token"
ID_RE = re.compile(r"^[0-9a-f]{32}$")

# URL path -> (file in layerforge/web, content type). The only static files the server sends.
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

    def __init__(self, token: str | None = None, root: Path | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.port = 0  # set once the socket is bound
        self._root = root
        self.lock = threading.Lock()
        self.uploads: dict[str, Upload] = {}
        self.jobs: dict[str, Job] = {}
        self.files: dict[str, Path] = {}   # random id -> file the server wrote itself

    @property
    def root(self) -> Path:
        if not getattr(self, "_root_ready", False):
            self._root = workspace_root(self._root)
            self._root_ready = True
        return self._root

    def token_ok(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate.encode(), self.token.encode())

    def host_ok(self, host_header: str | None) -> bool:
        return host_header == f"{HOST}:{self.port}"

    def register_file(self, path: Path) -> str:
        fid = new_job_id()
        with self.lock:
            self.files[fid] = path
        return fid


def presets_payload() -> dict:
    return {
        "version": __version__,
        "zones": list(ZONE_NAMES),
        "rates": list(C.OUTPUT_RATES),
        "min_inputs": C.MIN_INPUTS,
        "max_inputs": C.MAX_INPUTS,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "extensions": list(ALLOWED_UPLOAD_EXTENSIONS),
        "groups": list(GROUP_NAMES),
        "feedback_tags": list(feedback.TAGS),
        "variations": {"min": C.MIN_VARIATIONS, "max": C.MAX_VARIATIONS, "default": C.DEFAULT_VARIATIONS},
        "amount_default": C.DEFAULT_VARIATION_AMOUNT,
        "types": {k: {"pitched": p.pitched, "bands": list(p.band_names), "band_edges_hz": list(p.band_edges_hz),
                      "zone_edges_ms": list(p.zone_edges_ms),
                      "groups": [[p.band_names[b] for b in range(len(p.band_names)) if p.band_groups[b] == g]
                                 for g in range(len(GROUP_NAMES))]}
                  for k, p in PRESETS.items()},
    }


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Layerforge"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:  # keep the console quiet
            pass

        # --- helpers -----------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
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

        def _length(self, limit: int) -> int | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
                return None
            if length > limit:
                self.close_connection = True
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "file is larger than 50 MB" if limit == MAX_UPLOAD_BYTES else "request too large")
                return None
            return length

        # --- routes ------------------------------------------------------
        def do_GET(self) -> None:
            guarded = self._guard()
            if guarded is None:
                return
            path, query = guarded
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
            elif path == "/api/presets":
                self._json(HTTPStatus.OK, presets_payload())
            elif path.startswith("/api/jobs/"):
                job = app.jobs.get(path.rsplit("/", 1)[1])
                if job is None:
                    self._error(HTTPStatus.NOT_FOUND, "unknown job")
                else:
                    self._json(HTTPStatus.OK, job.snapshot())
            elif path.startswith("/api/file/"):
                self._send_file(path.rsplit("/", 1)[1], query)
            elif path == "/api/feedback/count":
                self._json(HTTPStatus.OK, {"count": feedback.count(app.root)})
            elif path == "/api/feedback/export":
                fb = app.root / feedback.FEEDBACK_FILE
                body = fb.read_bytes() if fb.is_file() else b""
                self._send(HTTPStatus.OK, body, "application/x-ndjson",
                           {"Content-Disposition": f'attachment; filename="layerforge_{feedback.FEEDBACK_FILE}"'})
            elif path.startswith("/api/recipe/"):
                wav = self._known_file(path.rsplit("/", 1)[1])
                rec = wav.with_suffix(".json") if wav else None
                if rec is None or not rec.is_file():
                    self._error(HTTPStatus.NOT_FOUND, "unknown recipe")
                else:
                    self._send(HTTPStatus.OK, rec.read_bytes(), "application/json")
            elif path in STATIC_FILES:
                name, ctype = STATIC_FILES[path]
                self._send(HTTPStatus.OK, _read_static(name), ctype)
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")

        do_HEAD = do_GET

        def _known_file(self, fid: str) -> Path | None:
            return app.files.get(fid) if ID_RE.match(fid) else None

        def _send_file(self, fid: str, query: dict[str, list[str]]) -> None:
            path = self._known_file(fid)
            if path is None or not path.is_file():
                self._error(HTTPStatus.NOT_FOUND, "unknown file")
                return
            extra = {}
            if query.get("download"):
                project = path.parent.parent.name if path.parent.name == "variations" else path.parent.name
                extra["Content-Disposition"] = f'attachment; filename="layerforge_{project}_{path.name}"'
            self._send(HTTPStatus.OK, path.read_bytes(), "audio/wav", extra)

        def do_POST(self) -> None:
            guarded = self._guard()
            if guarded is None:
                return
            path, _query = guarded
            if path == "/api/upload":
                self._upload()
            elif path == "/api/generate":
                self._generate()
            elif path == "/api/star":
                self._star()
            elif path == "/api/feedback":
                self._feedback()
            elif path == "/api/open-folder":
                ok = open_folder(app.root)
                self._json(HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"ok": ok, "path": str(app.root)})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")

        def _upload(self) -> None:
            length = self._length(MAX_UPLOAD_BYTES)
            if length is None:
                return
            name = unquote(self.headers.get("X-Filename") or "")[:200]
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                self.close_connection = True
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "only WAV, AIFF and FLAC files can be loaded")
                return
            data = self.rfile.read(length)
            try:
                x, sr = read_audio(data)
            except AudioError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            uid = new_job_id()
            with app.lock:
                app.uploads[uid] = Upload(name=Path(name).name, ext=ext, data=data, sample_rate=sr,
                                          channels=x.shape[0], frames=x.shape[1])
            self._json(HTTPStatus.OK, {"id": uid, "name": Path(name).name, "sample_rate": sr,
                                       "channels": int(x.shape[0]), "seconds": x.shape[1] / sr,
                                       "f0_hz": round(estimate_f0(x, sr, find_onset(x, sr)), 2)})

        def _generate(self) -> None:
            length = self._length(MAX_JSON_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                req = parse_request(body, app.uploads)
            except (json.JSONDecodeError, RequestError, AttributeError, TypeError, ValueError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc) or "bad request")
                return
            job = Job(id=new_job_id())
            with app.lock:
                app.jobs[job.id] = job
            threading.Thread(target=run_generate, daemon=True,
                             args=(job, req, app.root, new_project_dir, app.register_file)).start()
            self._json(HTTPStatus.OK, {"job": job.id, "seed": req.seed})

        def _feedback(self) -> None:
            length = self._length(MAX_JSON_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                wav = self._known_file(str(body["file"]))
                if wav is None or not wav.is_file() or wav.parent.name != "variations":
                    self._error(HTTPStatus.NOT_FOUND, "this variation is no longer on disk")
                    return
                rec = feedback.build_record(wav, body.get("rating", 0), body.get("tags", []), body.get("text", ""))
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError, feedback.FeedbackError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc) or "bad request")
                return
            n = feedback.append(app.root, rec)
            self._json(HTTPStatus.OK, {"ok": True, "count": n})

        def _star(self) -> None:
            length = self._length(MAX_JSON_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                fid, on = str(body["file"]), bool(body["on"])
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                self._error(HTTPStatus.BAD_REQUEST, "bad request")
                return
            wav = self._known_file(fid)
            if wav is None or not wav.is_file() or wav.parent.name != "variations":
                self._error(HTTPStatus.NOT_FOUND, "unknown file")
                return
            fav = app.root / "Favourites" / f"{wav.parent.parent.name}_{wav.name}"
            if on:
                fav.write_bytes(wav.read_bytes())
            elif fav.is_file():
                fav.unlink()
            self._json(HTTPStatus.OK, {"ok": True, "starred": on})

    return Handler


def open_folder(path: Path) -> bool:
    """Show the workspace folder in Finder / Explorer / the Linux file manager."""
    import os
    import subprocess
    import sys

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


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
    removed = cleanup_projects(app.root)
    httpd = make_server(app, port)
    url = f"http://{HOST}:{app.port}/?token={app.token}"
    print(f"Layerforge {__version__} is running. Your files: {app.root}")
    print(f"Open this address in your browser if it did not open by itself:\n  {url}")
    print("Keep this window open while you use Layerforge. Close it to quit.")
    if removed:
        print(f"Cleaned up {len(removed)} old project folder(s) (the newest are kept).")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
