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


def _generate(server, n=2):
    ids = []
    for seed in (1, 2):
        x = np.concatenate([np.zeros(500), make_kick(seed=seed, dur=0.3)])[None] * 0.8
        ids.append(json.loads(upload(server, wav_bytes(x, 48000), f"k{seed}.wav")[1])["id"])
    req = json.dumps({"sound_type": "kick", "variations": n, "amount": 50, "seed": 1,
                      "inputs": [{"id": i} for i in ids], "groups": [0, None, None]}).encode()
    job = json.loads(request(server, "POST", "/api/generate", dict(TOK, **{"Content-Length": str(len(req))}), req)[1])["job"]
    for _ in range(400):
        snap = json.loads(request(server, "GET", f"/api/jobs/{job}", TOK)[1])
        if snap["status"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def post(server, path, obj):
    body = json.dumps(obj).encode()
    return request(server, "POST", path, dict(TOK, **{"Content-Length": str(len(body))}), body)


def test_feedback_is_small_and_has_no_audio(server, tmp_path):
    snap = _generate(server)
    fid = snap["result"]["variations"][1]["file"]
    status, body = post(server, "/api/feedback", {"file": fid, "rating": -1, "tags": ["two_layers", "tail_wrong"],
                                                 "text": "staart van de andere kick hoorbaar"})
    assert status == 200, body
    assert json.loads(body)["count"] == 1
    line = (tmp_path / "feedback.jsonl").read_text().strip()
    rec = json.loads(line)
    assert rec["rating"] == -1 and rec["tags"] == ["tail_wrong", "two_layers"]
    assert rec["recipe"]["inputs"][0]["file"] == "inputs/input1.wav"
    assert len(rec["inputs"]) == 2 and "decay_s" in rec["inputs"][0] and "features" in rec["output"]
    assert len(line.encode()) < 20_000          # a few kB, no audio
    assert post(server, "/api/feedback", {"file": fid, "rating": 5})[0] == 400
    assert post(server, "/api/feedback", {"file": fid, "rating": 1, "tags": ["nonsense"]})[0] == 400
    status, data = request(server, "GET", "/api/feedback/export?token=test-token")
    assert status == 200 and data.decode().strip() == line
    assert json.loads(request(server, "GET", "/api/feedback/count", TOK)[1])["count"] == 1


def test_cleanup_keeps_newest_projects_only(tmp_path):
    from layerforge.server.workspace import cleanup_projects, workspace_root
    root = workspace_root(tmp_path)
    for k in range(25):
        p = root / "Projects" / f"202611{k + 1:02d}-120000_kick"
        (p / "inputs").mkdir(parents=True)
        (p / "project.json").write_text("{}")
    (root / "Projects" / "my own folder").mkdir()
    (root / "Projects" / "20260101-000000_kick-note").mkdir()   # no project.json/inputs: not ours
    (root / "Favourites" / "keep.wav").write_bytes(b"x")
    removed = cleanup_projects(root, keep=20)
    assert len(removed) == 5 and removed[0] == "20261101-120000_kick"
    left = sorted(p.name for p in (root / "Projects").iterdir())
    assert "my own folder" in left and "20260101-000000_kick-note" in left
    assert len([n for n in left if n.endswith("_kick")]) == 20
    assert (root / "Favourites" / "keep.wav").is_file()


def test_preview_bands_add_up_and_validate(server):
    import io
    import soundfile as sf
    x = np.concatenate([np.zeros(500), make_kick(seed=3, dur=0.3)])[None] * 0.8
    uid = json.loads(upload(server, wav_bytes(x, 48000), "k.wav")[1])["id"]
    parts = []
    for g in range(3):
        status, wav = post(server, "/api/preview", {"source": uid, "sound_type": "kick",
                                                   "select": {"kind": "group", "index": g}})
        assert status == 200
        parts.append(sf.read(io.BytesIO(wav))[0])
    status, wav = post(server, "/api/preview", {"source": uid, "sound_type": "kick", "select": {"kind": "all"}})
    full = sf.read(io.BytesIO(wav))[0]
    assert np.max(np.abs(sum(parts) - full)) < 1e-5            # Low + Mid + High = the sample
    status, wav = post(server, "/api/preview", {"source": uid, "sound_type": "kick", "band_edges_hz": [50, 150, 900, 5000],
                                               "select": {"kind": "band", "index": 0}})
    assert status == 200
    assert post(server, "/api/preview", {"source": uid, "sound_type": "kick", "band_edges_hz": [500, 150, 900, 5000],
                                        "select": {"kind": "band", "index": 0}})[0] == 400
    assert post(server, "/api/preview", {"source": "nope", "sound_type": "kick"})[0] == 404
    assert post(server, "/api/preview", {"source": uid, "sound_type": "kick", "select": {"kind": "group", "index": 9}})[0] == 400


def test_advanced_settings_reach_the_recipe(server, tmp_path):
    ids = []
    for seed in (1, 2):
        x = np.concatenate([np.zeros(500), make_kick(seed=seed, dur=0.3)])[None] * 0.8
        ids.append(json.loads(upload(server, wav_bytes(x, 48000), f"k{seed}.wav")[1])["id"])
    adv = {"band_edges_hz": [50, 180, 1000, 7000], "zone_edges_ms": [10, 150], "filter_order": 3,
           "guard_ms": 2.0, "drive_db": 0.0, "relaxed_alignment": False, "crossover_check": False}
    body = {"sound_type": "kick", "variations": 1, "amount": 0, "seed": 1, "inputs": [{"id": i, "trim_db": -3} for i in ids],
            "groups": [None, None, None], "advanced": adv}
    status, data = post(server, "/api/generate", body)
    assert status == 200, data
    job = json.loads(data)["job"]
    for _ in range(400):
        snap = json.loads(request(server, "GET", f"/api/jobs/{job}", TOK)[1])
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "done", snap
    rec = json.loads(request(server, "GET", f"/api/recipe/{snap['result']['variations'][0]['file']}?token=test-token")[1])
    assert rec["band_edges_hz"] == [50, 180, 1000, 7000] and rec["zone_edges_ms"] == [10, 150]
    assert rec["filter_order"] == 3 and rec["guard_ms"] == 2.0 and rec["drive_db"] == 0.0
    for bad in ({"band_edges_hz": [50, 55, 1000, 7000]}, {"zone_edges_ms": [100, 50]}, {"filter_order": 5},
                {"guard_ms": 9}, {"drive_db": 7}, {"crossover_check": "yes"}):
        assert post(server, "/api/generate", dict(body, advanced=bad))[0] == 400
