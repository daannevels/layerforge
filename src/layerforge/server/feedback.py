"""Feedback on generated variations: one JSON line per remark in <workspace>/feedback.jsonl.

No audio is stored: only the recipe (all numbers of the blend), a few measurements of the inputs
and of the output, and the user's rating, tags and text (a few kB per line).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

from layerforge import __version__
from layerforge.audio_io import AudioError, read_audio
from layerforge.engine import analysis as A
from layerforge.engine.features import feature_names, features
from layerforge.engine.presets import PRESETS

FEEDBACK_FILE = "feedback.jsonl"
MAX_TEXT = 2000   # characters
TAGS = (
    "good", "two_layers", "tail_wrong", "length_wrong", "low_end_thin", "low_end_boomy",
    "attack_weak", "attack_doubled", "click_artifact", "phasey", "too_dull", "too_harsh",
    "pitch_clash", "not_my_direction",
)
_lock = threading.Lock()


class FeedbackError(ValueError):
    pass


def _measure(x: np.ndarray, sr: int, pitched: bool) -> dict:
    onset = A.find_onset(x, sr)
    m = A.to_mono(x)
    w = m[onset:onset + int(0.15 * sr)]
    spec = np.abs(np.fft.rfft(w * np.hanning(w.size))) if w.size > 16 else np.zeros(1)
    freqs = np.fft.rfftfreq(w.size, 1.0 / sr) if w.size > 16 else np.zeros(1)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(w ** 2))) if w.size else 0.0
    return {
        "seconds": round(x.shape[-1] / sr, 4),
        "channels": int(x.shape[0]),
        "sample_rate": sr,
        "decay_s": round(A.decay_time(x, sr), 4),
        "attack_ms": round(1000 * A.attack_time(x, sr), 3),
        "centroid_hz": round(float(np.dot(freqs, spec) / spec.sum()), 1) if spec.sum() > 0 else 0.0,
        "peak_db": round(20 * np.log10(max(peak, 1e-12)), 2),
        "rms150_db": round(20 * np.log10(max(rms, 1e-12)), 2),
        "f0_hz": round(A.estimate_f0(x, sr, onset), 2) if pitched else 0.0,
    }


def build_record(wav: Path, rating: int, tags: list[str], text: str) -> dict:
    """Everything worth keeping about one variation, without audio."""
    if rating not in (-1, 0, 1):
        raise FeedbackError("rating must be -1, 0 or 1")
    if not isinstance(tags, list) or any(t not in TAGS for t in tags):
        raise FeedbackError("unknown feedback tag")
    if not isinstance(text, str):
        raise FeedbackError("text must be a string")
    project_dir = wav.parent.parent
    recipe = json.loads(wav.with_suffix(".json").read_text())
    project = json.loads((project_dir / "project.json").read_text()) \
        if (project_dir / "project.json").is_file() else {}
    sound_type = recipe.get("sound_type", "")
    pitched = PRESETS[sound_type].pitched if sound_type in PRESETS else False
    inputs = []
    for spec in recipe.get("inputs", []):
        f = project_dir / spec.get("file", "")
        try:
            x, sr = read_audio(f)
            inputs.append({"name": spec.get("name"), **_measure(x, sr, pitched)})
        except (AudioError, OSError, ValueError):
            inputs.append({"name": spec.get("name"), "missing": True})
    y, sr = read_audio(wav)
    out = _measure(y, sr, pitched)
    if sound_type in PRESETS:
        names = feature_names(PRESETS[sound_type].band_names)
        out["features"] = {k: round(float(v), 4)
                           for k, v in zip(names, features(y, sr, PRESETS[sound_type].band_edges_hz))}
    return {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version": __version__,
        "project": project_dir.name,
        "variation": wav.stem,
        "sound_type": sound_type,
        "rating": rating,
        "tags": sorted(set(tags)),
        "text": text.strip()[:MAX_TEXT],
        "settings": {k: project.get(k) for k in ("groups", "variations", "variation_amount", "seed")},
        "inputs": inputs,
        "output": out,
        "recipe": recipe,
    }


def append(root: Path, record: dict) -> int:
    """Append one line; returns the number of lines in the file."""
    path = root / FEEDBACK_FILE
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        return count(root)


def count(root: Path) -> int:
    path = root / FEEDBACK_FILE
    if not path.is_file():
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)
