"""Per-sound-type defaults: band edges, band names and time-zone edges.

All values are starting points to tune by ear (they are editable in the UI).
Time zones are always Attack (0..edge1), Sustain (edge1..edge2), Tail (after edge2).
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE_NAMES: tuple[str, str, str] = ("Attack", "Sustain", "Tail")
GROUP_NAMES: tuple[str, str, str] = ("Low", "Mid", "High")  # the simple UI: one source per group


INF = float("inf")


@dataclass(frozen=True)
class TypePreset:
    """Everything that makes one instrument blend differently (its "profile").

    The pipeline is the same for every type (so it ports to C++ as one engine); the instrument
    knowledge lives in these fields. See docs/ENGINE_SPEC.md for what each one does.
    """
    name: str                         # sound type id, e.g. "kick"
    band_edges_hz: tuple[float, ...]  # Hz, ascending; len = number of bands - 1
    band_names: tuple[str, ...]       # one name per band, low to high
    zone_edges_ms: tuple[float, float]  # ms after the onset: Attack|Sustain, Sustain|Tail
    band_groups: tuple[int, ...]      # per band: index into GROUP_NAMES (the simple UI)
    saturation_drive_db: float = 0.0  # dB: default tanh drive in the post chain
    single_owner_below_hz: float = 200.0  # Hz: bands whose DEFAULT upper edge is <= this never mix
    noise_from_hz: float = INF        # Hz: bands whose lower edge is >= this are noise-like: they are
                                      # aligned on their envelope (no polarity flips) and skip the crossover check
    envelope_align_ms: float = 1.5    # ms: lag range of the envelope alignment
    single_attack: bool = False       # the Attack zone of every band has one owner, and open Attack cells
                                      # all go to one "transient leader" (no double hits / flams)
    length_group: int = 0             # group whose source sets the length of the blend (decay matching)
    centroid_anchor: bool = False     # anchor = energy centroid of the attack (multi-burst sounds)
    pitched: bool = False             # show the detected pitch and warn when the inputs differ
    morph: bool = False               # tonal blending: shared volume curve + spectral morph above the
                                      # single-owner low end (instead of band patches and decay matching)

    @property
    def single_owner_bands(self) -> tuple[bool, ...]:
        """Per band: True when it may have only one owner (decided on the DEFAULT edges, so band-edge
        jitter can never turn mixing on in the low end)."""
        uppers = list(self.band_edges_hz) + [INF]
        return tuple(u <= self.single_owner_below_hz for u in uppers)


CYMBAL = dict(noise_from_hz=300.0, single_attack=True, length_group=2)

PRESETS: dict[str, TypePreset] = {
    # tonal low end with a click: one owner up to 200 Hz, phase alignment, length follows Low
    "kick": TypePreset("kick", (60.0, 200.0, 1200.0, 6000.0), ("Sub", "Body", "Mids", "Top", "Air"),
                       (12.0, 180.0), (0, 0, 1, 2, 2), saturation_drive_db=1.5,
                       noise_from_hz=6000.0, length_group=0),
    # several noise bursts: anchor on the burst centroid, one transient owner, envelope alignment
    "clap": TypePreset("clap", (300.0, 1500.0, 5000.0), ("Low", "Body", "Snap", "Air"),
                       (30.0, 150.0), (0, 1, 2, 2), noise_from_hz=300.0, envelope_align_ms=3.0,
                       single_attack=True, length_group=1, centroid_anchor=True),
    "closedhat": TypePreset("closedhat", (300.0, 4000.0, 8000.0), ("Low", "Body", "Presence", "Sizzle"),
                            (8.0, 50.0), (0, 1, 2, 2), **CYMBAL),
    "openhat": TypePreset("openhat", (300.0, 4000.0, 8000.0), ("Low", "Body", "Presence", "Sizzle"),
                          (12.0, 250.0), (0, 1, 2, 2), **CYMBAL),
    "crash": TypePreset("crash", (300.0, 2500.0, 8000.0), ("Low", "Body", "Presence", "Sizzle"),
                        (20.0, 500.0), (0, 1, 2, 2), **CYMBAL),
    # tonal body, noisy top: phase alignment below 2 kHz, one transient owner, length follows Mid
    "perc": TypePreset("perc", (300.0, 2000.0, 7000.0), ("Low", "Body", "Top", "Air"),
                       (10.0, 120.0), (0, 1, 2, 2), noise_from_hz=2000.0, single_attack=True,
                       length_group=1),
    # pitched bass: one owner up to 300 Hz, phase alignment everywhere, length follows Low
    "bass": TypePreset("bass", (80.0, 250.0, 1000.0, 4000.0), ("Sub", "Body", "Mids", "Top", "Air"),
                       (15.0, 400.0), (0, 0, 1, 2, 2), saturation_drive_db=1.0,
                       single_owner_below_hz=300.0, length_group=0, pitched=True, morph=True),
    # pitched synth one-shot: phase alignment below 5 kHz, length follows Mid
    "synth": TypePreset("synth", (200.0, 1000.0, 5000.0), ("Low", "Body", "Presence", "Air"),
                        (20.0, 300.0), (0, 1, 2, 2), noise_from_hz=5000.0, length_group=1,
                        pitched=True, morph=True),
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
