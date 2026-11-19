"""Deterministic rendering: Recipe + input audio -> output audio (steps 2-10).

The render step uses only numbers stored in the recipe (gains, shifts, lags, polarities,
owners, windows), so `layerforge render recipe.json` rebuilds the identical file.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from layerforge.engine.align import shift
from layerforge.engine.bands import split_bands
from layerforge.engine.mix import all_zone_windows, assemble
from layerforge.engine.post import mono_below, post_chain
from layerforge.engine.types import InputSpec, Recipe
from layerforge.engine.windows import guard_window


@dataclass
class RenderResult:
    audio: np.ndarray        # (channels, n) final output
    start: int               # index of audio[:, 0] on the internal timeline
    end: int                 # index after the last output sample on the internal timeline
    assembled: np.ndarray    # (channels, length) step 8 output, before stereo and post chain
    norm_gain: float         # linear gain of the final true-peak normalisation


class BandCache:
    """Caches guarded band splits of the aligned inputs, keyed by everything they depend on.
    Keeps at most `size` entries (least recently used are dropped); `pinned` keys are never dropped."""

    def __init__(self, size: int = 3, shared: dict[tuple, np.ndarray] | None = None) -> None:
        self.size = size
        self._store: dict[tuple, np.ndarray] = {}
        self.pinned: set[tuple] = set()
        self.shared = shared or {}   # read-only entries (e.g. the base split shared by threads)

    def get(self, key: tuple, make) -> np.ndarray:
        if key in self.shared:
            return self.shared[key]
        if key in self._store:
            val = self._store.pop(key)
        else:
            val = make()
        self._store[key] = val  # most recent last
        loose = [k for k in self._store if k not in self.pinned]
        while len(loose) > self.size:
            del self._store[loose.pop(0)]
        return val


def decay_envelope(n: int, start: int, slope_db_s: float, sr: float) -> np.ndarray:
    """1 until `start`, then 10^(slope * (t - start) / 20) with t in seconds (slope <= 0 dB/s)."""
    t = np.maximum(np.arange(n, dtype=np.float64) - start, 0.0) / sr
    return 10.0 ** (min(slope_db_s, 0.0) * t / 20.0)


def align_inputs(inputs: list[np.ndarray], specs: list[InputSpec], length: int) -> np.ndarray:
    """Steps 2-3: gain (level match + trim) and anchor shift. Returns (inputs, channels, length)."""
    n_c = inputs[0].shape[0]
    out = np.zeros((len(inputs), n_c, length))
    for i, (x, s) in enumerate(zip(inputs, specs)):
        g = 10.0 ** (s.gain_db / 20.0)
        n = min(x.shape[-1], length - s.shift)
        if n > 0:
            out[i, :, s.shift:s.shift + n] = g * x[:, :n]
    return out


def guarded_bands(aligned: np.ndarray, specs: list[InputSpec], edges_hz: list[float], sr: float,
                  order: int, guard_ms: float) -> np.ndarray:
    """Steps 4-5: band split and onset guard. Returns (inputs, bands, channels, length).
    (The decay-matching fade is applied after this, in `prepare_bands`, so it does not change
    the cached split.)"""
    n = aligned.shape[-1]
    gl = int(round(guard_ms * sr / 1000.0))
    out = []
    for i, s in enumerate(specs):
        bands = split_bands(aligned[i], edges_hz, sr, order)
        out.append(bands * guard_window(n, s.onset + s.shift, gl))
    return np.stack(out)


def zone_edges_samples(recipe_zone_ms: list[float], sr: float) -> tuple[float, float]:
    return recipe_zone_ms[0] * sr / 1000.0, recipe_zone_ms[1] * sr / 1000.0


def bands_key(recipe: Recipe, n_channels: int) -> tuple:
    return ("bands", tuple((s.gain_db, s.shift, s.onset) for s in recipe.inputs), recipe.length,
            tuple(recipe.band_edges_hz), recipe.filter_order, recipe.guard_ms, recipe.sample_rate,
            n_channels)


def prepare_bands(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache | None = None) -> np.ndarray:
    """Steps 2-5 plus decay matching: (inputs, bands, channels, length)."""
    key = bands_key(recipe, inputs[0].shape[0])

    def make() -> np.ndarray:
        aligned = align_inputs(inputs, recipe.inputs, recipe.length)
        return guarded_bands(aligned, recipe.inputs, recipe.band_edges_hz, recipe.sample_rate,
                             recipe.filter_order, recipe.guard_ms)

    bands = cache.get(key, make) if cache is not None else make()
    if not any(s.decay_slope_db_s < 0 for s in recipe.inputs):
        return bands
    # decay matching: the same slow fade on every band of an input (so its bands still sum to
    # the faded input), starting decay_start samples into the input = decay_start + shift here
    out = bands.copy()
    for i, s in enumerate(recipe.inputs):
        if s.decay_slope_db_s < 0:
            out[i] *= decay_envelope(recipe.length, s.decay_start + s.shift, s.decay_slope_db_s,
                                     recipe.sample_rate)
    return out


def moved_bands(recipe: Recipe, bands: np.ndarray) -> np.ndarray:
    """Step 7 results: per-band lag and polarity of every input. Only owned (band, input) pairs
    are computed; the others stay zero (they are never used)."""
    n_in, n_b = bands.shape[0], bands.shape[1]
    moved = np.zeros_like(bands)
    for b in range(n_b):
        owned = {o.input for owners in recipe.cells[b] for o in owners}
        for i in sorted(owned):
            moved[i, b] = recipe.polarities[b][i] * shift(bands[i, b], recipe.lags[b][i])
    return moved


def recipe_windows(recipe: Recipe) -> np.ndarray:
    return all_zone_windows(recipe.length, recipe.origin,
                            zone_edges_samples(recipe.zone_edges_ms, recipe.sample_rate),
                            recipe.crossfade_samples)


def band_trim_gains(recipe: Recipe) -> np.ndarray:
    return 10.0 ** (np.asarray(recipe.band_trims_db, dtype=np.float64) / 20.0)


def render(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache | None = None,
           fast_peak: bool = False) -> RenderResult:
    """Render one recipe. inputs: list of (channels, n) float64 arrays at recipe.sample_rate.
    fast_peak: normalise on the sample peak instead of the true peak (candidate pool only)."""
    sr = recipe.sample_rate
    moved = moved_bands(recipe, prepare_bands(recipe, inputs, cache))
    assembled = assemble(moved, recipe.cells, recipe_windows(recipe), band_trim_gains(recipe))
    y, start, end, gain = post_chain(mono_below(assembled, sr), sr, recipe.drive_db, fast_peak)
    return RenderResult(y, start, end, assembled, gain)


def low_end_coherence(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache | None = None,
                      fc: float = 200.0, win_ms: float = 150.0) -> float:
    """R = E(sum of contributions) / sum of E(contribution) below `fc` over [origin, origin + win),
    where contribution i is what input i adds to the assembled output (mono). 1.0 when fewer than
    two inputs contribute. Below 0.5 means the low ends of the inputs cancel each other."""
    sr = recipe.sample_rate
    moved = moved_bands(recipe, prepare_bands(recipe, inputs, cache))
    windows, trims = recipe_windows(recipe), band_trim_gains(recipe)
    n_in = moved.shape[0]
    start = recipe.origin
    stop = min(start + int(round(win_ms * sr / 1000.0)), recipe.length)
    parts = []
    for i in range(n_in):
        cells_i = [[[o for o in owners if o.input == i] for owners in row] for row in recipe.cells]
        c = assemble(moved, cells_i, windows, trims).mean(axis=0)
        if np.any(c):
            nfft = 1 << int(np.ceil(np.log2(c.size)))  # zero padded to a power of two: fast FFT
            spec = np.fft.rfft(c, nfft)
            spec[np.fft.rfftfreq(nfft, 1.0 / sr) > fc] = 0.0  # measurement only: brick-wall low-pass
            parts.append(np.fft.irfft(spec, nfft)[start:stop])
    if len(parts) < 2:
        return 1.0
    den = sum(float(np.dot(p, p)) for p in parts)
    tot = np.sum(parts, axis=0)
    return float(np.dot(tot, tot) / den) if den > 0 else 1.0


def render_simple_stack(recipe: Recipe, inputs: list[np.ndarray]) -> np.ndarray:
    """All inputs aligned on their anchors, level-matched and summed (no band logic), then the
    same stereo step and post chain as a variation."""
    aligned = align_inputs(inputs, recipe.inputs, recipe.length).sum(axis=0)
    y, _, _, _ = post_chain(mono_below(aligned, recipe.sample_rate), recipe.sample_rate, recipe.drive_db)
    return y
