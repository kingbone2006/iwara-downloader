from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VideoStatus(str, Enum):
    READY = "ready"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"
    STALLED = "stalled"  # no progress for too long
    ON_DISK = "on_disk"  # already exists on machine
    PRIVATE = "private"  # friends-only / paid — need manual select if purchased


def format_bytes(num: int | float | None) -> str:
    """Human-readable size, e.g. 1.2 GB. Unknown → —"""
    if num is None:
        return "—"
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    if n < 1024:
        return f"{int(n)} B"
    for unit, div in (
        ("KB", 1024),
        ("MB", 1024**2),
        ("GB", 1024**3),
        ("TB", 1024**4),
    ):
        val = n / div
        if val < 1024 or unit == "TB":
            if unit == "KB":
                return f"{val:.0f} {unit}"
            return f"{val:.2f} {unit}" if val < 10 else f"{val:.1f} {unit}"
    return f"{n:.0f} B"


def format_speed(bps: float | int | None) -> str:
    """Human download speed, e.g. 1.2 MB/s."""
    if bps is None:
        return "—"
    try:
        n = float(bps)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n < 1024:
        return f"{n:.0f} B/s"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB/s"
    return f"{n / (1024**2):.2f} MB/s"


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return ""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


@dataclass
class VideoItem:
    id: str
    title: str
    author: str
    views: int = 0
    url: str = ""
    thumbnail_url: str = ""
    thumbnail_alts: list[str] = field(default_factory=list)
    size_bytes: int = 0  # 0 = unknown
    duration_sec: int = 0
    selected: bool = False
    status: VideoStatus = VideoStatus.READY
    progress: float = 0.0  # 0..1
    error: str = ""
    local_path: str = ""
    # Channel username (folder key) — set when scanning channel(s)
    channel: str = ""
    # True when Iwara marks the video private (friends/purchase)
    is_private: bool = False

    def status_key(self) -> str:
        return f"status_{self.status.value}"

    def can_retry(self) -> bool:
        return self.status in (VideoStatus.ERROR, VideoStatus.STALLED)

    def is_already_local(self) -> bool:
        return self.status == VideoStatus.ON_DISK or (
            bool(self.local_path) and self.progress >= 1.0 and self.status == VideoStatus.DONE
        )

    def size_label(self) -> str:
        return format_bytes(self.size_bytes if self.size_bytes > 0 else None)

    def auto_selectable(self) -> bool:
        """Eligible for Select-all / Select-new (private must be ticked manually)."""
        if self.is_private or self.status == VideoStatus.PRIVATE:
            return False
        return True


@dataclass
class ScanResult:
    items: list[VideoItem] = field(default_factory=list)
    source: str = ""  # hashtag | channel
    query: str = ""  # tag id or channel username
