from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_DOWNLOAD_DIR = APP_DIR / "downloads"


def _norm_path_key(path: str) -> str:
    """Case-insensitive path key for de-duplication on Windows."""
    p = (path or "").strip()
    if not p:
        return ""
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return p.replace("\\", "/").rstrip("/").lower()


@dataclass
class AppConfig:
    language: str = "vi"
    theme: str = "dark"
    # Primary root for NEW channel folders / default save location
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    # Extra download roots on other drives (primary is not duplicated here)
    download_dirs: list[str] = field(default_factory=list)
    # Max videos returned when scanning hashtag/channel
    scan_limit: int = 32
    # Parallel download workers (1 = sequential, max 16)
    download_batch: int = 3
    # Delay between starting each new download (seconds)
    download_delay_sec: float = 3.0
    # Delay between API page requests while scanning
    scan_delay_sec: float = 1.5
    stall_timeout_sec: float = 90.0
    demo_mode: bool = False
    email: str = ""
    password: str = ""
    # UI backend: tk = CustomTkinter (CPU), flet = Flutter/GPU
    ui_backend: str = "tk"
    # GPU preference for Flet UI (Windows):
    #   auto | high_performance | power_saving | adapter:N (specific GPU index)
    ui_gpu: str = "auto"
    # Last chosen adapter display name (informational; selection key is ui_gpu)
    ui_gpu_adapter: str = ""
    # How many video rows to render per page (keeps UI smooth with huge lists)
    list_page_size: int = 60

    def all_download_dirs(self) -> list[str]:
        """
        All managed download roots, primary first.
        Used to scan channel libraries across multiple drives.
        """
        out: list[str] = []
        seen: set[str] = set()
        primary = (self.download_dir or "").strip()
        extras = list(self.download_dirs or [])
        for d in [primary, *extras]:
            d = (d or "").strip()
            if not d:
                continue
            key = _norm_path_key(d)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(d)
        if not out:
            out.append(str(DEFAULT_DOWNLOAD_DIR))
        return out

    def set_primary_download_dir(self, path: str, *, keep_old: bool = True) -> None:
        """
        Set the active download root for new content.
        If keep_old, previous primary is kept in download_dirs so library still
        sees channels on the old drive.
        """
        path = (path or "").strip()
        if not path:
            return
        old = (self.download_dir or "").strip()
        self.download_dir = path
        extras = [d for d in (self.download_dirs or []) if (d or "").strip()]
        if keep_old and old and _norm_path_key(old) != _norm_path_key(path):
            extras.append(old)
        # Drop primary from extras, de-dupe
        primary_key = _norm_path_key(path)
        cleaned: list[str] = []
        seen: set[str] = {primary_key}
        for d in extras:
            d = (d or "").strip()
            if not d:
                continue
            key = _norm_path_key(d)
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(d)
        self.download_dirs = cleaned

    def add_download_dir(self, path: str) -> bool:
        """Register an extra root (or make it primary if none). Returns True if added."""
        path = (path or "").strip()
        if not path:
            return False
        key = _norm_path_key(path)
        if not key:
            return False
        if not (self.download_dir or "").strip():
            self.download_dir = path
            return True
        if key == _norm_path_key(self.download_dir):
            return False
        extras = list(self.download_dirs or [])
        for d in extras:
            if _norm_path_key(d) == key:
                return False
        extras.append(path)
        self.download_dirs = extras
        return True

    def remove_download_dir(self, path: str) -> bool:
        """
        Remove a managed root. Cannot remove the only remaining primary;
        if removing primary, promote the first extra.
        """
        path = (path or "").strip()
        if not path:
            return False
        key = _norm_path_key(path)
        primary = (self.download_dir or "").strip()
        extras = [(d or "").strip() for d in (self.download_dirs or []) if (d or "").strip()]

        if key == _norm_path_key(primary):
            if not extras:
                return False  # keep at least one root
            self.download_dir = extras[0]
            self.download_dirs = extras[1:]
            return True

        new_extras = [d for d in extras if _norm_path_key(d) != key]
        if len(new_extras) == len(extras):
            return False
        self.download_dirs = new_extras
        return True

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            if "max_limit" in data and "scan_limit" not in data:
                data["scan_limit"] = data["max_limit"]
            if "rate_limit_sec" in data and "download_delay_sec" not in data:
                data["download_delay_sec"] = data["rate_limit_sec"]
            # Legacy alias: concurrent → download_batch
            if "concurrent" in data and "download_batch" not in data:
                try:
                    data["download_batch"] = int(data["concurrent"])
                except (TypeError, ValueError):
                    pass
            data.pop("concurrent", None)
            data.pop("thumb_workers", None)
            # Normalize download_dirs to list[str]
            raw_dirs = data.get("download_dirs")
            if isinstance(raw_dirs, str):
                data["download_dirs"] = [raw_dirs] if raw_dirs.strip() else []
            elif isinstance(raw_dirs, list):
                data["download_dirs"] = [
                    str(x).strip() for x in raw_dirs if str(x).strip()
                ]
            else:
                data["download_dirs"] = []
            filtered = {k: v for k, v in data.items() if k in known}
            cfg = cls(**filtered)
            # Clamp workers
            try:
                cfg.download_batch = max(1, min(16, int(cfg.download_batch)))
            except (TypeError, ValueError):
                cfg.download_batch = 3
            # Normalize UI options
            backend = (cfg.ui_backend or "tk").strip().lower()
            cfg.ui_backend = backend if backend in ("tk", "flet") else "tk"
            gpu = (cfg.ui_gpu or "auto").strip()
            low = gpu.lower()
            if low in ("auto", "high_performance", "power_saving"):
                cfg.ui_gpu = low
            elif low.startswith("adapter:"):
                cfg.ui_gpu = low
            else:
                cfg.ui_gpu = "auto"
            cfg.ui_gpu_adapter = str(getattr(cfg, "ui_gpu_adapter", "") or "")
            try:
                cfg.list_page_size = max(20, min(200, int(cfg.list_page_size)))
            except (TypeError, ValueError):
                cfg.list_page_size = 60
            # Ensure primary not duplicated in extras
            if cfg.download_dir:
                pk = _norm_path_key(cfg.download_dir)
                cfg.download_dirs = [
                    d
                    for d in (cfg.download_dirs or [])
                    if _norm_path_key(d) and _norm_path_key(d) != pk
                ]
            return cfg
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
