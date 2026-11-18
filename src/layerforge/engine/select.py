"""Variations (section 6): candidate pool, quality gate, greedy max-min selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from layerforge.engine import constants as C
from layerforge.engine.features import features, zscore
from layerforge.engine.planner import plan_candidate, prepare
from layerforge.engine.presets import get_preset
from layerforge.engine.render import BandCache, bands_key, low_end_coherence, prepare_bands, render
from layerforge.engine.types import KeepMap, Recipe, Settings


@dataclass
class Candidate:
    recipe: Recipe
    passed: bool
    reason: str = ""
    features: np.ndarray | None = None
    low_end_r: float = 1.0
    peak_db: float = 0.0           # true peak before normalisation, dBFS


@dataclass
class Variation:
    recipe: Recipe
    audio: np.ndarray              # (channels, n) final output


@dataclass
class GenerateResult:
    variations: list[Variation]
    notes: list[str] = field(default_factory=list)   # plain-English remarks for the user
    pool_size: int = 0
    passed: int = 0
    debug: dict = field(default_factory=dict)   # z-scored pool features and picked rows (tests)


def greedy_maxmin(z: np.ndarray, n: int, first: int = 0) -> list[int]:
    """Start with `first`, then repeatedly add the row whose smallest distance to the chosen
    rows is largest (ties: lowest index), until n rows are chosen."""
    chosen = [first]
    dmin = np.linalg.norm(z - z[first], axis=1)
    dmin[first] = -1.0
    while len(chosen) < min(n, z.shape[0]):
        k = int(np.argmax(dmin))
        chosen.append(k)
        dmin = np.minimum(dmin, np.linalg.norm(z - z[k], axis=1))
        dmin[chosen] = -1.0
    return chosen


def min_pairwise(z: np.ndarray) -> float:
    if z.shape[0] < 2:
        return 0.0
    d = np.linalg.norm(z[:, None, :] - z[None, :, :], axis=2)
    return float(d[np.triu_indices(z.shape[0], 1)].min())


def evaluate(recipe: Recipe, inputs: list[np.ndarray], cache: BandCache, base_peak_db: float | None,
             default_edges: tuple[float, ...]) -> Candidate:
    """Render one candidate (mono) and apply the quality gate."""
    sr = recipe.sample_rate
    res = render(recipe, inputs, cache, fast_peak=True)
    y = res.audio
    if not np.all(np.isfinite(y)) or res.norm_gain <= 0 or not np.isfinite(res.norm_gain):
        return Candidate(recipe, False, "NaN or inf")
    peak_db = C.TRUE_PEAK_DBFS - 20.0 * np.log10(res.norm_gain)
    head = y[..., :max(int(round(C.GATE_SILENCE_MS * sr / 1000.0)), 1)] / res.norm_gain
    if 20.0 * np.log10(max(float(np.sqrt(np.mean(head ** 2))), 1e-300)) < C.GATE_SILENCE_DBFS:
        return Candidate(recipe, False, "silent", peak_db=peak_db)
    if base_peak_db is not None and peak_db > base_peak_db + C.GATE_PEAK_OVER_BASE_DB:
        return Candidate(recipe, False, "peak far above the base", peak_db=peak_db)
    r = low_end_coherence(recipe, inputs, cache, C.GATE_LOW_END_HZ, C.ALIGN_WINDOW_MS)
    if r < C.GATE_LOW_END_R_MIN:
        return Candidate(recipe, False, "low end cancels", low_end_r=r, peak_db=peak_db)
    return Candidate(recipe, True, "", features(y, sr, default_edges), r, peak_db)


def generate(inputs: list[np.ndarray], names: list[str], keepmap: KeepMap, settings: Settings,
             n: int, amount: float, seed: int,
             progress: Callable[[float, str], None] | None = None) -> GenerateResult:
    """Plan a pool of P = POOL_FACTOR * n candidates (candidate 0 = base), gate them, pick n
    clearly different ones by greedy max-min over z-scored features, render those in full."""
    n = int(np.clip(n, C.MIN_VARIATIONS, C.MAX_VARIATIONS))
    report = progress or (lambda f, m: None)
    preset = get_preset(settings.sound_type)
    prep = prepare(inputs, names, settings)
    # the pool is planned and judged on mono sums (half the work for stereo, same decisions
    # every time); the chosen variations are rendered from the real inputs afterwards
    mono = [x.mean(axis=0, keepdims=True) for x in inputs] if prep.channels > 1 else inputs
    pool = C.POOL_FACTOR * n if amount > 0 else 1
    cache = BandCache(size=2)
    base = plan_candidate(prep, mono, keepmap, None, amount, 0, seed, cache)
    base_cand = evaluate(base, mono, cache, None, preset.band_edges_hz)
    shared = {bands_key(base, 1): prepare_bands(base, mono, cache)}
    for a in shared.values():
        a.setflags(write=False)
    cands: list[Candidate] = [base_cand]
    local = BandCache(size=1, shared=shared)
    for c in range(1, pool):
        rng = np.random.default_rng([int(seed), c])   # one generator per candidate
        cands.append(evaluate(plan_candidate(prep, mono, keepmap, rng, amount, c, seed, local),
                              mono, local, base_cand.peak_db, preset.band_edges_hz))
        report(0.05 + 0.8 * (c + 1) / pool, f"Trying blend {c + 1} of {pool}")
    notes: list[str] = []
    ok = [k for k, cd in enumerate(cands) if cd.passed]
    # drop exact duplicates (same features), keeping the first
    uniq: list[int] = []
    for k in ok:
        if all(np.max(np.abs(cands[k].features - cands[u].features)) > C.DUPLICATE_TOL for u in uniq):
            uniq.append(k)
    if amount <= 0:
        notes.append("Variation amount is 0 %, so every blend would be the same: you get only the base.")
    elif len(uniq) < n:
        notes.append(f"Only {len(uniq)} of {pool} tried blends passed the quality check, "
                     f"so you get {len(uniq)} instead of {n}.")
    if not uniq:
        return GenerateResult([], notes or ["No blend passed the quality check."], pool, 0)
    z = zscore(np.stack([cands[k].features for k in uniq]))
    rows = greedy_maxmin(z, n, first=0)
    picked = [uniq[i] for i in rows]
    out = []
    for i, k in enumerate(picked):
        report(0.85 + 0.15 * i / len(picked), f"Rendering variation {i + 1} of {len(picked)}")
        rec = cands[k].recipe
        rec.notes["low_end_r"] = round(cands[k].low_end_r, 3)
        y = render(rec, inputs).audio if prep.channels > 1 else render(rec, inputs, cache).audio
        if not np.all(np.isfinite(y)):
            continue
        out.append(Variation(rec, y))
    return GenerateResult(out, notes, pool, len(ok), {"z": z, "picked_rows": rows, "unique": uniq})
