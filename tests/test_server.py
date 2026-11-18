"""Server security rules: token, Host header, upload limit, no paths from client input."""

import http.client
import json
import threading

import time
from urllib.parse import quote

import numpy as np
import pytest

from layerforge.audio_io import wav_bytes
from layerforge.server.app import MAX_UPLOAD_BYTES, App, make_server
from tests.fixtures import make_kick

TOK = {"X-Layerforge-Token": "test-token"}


@pytest.fixture()
def server(tmp_path):
    app = App(token="test-token", root=tmp_path)
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
    assert b"Layerforge" in body and b"app.js" in body


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


@pytest.mark.parametrize("path", ["/api/file/../../pyproject.toml?token=test-token",
                                  "/api/file/..%2f..%2fpyproject.toml?token=test-token",
                                  "/api/file/00000000000000000000000000000000?token=test-token",
                                  "/../pyproject.toml", "/..%2f..%2fetc/passwd", "/web/app.js",
                                  "/app.js/../../cli.py", "/__init__.py", "//etc/passwd"])
def test_path_traversal_rejected(server, path):
    status, body = request(server, "GET", path)
    assert status == 404
    assert b"import" not in body


def upload(app, data, name):
    return request(app, "POST", "/api/upload",
                   dict(TOK, **{"X-Filename": quote(name), "Content-Length": str(len(data))}), data)


def test_upload_rejects_wrong_type_and_bad_audio(server):
    assert upload(server, b"ID3fake", "song.mp3")[0] == 415
    assert upload(server, b"not a wav", "fake.wav")[0] == 400
    status, _ = request(server, "POST", "/api/upload", {"X-Filename": "a.wav", "Content-Length": "4"}, b"RIFF")
    assert status == 401


def test_upload_generate_and_download(server, tmp_path):
    ids = []
    for seed in (1, 2):
        x = np.concatenate([np.zeros(500), make_kick(seed=seed)])[None] * 0.8
        status, body = upload(server, wav_bytes(x, 48000), f"kick {seed}.wav")
        assert status == 200, body
        ids.append(json.loads(body)["id"])
    req = json.dumps({"sound_type": "kick", "sample_rate": "auto", "variations": 3, "amount": 50,
                      "seed": 42, "inputs": [{"id": i, "trim_db": 0} for i in ids],
                      "groups": [0, None, 1]}).encode()
    status, body = request(server, "POST", "/api/generate",
                           dict(TOK, **{"Content-Type": "application/json", "Content-Length": str(len(req))}), req)
    assert status == 200, body
    job = json.loads(body)["job"]
    for _ in range(200):
        snap = json.loads(request(server, "GET", f"/api/jobs/{job}", TOK)[1])
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "done", snap
    fid = snap["result"]["variations"][0]["file"]
    status, wav = request(server, "GET", f"/api/file/{fid}?token=test-token&download=1")
    assert status == 200 and wav[:4] == b"RIFF"
    proj = tmp_path / "Projects" / snap["result"]["project"]
    assert (proj / "variations" / "v01.wav").is_file() and (proj / "variations" / "v01.json").is_file()
    assert (proj / "inputs" / "input1.wav").is_file() and (proj / "project.json").is_file()
    assert (proj / "simple_stack.wav").is_file()
    assert len(snap["result"]["variations"]) == 3 and snap["result"]["seed"] == 42
    assert (proj / "variations" / "v03.wav").is_file()
    status, rec = request(server, "GET", f"/api/recipe/{fid}?token=test-token")
    assert status == 200 and json.loads(rec)["inputs"][0]["file"] == "inputs/input1.wav"
    star = json.dumps({"file": fid, "on": True}).encode()
    assert request(server, "POST", "/api/star", dict(TOK, **{"Content-Length": str(len(star))}), star)[0] == 200
    favs = list((tmp_path / "Favourites").iterdir())
    assert len(favs) == 1 and favs[0].name.endswith("_v01.wav")
    unstar = json.dumps({"file": fid, "on": False}).encode()
    request(server, "POST", "/api/star", dict(TOK, **{"Content-Length": str(len(unstar))}), unstar)
    assert not list((tmp_path / "Favourites").iterdir())
    # `layerforge render` rebuilds v01 bit for bit
    from layerforge.cli import main
    assert main(["render", str(proj / "variations" / "v02.json")]) == 0
    from layerforge.audio_io import read_audio
    a, _ = read_audio(proj / "variations" / "v02.wav")
    b, _ = read_audio(proj / "variations" / "v02_render.wav")
    assert np.array_equal(a, b)
    assert (proj / "variations" / "v02.wav").read_bytes() == (proj / "variations" / "v02_render.wav").read_bytes()


def test_generate_rejects_bad_requests(server):
    for body in [b"{not json", json.dumps({"sound_type": "snare"}).encode(),
                 json.dumps({"sound_type": "kick", "inputs": [{"id": "x"}, {"id": "y"}]}).encode(),
                 b"[1, 2]"]:
        status, _ = request(server, "POST", "/api/generate", dict(TOK, **{"Content-Length": str(len(body))}), body)
        assert status == 400
