"""Plain dataclasses shared by the engine. Recipe is the JSON contract with phase 2 (C++)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from layerforge.engine.constants import (
    FILTER_ORDER_DEFAULT,
    GUARD_MS_DEFAULT,
    KEEP_STRENGTH_DEFAULT,
    RECIPE_SCHEMA_VERSION,
)

OPEN, KEEP, AVOID = "open", "keep", "avoid"
CELL_STATES = (OPEN, KEEP, AVOID)


@dataclass
class Analysis:
    """Step 1 results for one input (sample indices at the output rate, before alignment)."""
    onset: int          # samples
    anchor: int         # samples
    peak: float         # linear, max |x| over all channels
    decay_s: float      # s: peak to the last point above DECAY_DB
    attack_s: float     # s: envelope 10 % -> 90 %
    rms_as: float       # linear RMS over the Attack + Sustain zones


@dataclass
class Cell:
    state: str = OPEN                     # open | keep | avoid
    strength: float = KEEP_STRENGTH_DEFAULT  # 0.25..1, used when state == keep


@dataclass
class KeepMap:
    """cells[input][band][zone]"""
    cells: list[list[list[Cell]]]

    @classmethod
    def empty(cls, n_inputs: int, n_bands: int, n_zones: int = 3) -> "KeepMap":
        return cls([[[Cell() for _ in range(n_zones)] for _ in range(n_bands)] for _ in range(n_inputs)])

    @classmethod
    def from_dict(cls, d: Any) -> "KeepMap":
        rows = d["cells"] if isinstance(d, dict) else d
        return cls([[[Cell(**c) if isinstance(c, dict) else Cell(c) for c in band] for band in inp]
                    for inp in rows])

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Settings:
    """User settings that affect the render (Advanced panel included)."""
    sound_type: str = "kick"
    sample_rate: int = 48000
    band_edges_hz: list[float] | None = None    # None = preset default
    zone_edges_ms: list[float] | None = None    # None = preset default
    filter_order: int = FILTER_ORDER_DEFAULT
    guard_ms: float = GUARD_MS_DEFAULT
    relaxed_alignment: bool | None = None       # None = preset default
    crossover_check: bool = True
    drive_db: float | None = None               # None = preset default
    trims_db: list[float] = field(default_factory=list)  # per input


@dataclass
class Owner:
    input: int       # index into Recipe.inputs
    weight: float    # mixing weight (owners of one cell sum to 1)


@dataclass
class InputSpec:
    name: str
    onset: int           # samples (before the anchor shift)
    anchor: int          # samples (before the anchor shift)
    shift: int           # samples: anchor alignment delay (>= 0)
    gain_db: float       # level match + trim
    file: str = ""       # input file relative to the project folder (for `layerforge render`)


@dataclass
class Recipe:
    """Every resolved number needed to render one variation. Lags/positions are in samples at
    `sample_rate`; render() uses nothing else apart from the input audio."""
    sound_type: str
    sample_rate: int
    channels: int
    length: int                       # samples of the internal (aligned) timeline
    origin: int                       # samples: zone time 0 (earliest aligned onset)
    band_edges_hz: list[float]
    zone_edges_ms: list[float]
    filter_order: int
    guard_ms: float
    crossfade_samples: list[list[float]]   # [band] -> [w1, w2]
    inputs: list[InputSpec]
    cells: list[list[list[Owner]]]    # [band][zone] -> owners
    lags: list[list[int]]             # [band][input] samples (per-band alignment + extras)
    polarities: list[list[int]]       # [band][input] +1 / -1
    band_trims_db: list[float]        # [band]
    drive_db: float
    seed: int = 0
    candidate: int = 0                # 0 = base
    variation_amount: float = 0.0     # percent
    schema_version: int = RECIPE_SCHEMA_VERSION
    notes: dict[str, Any] = field(default_factory=dict)  # diagnostics only, not used by render

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        d = dict(d)
        if d.get("schema_version") != RECIPE_SCHEMA_VERSION:
            raise ValueError(f"unsupported recipe schema_version {d.get('schema_version')}")
        d["inputs"] = [InputSpec(**i) for i in d["inputs"]]
        d["cells"] = [[[Owner(**o) for o in zone] for zone in band] for band in d["cells"]]
        return cls(**d)
