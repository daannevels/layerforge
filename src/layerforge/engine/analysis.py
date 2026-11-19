"""Step 1: per-sample analysis (onset, anchor, peak, decay, attack, level)."""

from __future__ import annotations

import numpy as np

from layerforge.engine import constants as C
from layerforge.engine.types import Analysis


def to_mono(x: np.ndarray) -> np.ndarray:
    """(channels, n) or (n,) -> (n,) mean of the channels."""
    x = np.asarray(x, dtype=np.float64)
    return x if x.ndim == 1 else x.mean(axis=0)


def peak_abs(x: np.ndarray) -> np.ndarray:
    """(n,) max |x| over channels."""
    x = np.asarray(x, dtype=np.float64)
    return np.abs(x) if x.ndim == 1 else np.max(np.abs(x), axis=0)


def find_onset(x: np.ndarray, sr: float) -> int:
    """First index where |x| >= ONSET_THRESHOLD * peak, then walk back to the last index where
    |x| < ONSET_QUIET * peak (at most ONSET_MAX_WALKBACK_MS). |x| is the max over channels."""
    a = peak_abs(x)
    pk = float(a.max()) if a.size else 0.0
    if pk <= 0.0:
        return 0
    i0 = int(np.argmax(a >= C.ONSET_THRESHOLD * pk))
    lo = max(i0 - int(round(C.ONSET_MAX_WALKBACK_MS * sr / 1000.0)), 0)
    quiet = np.nonzero(a[lo:i0 + 1] < C.ONSET_QUIET * pk)[0]
    return lo + int(quiet[-1]) if quiet.size else lo


def find_anchor(x: np.ndarray, onset: int, sr: float, centroid: bool, edge1_ms: float) -> int:
    """Onset, or (claps) onset + energy centroid of x^2 over [onset, onset + edge1)."""
    if not centroid:
        return onset
    m = to_mono(x)
    seg = m[onset:onset + max(int(round(edge1_ms * sr / 1000.0)), 1)]
    e = seg * seg
    if e.sum() <= 0:
        return onset
    return onset + int(round(float(np.dot(np.arange(seg.size), e) / e.sum())))


def rms_envelope(x: np.ndarray, sr: float, win_ms: float) -> np.ndarray:
    """Centred moving RMS (all channels pooled)."""
    p = np.asarray(x, dtype=np.float64) ** 2
    if p.ndim > 1:
        p = p.mean(axis=0)
    w = max(int(round(win_ms * sr / 1000.0)), 1)
    kernel = np.ones(w) / w
    return np.sqrt(np.maximum(np.convolve(p, kernel, mode="same"), 0.0))


def decay_time(x: np.ndarray, sr: float) -> float:
    """Seconds from the envelope peak to the last point where it is above DECAY_DB."""
    env = rms_envelope(x, sr, C.ENVELOPE_MS)
    ip = int(np.argmax(env))
    if env[ip] <= 0:
        return 0.0
    above = np.nonzero(env[ip:] >= env[ip] * 10 ** (C.DECAY_DB / 20.0))[0]
    return float(above[-1]) / sr


def attack_time(x: np.ndarray, sr: float) -> float:
    """Seconds from the envelope first reaching ATTACK_LOW to first reaching ATTACK_HIGH of its peak."""
    env = rms_envelope(x, sr, C.ATTACK_ENVELOPE_MS)
    pk = float(env.max())
    if pk <= 0:
        return 0.0
    i_lo = int(np.argmax(env >= C.ATTACK_LOW * pk))
    i_hi = int(np.argmax(env >= C.ATTACK_HIGH * pk))
    return max(i_hi - i_lo, 0) / sr


def rms_attack_sustain(x: np.ndarray, onset: int, sr: float, edge2_ms: float) -> float:
    """RMS over [onset, onset + edge2) (the Attack and Sustain zones), all channels pooled."""
    seg = np.asarray(x, dtype=np.float64)[..., onset:onset + max(int(round(edge2_ms * sr / 1000.0)), 1)]
    return float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0


