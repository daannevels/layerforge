"""`layerforge selftest`: a quick end-to-end check in plain English.

Runs the whole pipeline on synthetic samples (no files of the user are touched).
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np


def _check_imports() -> str:
    import scipy  # noqa: F401
    import soundfile  # noqa: F401
    return "numpy, scipy and soundfile load"


def _check_presets() -> str:
    from layerforge.engine.presets import PRESETS
    for p in PRESETS.values():
        assert len(p.band_names) == len(p.band_edges_hz) + 1, p.name
        assert list(p.band_edges_hz) == sorted(p.band_edges_hz), p.name
        assert 0 < p.zone_edges_ms[0] < p.zone_edges_ms[1], p.name
    return f"{len(PRESETS)} sound-type presets are valid"


def _check_synth() -> str:
    from layerforge.synth import GENERATORS
    for name, gen in GENERATORS.items():
        x = gen()
        assert np.all(np.isfinite(x)), name
        assert abs(np.max(np.abs(x)) - 1.0) < 1e-12, name
    return f"{len(GENERATORS)} synthetic test sounds made"


def _check_bands() -> str:
    from layerforge.engine.bands import split_bands
    from layerforge.synth import make_kick
    x = np.concatenate([np.zeros(2400), make_kick()])
    err = np.max(np.abs(split_bands(x, (60, 200, 1200, 6000), 48000).sum(axis=0) - x))
    assert err < 1e-6, err
    return f"the bands add up to the original (error {20 * np.log10(max(err, 1e-300)):.0f} dB)"


def _check_render() -> str:
    from layerforge.audio_io import read_audio, wav_bytes
    from layerforge.engine.planner import plan_base
    from layerforge.engine.post import true_peak
    from layerforge.engine.presets import PRESETS
    from layerforge.engine.render import render, render_simple_stack
    from layerforge.engine.types import Cell, KeepMap, Settings
    from layerforge.synth import GENERATORS
    for stype, gen in GENERATORS.items():
        for sr in (44100, 48000):
            ins = [np.concatenate([np.zeros(120 * k), gen(seed=k + 1, sr=sr)])[None] for k in range(3)]
            km = KeepMap.empty(3, len(PRESETS[stype].band_names))
            km.cells[0][1] = [Cell("keep") for _ in range(3)]
            recipe = plan_base(ins, ["a", "b", "c"], km, Settings(sound_type=stype, sample_rate=sr))
            y = render(recipe, ins).audio
            assert np.all(np.isfinite(y)), stype
            tp = 20 * np.log10(true_peak(y))
            assert abs(tp + 0.3) < 0.05, (stype, tp)
            back, sr2 = read_audio(wav_bytes(y, sr))
            assert sr2 == sr and back.shape == y.shape, stype
            assert np.all(np.isfinite(render_simple_stack(recipe, ins)))
    return f"blended {len(GENERATORS)} sound types at 44.1 and 48 kHz into valid 24-bit WAVs"


CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("Dependencies", _check_imports),
    ("Presets", _check_presets),
    ("Synthetic sounds", _check_synth),
    ("Band split", _check_bands),
    ("Blend engine", _check_render),
]


def run(print_fn: Callable[[str], None] = print) -> bool:
    """Run all checks; print one line per check and a final PASS/FAIL. Returns True on PASS."""
    t0 = time.perf_counter()
    ok = True
    for label, fn in CHECKS:
        try:
            msg = fn()
            print_fn(f"  OK    {label}: {msg}")
        except Exception as exc:  # report every failure in plain words
            ok = False
            print_fn(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
    secs = time.perf_counter() - t0
    print_fn("")
    print_fn(f"PASS - everything works ({secs:.1f} s)" if ok else f"FAIL - see the lines above ({secs:.1f} s)")
    return ok
