"""Server security rules: token, Host header, upload limit, no paths from client input."""

import http.client
import json
import threading

import pytest

from layerforge.server.app import MAX_UPLOAD_BYTES, App, make_server


@pytest.fixture()
def server():
    app = App(token="test-token")
    httpd = make_server(app, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app
    httpd.shutdown()
    httpd.server_close()


def request(app, method, path, headers=None, body=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{app.port}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.endheaders(body)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def test_page_served_and_says_it_works(server):
    status, body = request(server, "GET", "/")
    assert status == 200
    assert b"Layerforge works" in body


def test_api_needs_token(server):
    assert request(server, "GET", "/api/health")[0] == 401
    assert request(server, "GET", "/api/health", {"X-Layerforge-Token": "wrong"})[0] == 401
    status, body = request(server, "GET", "/api/health", {"X-Layerforge-Token": "test-token"})
    assert status == 200 and json.loads(body)["ok"] is True
    assert request(server, "GET", "/api/health?token=test-token")[0] == 200


@pytest.mark.parametrize("host", ["localhost:{p}", "evil.example:{p}", "127.0.0.1", "127.0.0.1:1", ""])
def test_bad_host_rejected(server, host):
    status, _ = request(server, "GET", "/api/health?token=test-token", host=host.format(p=server.port))
    assert status == 403
    status, _ = request(server, "GET", "/", host=host.format(p=server.port))
    assert status == 403


def test_upload_size_limit(server):
    status, _ = request(server, "POST", "/api/upload",
                        {"X-Layerforge-Token": "test-token", "Content-Length": str(MAX_UPLOAD_BYTES + 1)})
    assert status == 413


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/..%2f..%2fetc/passwd", "/web/app.js",
                                  "/app.js/../../cli.py", "/__init__.py", "//etc/passwd"])
def test_path_traversal_rejected(server, path):
    status, body = request(server, "GET", path)
    assert status == 404
    assert b"import" not in body
