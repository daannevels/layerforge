"""`layerforge selftest`: a quick end-to-end check in plain English.

M1 stub: checks that the dependencies load, the presets are sane and the synthetic
samples can be made. Later milestones run the whole pipeline here.
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


CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("Dependencies", _check_imports),
    ("Presets", _check_presets),
    ("Synthetic sounds", _check_synth),
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
