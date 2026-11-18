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


class BandCache:
    """Caches guarded band splits of the aligned inputs, keyed by everything they depend on."""

    def __init__(self) -> None:
        self._store: dict[tuple, np.ndarray] = {}

    def get(self, key: tuple, make) -> np.ndarray:
        if key not in self._store:
            self._store[key] = make()
        return self._store[key]


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
    """Steps 4-5: band split and onset guard. Returns (inputs, bands, channels, length)."""
    n = aligned.shape[-1]
    gl = int(round(guard_ms * sr / 1000.0))
    out = []
    for i, s in enumerate(specs):
        bands = split_bands(aligned[i], edges_hz, sr, order)
        out.append(bands * guard_window(n, s.onset + s.shift, gl))
    return np.stack(out)


def zone_edges_samples(recipe_zone_ms: list[float], sr: float) -> tuple[float, float]:
    return recipe_zone_ms[0] * sr / 1000.0, recipe_zone_ms[1] * sr / 1000.0


def prepare_bands(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache | None = None) -> np.ndarray:
    key = ("bands", tuple((s.gain_db, s.shift, s.onset) for s in recipe.inputs), recipe.length,
           tuple(recipe.band_edges_hz), recipe.filter_order, recipe.guard_ms, recipe.sample_rate)

    def make() -> np.ndarray:
        aligned = align_inputs(inputs, recipe.inputs, recipe.length)
        return guarded_bands(aligned, recipe.inputs, recipe.band_edges_hz, recipe.sample_rate,
                             recipe.filter_order, recipe.guard_ms)

    return cache.get(key, make) if cache is not None else make()


def render(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache | None = None) -> RenderResult:
    """Render one recipe. inputs: list of (channels, n) float64 arrays at recipe.sample_rate."""
    sr = recipe.sample_rate
    bands = prepare_bands(recipe, inputs, cache)
    n_in, n_b = bands.shape[0], bands.shape[1]
    # step 7 results: per-band lag and polarity of every input
    moved = np.empty_like(bands)
    for b in range(n_b):
        for i in range(n_in):
            moved[i, b] = recipe.polarities[b][i] * shift(bands[i, b], recipe.lags[b][i])
    windows = all_zone_windows(recipe.length, recipe.origin,
                               zone_edges_samples(recipe.zone_edges_ms, sr), recipe.crossfade_samples)
    trims = 10.0 ** (np.asarray(recipe.band_trims_db, dtype=np.float64) / 20.0)
    assembled = assemble(moved, recipe.cells, windows, trims)
    y, start, end = post_chain(mono_below(assembled, sr), sr, recipe.drive_db)
    return RenderResult(y, start, end, assembled)


def render_simple_stack(recipe: Recipe, inputs: list[np.ndarray]) -> np.ndarray:
    """All inputs aligned on their anchors, level-matched and summed (no band logic), then the
    same stereo step and post chain as a variation."""
    aligned = align_inputs(inputs, recipe.inputs, recipe.length).sum(axis=0)
    y, _, _ = post_chain(mono_below(aligned, recipe.sample_rate), recipe.sample_rate, recipe.drive_db)
    return y
