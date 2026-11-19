"""The ONLY module that reads or writes audio files (via soundfile)."""

from __future__ import annotations

import io
from collections import Counter
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from layerforge.engine.constants import OUTPUT_RATES, OUTPUT_SUBTYPE

READABLE_FORMATS = {"WAV", "AIFF", "FLAC", "W64", "RF64"}


class AudioError(ValueError):
    """A file that cannot be used as an input (unreadable, wrong format, silent, ...)."""


def read_audio(source: str | Path | bytes) -> tuple[np.ndarray, int]:
    """Read a wav/aiff/flac file (path or bytes). Returns ((channels, n) float64, sample_rate)."""
    try:
        f = sf.SoundFile(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source))
    except Exception as exc:
        raise AudioError(f"cannot read this file as audio ({exc})") from None
    with f:
        if f.format not in READABLE_FORMATS:
            raise AudioError(f"unsupported format {f.format}; use WAV, AIFF or FLAC")
        data = f.read(dtype="float64", always_2d=True).T.copy()
        sr = f.samplerate
    if data.shape[1] == 0:
        raise AudioError("the file is empty")
    if not np.all(np.isfinite(data)):
        raise AudioError("the file contains invalid sample values")
    if np.max(np.abs(data)) == 0:
        raise AudioError("the file is silent")
    if data.shape[0] > 2:
        data = data[:2]  # keep the first two channels
    return data, int(sr)


def default_output_rate(rates: list[int]) -> int:
    """The most common input rate if it is supported, else 48000 (ties: the higher rate)."""
    counts = Counter(r for r in rates if r in OUTPUT_RATES)
    if not counts:
        return 48000
    return max(counts, key=lambda r: (counts[r], r))


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    g = gcd(sr_in, sr_out)
    return signal.resample_poly(x, sr_out // g, sr_in // g, axis=-1)


def prepare_inputs(decoded: list[tuple[np.ndarray, int]], sr_out: int) -> list[np.ndarray]:
    """Resample every input to sr_out. Mono stays mono only if all inputs are mono;
    otherwise mono inputs are duplicated to stereo."""
    out = [resample(x, sr, sr_out) for x, sr in decoded]
    if any(x.shape[0] == 2 for x in out):
        out = [np.repeat(x, 2, axis=0) if x.shape[0] == 1 else x for x in out]
    return [np.ascontiguousarray(x, dtype=np.float64) for x in out]


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    """24-bit PCM WAV of a (channels, n) array."""
    buf = io.BytesIO()
    sf.write(buf, np.clip(x.T, -1.0, 1.0), sr, subtype=OUTPUT_SUBTYPE, format="WAV")
    return buf.getvalue()


def write_wav(path: str | Path, x: np.ndarray, sr: int) -> None:
    Path(path).write_bytes(wav_bytes(x, sr))


def wav_bytes_float(x: np.ndarray, sr: int) -> bytes:
    """32-bit float WAV (previews in the browser; not clipped)."""
    buf = io.BytesIO()
    sf.write(buf, x.T, sr, subtype="FLOAT", format="WAV")
    return buf.getvalue()
