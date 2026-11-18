"""Windows: onset guard (step 5), time-zone partition of unity (step 6) and fades (step 10)."""

from __future__ import annotations

import numpy as np

from layerforge.engine.constants import (
    CROSSFADE_MAX_MS,
    CROSSFADE_MIN_FREQ_HZ,
    CROSSFADE_MIN_MS,
    CROSSFADE_PERIODS,
)


def raised_cosine_ramp(n: int, start: float, width: float) -> np.ndarray:
    """Rising ramp over sample indices: 0 for i <= start, 1 for i >= start + width,
    0.5 - 0.5 cos(pi (i - start) / width) in between. `start` and `width` may be fractional."""
    i = np.arange(n, dtype=np.float64)
    if width <= 0:
        return (i >= start).astype(np.float64)
    u = np.clip((i - start) / width, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * u)


def guard_window(n: int, onset: int, guard_len: int) -> np.ndarray:
    """0 until onset - guard_len, then a raised-cosine fade-in that reaches 1 at the onset."""
    g = np.ones(n)
    start = onset - guard_len
    g[:max(start, 0)] = 0.0
    if guard_len > 0:
        idx = np.arange(max(start, 0), max(min(onset, n), 0))
        g[idx] = 0.5 - 0.5 * np.cos(np.pi * (idx - start) / guard_len)
    return g


def crossfade_width_s(f_low_hz: float) -> float:
    """Zone crossfade width in seconds for a band with lower edge f_low (0 for the lowest band):
    about half a period of its lowest frequency, clamped to [CROSSFADE_MIN_MS, CROSSFADE_MAX_MS]."""
    w = CROSSFADE_PERIODS / max(f_low_hz, CROSSFADE_MIN_FREQ_HZ)
    return float(np.clip(w, CROSSFADE_MIN_MS / 1000.0, CROSSFADE_MAX_MS / 1000.0))


def zone_crossfades(width: float, e1: float, e2: float) -> tuple[float, float]:
    """Shrink the two crossfade widths (samples) so they do not overlap each other and the
    first one does not start before the zone origin. e1 < e2 are the edges in samples."""
    w1 = min(width, 2.0 * e1, e2 - e1)
    w2 = min(width, e2 - e1)
    return max(w1, 0.0), max(w2, 0.0)


def zone_windows(n: int, origin: float, e1: float, e2: float, w1: float, w2: float) -> np.ndarray:
    """Attack, Sustain and Tail windows, shape (3, n), summing to 1 everywhere.

    Raised-cosine crossfades of width w1, w2 are centred on origin + e1 and origin + e2 (samples).
    """
    r1 = raised_cosine_ramp(n, origin + e1 - w1 / 2.0, w1)
    r2 = raised_cosine_ramp(n, origin + e2 - w2 / 2.0, w2)
    return np.stack([1.0 - r1, r1 - r2, r2])
