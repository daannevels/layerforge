"""M1 tests: presets, synthetic fixtures, CLI and self-test."""

import numpy as np
import pytest

from layerforge import __version__
from layerforge.cli import main
from layerforge.engine.presets import PRESETS, ZONE_NAMES, get_preset
from tests.fixtures import GENERATORS, make_clap, make_kick


def test_presets_match_spec_table():
    assert get_preset("kick").band_edges_hz == (60.0, 200.0, 1200.0, 6000.0)
    assert get_preset("kick").band_names == ("Sub", "Body", "Mids", "Top", "Air")
    assert get_preset("crash").zone_edges_ms == (20.0, 500.0)
    assert ZONE_NAMES == ("Attack", "Sustain", "Tail")
    for p in PRESETS.values():
        assert len(p.band_names) == len(p.band_edges_hz) + 1
        assert list(p.band_edges_hz) == sorted(p.band_edges_hz)
    with pytest.raises(KeyError):
        get_preset("snare")


@pytest.mark.parametrize("name", sorted(GENERATORS))
@pytest.mark.parametrize("sr", [44100, 48000])
def test_fixtures_deterministic_and_normalised(name, sr):
    a = GENERATORS[name](sr=sr)
    b = GENERATORS[name](sr=sr)
    assert np.array_equal(a, b)
    assert a.dtype == np.float64 and a.ndim == 1
    assert np.all(np.isfinite(a))
    assert np.max(np.abs(a)) == pytest.approx(1.0, abs=1e-12)


def test_kick_seed_changes_click():
    assert not np.array_equal(make_kick(seed=1), make_kick(seed=2))


def test_clap_has_several_bursts():
    x = make_clap()
    sr = 48000
    env = np.abs(x[: int(0.04 * sr)])
    first = np.argmax(env > 0.1)
    # the loudest point of the attack lies clearly after the first burst
    assert np.argmax(env) > first + int(0.005 * sr)


def test_selftest_passes(capsys):
    assert main(["selftest"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "FAIL" not in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out