def estimate_f0(x: np.ndarray, sr: float, onset: int) -> float:
    """Pitch in Hz from the normalised autocorrelation of a segment after the attack; 0 if unclear.
    The shortest lag whose correlation is within 10 % of the best one wins (avoids octave errors)."""
    m = to_mono(x)
    a = onset + int(round(C.F0_SKIP_MS * sr / 1000.0))
    seg = m[a:a + int(round(C.F0_SEGMENT_MS * sr / 1000.0))]
    lo, hi = int(sr / C.F0_MAX_HZ), int(sr / C.F0_MIN_HZ)
    if seg.size < 2 * hi or not np.any(seg):
        seg = m[onset:onset + 2 * hi + 1]
        if seg.size < 2 * hi or not np.any(seg):
            return 0.0
    seg = (seg - seg.mean()) * np.hanning(seg.size)
    nfft = 1 << int(np.ceil(np.log2(2 * seg.size)))
    r = np.fft.irfft(np.abs(np.fft.rfft(seg, nfft)) ** 2, nfft)[:hi + 2]
    # unbiased normalisation (the window overlap shrinks with the lag)
    w = np.hanning(seg.size)
    rw = np.fft.irfft(np.abs(np.fft.rfft(w, nfft)) ** 2, nfft)[:hi + 2]
    if r[0] <= 0:
        return 0.0
    rn = (r / r[0]) / np.maximum(rw / rw[0], 1e-3)
    band = rn[lo:hi + 1]
    best = float(band.max())
    if best < C.F0_MIN_CLARITY:
        return 0.0
    peaks = [k for k in range(1, band.size - 1)
             if band[k] >= band[k - 1] and band[k] >= band[k + 1] and band[k] >= 0.9 * best]
    k = (peaks[0] if peaks else int(np.argmax(band))) + lo
    y0, y1, y2 = rn[k - 1], rn[k], rn[k + 1]
    den = y0 - 2 * y1 + y2
    frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    return float(sr / (k + float(np.clip(frac, -0.5, 0.5))))


def analyse(x: np.ndarray, sr: float, zone_edges_ms: tuple[float, float] | list[float],
            centroid_anchor: bool, pitched: bool = False) -> Analysis:
    """Analyse one input, shape (channels, n) or (n,)."""
    onset = find_onset(x, sr)
    return Analysis(
        onset=onset,
        anchor=find_anchor(x, onset, sr, centroid_anchor, zone_edges_ms[0]),
        peak=float(peak_abs(x).max()),
        decay_s=decay_time(x, sr),
        attack_s=attack_time(x, sr),
        rms_as=rms_attack_sustain(x, onset, sr, zone_edges_ms[1]),
        f0_hz=estimate_f0(x, sr, onset) if pitched else 0.0,
    )


def level_match_gains_db(rms_values: list[float], trims_db: list[float] | None = None) -> list[float]:
    """Step 2: gain per input so its Attack+Sustain RMS equals the median, clamped to
    +-LEVEL_MATCH_LIMIT_DB, plus a trim clamped to +-TRIM_LIMIT_DB."""
    r = np.asarray(rms_values, dtype=np.float64)
    med = float(np.median(r))
    gains = []
    for i, v in enumerate(r):
        g = 20.0 * np.log10(med / v) if v > 0 and med > 0 else 0.0
        g = float(np.clip(g, -C.LEVEL_MATCH_LIMIT_DB, C.LEVEL_MATCH_LIMIT_DB))
        t = float(np.clip(trims_db[i], -C.TRIM_LIMIT_DB, C.TRIM_LIMIT_DB)) if trims_db and i < len(trims_db) else 0.0
        gains.append(g + t)
    return gains


def anchor_shifts(anchors: list[int]) -> list[int]:
    """Step 3: delay per input (>= 0) so all anchors land on max(anchors)."""
    top = max(anchors)
    return [top - a for a in anchors]
