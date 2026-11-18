"""Background generate jobs (one thread each); the page polls their status."""

from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from layerforge import __version__
from layerforge.audio_io import default_output_rate, prepare_inputs, read_audio, wav_bytes
from layerforge.engine import constants as C
from layerforge.engine.planner import plan_base
from layerforge.engine.presets import PRESETS
from layerforge.engine.render import BandCache, render, render_simple_stack
from layerforge.engine.types import CELL_STATES, Cell, KeepMap, Settings


@dataclass
class Upload:
    name: str          # original file name, for display and project.json only
    ext: str           # validated extension, e.g. ".wav"
    data: bytes
    sample_rate: int
    channels: int
    frames: int


@dataclass
class Job:
    id: str
    status: str = "running"            # running | done | error
    progress: float = 0.0
    message: str = "Starting..."
    result: dict[str, Any] | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"id": self.id, "status": self.status, "progress": self.progress,
                    "message": self.message, "result": self.result, "error": self.error}


class RequestError(ValueError):
    """Bad generate request; the message is shown to the user."""


def parse_request(body: dict, uploads: dict[str, Upload]) -> tuple[Settings, list[Upload], KeepMap]:
    sound_type = body.get("sound_type")
    if sound_type not in PRESETS:
        raise RequestError("unknown sound type")
    items = body.get("inputs")
    if not isinstance(items, list) or not C.MIN_INPUTS <= len(items) <= C.MAX_INPUTS:
        raise RequestError(f"load {C.MIN_INPUTS} to {C.MAX_INPUTS} samples")
    ups, trims = [], []
    for it in items:
        up = uploads.get(str(it.get("id"))) if isinstance(it, dict) else None
        if up is None:
            raise RequestError("a sample is missing; load it again")
        ups.append(up)
        trims.append(float(np.clip(float(it.get("trim_db", 0.0)), -C.TRIM_LIMIT_DB, C.TRIM_LIMIT_DB)))
    rate = body.get("sample_rate", "auto")
    if rate == "auto":
        rate = default_output_rate([u.sample_rate for u in ups])
    if rate not in C.OUTPUT_RATES:
        raise RequestError("output rate must be 44100 or 48000")
    n_b = len(PRESETS[sound_type].band_names)
    raw = body.get("keepmap")
    try:
        cells = [[[Cell(state=str(c[0]), strength=float(c[1])) for c in raw[i][b]] for b in range(n_b)]
                 for i in range(len(ups))]
    except (TypeError, IndexError, KeyError, ValueError):
        raise RequestError("the keep-map does not match the samples and bands") from None
    for inp in cells:
        for band in inp:
            if len(band) != 3:
                raise RequestError("the keep-map needs 3 zones per band")
            for c in band:
                if c.state not in CELL_STATES:
                    raise RequestError("unknown keep-map state")
                c.strength = float(np.clip(c.strength, C.KEEP_STRENGTH_MIN, 1.0))
    settings = Settings(sound_type=sound_type, sample_rate=int(rate), trims_db=trims)
    return settings, ups, KeepMap(cells)


def run_generate(job: Job, settings: Settings, ups: list[Upload], keepmap: KeepMap, root: Path,
                 make_project: Callable[[Path, str], Path],
                 register_file: Callable[[Path], str]) -> None:
    """Job body: decode, plan the base variation, render, write the project folder."""
    try:
        t0 = time.perf_counter()
        job.update(progress=0.05, message="Reading samples...")
        decoded = [read_audio(u.data) for u in ups]
        inputs = prepare_inputs(decoded, settings.sample_rate)
        proj = make_project(root, settings.sound_type)
        for i, u in enumerate(ups, 1):
            (proj / "inputs" / f"input{i}{u.ext}").write_bytes(u.data)
        job.update(progress=0.2, message="Planning...")
        cache = BandCache()
        names = [u.name for u in ups]
        recipe = plan_base(inputs, names, keepmap, settings, cache=cache)
        job.update(progress=0.6, message="Rendering...")
        res = render(recipe, inputs, cache=cache)
        sr = settings.sample_rate
        v_wav = proj / "variations" / "v01.wav"
        v_wav.write_bytes(wav_bytes(res.audio, sr))
        (proj / "variations" / "v01.json").write_text(json.dumps(recipe.to_dict(), indent=1))
        job.update(progress=0.85, message="Rendering the simple stack...")
        stack = render_simple_stack(recipe, inputs)
        s_wav = proj / "simple_stack.wav"
        s_wav.write_bytes(wav_bytes(stack, sr))
        project = {
            "app_version": __version__,
            "sound_type": settings.sound_type,
            "sample_rate": sr,
            "seed": recipe.seed,
            "inputs": [{"file": f"inputs/input{i}{u.ext}", "name": u.name, "trim_db": t}
                       for i, (u, t) in enumerate(zip(ups, settings.trims_db), 1)],
            "keepmap": keepmap.to_dict(),
        }
        (proj / "project.json").write_text(json.dumps(project, indent=1))
        job.update(status="done", progress=1.0,
                   message=f"Done in {time.perf_counter() - t0:.1f} s",
                   result={"project": proj.name,
                           "variations": [{"name": "v01", "file": register_file(v_wav),
                                           "seconds": res.audio.shape[1] / sr}],
                           "stack": register_file(s_wav), "sample_rate": sr})
    except Exception as exc:  # report to the page in plain words
        job.update(status="error", error=f"{type(exc).__name__}: {exc}", message="Generate failed")
        traceback.print_exc()


def new_job_id() -> str:
    return uuid.uuid4().hex
