"""Engine tests (section 8): bands, guard, zone windows, alignment, anchors, stereo, post chain,
keep-map. All audio is synthetic."""

import numpy as np
import pytest

from layerforge.engine.align import best_align, shift
from layerforge.engine.analysis import anchor_shifts, find_anchor, find_onset, level_match_gains_db
from layerforge.engine.bands import lowpass, split_bands
from layerforge.engine.planner import plan_base
from layerforge.engine.post import mono_below, post_chain, true_peak
from layerforge.engine.presets import PRESETS
from layerforge.engine.render import render
from layerforge.engine.types import Cell, KeepMap, Settings
from layerforge.engine.windows import guard_window, zone_crossfades, zone_windows
from tests.fixtures import make_clap, make_kick


def db(v):
    return 20 * np.log10(max(float(v), 1e-300))


def padded(x, sr, ms=50.0):
    return np.concatenate([np.zeros(int(ms * sr / 1000)), x])


# --- bands ---------------------------------------------------------------------
@pytest.mark.parametrize("sr", [44100, 48000])
@pytest.mark.parametrize("order", [1, 2, 3])
@pytest.mark.parametrize("stype", sorted(PRESETS))
def test_bands_sum_to_input(stype, order, sr):
    x = padded(make_kick(sr=sr), sr)
    bands = split_bands(x, PRESETS[stype].band_edges_hz, sr, order)
    assert bands.shape == (len(PRESETS[stype].band_names), x.size)
    assert db(np.max(np.abs(bands.sum(axis=0) - x))) < -120  # expect about -250


def test_bands_work_on_stereo_arrays():
    sr = 48000
    x = np.stack([padded(make_kick(seed=1), sr), padded(make_kick(seed=2), sr)])
    bands = split_bands(x, (60, 200, 1200, 6000), sr)
    assert bands.shape == (5, 2, x.shape[1])
    assert np.allclose(bands[:, 1], split_bands(x[1], (60, 200, 1200, 6000), sr), atol=1e-14)


# --- onset guard --------------------------------------------------------------
def test_onset_guard_reduces_sub_preringing_and_keeps_sum():
    sr = 48000
    x = padded(make_kick(), sr)
    onset = find_onset(x, sr)
    bands = split_bands(x, (60, 200, 1200, 6000), sr)
    sub = bands[0]
    share_none = np.sum(sub[:onset] ** 2) / np.sum(sub ** 2)
    g = guard_window(x.size, onset, int(round(0.002 * sr)))
    share_2ms = np.sum((sub * g)[:onset] ** 2) / np.sum((sub * g) ** 2)
    assert share_none > 0.003          # about 0.4 % without a guard
    assert share_2ms < 0.003           # below 0.3 % with a 2 ms guard (about 0.12 %)
    assert db(np.max(np.abs((bands * g).sum(axis=0) - x))) < -120


def test_guard_window_shape():
    g = guard_window(100, 50, 10)
    assert np.all(g[:40] == 0) and np.all(g[50:] == 1)
    assert np.all(np.diff(g) >= 0)


# --- zone windows -------------------------------------------------------------
@pytest.mark.parametrize("width", [0.0, 48.0, 800.0, 5000.0])
def test_zone_windows_partition_of_unity(width):
    n, origin, e1, e2 = 30000, 1000.0, 576.0, 8640.0
    w1, w2 = zone_crossfades(width, e1, e2)
    w = zone_windows(n, origin, e1, e2, w1, w2)
    assert np.max(np.abs(w.sum(axis=0) - 1.0)) < 1e-12
    assert w.min() >= 0.0 and w.max() <= 1.0
    assert w1 <= 2 * e1 and w1 / 2 + w2 / 2 <= e2 - e1


# --- alignment ----------------------------------------------------------------
def _random_kick(rng, sr):
    return make_kick(rng.uniform(100, 250), rng.uniform(40, 70), rng.uniform(0.01, 0.06),
                     rng.uniform(0.1, 0.4), rng.uniform(0.1, 0.6), rng.uniform(0, 2 * np.pi),
                     int(rng.integers(1_000_000)), sr=sr)


def test_alignment_never_worse_and_helps_on_average():
    sr = 48000
    rng = np.random.default_rng(1234)
    max_lag, win = int(0.0015 * sr), int(0.150 * sr)
    gains = []
    for _ in range(200):
        a = lowpass(_random_kick(rng, sr), 200, sr)
        b = lowpass(_random_kick(rng, sr), 200, sr)
        k, p, _g = best_align(a, b, max_lag, 0, win, 0.5)
        e0 = np.sum((a + b)[:win] ** 2)
        e1 = np.sum((a + p * shift(b, k))[:win] ** 2)
        assert e1 >= e0
        gains.append(10 * np.log10(e1 / e0))
    assert np.mean(gains) >= 0.5  # about +1 dB on these kicks


