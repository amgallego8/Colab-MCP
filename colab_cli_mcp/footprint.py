"""Keep the footprint on the user's computer small, and clean up after ourselves.

Everything this server writes locally lives under the working directory:
  .colab-mcp/tmp/      zips in transit, notebooks fetched from Drive  -> swept when older than 1 hour
  .colab-mcp/images/   plots saved while running notebook cells        -> swept after COLAB_MCP_IMAGE_DAYS (default 2)
  .colab-mcp/drive/    working copies of Drive notebooks (may hold unsaved edits) -> swept after COLAB_MCP_DRIVE_COPY_HOURS (default 24; 0 = never)
  .colab-mcp-trash/    notebooks the user asked to delete locally      -> swept after COLAB_MCP_TRASH_DAYS (default 7; 0 = never)

Only files inside those three folders are ever removed, symlinks are never followed, and nothing
else in the working directory is touched. Heavy data belongs on the VM disk or in Drive, not here.

Guards (all overridable by environment variable):
  COLAB_MCP_MIN_FREE_GB      refuse downloads that would leave less than this free on the disk (default 5)
  COLAB_MCP_WORKDIR_MAX_GB   refuse downloads that would take the working directory above this (default 20)
"""

import os
import shutil
import time
from pathlib import Path

from .core import ToolError, workdir

STATE_DIR = ".colab-mcp"
TMP_DIR = f"{STATE_DIR}/tmp"
IMAGE_DIR = f"{STATE_DIR}/images"
DRIVE_DIR = f"{STATE_DIR}/drive"
TRASH_DIR = ".colab-mcp-trash"
TMP_MAX_AGE_S = 3600
GB = 1024**3


def _num(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value >= 0 else default


def min_free_bytes() -> int:
    return int(_num("COLAB_MCP_MIN_FREE_GB", 5) * GB)


def workdir_max_bytes() -> int:
    return int(_num("COLAB_MCP_WORKDIR_MAX_GB", 20) * GB)


def image_days() -> float:
    return _num("COLAB_MCP_IMAGE_DAYS", 2)


def drive_copy_hours() -> float:
    return _num("COLAB_MCP_DRIVE_COPY_HOURS", 24)


def trash_days() -> float:
    return _num("COLAB_MCP_TRASH_DAYS", 7)


def _managed(rel: str) -> Path:
    return workdir() / rel


def _scan(folder: Path, max_entries: int = 200_000):
    """Yield (path, lstat) for regular files under folder. Never follows symlinks."""
    if folder.is_symlink() or not folder.is_dir():
        return
    seen = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        for f in files:
            seen += 1
            if seen > max_entries:
                return
            p = Path(root) / f
            try:
                yield p, p.lstat()
            except OSError:
                continue


def dir_bytes(folder: Path) -> int:
    return sum(st.st_size for _, st in _scan(folder))


def _sweep_folder(folder: Path, max_age_s: float | None) -> tuple[int, int]:
    """Remove files older than max_age_s (all of them when None). Returns (files, bytes)."""
    removed = freed = 0
    now = time.time()
    for p, st in list(_scan(folder)):
        if max_age_s is not None and now - st.st_mtime < max_age_s:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        removed += 1
        freed += st.st_size
    # drop empty sub-folders, deepest first, never the folder itself
    if folder.is_dir() and not folder.is_symlink():
        for root, dirs, _ in os.walk(folder, topdown=False, followlinks=False):
            for d in dirs:
                q = Path(root) / d
                try:
                    if not q.is_symlink():
                        q.rmdir()
                except OSError:
                    pass
    return removed, freed


def sweep(*, everything: bool = False) -> dict:
    """Delete stale temp files, old plots and old trashed notebooks. `everything` also removes fresh temp files and plots."""
    out = {"tmp": (0, 0), "images": (0, 0), "drive": (0, 0), "trash": (0, 0)}
    out["tmp"] = _sweep_folder(_managed(TMP_DIR), None if everything else TMP_MAX_AGE_S)
    out["images"] = _sweep_folder(_managed(IMAGE_DIR), None if everything else image_days() * 86400)
    # Working copies of Drive notebooks may hold unsaved edits: only ever swept by age, never by "everything".
    out["drive"] = _sweep_folder(_managed(DRIVE_DIR), drive_copy_hours() * 3600) if drive_copy_hours() > 0 else (0, 0)
    td = trash_days()
    if td > 0:
        out["trash"] = _sweep_folder(_managed(TRASH_DIR), td * 86400)
    return out


def sweep_quietly() -> None:
    """Called at server start: cleaning must never prevent the server from starting."""
    try:
        sweep()
    except Exception:  # noqa: BLE001
        pass


def free_bytes() -> int:
    root = workdir()
    return shutil.disk_usage(root if root.exists() else root.parent).free


def check_space(needed: int, what: str) -> None:
    """Refuse an operation that would eat into the reserve of free disk or the working-directory budget."""
    free = free_bytes()
    if free - needed < min_free_bytes():
        raise ToolError(
            f"Not enough free disk for {what}: needs about {needed / GB:.2f} GB, {free / GB:.2f} GB free, "
            f"and {min_free_bytes() / GB:.1f} GB must stay free (COLAB_MCP_MIN_FREE_GB). "
            "Keep heavy data on the VM disk or in Drive and download only the result."
        )
    cap = workdir_max_bytes()
    if cap:
        used = dir_bytes(workdir())
        if used + needed > cap:
            raise ToolError(
                f"The working directory would exceed its budget for {what}: {used / GB:.2f} GB used + "
                f"{needed / GB:.2f} GB > {cap / GB:.1f} GB (COLAB_MCP_WORKDIR_MAX_GB). "
                "Delete what you no longer need, or keep it on the VM disk or in Drive."
            )


def report() -> str:
    parts = []
    for label, rel in (("temp", TMP_DIR), ("plots", IMAGE_DIR), ("drive copies", DRIVE_DIR), ("local trash", TRASH_DIR)):
        folder = _managed(rel)
        files = list(_scan(folder))
        parts.append(f"{label:11}: {len(files)} file(s), {sum(st.st_size for _, st in files) / 1024 / 1024:.1f} MB  ({rel})")
    parts.append(f"free disk   : {free_bytes() / GB:.1f} GB (reserve {min_free_bytes() / GB:.1f} GB)")
    parts.append(f"workdir     : {dir_bytes(workdir()) / GB:.2f} GB of {workdir_max_bytes() / GB:.1f} GB budget")
    return "\n".join(parts)


def describe(result: dict) -> str:
    files = sum(v[0] for v in result.values())
    size = sum(v[1] for v in result.values())
    return f"Removed {files} file(s), {size / 1024 / 1024:.1f} MB (temp {result['tmp'][0]}, plots {result['images'][0]}, old Drive copies {result['drive'][0]}, local trash {result['trash'][0]})."
