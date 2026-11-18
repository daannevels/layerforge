"""Zero-phase Butterworth filtering and the complementary band split (render step 4).

All filters are 2nd-order sections from scipy.signal.butter, run forward and then backward with
zero initial state on a signal padded with FILTER_PAD_S of zeros on both sides. Because a
zero-phase Butterworth low-pass and high-pass at the same cutoff are power complementary
(|H_lp|^2 + |H_hp|^2 = 1), every split step sums back to its input to rounding error.
Arrays are filtered along their last axis, so (channels, samples) works as well as (samples,).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy import signal

from layerforge.engine.constants import FILTER_PAD_S


@lru_cache(maxsize=256)
def butter_sos(order: int, fc: float | tuple[float, float], btype: str, sr: float) -> np.ndarray:
    """Butterworth 2nd-order sections; `fc` in Hz (a pair for 'bandpass')."""
    sos = signal.butter(order, fc, btype, fs=sr, output="sos")
    return sos


def forward_backward(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Filter forward, then backward, with zero initial state (no padding)."""
    y = signal.sosfilt(sos, x, axis=-1)
    return signal.sosfilt(sos, y[..., ::-1], axis=-1)[..., ::-1]


def _pad(x: np.ndarray, pad: int) -> np.ndarray:
    widths = [(0, 0)] * (x.ndim - 1) + [(pad, pad)]
    return np.pad(x, widths)


def zero_phase(x: np.ndarray, sos: np.ndarray, sr: float, pad_s: float = FILTER_PAD_S) -> np.ndarray:
    """Zero-phase filtering of `x` with zero padding of `pad_s` seconds on both sides."""
    pad = int(round(pad_s * sr))
    y = forward_backward(sos, _pad(np.asarray(x, dtype=np.float64), pad))
    return y[..., pad:pad + x.shape[-1]]


def lowpass(x: np.ndarray, fc: float, sr: float, order: int = 2) -> np.ndarray:
    return zero_phase(x, butter_sos(order, float(fc), "lowpass", float(sr)), sr)


def highpass(x: np.ndarray, fc: float, sr: float, order: int = 2) -> np.ndarray:
    return zero_phase(x, butter_sos(order, float(fc), "highpass", float(sr)), sr)


def bandpass(x: np.ndarray, lo: float, hi: float, sr: float, order: int = 2) -> np.ndarray:
    hi = min(hi, 0.49 * sr)
    return zero_phase(x, butter_sos(order, (float(lo), float(hi)), "bandpass", float(sr)), sr)


def split_bands(x: np.ndarray, edges_hz: tuple[float, ...] | list[float], sr: float,
                order: int = 2, pad_s: float = FILTER_PAD_S) -> np.ndarray:
    """Split `x` into len(edges)+1 bands, low to high. Returns shape (bands, *x.shape).

    Cascade: band k = LP(edge k) of the remainder; the remainder becomes HP(edge k) of itself.
    """
    pad = int(round(pad_s * sr))
    rest = _pad(np.asarray(x, dtype=np.float64), pad)
    bands = []
    for fc in edges_hz:
        bands.append(forward_backward(butter_sos(order, float(fc), "lowpass", float(sr)), rest))
        rest = forward_backward(butter_sos(order, float(fc), "highpass", float(sr)), rest)
    bands.append(rest)
    n = x.shape[-1]
    return np.stack([b[..., pad:pad + n] for b in bands])
