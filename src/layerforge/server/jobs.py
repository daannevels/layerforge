"""Background generate jobs (one thread each); the page polls their status."""

from __future__ import annotations

import json
import secrets
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
from layerforge.engine.presets import GROUP_NAMES, PRESETS, group_keepmap
from layerforge.engine.render import render_simple_stack
from layerforge.engine.select import generate
from layerforge.engine.types import Settings


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


@dataclass
class GenerateRequest:
    settings: Settings
    uploads: list[Upload]
    groups: list[int | None]      # Low, Mid, High: input index or None (free)
    variations: int
    amount: float                 # percent
    seed: int


def parse_request(body: dict, uploads: dict[str, Upload]) -> GenerateRequest:
    if not isinstance(body, dict):
        raise RequestError("bad request")
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
    groups_raw = body.get("groups", [None] * len(GROUP_NAMES))
    if not isinstance(groups_raw, list) or len(groups_raw) != len(GROUP_NAMES):
        raise RequestError("choose a source (or Free) for Low, Mid and High")
    groups: list[int | None] = []
    for g in groups_raw:
        if g is None:
            groups.append(None)
        elif isinstance(g, int) and not isinstance(g, bool) and 0 <= g < len(ups):
            groups.append(g)
        else:
            raise RequestError("a group points to a sample that is not loaded")
    n = body.get("variations", C.DEFAULT_VARIATIONS)
    amount = body.get("amount", C.DEFAULT_VARIATION_AMOUNT)
    if not isinstance(n, int) or not C.MIN_VARIATIONS <= n <= C.MAX_VARIATIONS:
        raise RequestError(f"variations must be {C.MIN_VARIATIONS} to {C.MAX_VARIATIONS}")
    if not isinstance(amount, (int, float)) or not 0 <= amount <= 100:
        raise RequestError("variation amount must be 0 to 100")
    seed = body.get("seed")
    if seed is None:
        seed = secrets.randbelow(2 ** 31)
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2 ** 63:
        raise RequestError("seed must be a whole number")
    settings = Settings(sound_type=sound_type, sample_rate=int(rate), trims_db=trims)
    return GenerateRequest(settings, ups, groups, n, float(amount), seed)


def run_generate(job: Job, req: GenerateRequest, root: Path,
                 make_project: Callable[[Path, str], Path],
                 register_file: Callable[[Path], str]) -> None:
    """Job body: decode, plan and pick the variations, write the project folder."""
    try:
        t0 = time.perf_counter()
        settings, ups = req.settings, req.uploads
        job.update(progress=0.02, message="Reading samples...")
        decoded = [read_audio(u.data) for u in ups]
        inputs = prepare_inputs(decoded, settings.sample_rate)
        proj = make_project(root, settings.sound_type)
        files = [f"inputs/input{i}{u.ext}" for i, u in enumerate(ups, 1)]
        for f, u in zip(files, ups):
            (proj / f).write_bytes(u.data)
        keepmap = group_keepmap(settings.sound_type, len(ups), req.groups)
        names = [u.name for u in ups]
        result = generate(inputs, names, keepmap, settings, req.variations, req.amount, req.seed,
                          progress=lambda f, m: job.update(progress=0.02 + 0.9 * f, message=m))
        sr = settings.sample_rate
        cards = []
        for k, v in enumerate(result.variations, 1):
            for spec, f in zip(v.recipe.inputs, files):
                spec.file = f
            name = f"v{k:02d}"
            wav = proj / "variations" / f"{name}.wav"
            wav.write_bytes(wav_bytes(v.audio, sr))
            wav.with_suffix(".json").write_text(json.dumps(v.recipe.to_dict(), indent=1))
            cards.append({"name": name, "file": register_file(wav), "seconds": v.audio.shape[1] / sr,
                          "candidate": v.recipe.candidate})
        job.update(progress=0.95, message="Rendering the simple stack...")
        stack_file = None
        if result.variations:
            stack = render_simple_stack(result.variations[0].recipe, inputs)
            s_wav = proj / "simple_stack.wav"
            s_wav.write_bytes(wav_bytes(stack, sr))
            stack_file = register_file(s_wav)
        project = {
            "app_version": __version__,
            "sound_type": settings.sound_type,
            "sample_rate": sr,
            "seed": req.seed,
            "variations": req.variations,
            "variation_amount": req.amount,
            "groups": {g: (None if c is None else c + 1) for g, c in zip(GROUP_NAMES, req.groups)},
            "inputs": [{"file": f, "name": u.name, "trim_db": t}
                       for f, u, t in zip(files, ups, settings.trims_db)],
            "keepmap": keepmap.to_dict(),
            "notes": result.notes,
        }
        (proj / "project.json").write_text(json.dumps(project, indent=1))
        msg = f"{len(cards)} variation{'s' if len(cards) != 1 else ''} in {time.perf_counter() - t0:.1f} s"
        job.update(status="done", progress=1.0, message=msg,
                   result={"project": proj.name, "variations": cards, "stack": stack_file,
                           "sample_rate": sr, "seed": req.seed, "notes": result.notes})
    except Exception as exc:  # report to the page in plain words
        job.update(status="error", error=f"{type(exc).__name__}: {exc}", message="Generate failed")
        traceback.print_exc()


def new_job_id() -> str:
    return uuid.uuid4().hex
