"""Tonal blending for pitched types (bass, synth): shared volume curve and spectral morph."""

import json

import numpy as np
import pytest

from layerforge.engine import morph as M
from layerforge.engine.analysis import pitch_spread_cents, rms_envelope
from layerforge.engine.presets import group_keepmap
from layerforge.engine.render import render
from layerforge.engine.select import generate
from layerforge.engine.types import Recipe, Settings
from tests.fixtures import make_bass, make_synth

SR = 48000


def length_at(y, db=-40.0):
    env = rms_envelope(y, SR, 20)
    return np.nonzero(env >= env.max() * 10 ** (db / 20))[0][-1] / SR


@pytest.mark.parametrize("n", [1000, 10007, 48000])
def test_stft_round_trip_is_exact(n):
    x = np.random.default_rng(n).standard_normal(n)
    n_fft = M.fft_size_for(SR)
    y = M.istft(M.stft(x, n_fft, n_fft // 4), n_fft, n_fft // 4, n)
    assert np.max(np.abs(y - x)) < 1e-12


def test_morph_of_identical_inputs_is_the_input():
    x = make_synth(220.0, dur=0.5)
    w = np.full((4, 2), 0.5)
    alive = np.ones((2, 10))
    y = M.morph(np.stack([x, x]), w, [200.0, 1000.0, 5000.0], SR, alive, 0, 4800, 0.5, 0, 400.0)
    assert np.max(np.abs(y - x)) < 1e-9


def test_target_curve_releases_to_silence():
    envs = np.stack([np.zeros(100), np.r_[np.zeros(40), np.full(60, -80.0)]])
    t = M.target_curve(envs, np.array([0.5, 0.5]), 70)
    assert t[30] > -1.0          # both sounding: the average of 0 and 0
    assert t[55] > -6.0          # the second one has finished: it does not drag the curve down
    assert t[75] <= -100.0       # after the release: silence


def _synths():
    a = np.concatenate([np.zeros(200), make_synth(220.0, decay=0.15, dur=0.8, seed=1)])[None]
    b = make_synth(110.0, decay=1.5, dur=2.5, seed=2)[None]
    return [a, b]


def test_free_length_lies_between_the_inputs_and_ends_cleanly():
    ins = _synths()
    la, lb = length_at(ins[0][0]), length_at(ins[1][0])
    res = generate(ins, ["short", "long"], group_keepmap("synth", 2, [None, None, None]),
                   Settings(sound_type="synth"), 6, 60, 5, workers=1)
    for v in res.variations:
        assert v.recipe.morph
        y = v.audio[0]
        assert la * 0.9 <= length_at(y) <= lb * 1.05
        assert y.shape[0] / SR <= length_at(y) + 0.3     # no long quiet tail after the release


def test_length_leader_sets_the_length():
    ins = _synths()
    for leader, src in ((0, ins[0][0]), (1, ins[1][0])):
        res = generate(ins, ["short", "long"], group_keepmap("synth", 2, [None, leader, None]),
                       Settings(sound_type="synth"), 3, 50, 2, workers=1)
        for v in res.variations:
            assert length_at(v.audio[0]) == pytest.approx(length_at(src), rel=0.12, abs=0.03)


def test_bass_low_end_is_one_source_and_recipe_round_trips():
    ins = [make_bass(55.0, decay=0.4, seed=1)[None], make_bass(55.0, decay=0.9, drive=4.0, seed=2)[None]]
    res = generate(ins, ["a", "b"], group_keepmap("bass", 2, [1, None, None]),
                   Settings(sound_type="bass"), 4, 50, 8, workers=1)
    for v in res.variations:
        r = v.recipe
        assert r.morph_bands == [False, False, True, True, True]
        assert all(o.input == 1 for b in (0, 1) for owners in r.cells[b] for o in owners)
        assert r.phase_leader == 1
        again = Recipe.from_dict(json.loads(json.dumps(r.to_dict())))
        assert np.array_equal(render(again, ins).audio, v.audio)


def test_old_recipes_still_load():
    ins = [make_bass(55.0, seed=1)[None], make_bass(55.0, seed=2)[None]]
    res = generate(ins, ["a", "b"], group_keepmap("kick", 2, [None, None, None]), Settings(), 1, 0, 1, workers=1)
    d = res.variations[0].recipe.to_dict()
    d["schema_version"] = 1
    for k in ("morph", "morph_bands", "morph_weights", "morph_power", "phase_leader", "leader_phase_below_hz",
              "env_start", "env_frame", "env_gain_db", "env_alive"):
        d.pop(k)
    assert np.array_equal(render(Recipe.from_dict(d), ins).audio, res.variations[0].audio)


def test_pitch_warning_ignores_octaves():
    assert pitch_spread_cents([65.4, 130.8, 261.6, 523.3]) < 5     # C2 C3 C4 C5
    assert pitch_spread_cents([65.4, 73.4]) == pytest.approx(200, abs=5)   # C vs D
    assert pitch_spread_cents([65.4, 43.65]) == pytest.approx(500, abs=5)  # C vs F: 5 semitones
