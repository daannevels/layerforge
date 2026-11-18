"""Keep-map ownership (section 4) and cell assembly (step 8)."""

from __future__ import annotations

import numpy as np

from layerforge.engine.constants import LOW_END_SINGLE_OWNER_HZ
from layerforge.engine.types import AVOID, KEEP, KeepMap, Owner
from layerforge.engine.windows import zone_windows


def band_upper_edge(edges_hz: list[float], b: int) -> float:
    return float(edges_hz[b]) if b < len(edges_hz) else float("inf")


def band_lower_edge(edges_hz: list[float], b: int) -> float:
    return float(edges_hz[b - 1]) if b > 0 else 0.0


def is_low_end(edges_hz: list[float], b: int) -> bool:
    """Bands whose upper edge is <= LOW_END_SINGLE_OWNER_HZ get exactly one owner per cell."""
    return band_upper_edge(edges_hz, b) <= LOW_END_SINGLE_OWNER_HZ


def resolve_cells(keepmap: KeepMap, energy: np.ndarray, edges_hz: list[float],
                  rng: np.random.Generator | None = None,
                  strengths: np.ndarray | None = None) -> list[list[list[Owner]]]:
    """Owners per (band, zone).

    energy: (inputs, bands, zones) energy of each input in each cell (after level match).
    rng None = base variation (open cells go to the non-avoided input with the most energy);
    otherwise open cells go to a random non-avoided input, weighted by energy.
    strengths: optional (inputs, bands, zones) override of the keep strengths (jitter).
    Ties are broken by more energy, then by the lower input index.
    """
    n_in, n_b, n_z = energy.shape
    cells: list[list[list[Owner]]] = []
    for b in range(n_b):
        row = []
        for z in range(n_z):
            keepers = []
            for i in range(n_in):
                c = keepmap.cells[i][b][z]
                if c.state == KEEP:
                    s = float(strengths[i, b, z]) if strengths is not None else float(c.strength)
                    keepers.append((i, max(s, 1e-6)))
            if keepers:
                if is_low_end(edges_hz, b):
                    i = max(keepers, key=lambda t: (t[1], energy[t[0], b, z], -t[0]))[0]
                    row.append([Owner(i, 1.0)])
                else:
                    total = sum(s for _, s in keepers)
                    row.append([Owner(i, s / total) for i, s in keepers])
                continue
            cand = [i for i in range(n_in) if keepmap.cells[i][b][z].state != AVOID]
            if not cand:
                row.append([])
                continue
            e = np.array([energy[i, b, z] for i in cand])
            if rng is None:
                i = max(cand, key=lambda k: (energy[k, b, z], -k))
            else:
                p = e / e.sum() if e.sum() > 0 else np.full(len(cand), 1.0 / len(cand))
                i = cand[int(rng.choice(len(cand), p=p))]
            row.append([Owner(i, 1.0)])
        cells.append(row)
    return cells


def band_weights(cells: list[list[list[Owner]]], n_inputs: int) -> np.ndarray:
    """(bands, inputs) total owner weight of each input over the zones of each band."""
    w = np.zeros((len(cells), n_inputs))
    for b, row in enumerate(cells):
        for owners in row:
            for o in owners:
                w[b, o.input] += o.weight
    return w


def assemble(band_sigs: np.ndarray, cells: list[list[list[Owner]]], windows: np.ndarray,
             band_gains: np.ndarray) -> np.ndarray:
    """Step 8. band_sigs: (inputs, bands, channels, n), already guarded, lag-shifted and
    polarity-applied. windows: (bands, 3, n). band_gains: (bands,) linear trims.
    Returns (channels, n): sum over bands, zones and owners of signal x window x weight x trim."""
    _, n_b, n_c, n = band_sigs.shape
    out = np.zeros((n_c, n))
    for b in range(n_b):
        acc = np.zeros((n_c, n))
        for z, owners in enumerate(cells[b]):
            if not owners:
                continue
            mix = np.zeros((n_c, n))
            for o in owners:
                mix += o.weight * band_sigs[o.input, b]
            acc += windows[b, z] * mix
        out += band_gains[b] * acc
    return out


def all_zone_windows(n: int, origin: int, zone_edges_samples: tuple[float, float],
                     crossfades: list[list[float]]) -> np.ndarray:
    """(bands, 3, n) zone windows using the resolved crossfade widths per band."""
    e1, e2 = zone_edges_samples
    return np.stack([zone_windows(n, origin, e1, e2, w1, w2) for w1, w2 in crossfades])
