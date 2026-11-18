"""Integer-lag and polarity alignment (steps 3 and 7) and the crossover coherence measure."""

from __future__ import annotations

import numpy as np


def shift(x: np.ndarray, k: int) -> np.ndarray:
    """Whole-sample shift along the last axis, zero fill, same length (k > 0 = later)."""
    y = np.zeros_like(x)
    n = x.shape[-1]
    if k >= n or -k >= n:
        return y
    if k > 0:
        y[..., k:] = x[..., :-k]
    elif k < 0:
        y[..., :k] = x[..., -k:]
    else:
        y[...] = x
    return y


def _energy(s: np.ndarray) -> float:
    return float(np.dot(s, s))


def candidates(max_lag: int) -> list[tuple[int, int]]:
    """(lag, polarity) candidates, smallest |lag| first, + before -; (0, +1) comes first."""
    return [(k, p) for k in sorted(range(-max_lag, max_lag + 1), key=lambda v: (abs(v), v < 0))
            for p in (1, -1)]


def best_align(a: np.ndarray, b: np.ndarray, max_lag: int, start: int, win: int,
               min_gain_db: float) -> tuple[int, int, float]:
    """Lag and polarity of `b` that maximise the energy of a + p * shift(b, k) over
    [start, start + win). Returns (lag, polarity, gain_db vs no alignment). If the best gain is
    below `min_gain_db`, returns (0, 1, 0.0). Strictly larger energy is needed to replace an
    earlier candidate, so ties keep the smallest lag."""
    start = max(start, 0)
    stop = min(start + win, a.shape[-1])
    aw = a[start:stop]
    e0 = None
    best = (-1.0, 0, 1)
    for k, p in candidates(max_lag):
        bw = window_of_shift(b, k, start, stop)
        e = _energy(aw + p * bw)
        if (k, p) == (0, 1):
            e0 = e
        if e > best[0]:
            best = (e, k, p)
    assert e0 is not None
    if e0 <= 0.0:
        return 0, 1, 0.0
    gain_db = 10.0 * np.log10(best[0] / e0) if best[0] > 0 else 0.0
    if gain_db < min_gain_db:
        return 0, 1, 0.0
    return best[1], best[2], float(gain_db)


def window_of_shift(b: np.ndarray, k: int, start: int, stop: int) -> np.ndarray:
    """shift(b, k)[start:stop] without shifting the whole array."""
    n = b.shape[-1]
    out = np.zeros(stop - start)
    lo, hi = start - k, stop - k
    clo, chi = max(lo, 0), min(hi, n)
    if chi > clo:
        out[clo - lo:chi - lo] = b[clo:chi]
    return out


def best_coherence_shift(a: np.ndarray, b: np.ndarray, max_lag: int, start: int, win: int,
                         r_now: float) -> tuple[float, int, int]:
    """Extra (lag, polarity) of `b` that maximises coherence_ratio(a, p * shift(b, k)) over the
    window. Returns (best R, lag, polarity); (r_now, 0, 1) if nothing beats r_now."""
    start = max(start, 0)
    stop = min(start + win, a.shape[-1])
    aw = a[start:stop]
    ea = _energy(aw)
    best = (r_now, 0, 1)
    for k, p in candidates(max_lag):
        bw = window_of_shift(b, k, start, stop)
        den = ea + _energy(bw)
        r = _energy(aw + p * bw) / den if den > 0 else 1.0
        if r > best[0]:
            best = (r, k, p)
    return best


def coherence_ratio(a: np.ndarray, b: np.ndarray, start: int, win: int) -> float:
    """R = E(a + b) / (E(a) + E(b)) over [start, start + win). 1 = unrelated, 2 = identical,
    0 = full cancellation. Returns 1.0 when both are silent."""
    start = max(start, 0)
    aw, bw = a[start:start + win], b[start:start + win]
    den = _energy(aw) + _energy(bw)
    if den <= 0.0:
        return 1.0
    return _energy(aw + bw) / den
