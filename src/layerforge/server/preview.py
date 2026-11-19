"""Listening previews: one band or one group (Low/Mid/High) of a sample or a result, made with the
engine's own band split, so 'solo' plays exactly what the blend works with."""

from __future__ import annotations

import numpy as np

from layerforge.audio_io import read_audio, wav_bytes_float
from layerforge.engine.bands import split_bands
from layerforge.engine.presets import GROUP_NAMES, PRESETS


class PreviewError(ValueError):
    pass


def band_mask(sound_type: str, kind: str, index: int) -> list[bool]:
    p = PRESETS[sound_type]
    n_b = len(p.band_names)
    if kind == "all":
        return [True] * n_b
    if kind == "group" and 0 <= index < len(GROUP_NAMES):
        return [g == index for g in p.band_groups]
    if kind == "band" and 0 <= index < n_b:
        return [b == index for b in range(n_b)]
    raise PreviewError("unknown selection")


def render_preview(audio: bytes | str, sound_type: str, edges: list[float], order: int,
                   kind: str, index: int) -> bytes:
    """The selected bands of the audio (path or bytes) as a 32-bit float WAV at its own rate."""
    if sound_type not in PRESETS:
        raise PreviewError("unknown sound type")
    mask = band_mask(sound_type, kind, index)
    x, sr = read_audio(audio)
    if kind == "all":
        return wav_bytes_float(x, sr)
    if len(edges) != len(PRESETS[sound_type].band_edges_hz) or edges[-1] >= 0.49 * sr:
        raise PreviewError("band edges do not fit")
    bands = split_bands(x, edges, sr, order)
    return wav_bytes_float(np.sum([bands[b] for b in range(len(mask)) if mask[b]], axis=0), sr)
