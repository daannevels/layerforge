"""Workspace folders: ~/Layerforge (or $LAYERFORGE_HOME). File names are made by the server only."""

from __future__ import annotations

import os
import time
from pathlib import Path


def workspace_root() -> Path:
    env = os.environ.get("LAYERFORGE_HOME")
    root = Path(env).expanduser() if env else Path.home() / "Layerforge"
    for sub in ("Projects", "Favourites"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def new_project_dir(root: Path, sound_type: str) -> Path:
    """Projects/<YYYYmmdd-HHMMSS>_<type>/ with inputs/ and variations/ (suffix -2, -3 on clashes).
    sound_type must already be validated against the preset list."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = root / "Projects" / f"{stamp}_{sound_type}"
    path, k = base, 2
    while path.exists():
        path = base.with_name(f"{base.name}-{k}")
        k += 1
    (path / "inputs").mkdir(parents=True)
    (path / "variations").mkdir()
    return path
