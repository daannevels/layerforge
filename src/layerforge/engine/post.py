"""Steps 9-10: stereo low-end mono, post chain, explicit 4x oversampling FIR, true peak."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy import signal

from layerforge.engine import constants as C
from layerforge.engine.analysis import find_onset, peak_abs, to_mono
from layerforge.engine.bands import highpass


def mono_below(x: np.ndarray, sr: float, fc: float = C.MONO_BELOW_HZ) -> np.ndarray:
    """Step 9: high-pass the Side channel (zero-phase, MONO_FILTER_ORDER) and rebuild L/R.
    Mono input is returned unchanged."""
    if x.shape[0] != 2:
        return x
    mid = 0.5 * (x[0] + x[1])
    side = highpass(0.5 * (x[0] - x[1]), fc, sr, C.MONO_FILTER_ORDER)
    return np.stack([mid + side, mid - side])


@lru_cache(maxsize=8)
def fir_kernel(factor: int = C.OVERSAMPLE, half_taps: int = C.FIR_HALF_TAPS,
               beta: float = C.FIR_KAISER_BETA, cutoff: float = C.FIR_CUTOFF) -> np.ndarray:
    """Kaiser-windowed sinc low-pass at the oversampled rate, length 2*half_taps*factor + 1.

    h[n] = 2 fc sinc(2 fc n) * kaiser(beta)[n], n = -D..D, D = half_taps * factor,
    fc = cutoff / (2 factor) cycles per oversampled sample; then h /= sum(h) (DC gain 1).
    """
    d = half_taps * factor
    n = np.arange(-d, d + 1, dtype=np.float64)
    fc = cutoff / (2.0 * factor)
    h = 2.0 * fc * np.sinc(2.0 * fc * n) * np.kaiser(2 * d + 1, beta)
    h = h / h.sum()
    h.setflags(write=False)
    return h


def upsample(x: np.ndarray, factor: int = C.OVERSAMPLE) -> np.ndarray:
    """Zero-stuff by `factor`, filter with factor * h, centred: output length factor * n."""
    h = fir_kernel(factor)
    d = (h.size - 1) // 2
    n = x.shape[-1]
    y = signal.upfirdn(factor * h, x, up=factor, axis=-1)
    return y[..., d:d + factor * n]


def downsample(u: np.ndarray, factor: int = C.OVERSAMPLE) -> np.ndarray:
    """Filter with h and keep every `factor`-th sample, centred: output length n / factor."""
    h = fir_kernel(factor)
    half = (h.size - 1) // (2 * factor)
    n = u.shape[-1] // factor
    y = signal.upfirdn(h, u, down=factor, axis=-1)
    return y[..., half:half + n]


def true_peak(x: np.ndarray) -> float:
    """Max |x| of the 4x oversampled signal (and of the samples themselves)."""
    if x.shape[-1] == 0:
        return 0.0
    return float(max(np.max(np.abs(upsample(x))), np.max(np.abs(x))))


def saturate(x: np.ndarray, drive_db: float) -> np.ndarray:
    """tanh(g * x / peak) at 4x oversampling, g = 10^(drive/20). drive <= 0 = bypass.
    The output level is arbitrary (the post chain normalises afterwards)."""
    if drive_db <= 0:
        return x
    pk = float(np.max(np.abs(x)))
    if pk <= 0:
        return x
    g = 10.0 ** (min(drive_db, C.DRIVE_MAX_DB) / 20.0)
    return downsample(np.tanh(g * upsample(x / pk))) * pk


def _start_index(x: np.ndarray, sr: float) -> tuple[int, int]:
    """(start, onset): the zero crossing of the mono sum nearest to onset - START_PREROLL_MS."""
    onset = find_onset(x, sr)
    m = to_mono(x)
    target = onset - int(round(C.START_PREROLL_MS * sr / 1000.0))
    r = int(round(C.START_SEARCH_MS * sr / 1000.0))
    lo, hi = max(target - r, 0), min(target + r, m.size - 1)
    best = max(target, 0)
    best_d = None
    for i in range(lo, hi):
        if m[i] == 0.0 or (m[i] > 0) != (m[i + 1] > 0):
            j = i if abs(m[i]) <= abs(m[i + 1]) else i + 1
            if best_d is None or abs(j - target) < best_d:
                best, best_d = j, abs(j - target)
    return max(min(best, onset), 0), onset


def _tail_end(x: np.ndarray, sr: float, onset: int) -> int:
    """First index t >= onset after which the envelope stays below TAIL_THRESHOLD_DB (re peak)
    for TAIL_HOLD_MS; returns len(x) when there is no such run."""
    env = peak_abs(x)
    thr = env.max() * 10 ** (C.TAIL_THRESHOLD_DB / 20.0)
    hold = int(round(C.TAIL_HOLD_MS * sr / 1000.0))
    q = (env[onset:] < thr).astype(np.int64)
    if hold <= 0 or q.size < hold:
        return env.size
    c = np.concatenate([[0], np.cumsum(q)])
    starts = np.nonzero(c[hold:] - c[:-hold] == hold)[0]  # quiet runs of length >= hold start here
    if starts.size == 0:
        return env.size
    return onset + int(starts[0])


def post_chain(x: np.ndarray, sr: float, drive_db: float) -> tuple[np.ndarray, int, int]:
    """Step 10. x: (channels, n). Returns (y, start, end) where y = processed x[:, start:end].

    Order: subtract mean; saturation (4x oversampled); 10 Hz zero-phase high-pass; start at the
    zero crossing nearest to onset - 1 ms with a raised-cosine fade-in (<= 1 ms) ending at the
    onset; trim the silent tail; subtract the trimmed segment's mean; fade in and out; scale to TRUE_PEAK_DBFS true peak.
    """
    y = x - x.mean(axis=-1, keepdims=True)
    y = saturate(y, drive_db)
    y = highpass(y, C.POST_HP_HZ, sr, C.POST_HP_ORDER)
    start, onset = _start_index(y, sr)
    end = _tail_end(y, sr, onset)
    fo_max = int(round(C.FADE_OUT_MS * sr / 1000.0))
    fo_min = int(round(C.FADE_OUT_MIN_MS * sr / 1000.0))
    end = min(max(end + fo_max, onset + fo_min), y.shape[-1])
    y = y[..., start:end].copy()
    y -= y.mean(axis=-1, keepdims=True)  # the trimmed segment's own mean (a decaying kick has some)
    n = y.shape[-1]
    fi = min(int(round(C.FADE_IN_MAX_MS * sr / 1000.0)), max(onset - start, 1), n)
    i = np.arange(fi)
    y[..., :fi] *= 0.5 - 0.5 * np.cos(np.pi * i / fi)
    fo = min(fo_max, n)
    i = np.arange(1, fo + 1)
    y[..., n - fo:] *= 0.5 + 0.5 * np.cos(np.pi * i / fo)
    tp = true_peak(y)
    if tp > 0:
        y *= 10 ** (C.TRUE_PEAK_DBFS / 20.0) / tp
    return y, start, start + n
