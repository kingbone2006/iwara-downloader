"""Cache cleanup helpers (thumbnails, temp files)."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import APP_DIR
from . import thumbnails as thumb_mod

CACHE_ROOT = APP_DIR / "cache"


def cache_stats() -> dict[str, int | float]:
    """Return file count and total bytes under cache/."""
    n = 0
    total = 0
    if not CACHE_ROOT.is_dir():
        return {"files": 0, "bytes": 0, "mb": 0.0}
    try:
        for p in CACHE_ROOT.rglob("*"):
            if p.is_file():
                n += 1
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return {"files": n, "bytes": total, "mb": round(total / (1024 * 1024), 2)}


def clear_cache(*, thumbs: bool = True, memory: bool = True) -> dict[str, int]:
    """
    Delete cached thumbnails (and clear in-memory thumb dict).
    Returns counts of removed files / freed bytes (approx).
    """
    removed = 0
    freed = 0
    if memory:
        try:
            with thumb_mod._lock:  # type: ignore[attr-defined]
                thumb_mod._memory.clear()  # type: ignore[attr-defined]
        except Exception:
            pass

    if thumbs:
        thumbs_dir = thumb_mod.CACHE_DIR
        if thumbs_dir.is_dir():
            for p in list(thumbs_dir.iterdir()):
                try:
                    if p.is_file():
                        sz = p.stat().st_size
                        p.unlink(missing_ok=True)
                        removed += 1
                        freed += sz
                    elif p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                        removed += 1
                except OSError:
                    continue
        # Drop empty leftover junk under cache/
        try:
            for p in CACHE_ROOT.rglob("*"):
                if p.is_file() and p.suffix.lower() in {
                    ".tmp",
                    ".part",
                    ".ytdl",
                    ".temp",
                }:
                    try:
                        sz = p.stat().st_size
                        p.unlink(missing_ok=True)
                        removed += 1
                        freed += sz
                    except OSError:
                        pass
        except OSError:
            pass

    return {"removed": removed, "freed_bytes": freed}
