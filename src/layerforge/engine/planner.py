"""Planning: analyses + keep-map + settings (+ random generator) -> Recipe.

Candidate 0 is the base (the user's markings, no jitter); other candidates add random jitter
scaled by the variation amount. The random generator is only used here, never in render.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from layerforge.engine import constants as C
from layerforge.engine.align import best_align, best_coherence_shift, best_envelope_lag, coherence_ratio, shift
from layerforge.engine.analysis import analyse, anchor_shifts, level_match_gains_db
from layerforge.engine.bands import bandpass
from layerforge.engine.mix import all_zone_windows, band_lower_edge, band_weights, resolve_cells
from layerforge.engine.presets import get_preset
from layerforge.engine.render import BandCache, prepare_bands, zone_edges_samples
from layerforge.engine.types import KEEP, Analysis, InputSpec, KeepMap, Owner, Recipe, Settings
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
        "envelope_align": settings.relaxed_alignment is not False,
        "crossover": settings.crossover_check,
        "drive_db": p.saturation_drive_db if settings.drive_db is None else float(settings.drive_db),
    }


def analyse_inputs(inputs: list[np.ndarray], sr: int, sound_type: str,
                   zone_edges_ms: list[float]) -> list[Analysis]:
    p = get_preset(sound_type)
    return [analyse(x, sr, zone_edges_ms, centroid_anchor=p.centroid_anchor, pitched=p.pitched)
            for x in inputs]


def noise_bands(sound_type: str, envelope_align: bool) -> list[bool]:
    """Per band: noise-like (envelope alignment, no crossover check). Decided on the DEFAULT edges."""
    p = get_preset(sound_type)
    lowers = [0.0] + list(p.band_edges_hz)
    return [envelope_align and lo >= p.noise_from_hz for lo in lowers]


def group_leader(sound_type: str, keepmap: KeepMap, group: int) -> int | None:
    """The input the user chose for a group: the one with the most keep cells in its bands."""
    p = get_preset(sound_type)
    bands = [b for b, g in enumerate(p.band_groups) if g == group]
    counts = [sum(c.state == KEEP for b in bands for c in keepmap.cells[i][b])
              for i in range(len(keepmap.cells))]
    best = max(range(len(counts)), key=lambda i: (counts[i], -i)) if counts else 0
    return best if counts and counts[best] > 0 else None


def target_decay(decays: list[float], leader: int | None, rng: np.random.Generator | None,
                 j: float) -> float:
    """Length of the blend (s, decay to DECAY_DB): the leader's decay, or else the median decay,
    moved towards the shortest/longest input by the variation jitter."""
    if leader is not None:
        return decays[leader]
    d = np.maximum(np.asarray(decays, dtype=np.float64), 1e-3)
    t = float(np.median(d))
    if rng is not None and j > 0:
        span = np.log(d.max() / d.min())
        t = float(np.clip(t * np.exp(rng.uniform(-1, 1) * C.JITTER_LENGTH * j * span), d.min(), d.max()))
    return t


def decay_slopes(decays: list[float], target: float) -> list[float]:
    """Extra fade (dB/s, <= 0) per input so that an input longer than the target reaches DECAY_DB
    at the target time: slope = DECAY_DB * (1/target - 1/decay)."""
    if not C.DECAY_MATCH or target <= 0:
        return [0.0] * len(decays)
    return [C.DECAY_DB * (1.0 / target - 1.0 / d) if d > target else 0.0 for d in decays]


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
                   edges: list[float], sr: int, origin: int, noise: list[bool], crossover: bool,
                   envelope_ms: float = C.ALIGN_MAX_LAG_MS,
                   ) -> tuple[list[list[int]], list[list[int]], dict]:
    """Step 7. mono: (inputs, bands, n) guarded band signals. Tonal bands: lag + polarity on the
    waveform; noise-like bands (noise[b]): lag on the RMS envelope, no polarity, no crossover check.
    Returns lags[b][i], pols[b][i], notes."""
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

    env_lag = int(round(envelope_ms * sr / 1000.0))
    env_win = int(round(C.ENVELOPE_WINDOW_MS * sr / 1000.0))
    for b in range(n_b):
        ref = dominant(b)
        if ref is None:
            continue
        for i in range(n_in):
            if i == ref or weights[b, i] <= 0:
                continue
            if noise[b]:
                k, g = best_envelope_lag(mono[ref, b], mono[i, b], env_lag, origin, win, env_win,
                                         C.ENVELOPE_ALIGN_MIN_GAIN)
                lags[b][i] = k
                notes["align_gain_db"][f"{b}:{i}"] = f"env {round(100 * g, 1)}%"
            else:
                k, p, g = best_align(mono[ref, b], mono[i, b], max_lag, origin, win, C.ALIGN_MIN_GAIN_DB)
                lags[b][i], pols[b][i] = k, p
                notes["align_gain_db"][f"{b}:{i}"] = round(g, 3)

    if crossover:
        for b in range(n_b - 1):
            lo_owner, hi_owner = dominant(b), dominant(b + 1)
            if lo_owner is None or hi_owner is None or lo_owner == hi_owner or noise[b + 1]:
                continue
            fc = float(edges[b])
            half = 2.0 ** (C.CROSSOVER_OCTAVES / 2.0)
            a = pols[b][lo_owner] * shift(bandpass(mono[lo_owner, b], fc / half, fc * half, sr), lags[b][lo_owner])
            hb = bandpass(mono[hi_owner, b + 1], fc / half, fc * half, sr)
            base_k, base_p = lags[b + 1][hi_owner], pols[b + 1][hi_owner]
            hb_now = base_p * shift(hb, base_k)
            r0 = coherence_ratio(a, hb_now, origin, win)
            best = (r0, 0, 1)
            if r0 < C.CROSSOVER_R_MIN:
                best = best_coherence_shift(a, hb_now, max_lag, origin, win, r0)
                if best[1:] != (0, 1):  # move the whole upper band (all its owners) together
                    for i in range(n_in):
                        lags[b + 1][i] += best[1]
                        pols[b + 1][i] *= best[2]
            notes["crossover"][f"{b}|{b + 1}"] = {"R_before": round(r0, 3), "R_after": round(best[0], 3),
                                                  "lag": best[1], "polarity": best[2]}
    return lags, pols, notes


@dataclass
class Prepared:
    """Everything that does not change between candidates: settings, analyses, gains, shifts."""
    sound_type: str
    sample_rate: int
    channels: int
    names: list[str]
    rs: dict
    analyses: list[Analysis]
    specs: list[InputSpec]
    length: int
    origin: int


def prepare(inputs: list[np.ndarray], names: list[str], settings: Settings) -> Prepared:
    """Steps 1-3 for all inputs (level match and anchor shifts use the un-jittered zone edges)."""
    if not C.MIN_INPUTS <= len(inputs) <= C.MAX_INPUTS:
        raise ValueError(f"need {C.MIN_INPUTS} to {C.MAX_INPUTS} inputs")
    sr = settings.sample_rate
    rs = resolved_settings(settings)
    analyses = analyse_inputs(inputs, sr, settings.sound_type, rs["zones"])
    specs = input_specs(names, analyses, settings.trims_db)
    return Prepared(
        sound_type=settings.sound_type, sample_rate=sr, channels=inputs[0].shape[0], names=list(names),
        rs=rs, analyses=analyses, specs=specs,
        length=max(x.shape[-1] + s.shift for x, s in zip(inputs, specs)),
        origin=min(s.onset + s.shift for s in specs),
    )


def jitter_edges(edges: list[float], rng: np.random.Generator, j: float, sr: int) -> list[float]:
    """Band edges x exp(U(-1,1) * JITTER_BAND_EDGE * j), then pushed apart to BAND_EDGE_MIN_RATIO
    (bottom-up) and kept below BAND_EDGE_MAX_FRACTION * sr (top-down)."""
    e = [float(v) * float(np.exp(rng.uniform(-1, 1) * C.JITTER_BAND_EDGE * j)) for v in edges]
    for k in range(1, len(e)):
        e[k] = max(e[k], e[k - 1] * C.BAND_EDGE_MIN_RATIO)
    top = C.BAND_EDGE_MAX_FRACTION * sr
    for k in range(len(e) - 1, -1, -1):
        cap = top if k == len(e) - 1 else e[k + 1] / C.BAND_EDGE_MIN_RATIO
        e[k] = min(e[k], cap)
    return e


def plan_candidate(prep: Prepared, inputs: list[np.ndarray], keepmap: KeepMap,
                   rng: np.random.Generator | None, amount: float, candidate: int, seed: int,
                   cache: BandCache | None = None) -> Recipe:
    """One candidate recipe. rng None or amount 0 = the base (no jitter). amount in percent.

    Jitter (j = amount / 100): open-cell owners redrawn; keep strengths, band edges, zone edges,
    extra per-band alignment offsets, per-band trims and saturation drive (section 6)."""
    sr = prep.sample_rate
    rs = prep.rs
    j = float(np.clip(amount, 0.0, 100.0)) / 100.0
    jitter = rng is not None and j > 0
    n_in = len(prep.specs)
    edges, zones, drive = list(rs["edges"]), list(rs["zones"]), rs["drive_db"]
    n_b = len(edges) + 1
    strengths = None
    trims = [0.0] * n_b
    if jitter:
        # fixed draw order, so a seed always gives the same candidate
        strengths = np.array([[[c.strength for c in band] for band in inp] for inp in keepmap.cells])
        strengths = strengths * (1.0 + rng.uniform(-1, 1, strengths.shape) * C.JITTER_STRENGTH * j)
        moved_edges = jitter_edges(edges, rng, j, sr)   # always drawn, so the draw order is fixed
        if rng.uniform() < C.BAND_EDGE_JITTER_PROB:
            edges = moved_edges
        z = [v * float(np.exp(rng.uniform(-1, 1) * C.JITTER_ZONE_EDGE * j)) for v in zones]
        zones = [z[0], max(z[1], z[0] * 1.5)]
        extra_ms = rng.uniform(-1, 1, (n_b, n_in)) * C.JITTER_ALIGN_MS * j
        trims = [float(v) for v in rng.uniform(-1, 1, n_b) * C.JITTER_TRIM_DB * j]
        drive = float(min(drive + rng.uniform(0, 1) * C.JITTER_DRIVE_DB * j, C.DRIVE_MAX_DB))
    # length: one target decay for the whole blend; longer inputs are faded to it
    preset = get_preset(prep.sound_type)
    decays = [a.decay_s for a in prep.analyses]
    length_leader = group_leader(prep.sound_type, keepmap, preset.length_group)
    target = target_decay(decays, length_leader, rng if jitter else None, j)
    slopes = decay_slopes(decays, target)
    attack_n = int(round(rs["zones"][0] * sr / 1000.0))
    specs = [InputSpec(**{**vars(s), "decay_slope_db_s": float(sl), "decay_start": s.onset + attack_n})
             for s, sl in zip(prep.specs, slopes)]
    tail_ok = [d >= C.TAIL_OWNER_MIN_RATIO * target for d in decays]
    recipe = Recipe(
        sound_type=prep.sound_type, sample_rate=sr, channels=prep.channels,
        length=prep.length, origin=prep.origin, band_edges_hz=edges, zone_edges_ms=zones,
        filter_order=rs["order"], guard_ms=rs["guard_ms"],
        crossfade_samples=crossfades_for(edges, zones, sr),
        inputs=specs, cells=[],
        lags=[[0] * n_in for _ in range(n_b)], polarities=[[1] * n_in for _ in range(n_b)],
        band_trims_db=trims, drive_db=drive, seed=int(seed), candidate=int(candidate),
        variation_amount=float(amount) if jitter else 0.0,
    )
    bands = prepare_bands(recipe, inputs, cache)
    mono = bands.mean(axis=2)
    windows = all_zone_windows(prep.length, prep.origin, zone_edges_samples(zones, sr), recipe.crossfade_samples)
    energy = cell_energies(mono, windows)
    transient = group_leader(prep.sound_type, keepmap, 2) if preset.single_attack else None
    recipe.cells = resolve_cells(keepmap, energy, preset.single_owner_bands, rng=rng if jitter else None,
                                 strengths=strengths, single_attack=preset.single_attack,
                                 tail_ok=tail_ok, transient_leader=transient)
    recipe.lags, recipe.polarities, notes = plan_alignment(
        mono, recipe.cells, energy, edges, sr, prep.origin,
        noise_bands(prep.sound_type, rs["envelope_align"]), rs["crossover"], preset.envelope_align_ms)
    notes["target_decay_s"] = round(target, 4)
    notes["length_leader"] = length_leader
    if jitter:
        weights = band_weights(recipe.cells, n_in)
        for b in range(n_b):
            owners = [i for i in range(n_in) if weights[b, i] > 0]
            if len(owners) < 2:
                continue
            dom = max(owners, key=lambda i: (weights[b, i], -i))
            for i in owners:
                if i != dom:  # the band's lead owner stays put, the others move a little
                    recipe.lags[b][i] += int(round(extra_ms[b, i] * sr / 1000.0))
    recipe.notes = notes
    return recipe


def plan_base(inputs: list[np.ndarray], names: list[str], keepmap: KeepMap, settings: Settings,
              cache: BandCache | None = None, seed: int = 0) -> Recipe:
    """Candidate 0: the user's markings, no jitter; open cells go to the input with most energy."""
    prep = prepare(inputs, names, settings)
    return plan_candidate(prep, inputs, keepmap, None, 0.0, 0, seed, cache)