def test_alignment_fixes_polarity_and_leaves_good_pairs_alone():
    sr = 48000
    max_lag, win = int(0.0015 * sr), int(0.150 * sr)
    a = lowpass(make_kick(seed=1), 200, sr)
    b = lowpass(make_kick(165, 52, 0.03, 0.22, 0.4, 0.1, seed=2), 200, sr)
    k, p, g = best_align(a, -b, max_lag, 0, win, 0.5)
    assert p == -1 and g > 6
    assert best_align(a, a.copy(), max_lag, 0, win, 0.5)[:2] == (0, 1)


# --- anchors ------------------------------------------------------------------
def test_delayed_copy_gets_identical_anchor():
    sr = 48000
    x = padded(make_kick(), sr, 10)
    y = padded(x, sr, 7)
    anchors = [find_anchor(v, find_onset(v, sr), sr, False, 12) for v in (x, y)]
    assert anchors[1] - anchors[0] == int(0.007 * sr)
    s = anchor_shifts(anchors)
    xs, ys = shift(np.pad(x, (0, 400)), s[0])[: y.size], shift(y, s[1])
    assert anchors[0] + s[0] == anchors[1] + s[1]
    assert np.allclose(xs, ys)


def test_clap_anchor_lies_after_first_peak():
    sr = 48000
    x = padded(make_clap(), sr, 10)
    onset = find_onset(x, sr)
    first_peak = onset + int(np.argmax(np.abs(x[onset:onset + int(0.004 * sr)])))
    anchor = find_anchor(x, onset, sr, True, 30)
    assert anchor > first_peak


def test_level_match_clamped():
    g = level_match_gains_db([1.0, 0.5, 0.001], [0.0, 0.0, 3.0])
    assert g[0] == pytest.approx(-6.0206, abs=1e-3) and g[1] == pytest.approx(0.0)
    assert g[2] == pytest.approx(15.0)  # +12 clamp plus a +3 dB trim


