"""Download folder layout + local duplicate detection (multi-drive aware)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .database import HistoryDB
from .models import VideoItem, VideoStatus


@dataclass
class LocalChannelInfo:
    """A channel folder under downloads/ that already has videos on disk."""

    username: str
    local_count: int
    folder: str
    # Drive letter / root, e.g. "E:" or "D:"
    drive: str = ""
    # Download root that owns this channel folder
    root: str = ""
    # All folders if same channel exists on multiple drives
    all_folders: list[str] = field(default_factory=list)
    # Filled later by API refresh
    remote_count: int = -1  # -1 = unknown / not fetched
    display_name: str = ""
    error: str = ""
    # Gap vs local after full list scan (-1 = not scanned yet)
    missing_public: int = -1  # not on disk, not private (worth downloading)
    missing_private: int = -1  # not on disk, private (safe to skip)

    @property
    def new_count(self) -> int:
        """Downloadable missing (excludes private when known)."""
        if self.missing_public >= 0:
            return self.missing_public
        if self.remote_count < 0:
            return -1
        return max(0, self.remote_count - self.local_count)

    @property
    def all_missing_are_private(self) -> bool:
        return (
            self.missing_public == 0
            and self.missing_private is not None
            and self.missing_private > 0
        )

    def local_video_ids(self) -> set[str]:
        """All video IDs found across every folder for this channel."""
        ids: set[str] = set()
        folders = self.all_folders or ([self.folder] if self.folder else [])
        for f in folders:
            ids |= set(index_local_files(Path(f)).keys())
        return ids


@dataclass
class DownloadRootInfo:
    """One managed downloads root (often one drive)."""

    path: str
    drive: str
    channel_count: int
    video_count: int
    free_gb: float = -1.0
    total_gb: float = -1.0
    is_primary: bool = False
    exists: bool = False


def path_drive(path: str | Path) -> str:
    """Windows drive letter like 'E:', or anchor / host for UNC, else '?'."""
    try:
        p = Path(path)
        try:
            p = p.resolve()
        except OSError:
            pass
        drive = (p.drive or "").strip()
        if drive:
            # Normalize "e:" → "E:"
            if len(drive) >= 2 and drive[1] == ":":
                return drive[0].upper() + ":"
            return drive
        parts = p.parts
        if parts and str(parts[0]).startswith("\\\\"):
            return str(parts[0])
        anchor = (p.anchor or "").strip()
        return anchor or "?"
    except Exception:
        return "?"


def disk_free_gb(path: str | Path) -> tuple[float, float]:
    """Return (free_gb, total_gb) or (-1, -1) if unknown."""
    try:
        p = Path(path)
        # disk_usage needs an existing path; walk up if needed
        check = p
        while not check.exists() and check.parent != check:
            check = check.parent
        if not check.exists():
            return -1.0, -1.0
        u = shutil.disk_usage(str(check))
        return u.free / (1024**3), u.total / (1024**3)
    except OSError:
        return -1.0, -1.0


def safe_folder_name(name: str) -> str:
    """Windows-safe folder name for channel or hashtag."""
    name = (name or "unknown").strip()
    # Drop path separators and reserved characters (# is allowed on Windows)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    return name[:80] or "unknown"


def folder_label(source: str, query: str) -> str:
    """Human folder name under downloads/."""
    q = (query or "").strip()
    if not q:
        return ""
    if source == "channel":
        return safe_folder_name(q.lstrip("@"))
    if source == "hashtag":
        tag = q.lstrip("#").strip()
        # e.g. "tag aemeath" — clear that this folder is from a hashtag scan
        return safe_folder_name(f"tag {tag}" if tag else "tag unknown")
    return safe_folder_name(q)


def target_download_dir(
    base: str | Path,
    *,
    source: str,
    query: str,
) -> Path:
    """
    Channel scans  → downloads/<username>/
    Hashtag scans  → downloads/tag <tag>/   (e.g. tag aemeath)
    Unknown/empty  → downloads/
    """
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    label = folder_label(source, query)
    if not label:
        return root
    folder = root / label
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_channel_dir(
    username: str,
    roots: list[str | Path],
    *,
    primary: str | Path | None = None,
    create: bool = True,
) -> Path:
    """
    Prefer an existing channel folder on any managed root (largest file count).
    Otherwise create under primary (or first root).
    """
    uname = safe_folder_name((username or "").lstrip("@"))
    if not uname:
        root = Path(primary or (roots[0] if roots else "."))
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    best: Path | None = None
    best_n = -1
    for r in roots:
        p = Path(r) / uname
        if not p.is_dir():
            continue
        n = len(index_local_files(p))
        if n > best_n:
            best_n = n
            best = p
        elif best is None:
            best = p
    if best is not None:
        return best

    base = Path(primary if primary is not None else (roots[0] if roots else "."))
    if create:
        base.mkdir(parents=True, exist_ok=True)
        folder = base / uname
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    return base / uname


def _extract_id_from_filename(name: str) -> str | None:
    """Parse [video_id] from our download naming scheme."""
    # Prefer last [...] segment to avoid false matches in titles
    if "[" not in name or "]" not in name:
        return None
    start = name.rfind("[")
    end = name.find("]", start + 1)
    if start < 0 or end < 0:
        return None
    vid = name[start + 1 : end].strip()
    return vid or None


def index_local_files(folder: Path) -> dict[str, Path]:
    """
    Single-pass directory index: video_id → largest matching file.
    Much faster than scanning the folder once per video on large channels.
    """
    index: dict[str, Path] = {}
    if not folder.is_dir():
        return index
    try:
        entries = list(folder.iterdir())
    except OSError:
        return index

    for p in entries:
        try:
            if not p.is_file():
                continue
            name = p.name
            if name.endswith((".part", ".ytdl", ".temp")):
                continue
            vid = _extract_id_from_filename(name)
            if not vid:
                continue
            prev = index.get(vid)
            if prev is None or p.stat().st_size > prev.stat().st_size:
                index[vid] = p
        except OSError:
            continue
    return index


def find_local_file(
    folder: Path,
    video_id: str,
    *,
    index: dict[str, Path] | None = None,
) -> Path | None:
    """Find a file that contains [video_id] in its name (our naming scheme)."""
    if not video_id:
        return None
    if index is not None:
        return index.get(video_id)
    if not folder.is_dir():
        return None
    needle = f"[{video_id}]"
    matches: list[Path] = []
    try:
        for p in folder.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if needle not in name:
                continue
            if name.endswith((".part", ".ytdl", ".temp")):
                continue
            matches.append(p)
    except OSError:
        return None
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_size)


def find_local_file_in_roots(
    roots: list[str | Path],
    video_id: str,
    *,
    channel: str = "",
) -> Path | None:
    """Search for a video file under channel subfolders or root of each downloads dir."""
    if not video_id:
        return None
    uname = safe_folder_name(channel.lstrip("@")) if channel else ""
    candidates: list[Path] = []
    for r in roots:
        root = Path(r)
        if uname:
            candidates.append(root / uname)
        candidates.append(root)
    for folder in candidates:
        found = find_local_file(folder, video_id)
        if found and found.is_file():
            return found
    return None


def list_local_channels(
    base: str | Path,
    *,
    include_empty: bool = True,
) -> list[LocalChannelInfo]:
    """
    Scan one download root for channel folders.
    Channel = subfolder name under downloads/ (skips hashtag folders
    starting with 'tag ' and hidden folders).
    Empty folders still count as channels (include_empty=True by default).
    """
    root = Path(base)
    if not root.is_dir():
        return []
    drive = path_drive(root)
    root_s = str(root)
    results: list[LocalChannelInfo] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for p in entries:
        try:
            if not p.is_dir():
                continue
            name = p.name.strip()
            if not name or name.lower().startswith("tag "):
                continue
            # Skip hidden / system-ish
            if name.startswith("."):
                continue
            index = index_local_files(p)
            n = len(index)
            if n <= 0 and not include_empty:
                continue
            folder_s = str(p)
            results.append(
                LocalChannelInfo(
                    username=name,
                    local_count=n,
                    folder=folder_s,
                    drive=drive,
                    root=root_s,
                    all_folders=[folder_s],
                    display_name=name,
                )
            )
        except OSError:
            continue
    return results


def list_local_channels_multi(
    roots: list[str | Path],
    *,
    include_empty: bool = True,
) -> list[LocalChannelInfo]:
    """
    Scan all managed download roots (multi-drive) and merge same username.
    Channel = folder name (empty folders included by default).
    New downloads for a merged channel go to the folder with the most files.
    """
    by_user: dict[str, LocalChannelInfo] = {}
    for base in roots:
        for ch in list_local_channels(base, include_empty=include_empty):
            key = ch.username.lower()
            existing = by_user.get(key)
            if existing is None:
                by_user[key] = ch
                continue
            # Merge counts & folders
            existing.local_count += ch.local_count
            for f in ch.all_folders:
                if f not in existing.all_folders:
                    existing.all_folders.append(f)
            # Prefer folder with more local files as update target
            try:
                old_n = len(index_local_files(Path(existing.folder)))
            except OSError:
                old_n = 0
            if ch.local_count > old_n:
                existing.folder = ch.folder
                existing.drive = ch.drive
                existing.root = ch.root
            # Multi-drive annotation on display
            drives = sorted(
                {
                    path_drive(f)
                    for f in existing.all_folders
                    if path_drive(f) not in ("", "?")
                }
            )
            if len(drives) > 1:
                existing.drive = "+".join(drives)
    return sorted(by_user.values(), key=lambda c: c.username.lower())


def summarize_download_roots(
    roots: list[str | Path],
    *,
    primary: str | Path | None = None,
    include_empty: bool = True,
) -> list[DownloadRootInfo]:
    """Per-root channel/video counts + free disk space."""
    primary_key = ""
    if primary is not None:
        try:
            primary_key = str(Path(primary).resolve()).lower()
        except OSError:
            primary_key = str(primary).replace("\\", "/").lower()

    out: list[DownloadRootInfo] = []
    for r in roots:
        path_s = str(r)
        p = Path(r)
        exists = p.is_dir()
        drive = path_drive(p)
        channels = list_local_channels(p, include_empty=include_empty) if exists else []
        free_gb, total_gb = disk_free_gb(p if exists else Path(path_s[:3] if len(path_s) >= 3 else path_s))
        try:
            key = str(p.resolve()).lower() if exists else path_s.replace("\\", "/").lower()
        except OSError:
            key = path_s.replace("\\", "/").lower()
        out.append(
            DownloadRootInfo(
                path=path_s,
                drive=drive,
                channel_count=len(channels),
                video_count=sum(c.local_count for c in channels),
                free_gb=free_gb,
                total_gb=total_gb,
                is_primary=bool(primary_key) and key == primary_key,
                exists=exists,
            )
        )
    return out


def annotate_local_status(
    items: list[VideoItem],
    folder: Path,
    history: HistoryDB,
    *,
    extra_folders: list[Path] | None = None,
) -> tuple[int, int]:
    """
    Mark items already on disk / in history as ON_DISK.
    Uses one directory scan for speed on multi-core / large libraries.
    Optionally scans extra_folders (other drives) for the same channel.
    Returns (already_count, new_count).
    """
    already = 0
    new = 0
    # O(files) once, then O(1) per video — huge win for full-channel re-scans
    file_index = index_local_files(folder)
    if extra_folders:
        for ef in extra_folders:
            if ef.resolve() == folder.resolve() if ef.is_dir() and folder.is_dir() else False:
                continue
            for vid, path in index_local_files(ef).items():
                prev = file_index.get(vid)
                if prev is None or path.stat().st_size > prev.stat().st_size:
                    file_index[vid] = path

    for item in items:
        local = file_index.get(item.id)
        hist_path = history.get_path(item.id)
        if hist_path:
            p = Path(hist_path)
            if p.is_file():
                local = local or p

        if local and local.is_file():
            item.status = VideoStatus.ON_DISK
            item.progress = 1.0
            item.local_path = str(local)
            item.selected = False
            item.error = ""
            if not history.is_downloaded(item.id):
                history.mark_downloaded(
                    item.id, item.title, item.author, str(local)
                )
            already += 1
        else:
            if history.is_downloaded(item.id) and not (
                hist_path and Path(hist_path).is_file()
            ):
                history.remove(item.id)
            # Keep PRIVATE status; never auto-select — safe to skip
            if item.is_private or item.status == VideoStatus.PRIVATE:
                item.status = VideoStatus.PRIVATE
                item.is_private = True
                item.progress = 0.0
                item.local_path = ""
                item.selected = False
                item.error = ""
            elif item.status not in (
                VideoStatus.ERROR,
                VideoStatus.STALLED,
                VideoStatus.DONE,
            ):
                item.status = VideoStatus.READY
                item.progress = 0.0
                item.local_path = ""
                item.selected = False
            else:
                item.selected = False
            new += 1
    return already, new
