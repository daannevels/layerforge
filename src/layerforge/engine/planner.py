"""Planning: analyses + keep-map + settings (+ random generator) -> Recipe.

M2 implements the base variation (candidate 0: the user's markings, no jitter).
"""

from __future__ import annotations

import numpy as np

from layerforge.engine import constants as C
from layerforge.engine.align import best_align, coherence_ratio, candidates, shift
from layerforge.engine.analysis import analyse, anchor_shifts, level_match_gains_db
from layerforge.engine.bands import bandpass
from layerforge.engine.mix import all_zone_windows, band_lower_edge, band_weights, resolve_cells
from layerforge.engine.presets import get_preset
from layerforge.engine.render import BandCache, prepare_bands, zone_edges_samples
from layerforge.engine.types import Analysis, InputSpec, KeepMap, Owner, Recipe, Settings
from layerforge.engine.windows import crossfade_width_s, zone_crossfades


def resolved_settings(settings: Settings) -> dict:
    """Fill preset defaults into the user settings."""
    p = get_preset(settings.sound_type)
    edges = list(settings.band_edges_hz) if settings.band_edges_hz else list(p.band_edges_hz)
    zones = list(settings.zone_edges_ms) if settings.zone_edges_ms else list(p.zone_edges_ms)
    if sorted(edges) != edges or len(edges) != len(p.band_edges_hz):
        raise ValueError("band edges must be ascending and match the sound type's band count")
    if not 0 < zones[0] < zones[1]:
        raise ValueError("zone edges must satisfy 0 < edge1 < edge2")
    if settings.filter_order not in C.FILTER_ORDERS:
        raise ValueError(f"filter order must be one of {C.FILTER_ORDERS}")
    return {
        "edges": edges,
        "zones": zones,
        "order": settings.filter_order,
        "guard_ms": max(float(settings.guard_ms), 0.0),
        "relaxed": p.relaxed_alignment if settings.relaxed_alignment is None else settings.relaxed_alignment,
        "crossover": settings.crossover_check,
        "drive_db": p.saturation_drive_db if settings.drive_db is None else float(settings.drive_db),
    }


def analyse_inputs(inputs: list[np.ndarray], sr: int, sound_type: str,
                   zone_edges_ms: list[float]) -> list[Analysis]:
    return [analyse(x, sr, zone_edges_ms, centroid_anchor=(sound_type == "clap")) for x in inputs]


def input_specs(names: list[str], analyses: list[Analysis], trims_db: list[float]) -> list[InputSpec]:
    gains = level_match_gains_db([a.rms_as for a in analyses], trims_db)
    shifts = anchor_shifts([a.anchor for a in analyses])
    return [InputSpec(name=n, onset=a.onset, anchor=a.anchor, shift=s, gain_db=g)
            for n, a, s, g in zip(names, analyses, shifts, gains)]


def crossfades_for(edges: list[float], zones_ms: list[float], sr: int) -> list[list[float]]:
    e1, e2 = zone_edges_samples(zones_ms, sr)
    return [list(zone_crossfades(crossfade_width_s(band_lower_edge(edges, b)) * sr, e1, e2))
            for b in range(len(edges) + 1)]


def cell_energies(mono: np.ndarray, windows: np.ndarray) -> np.ndarray:
    """mono: (inputs, bands, n); windows: (bands, 3, n) -> (inputs, bands, 3) energies."""
    return np.einsum("ibn,bzn->ibz", mono ** 2, windows ** 2)