# --- stereo -------------------------------------------------------------------
@pytest.mark.parametrize("freq,limit", [(60, None), (480, None)])
def test_side_channel_mono_below_120(freq, limit):
    sr = 48000
    t = np.arange(sr) / sr
    s = np.sin(2 * np.pi * freq * t)
    y = mono_below(np.stack([s, -s]), sr)
    side = 0.5 * (y[0] - y[1])
    mid_part = slice(sr // 4, 3 * sr // 4)
    att = db(np.sqrt(np.mean(side[mid_part] ** 2)) / np.sqrt(np.mean(s[mid_part] ** 2)))
    if freq == 60:
        assert att < -20     # -24.6 dB expected
    else:
        assert att > -0.5    # about -0.0 dB expected


# --- post chain ---------------------------------------------------------------
@pytest.mark.parametrize("drive", [0.0, 1.5, 6.0])
@pytest.mark.parametrize("sr", [44100, 48000])
def test_post_chain(drive, sr):
    x = padded(make_kick(sr=sr), sr, 30)[None] * 0.7 + 0.01
    y, start, end, _ = post_chain(x, sr, drive)
    assert np.all(np.isfinite(y))
    assert db(true_peak(y)) == pytest.approx(-0.3, abs=0.05)
    assert abs(y[0, 0]) < 1e-3
    assert abs(float(y.mean())) < 1e-4
    assert y[0, -1] == 0.0
    # the fixture is cut while still sounding, so the fade-out does the work: the last 1 ms of a
    # 10 ms raised-cosine fade has a gain below 0.025
    last_ms = np.abs(y[0, -int(0.001 * sr):])
    assert last_ms.max() < 0.025 * np.abs(y).max()


def test_tail_is_trimmed():
    sr = 48000
    x = padded(make_kick(dur=0.6, sr=sr), sr, 30)
    x = np.concatenate([x, np.zeros(sr)])[None]  # 1 s of silence after the kick
    y, _, _, _ = post_chain(x, sr, 0.0)
    assert y.shape[1] < x.shape[1] - sr // 2


# --- keep-map -----------------------------------------------------------------
def _corr(a, b):
    return float(np.dot(a, b) / np.sqrt(np.dot(a, a) * np.dot(b, b)))


@pytest.mark.parametrize("sr", [44100, 48000])
def test_keep_map_body_from_1_top_from_2(sr):
    k1 = padded(make_kick(170, 50, 0.03, 0.25, 0.3, 0.0, seed=1, sr=sr), sr, 20)[None]
    k2 = padded(make_kick(110, 45, 0.015, 0.15, 0.8, 1.3, seed=9, sr=sr), sr, 25)[None]
    km = KeepMap.empty(2, 5)
    body, top = 1, 3
    for z in range(3):
        km.cells[0][body][z] = Cell("keep", 1.0)
        km.cells[1][top][z] = Cell("keep", 1.0)
    settings = Settings(sound_type="kick", sample_rate=sr)
    recipe = plan_base([k1, k2], ["k1", "k2"], km, settings)
    res = render(recipe, [k1, k2])
    edges = recipe.band_edges_hz
    out_bands = split_bands(res.assembled[0], edges, sr)
    from layerforge.engine.render import align_inputs
    aligned = align_inputs([k1, k2], recipe.inputs, recipe.length)[:, 0]
    in_bands = np.stack([split_bands(a, edges, sr) for a in aligned])
    c_body = [_corr(out_bands[body], in_bands[i, body]) for i in range(2)]
    c_top = [_corr(out_bands[top], in_bands[i, top]) for i in range(2)]
    assert c_body[0] > c_body[1] + 0.2
    assert c_top[1] > c_top[0] + 0.2


def test_avoid_everywhere_gives_silent_cell():
    sr = 48000
    k1 = padded(make_kick(seed=1), sr)[None]
    k2 = padded(make_kick(seed=2), sr)[None]
    km = KeepMap.empty(2, 5)
    for i in range(2):
        km.cells[i][4][2] = Cell("avoid")
    recipe = plan_base([k1, k2], ["a", "b"], km, Settings())
    assert recipe.cells[4][2] == []


def test_low_end_has_one_owner_high_bands_mix():
    sr = 48000
    k1 = padded(make_kick(seed=1), sr)[None]
    k2 = padded(make_kick(seed=2), sr)[None]
    km = KeepMap.empty(2, 5)
    for i in range(2):
        for b in range(5):
            km.cells[i][b][1] = Cell("keep", 1.0 if i == 0 else 0.5)
    recipe = plan_base([k1, k2], ["a", "b"], km, Settings())
    assert [o.input for o in recipe.cells[0][1]] == [0]          # Sub: one owner, strongest
    assert [o.input for o in recipe.cells[1][1]] == [0]          # Body (upper edge 200 Hz)
    w = {o.input: o.weight for o in recipe.cells[2][1]}           # Mids: mixed 2:1
    assert w[0] == pytest.approx(2 / 3) and w[1] == pytest.approx(1 / 3)


# --- crossover check ------------------------------------------------------------
def test_crossover_check_fixes_inverted_neighbour():
    sr = 48000
    k1 = padded(make_kick(seed=1), sr)[None]
    k2 = -padded(make_kick(seed=1), sr)[None] * 0.9   # the same kick, inverted
    km = KeepMap.empty(2, 5)
    for z in range(3):
        for b in (0, 1):
            km.cells[0][b][z] = Cell("keep")
        for b in (2, 3, 4):
            km.cells[1][b][z] = Cell("keep")
    recipe = plan_base([k1, k2], ["a", "b"], km, Settings())
    info = recipe.notes["crossover"]["1|2"]
    assert info["R_before"] < 0.5 and info["R_after"] > 1.5
    assert recipe.polarities[2][1] == -1
    off = plan_base([k1, k2], ["a", "b"], km, Settings(crossover_check=False))
    assert off.polarities[2][1] == 1


# --- end to end ---------------------------------------------------------------
@pytest.mark.parametrize("stype", sorted(PRESETS))
@pytest.mark.parametrize("stereo", [False, True])
def test_render_every_type(stype, stereo):
    from tests.fixtures import GENERATORS
    sr = 44100
    ins = []
    for k in range(3):
        x = np.concatenate([np.zeros(150 * k), GENERATORS[stype](seed=k + 1, sr=sr)])
        ins.append(np.stack([x, 0.8 * x]) if stereo else x[None])
    km = KeepMap.empty(3, len(PRESETS[stype].band_names))
    recipe = plan_base(ins, ["a", "b", "c"], km, Settings(sound_type=stype, sample_rate=sr))
    res = render(recipe, ins)
    assert res.audio.shape[0] == (2 if stereo else 1)
    assert np.all(np.isfinite(res.audio))
    assert db(true_peak(res.audio)) == pytest.approx(-0.3, abs=0.05)
    again = render(type(recipe).from_dict(recipe.to_dict()), ins)
    assert np.array_equal(res.audio, again.audio)   # recipe round trip renders identically
