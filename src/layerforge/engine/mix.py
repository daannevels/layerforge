"""Keep-map ownership (section 4) and cell assembly (step 8)."""

from __future__ import annotations

import numpy as np

from layerforge.engine.types import AVOID, KEEP, KeepMap, Owner
from layerforge.engine.windows import zone_windows


def band_upper_edge(edges_hz: list[float], b: int) -> float:
    return float(edges_hz[b]) if b < len(edges_hz) else float("inf")


def band_lower_edge(edges_hz: list[float], b: int) -> float:
    return float(edges_hz[b - 1]) if b > 0 else 0.0


def _pick(cands: list[int], score: np.ndarray, rng: np.random.Generator | None) -> int:
    """Base (rng None): the candidate with the highest score (ties: lower index).
    Otherwise a random candidate weighted by score (uniform when all scores are 0)."""
    if rng is None:
        return max(cands, key=lambda k: (score[k], -k))
    e = np.array([max(float(score[k]), 0.0) for k in cands])
    p = e / e.sum() if e.sum() > 0 else np.full(len(cands), 1.0 / len(cands))
    return cands[int(rng.choice(len(cands), p=p))]


def resolve_cells(keepmap: KeepMap, energy: np.ndarray, single_owner: list[bool] | tuple[bool, ...],
                  rng: np.random.Generator | None = None,
                  strengths: np.ndarray | None = None,
                  single_attack: bool = False,
                  tail_ok: list[bool] | None = None,
                  transient_leader: int | None = None) -> list[list[list[Owner]]]:
    """Owners per (band, zone) (section 4 plus the instrument profile rules).

    energy: (inputs, bands, zones) energy of each input in each cell (after level match).
    single_owner[b]: band b never mixes keepers (the strongest keeper wins).
    rng None = base variation (open cells go to the non-avoided input with the most energy);
    otherwise open cells go to a random non-avoided input, weighted by energy.
    strengths: optional (inputs, bands, zones) override of the keep strengths (jitter).
    single_attack: Attack cells never mix, and open Attack cells all go to one transient leader
    (`transient_leader`, or the input with the most Attack energy / a random one weighted by it).
    tail_ok[i]: False = input i is too short to own an open Tail cell.
    Continuity: in every band an open Tail cell follows the owner of the Sustain cell, so the
    tail of a band is never taken from a different sample than its body.
    """
    n_in, n_b, n_z = energy.shape
    avoid = [[[keepmap.cells[i][b][z].state == AVOID for z in range(n_z)] for b in range(n_b)]
             for i in range(n_in)]
    ok_tail = tail_ok if tail_ok is not None else [True] * n_in

    def keepers(b: int, z: int) -> list[tuple[int, float]]:
        out = []
        for i in range(n_in):
            c = keepmap.cells[i][b][z]
            if c.state == KEEP:
                s = float(strengths[i, b, z]) if strengths is not None else float(c.strength)
                out.append((i, max(s, 1e-6)))
        return out

    def kept(b: int, z: int, single: bool) -> list[Owner]:
        ks = keepers(b, z)
        if single:
            return [Owner(max(ks, key=lambda t: (t[1], energy[t[0], b, z], -t[0]))[0], 1.0)]
        total = sum(s for _, s in ks)
        return [Owner(i, s / total) for i, s in ks]

    leader = transient_leader
    if single_attack and leader is None:
        cands = [i for i in range(n_in) if not all(avoid[i][b][0] for b in range(n_b))]
        if cands:
            leader = _pick(cands, energy[:, :, 0].sum(axis=1), rng)

    cells: list[list[list[Owner]]] = []
    for b in range(n_b):
        row: list[list[Owner]] = []
        for z in range(n_z):
            single = single_owner[b] or (single_attack and z == 0)
            if keepers(b, z):
                row.append(kept(b, z, single))
                continue
            cands = [i for i in range(n_in) if not avoid[i][b][z]]
            if z == n_z - 1:
                long_enough = [i for i in cands if ok_tail[i]]
                cands = long_enough or cands
                prev = row[z - 1] if z > 0 else []
                if prev:  # continuity: the tail follows the body of this band
                    lead = max(prev, key=lambda o: (o.weight, -o.input)).input
                    if lead in cands:
                        row.append([Owner(lead, 1.0)])
                        continue
            if not cands:
                row.append([])
                continue
            if single_attack and z == 0 and leader is not None and leader in cands:
                row.append([Owner(leader, 1.0)])
                continue
            if z == 1 and n_z > 2 and not keepers(b, n_z - 1):
                # Sustain and Tail are chosen together (energy of both), so the body is picked by an
                # input that can also carry the tail
                both = [i for i in cands if ok_tail[i] and not avoid[i][b][n_z - 1]] or cands
                row.append([Owner(_pick(both, energy[:, b, 1] + energy[:, b, 2], rng), 1.0)])
                continue
            row.append([Owner(_pick(cands, energy[:, b, z], rng), 1.0)])
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
