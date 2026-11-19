"""Workspace folders: ~/Layerforge (or $LAYERFORGE_HOME). File names are made by the server only."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path


def workspace_root(root: Path | None = None) -> Path:
    """The workspace folder (created with its sub-folders if needed)."""
    if root is None:
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


KEEP_PROJECTS = 20   # count: newest project folders kept; older ones are deleted (Favourites are never touched)
PROJECT_RE = re.compile(r"^\d{8}-\d{6}_[a-z]+(-\d+)?$")


def cleanup_projects(root: Path, keep: int = KEEP_PROJECTS) -> list[str]:
    """Delete all but the `keep` newest project folders in Projects/. Only folders that look exactly
    like ones Layerforge made (name pattern + project.json or an inputs/ folder) are ever removed.
    Returns the names of the removed folders."""
    projects = root / "Projects"
    if not projects.is_dir():
        return []
    ours = sorted((p for p in projects.iterdir()
                   if p.is_dir() and not p.is_symlink() and PROJECT_RE.match(p.name)
                   and ((p / "project.json").is_file() or (p / "inputs").is_dir())),
                  key=lambda p: p.name)
    removed = []
    for p in ours[:max(len(ours) - keep, 0)]:
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p.name)
    return removed
