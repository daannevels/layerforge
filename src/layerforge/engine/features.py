"""Feature vector per candidate, used to pick clearly different variations (section 6)."""

from __future__ import annotations

import numpy as np

from layerforge.engine import constants as C
from layerforge.engine.analysis import attack_time, decay_time, to_mono

FEATURE_NAMES_FIXED = ("log2_centroid_hz", "decay_s", "attack_s", "crest_db")


def feature_names(band_names: tuple[str, ...] | list[str]) -> list[str]:
    return ["log2_centroid_hz"] + [f"band_db_{b}" for b in band_names] + ["decay_s", "attack_s", "crest_db"]


def features(y: np.ndarray, sr: float, band_edges_hz: tuple[float, ...] | list[float]) -> np.ndarray:
    """y: (channels, n) output audio. Band energies use the type's DEFAULT edges (not the
    candidate's jittered ones) so all candidates are measured the same way."""
    m = to_mono(y)
    w = m[:max(int(round(C.FEATURE_WINDOW_MS * sr / 1000.0)), 16)]
    spec = np.abs(np.fft.rfft(w * np.hanning(w.size)))
    freqs = np.fft.rfftfreq(w.size, 1.0 / sr)
    centroid = float(np.dot(freqs, spec) / spec.sum()) if spec.sum() > 0 else 0.0
    # band energies from the power spectrum (a measurement only, so no filter bank needed)
    nfft = 1 << int(np.ceil(np.log2(max(m.size, 2))))  # power of two: fast FFT
    pw = np.abs(np.fft.rfft(m, nfft)) ** 2
    f_all = np.fft.rfftfreq(nfft, 1.0 / sr)
    idx = np.searchsorted(f_all, np.asarray(band_edges_hz, dtype=np.float64))
    e = np.array([seg.sum() for seg in np.split(pw, idx)])
    total = float(e.sum()) or 1.0
    band_db = 10.0 * np.log10(np.maximum(e / total, 1e-12))
    rms = float(np.sqrt(np.mean(w ** 2))) or 1e-12
    crest = 20.0 * np.log10(max(float(np.max(np.abs(m))), 1e-12) / rms)
    return np.concatenate([[np.log2(max(centroid, 1.0))], band_db,
                           [decay_time(m, sr), attack_time(m, sr), crest]])


def zscore(f: np.ndarray) -> np.ndarray:
    """Z-score each column over the rows; constant columns become 0."""
    mu = f.mean(axis=0)
    sd = f.std(axis=0)
    return np.where(sd > 0, (f - mu) / np.where(sd > 0, sd, 1.0), 0.0)
