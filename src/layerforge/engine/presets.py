"""Per-sound-type defaults: band edges, band names and time-zone edges.

All values are starting points to tune by ear (they are editable in the UI).
Time zones are always Attack (0..edge1), Sustain (edge1..edge2), Tail (after edge2).
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE_NAMES: tuple[str, str, str] = ("Attack", "Sustain", "Tail")
GROUP_NAMES: tuple[str, str, str] = ("Low", "Mid", "High")  # the simple UI: one source per group


@dataclass(frozen=True)
class TypePreset:
    name: str                         # sound type id, e.g. "kick"
    band_edges_hz: tuple[float, ...]  # Hz, ascending; len = number of bands - 1
    band_names: tuple[str, ...]       # one name per band, low to high
    zone_edges_ms: tuple[float, float]  # ms after the anchor: Attack|Sustain, Sustain|Tail
    saturation_drive_db: float = 0.0  # dB: default tanh drive in the post chain
    relaxed_alignment: bool = False   # skip per-band alignment in bands >= 2 kHz
    band_groups: tuple[int, ...] = (0, 1, 2, 2)  # per band: index into GROUP_NAMES


PRESETS: dict[str, TypePreset] = {
    "kick": TypePreset("kick", (60.0, 200.0, 1200.0, 6000.0),
                       ("Sub", "Body", "Mids", "Top", "Air"), (12.0, 180.0),
                       saturation_drive_db=1.5, band_groups=(0, 0, 1, 2, 2)),
    "clap": TypePreset("clap", (300.0, 1500.0, 5000.0),
                       ("Low", "Body", "Snap", "Air"), (30.0, 150.0)),
    "closedhat": TypePreset("closedhat", (300.0, 4000.0, 8000.0),
                            ("Low", "Body", "Presence", "Sizzle"), (8.0, 50.0),
                            relaxed_alignment=True),
    "openhat": TypePreset("openhat", (300.0, 4000.0, 8000.0),
                          ("Low", "Body", "Presence", "Sizzle"), (12.0, 250.0),
                          relaxed_alignment=True),
    "crash": TypePreset("crash", (300.0, 2500.0, 8000.0),
                        ("Low", "Body", "Presence", "Sizzle"), (20.0, 500.0),
                        relaxed_alignment=True),
    "perc": TypePreset("perc", (300.0, 2000.0, 7000.0),
                       ("Low", "Body", "Top", "Air"), (10.0, 120.0)),
}

SOUND_TYPES: tuple[str, ...] = tuple(PRESETS)


def get_preset(sound_type: str) -> TypePreset:
    """Return the preset for a sound type; raises KeyError with the valid names."""
    try:
        return PRESETS[sound_type]
    except KeyError:
        raise KeyError(f"unknown sound type {sound_type!r}; choose one of {', '.join(SOUND_TYPES)}") from None


def group_keepmap(sound_type: str, n_inputs: int, choices: list[int | None]):
    """Keep-map from the simple UI: choices[g] = input index that leads group g (Low, Mid, High),
    or None (free: Layerforge decides per variation). A chosen input gets keep (100 %) on every
    zone of every band in that group; everything else stays open."""
    from layerforge.engine.types import KEEP, Cell, KeepMap

    p = get_preset(sound_type)
    km = KeepMap.empty(n_inputs, len(p.band_names))
    for b, g in enumerate(p.band_groups):
        src = choices[g] if g < len(choices) else None
        if src is not None:
            if not 0 <= src < n_inputs:
                raise ValueError("group source out of range")
            km.cells[src][b] = [Cell(KEEP, 1.0) for _ in range(3)]
    return km