def plan_alignment(mono: np.ndarray, cells: list[list[list[Owner]]], energy: np.ndarray,
                   edges: list[float], sr: int, origin: int, relaxed: bool, crossover: bool,
                   ) -> tuple[list[list[int]], list[list[int]], dict]:
    """Step 7. mono: (inputs, bands, n) guarded band signals. Returns lags[b][i], pols[b][i], notes."""
    n_in, n_b, _ = mono.shape
    max_lag = int(round(C.ALIGN_MAX_LAG_MS * sr / 1000.0))
    win = int(round(C.ALIGN_WINDOW_MS * sr / 1000.0))
    lags = [[0] * n_in for _ in range(n_b)]
    pols = [[1] * n_in for _ in range(n_b)]
    weights = band_weights(cells, n_in)
    band_energy = energy.sum(axis=2)  # (inputs, bands)
    notes: dict = {"align_gain_db": {}, "crossover": {}}

    def dominant(b: int) -> int | None:
        owners = [i for i in range(n_in) if weights[b, i] > 0]
        if not owners:
            return None
        return max(owners, key=lambda i: (weights[b, i], band_energy[i, b], -i))

    def is_relaxed(b: int) -> bool:
        return relaxed and band_lower_edge(edges, b) >= C.RELAXED_MIN_HZ

    for b in range(n_b):
        ref = dominant(b)
        if ref is None or is_relaxed(b):
            continue
        for i in range(n_in):
            if i == ref or weights[b, i] <= 0:
                continue
            k, p, g = best_align(mono[ref, b], mono[i, b], max_lag, origin, win, C.ALIGN_MIN_GAIN_DB)
            lags[b][i], pols[b][i] = k, p
            notes["align_gain_db"][f"{b}:{i}"] = round(g, 3)

    if crossover:
        for b in range(n_b - 1):
            lo_owner, hi_owner = dominant(b), dominant(b + 1)
            if lo_owner is None or hi_owner is None or lo_owner == hi_owner or is_relaxed(b + 1):
                continue
            fc = float(edges[b])
            half = 2.0 ** (C.CROSSOVER_OCTAVES / 2.0)
            a = pols[b][lo_owner] * shift(bandpass(mono[lo_owner, b], fc / half, fc * half, sr), lags[b][lo_owner])
            hb = bandpass(mono[hi_owner, b + 1], fc / half, fc * half, sr)
            base_k, base_p = lags[b + 1][hi_owner], pols[b + 1][hi_owner]
            r0 = coherence_ratio(a, base_p * shift(hb, base_k), origin, win)
            best = (r0, 0, 1)
            if r0 < C.CROSSOVER_R_MIN:
                for k, p in candidates(max_lag):
                    r = coherence_ratio(a, p * base_p * shift(hb, base_k + k), origin, win)
                    if r > best[0]:
                        best = (r, k, p)
                if best[1:] != (0, 1):  # move the whole upper band (all its owners) together
                    for i in range(n_in):
                        lags[b + 1][i] += best[1]
                        pols[b + 1][i] *= best[2]
            notes["crossover"][f"{b}|{b + 1}"] = {"R_before": round(r0, 3), "R_after": round(best[0], 3),
                                                  "lag": best[1], "polarity": best[2]}
    return lags, pols, notes


def plan_base(inputs: list[np.ndarray], names: list[str], keepmap: KeepMap, settings: Settings,
              cache: BandCache | None = None, seed: int = 0) -> Recipe:
    """Candidate 0: the user's markings, no jitter; open cells go to the input with most energy."""
    if not C.MIN_INPUTS <= len(inputs) <= C.MAX_INPUTS:
        raise ValueError(f"need {C.MIN_INPUTS} to {C.MAX_INPUTS} inputs")
    sr = settings.sample_rate
    rs = resolved_settings(settings)
    analyses = analyse_inputs(inputs, sr, settings.sound_type, rs["zones"])
    specs = input_specs(names, analyses, settings.trims_db)
    length = max(x.shape[-1] + s.shift for x, s in zip(inputs, specs))
    origin = min(s.onset + s.shift for s in specs)
    n_b = len(rs["edges"]) + 1
    recipe = Recipe(
        sound_type=settings.sound_type, sample_rate=sr, channels=inputs[0].shape[0],
        length=length, origin=origin, band_edges_hz=rs["edges"], zone_edges_ms=rs["zones"],
        filter_order=rs["order"], guard_ms=rs["guard_ms"],
        crossfade_samples=crossfades_for(rs["edges"], rs["zones"], sr),
        inputs=specs, cells=[], lags=[[0] * len(inputs) for _ in range(n_b)],
        polarities=[[1] * len(inputs) for _ in range(n_b)], band_trims_db=[0.0] * n_b,
        drive_db=rs["drive_db"], seed=seed, candidate=0, variation_amount=0.0,
    )
    bands = prepare_bands(recipe, inputs, cache)
    mono = bands.mean(axis=2)
    windows = all_zone_windows(length, origin, zone_edges_samples(rs["zones"], sr), recipe.crossfade_samples)
    energy = cell_energies(mono, windows)
    recipe.cells = resolve_cells(keepmap, energy, rs["edges"])
    recipe.lags, recipe.polarities, notes = plan_alignment(
        mono, recipe.cells, energy, rs["edges"], sr, origin, rs["relaxed"], rs["crossover"])
    recipe.notes = notes
    return recipe
