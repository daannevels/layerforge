"""Instrument profiles: per-type rules for ownership, alignment, length and pitch."""

import numpy as np
import pytest

from layerforge.engine.align import best_envelope_lag
from layerforge.engine.analysis import decay_time, estimate_f0, find_onset
from layerforge.engine.planner import group_leader, noise_bands, plan_base, target_decay
from layerforge.engine.presets import PRESETS, group_keepmap
from layerforge.engine.select import generate
from layerforge.engine.types import KeepMap, Settings
from tests.fixtures import make_bass, make_clap, make_crash, make_kick, make_synth

SR = 48000


def test_every_profile_is_consistent():
    for p in PRESETS.values():
        assert len(p.band_groups) == len(p.band_names) == len(p.band_edges_hz) + 1
        assert set(p.band_groups) == {0, 1, 2}
        assert 0 <= p.length_group <= 2
    assert PRESETS["kick"].single_owner_bands == (True, True, False, False, False)
    assert PRESETS["bass"].single_owner_bands == (True, True, False, False, False)
    assert PRESETS["clap"].single_attack and not PRESETS["kick"].single_attack
    assert noise_bands("crash", True) == [False, True, True, True]
    assert noise_bands("crash", False) == [False] * 4
    assert noise_bands("bass", True) == [False] * 5


def test_low_end_single_owner_survives_band_edge_jitter():
    """Kick Body (upper edge 200 Hz) must never mix, even when jitter moves the edge above 200."""
    ins = [np.concatenate([np.zeros(100 * k), make_kick(140 + 30 * k, 50, 0.03, 0.2, 0.4, k, seed=k, dur=0.4)])[None]
           for k in range(3)]
    km = KeepMap.empty(3, 5)
    for i in range(3):
        for b in range(5):
            for z in range(3):
                km.cells[i][b][z].state = "keep"
    res = generate(ins, ["a", "b", "c"], km, Settings(), 6, 100, 9, workers=1)
    for v in res.variations:
        for b in (0, 1):
            assert all(len(owners) == 1 for owners in v.recipe.cells[b])


def test_tail_follows_sustain_and_attack_has_one_leader():
    ins = [np.concatenate([np.zeros(80 * k), make_clap(n_bursts=3 + k % 2, spacing_ms=7 + 2 * k, seed=k)])[None]
           for k in range(3)]
    res = generate(ins, ["a", "b", "c"], KeepMap.empty(3, 4), Settings(sound_type="clap"), 8, 100, 4, workers=1)
    for v in res.variations:
        attack = [owners[0].input for owners in (row[0] for row in v.recipe.cells) if owners]
        assert len(set(attack)) == 1                       # one transient leader for all bands
        for row in v.recipe.cells:
            if row[1] and row[2]:
                assert row[2][0].input == row[1][0].input  # tail from the same clap as the body


def test_decay_matching_follows_the_length_leader():
    short = make_crash(decay=0.25, seed=1, dur=1.0)
    long_ = make_crash(decay=1.2, seed=2, dur=4.0)
    d_short, d_long = decay_time(short, SR), decay_time(long_, SR)
    for leader, expect in ((0, d_short), (1, d_long)):
        km = group_keepmap("crash", 2, [None, None, leader])
        rec = plan_base([short[None], long_[None]], ["s", "l"], km, Settings(sound_type="crash"))
        from layerforge.engine.render import render
        y = render(rec, [short[None], long_[None]]).audio[0]
        assert decay_time(y, SR) == pytest.approx(expect, rel=0.15)
    # without a leader the length lies between the two
    km = group_keepmap("crash", 2, [None, None, None])
    rec = plan_base([short[None], long_[None]], ["s", "l"], km, Settings(sound_type="crash"))
    assert d_short <= rec.notes["target_decay_s"] <= d_long


def test_target_decay_jitter_stays_between_inputs():
    rng = np.random.default_rng(0)
    vals = [target_decay([0.5, 2.0, 1.0], None, rng, 1.0) for _ in range(200)]
    assert min(vals) >= 0.5 and max(vals) <= 2.0 and np.std(vals) > 0.1
    assert target_decay([0.5, 2.0], 0, rng, 1.0) == 0.5


def test_group_leader():
    km = group_keepmap("kick", 3, [2, None, 1])
    assert group_leader("kick", km, 0) == 2
    assert group_leader("kick", km, 1) is None
    assert group_leader("kick", km, 2) == 1


def _bursts(seed, delay=0):
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    env = sum(np.where(t >= t0, np.exp(-np.clip(t - t0, 0, None) / 0.003), 0.0) for t0 in (0.0, 0.008, 0.017))
    x = np.random.default_rng(seed).standard_normal(n) * env
    return np.concatenate([np.zeros(delay), x])[:n]


def test_envelope_alignment_finds_burst_delay():
    """Two clap-like burst patterns made of different noise; the second is 1 ms late."""
    k, gain = best_envelope_lag(_bursts(3), _bursts(4, 48), 144, 0, int(0.15 * SR), 48, 0.02)
    assert abs(k + 48) <= 12 and gain > 0.02   # within 0.25 ms


@pytest.mark.parametrize("freq", [41.2, 55.0, 98.0])
def test_bass_pitch(freq):
    x = make_bass(freq)
    assert estimate_f0(x, SR, find_onset(x, SR)) == pytest.approx(freq, rel=0.01)


@pytest.mark.parametrize("freq", [110.0, 220.0, 440.0, 880.0])
def test_synth_pitch(freq):
    x = make_synth(freq)
    assert estimate_f0(x, SR, find_onset(x, SR)) == pytest.approx(freq, rel=0.01)


def test_pitch_of_noise_is_zero():
    x = make_crash()
    assert estimate_f0(x, SR, find_onset(x, SR)) == 0.0


def test_parallel_and_sequential_give_identical_results():
    ins = [np.concatenate([np.zeros(90 * k), make_kick(150 + 25 * k, 45 + 5 * k, 0.02, 0.15, 0.3, k, seed=k, dur=0.3)])[None]
           for k in range(2)]
    km = group_keepmap("kick", 2, [0, None, None])
    a = generate(ins, ["a", "b"], km, Settings(), 3, 50, 77, workers=1)
    b = generate(ins, ["a", "b"], km, Settings(), 3, 50, 77, workers=2)
    assert [v.recipe.to_dict() for v in a.variations] == [v.recipe.to_dict() for v in b.variations]
    assert all(np.array_equal(x.audio, y.audio) for x, y in zip(a.variations, b.variations))
