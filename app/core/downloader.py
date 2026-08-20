"""Download worker — demo placeholders or real downloads via yt-dlp.

Supports progress callbacks and stall (no-progress) timeout.
On 404 / CDN failures, tries alternate Iwara media hosts (mikoto, hime, …).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .cdn import (
    IWARA_CDN_HOSTS,
    candidate_download_urls,
    host_of,
    is_cdn_retryable_error,
    normalize_media_url,
    short_server_name,
)
from .database import HistoryDB
from .errors import ErrorHelp, diagnose_download_error
from .models import VideoItem, VideoStatus
from .storage import find_local_file, index_local_files


class StallTimeoutError(RuntimeError):
    """Raised when download progress freezes longer than stall_timeout_sec."""


class Downloader:
    def __init__(
        self,
        download_dir: str | Path,
        *,
        demo_mode: bool = True,
        history: HistoryDB | None = None,
        stop_flag: Callable[[], bool] | None = None,
        pause_wait: Callable[[], None] | None = None,
        on_progress: Callable[[VideoItem], None] | None = None,
        on_live: Callable[[dict], None] | None = None,
        email: str = "",
        password: str = "",
        stall_timeout_sec: float = 90.0,
        force: bool = False,
        channel: str = "",
    ) -> None:
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.demo_mode = demo_mode
        self.history = history or HistoryDB()
        self._stop_flag = stop_flag or (lambda: False)
        self._pause_wait = pause_wait or (lambda: None)
        self._on_progress = on_progress or (lambda _v: None)
        self._on_live = on_live or (lambda _d: None)
        self.email = (email or "").strip()
        self.password = password or ""
        self.stall_timeout_sec = max(15.0, float(stall_timeout_sec))
        self.force = force
        self.channel = channel or ""

    def download(self, item: VideoItem) -> VideoItem:
        self._pause_wait()
        if self._stop_flag():
            item.status = VideoStatus.SKIPPED
            return item

        if not self.force:
            # Prefer folder index if caller cached one on the instance
            idx = getattr(self, "_file_index", None)
            local = find_local_file(self.download_dir, item.id, index=idx)
            hist = self.history.get_path(item.id)
            if hist and Path(hist).is_file():
                local = local or Path(hist)
            if local and local.is_file():
                item.status = VideoStatus.ON_DISK
                item.progress = 1.0
                item.local_path = str(local)
                item.error = ""
                self.history.mark_downloaded(
                    item.id, item.title, item.author, str(local), self.channel
                )
                self._on_progress(item)
                return item
            if self.history.is_downloaded(item.id) and not (
                hist and Path(hist).is_file()
            ):
                # Stale history entry — allow re-download
                self.history.remove(item.id)

        item.status = VideoStatus.DOWNLOADING
        item.progress = 0.0
        item.error = ""
        self._on_progress(item)
        self._emit_live(
            item,
            status="starting",
            speed=0.0,
            file_pct=0.0,
            message="Bắt đầu tải…",
        )

        # Shared stall tracking + UI throttle (prevents main-thread freeze)
        state = {
            "last_progress": 0.0,
            "last_change": time.time(),
            "last_ui": 0.0,
            "stalled": False,
            "done": False,
            "speed": 0.0,
            "eta": None,
            "downloaded": 0,
            "total_bytes": 0,
            "server": "",
            "message": "",
        }
        cancel = threading.Event()
        # Min interval between progress UI events (ms-scale freeze if unthrottled)
        ui_interval = 0.28

        def _after_pause() -> None:
            """Block while paused; do not count pause time toward stall timeout."""
            t0 = time.time()
            self._pause_wait()
            if time.time() - t0 > 0.2:
                state["last_change"] = time.time()

        def report(
            progress: float | None = None,
            *,
            heartbeat: bool = False,
            status: str = "downloading",
            speed: float | None = None,
            eta: float | None = None,
            downloaded: int | None = None,
            total_bytes: int | None = None,
            message: str = "",
            server: str = "",
            force_ui: bool = False,
        ) -> None:
            """Update progress; UI callbacks throttled to keep GUI responsive."""
            _after_pause()
            if progress is not None:
                if progress > state["last_progress"] + 1e-6:
                    state["last_progress"] = progress
                    item.progress = progress
                    state["last_change"] = time.time()
                elif heartbeat:
                    state["last_change"] = time.time()
            elif heartbeat:
                state["last_change"] = time.time()
            if speed is not None:
                state["speed"] = float(speed or 0)
            if eta is not None:
                state["eta"] = eta
            if downloaded is not None:
                state["downloaded"] = int(downloaded)
            if total_bytes is not None:
                state["total_bytes"] = int(total_bytes)
            if server:
                if server != state.get("server"):
                    force_ui = True
                state["server"] = server
            if message:
                state["message"] = message

            now = time.time()
            must = force_ui or status in (
                "starting",
                "finishing",
                "error",
                "done",
                "skipped",
            )
            if not must and (now - float(state["last_ui"])) < ui_interval:
                return
            state["last_ui"] = now
            self._on_progress(item)
            self._emit_live(
                item,
                status=status,
                speed=state["speed"],
                file_pct=float(item.progress),
                eta=state.get("eta"),
                downloaded=state.get("downloaded") or 0,
                total_bytes=state.get("total_bytes") or 0,
                message=state.get("message") or message,
                server=str(state.get("server") or ""),
            )

        def reset_host_attempt() -> None:
            """Clear stall flag when rotating to another CDN host."""
            state["stalled"] = False
            state["last_change"] = time.time()
            state["last_progress"] = 0.0
            item.progress = 0.0
            cancel.clear()

        def should_abort() -> bool:
            """True if user stopped OR current host stalled (not permanent for all hosts)."""
            _after_pause()
            if self._stop_flag():
                cancel.set()
                return True
            if cancel.is_set() and state["stalled"]:
                return True
            if cancel.is_set() and not state["stalled"]:
                # User stop
                return True
            idle = time.time() - state["last_change"]
            if idle >= self.stall_timeout_sec:
                state["stalled"] = True
                cancel.set()
                return True
            return False

        def watchdog() -> None:
            while not state["done"]:
                if self._stop_flag():
                    cancel.set()
                    return
                idle = time.time() - state["last_change"]
                if idle >= self.stall_timeout_sec:
                    state["stalled"] = True
                    cancel.set()
                    return
                time.sleep(0.5)

        wd = threading.Thread(target=watchdog, daemon=True)
        wd.start()

        # Expose reset so multi-CDN can start each host cleanly
        self._reset_host_attempt = reset_host_attempt  # type: ignore[attr-defined]

        try:
            if self.demo_mode:
                out = self._demo_download(item, report, should_abort)
            else:
                out = self._real_download(item, report, should_abort)

            if self._stop_flag():
                item.status = VideoStatus.SKIPPED
                return item
            # Successful path: ignore residual stall flags from earlier hosts
            reset_host_attempt()

            self.history.mark_downloaded(
                item.id, item.title, item.author, str(out), self.channel
            )
            item.status = VideoStatus.DONE
            item.progress = 1.0
            item.local_path = str(out)
            item.error = ""
            self._emit_live(
                item,
                status="done",
                speed=0.0,
                file_pct=1.0,
                message="Hoàn tất",
            )
        except StallTimeoutError as exc:
            help_ = diagnose_download_error(exc)
            item.status = VideoStatus.STALLED
            item.error = help_.full_message()
            self._emit_live(
                item,
                status="error",
                speed=0.0,
                file_pct=float(item.progress),
                message=help_.log_message(),
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if msg == "stopped" or self._stop_flag():
                item.status = VideoStatus.SKIPPED
                item.error = ""
                self._emit_live(
                    item,
                    status="skipped",
                    speed=0.0,
                    file_pct=float(item.progress),
                    message="Đã dừng",
                )
            else:
                help_ = diagnose_download_error(exc)
                if state["stalled"] or "stall" in msg.lower():
                    item.status = VideoStatus.STALLED
                else:
                    item.status = VideoStatus.ERROR
                item.error = help_.full_message()
                self._emit_live(
                    item,
                    status="error",
                    speed=0.0,
                    file_pct=float(item.progress),
                    message=help_.log_message(),
                )
        finally:
            state["done"] = True
            cancel.set()

        self._on_progress(item)
        return item

    def _emit_live(
        self,
        item: VideoItem,
        *,
        status: str,
        speed: float = 0.0,
        file_pct: float = 0.0,
        eta: float | None = None,
        downloaded: int = 0,
        total_bytes: int = 0,
        message: str = "",
        server: str = "",
    ) -> None:
        try:
            self._on_live(
                {
                    "id": item.id,
                    "title": item.title,
                    "author": item.author,
                    "status": status,
                    "speed": speed,
                    "file_pct": max(0.0, min(1.0, float(file_pct))),
                    "eta": eta,
                    "downloaded": downloaded,
                    "total_bytes": total_bytes,
                    "message": message,
                    "server": server,
                }
            )
        except Exception:
            pass

    def _demo_download(
        self,
        item: VideoItem,
        report: Callable[..., None],
        should_abort: Callable[[], bool],
    ) -> Path:
        steps = 20
        for i in range(1, steps + 1):
            if should_abort():
                if self._stop_flag():
                    raise RuntimeError("stopped")
                raise StallTimeoutError(
                    f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                )
            time.sleep(0.06)
            # Fake ~1.5 MB/s for demo
            report(
                i / steps,
                heartbeat=True,
                status="downloading",
                speed=1.5 * 1024 * 1024,
                downloaded=int(i / steps * 10_000_000),
                total_bytes=10_000_000,
                message="Demo download…",
            )

        safe_name = self._safe_filename(
            f"{item.author} - {item.title} [{item.id}].txt"
        )
        out = self.download_dir / safe_name
        out.write_text(
            f"DEMO PLACEHOLDER\nid={item.id}\ntitle={item.title}\nurl={item.url}\n",
            encoding="utf-8",
        )
        return out

    def _real_download(
        self,
        item: VideoItem,
        report: Callable[..., None],
        should_abort: Callable[[], bool],
    ) -> Path:
        """
        Download with multi-server rotation.

        1) Quick yt-dlp attempt (few retries)
        2) Multi-CDN: on any host error/stall/404 → switch server immediately
           (up to N hosts). Only after all hosts fail → final 404 message.
        """
        ytdlp_err: Exception | None = None
        try:
            return self._ytdlp_download(item, report, should_abort)
        except StallTimeoutError as exc:
            # One host stalled via yt-dlp — rotate CDNs instead of failing hard
            if self._stop_flag():
                raise
            ytdlp_err = exc
        except RuntimeError as exc:
            if str(exc) == "stopped" or self._stop_flag():
                raise
            ytdlp_err = exc
        except Exception as exc:  # noqa: BLE001
            ytdlp_err = exc

        msg = str(ytdlp_err or "")
        report(
            item.progress,
            heartbeat=True,
            status="downloading",
            message=(
                f"Server lỗi ({msg[:60]}) — đổi CDN (thử tối đa "
                f"{len(IWARA_CDN_HOSTS)} server)…"
                if msg
                else "Đổi CDN dự phòng…"
            ),
            force_ui=True,
        )
        try:
            return self._cdn_multi_server_download(item, report, should_abort)
        except StallTimeoutError:
            raise
        except RuntimeError as exc:
            if str(exc) == "stopped":
                raise
            help_ = diagnose_download_error(exc)
            raise RuntimeError(help_.full_message()) from exc
        except Exception as exc:  # noqa: BLE001
            help_ = diagnose_download_error(exc)
            raise RuntimeError(help_.full_message()) from exc

    def _ytdlp_download(
        self,
        item: VideoItem,
        report: Callable[..., None],
        should_abort: Callable[[], bool],
    ) -> Path:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài yt-dlp. Chạy: pip install yt-dlp"
            ) from exc

        outtmpl = str(
            self.download_dir
            / self._safe_filename(f"{item.author} - {item.title} [{item.id}].%(ext)s")
        )

        def _host_from_ytdlp(d: dict) -> str:
            info = d.get("info_dict") if isinstance(d, dict) else None
            url = ""
            if isinstance(info, dict):
                url = str(info.get("url") or "")
                if not url:
                    req = info.get("requested_formats") or info.get("formats") or []
                    if isinstance(req, list) and req:
                        url = str((req[-1] or {}).get("url") or "")
            if not url:
                url = str(d.get("url") or "")
            h = host_of(url) if url else ""
            if h:
                # Short label: mikoto.iwara.tv → mikoto
                if h.endswith(".iwara.tv"):
                    return h.split(".")[0]
                return h
            return "yt-dlp"

        def hook(d: dict) -> None:
            if should_abort():
                if self._stop_flag():
                    raise RuntimeError("stopped")
                raise StallTimeoutError(
                    f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                )
            st = d.get("status")
            srv = _host_from_ytdlp(d)
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta")
                if total:
                    pct = min(0.99, done / total)
                else:
                    pct = max(item.progress, 0.01)
                report(
                    pct,
                    heartbeat=True,
                    status="downloading",
                    speed=float(speed or 0),
                    eta=float(eta) if eta is not None else None,
                    downloaded=int(done or 0),
                    total_bytes=int(total or 0),
                    message=f"Đang tải · {srv}",
                    server=srv,
                )
            elif st == "finished":
                report(
                    0.99,
                    heartbeat=True,
                    status="finishing",
                    speed=0.0,
                    message=f"Đang ghi file · {srv}",
                    server=srv,
                    force_ui=True,
                )
            elif st == "error":
                report(
                    item.progress,
                    heartbeat=True,
                    status="error",
                    speed=0.0,
                    message=str(d.get("error") or "Lỗi tải"),
                    server=srv,
                    force_ui=True,
                )

        ydl_opts: dict = {
            "outtmpl": outtmpl,
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            # Few retries only — we rotate CDN hosts ourselves on failure
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "socket_timeout": 45,
            "progress_hooks": [hook],
            "format": "best[ext=mp4]/best",
            "continuedl": True,
            "overwrites": self.force,
        }
        if self.email and self.password:
            ydl_opts["username"] = self.email
            ydl_opts["password"] = self.password

        url = item.url or f"https://www.iwara.tv/video/{item.id}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if should_abort():
                    if self._stop_flag():
                        raise RuntimeError("stopped")
                    raise StallTimeoutError(
                        f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                    )
                if not info:
                    raise RuntimeError("yt-dlp returned no info")
                requested = info.get("requested_downloads") or []
                if requested and requested[0].get("filepath"):
                    path = Path(requested[0]["filepath"])
                else:
                    path = Path(ydl.prepare_filename(info))
                if not path.exists():
                    found = find_local_file(self.download_dir, item.id)
                    if found:
                        path = found
                    else:
                        raise RuntimeError(f"File not found after download: {path}")
                return path
        except StallTimeoutError:
            raise
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Keep raw message so CDN fallback can classify (404 etc.)
            raise RuntimeError(str(exc)) from exc

    def _cdn_multi_server_download(
        self,
        item: VideoItem,
        report: Callable[..., None],
        should_abort: Callable[[], bool],
    ) -> Path:
        """
        Fetch formats via API, then try each CDN host immediately on failure.

        Max attempts per quality ≈ number of known servers. Stall / 404 /
        network errors on one host → next host right away (do not give up early).
        Only after all hosts fail → final «404 not found on all servers».
        """
        from .api import IwaraAPI, IwaraAPIError

        def on_log(msg: str) -> None:
            report(
                item.progress,
                heartbeat=True,
                status="downloading",
                message=msg[:120],
            )

        api = IwaraAPI(
            demo_mode=False,
            email=self.email,
            password=self.password,
            on_log=on_log,
            stop_flag=self._stop_flag,
            pause_wait=self._pause_wait,
        )
        try:
            formats = api.get_video_file_sources(item.id)
        except IwaraAPIError as exc:
            raise RuntimeError(
                f"Không tìm thấy file để tải (API): {exc}"
            ) from exc

        out_base = self.download_dir / self._safe_filename(
            f"{item.author} - {item.title} [{item.id}]"
        )
        errors: list[str] = []
        tried_hosts: list[str] = []

        # Quality order: Source → 540 → 360 → others → preview last
        def _quality_rank(name: str) -> int:
            n = (name or "").lower()
            order = {"source": 0, "540": 1, "360": 2, "preview": 9}
            return order.get(n, 5)

        ordered_fmts = sorted(
            formats,
            key=lambda f: _quality_rank(str(f.get("name") or "")),
        )

        for fmt in ordered_fmts:
            if self._stop_flag():
                raise RuntimeError("stopped")
            name = str(fmt.get("name") or "video")
            src_url = str(fmt.get("url") or "")
            if not src_url:
                continue
            candidates = candidate_download_urls(src_url)
            n_hosts = len(candidates)
            if n_hosts == 0:
                continue

            for i, dl_url in enumerate(candidates, start=1):
                if self._stop_flag():
                    raise RuntimeError("stopped")
                # Pause still respected, but host stall ≠ global abort
                self._pause_wait()
                if self._stop_flag():
                    raise RuntimeError("stopped")

                host = host_of(dl_url) or "?"
                short = short_server_name(host)
                if short not in tried_hosts:
                    tried_hosts.append(short)

                # Fresh stall window for this host (previous host stall must not block)
                reset = getattr(self, "_reset_host_attempt", None)
                if callable(reset):
                    reset()

                report(
                    0.01,  # reset visual progress for new host attempt
                    heartbeat=True,
                    status="downloading",
                    message=f"Thử server {short} ({i}/{n_hosts}) · {name}…",
                    server=short,
                    force_ui=True,
                )

                try:
                    path = self._http_download_file(
                        dl_url,
                        out_base,
                        report=report,
                        should_abort=should_abort,
                        quality_name=name,
                        server=short,
                    )
                    report(
                        0.99,
                        heartbeat=True,
                        status="finishing",
                        message=f"OK qua {short}",
                        server=short,
                        force_ui=True,
                    )
                    return path
                except RuntimeError as exc:
                    if str(exc) == "stopped" or self._stop_flag():
                        raise
                    # Host-level failure (incl. stall wrapped below) → next server
                    err_s = str(exc)
                    errors.append(f"{short}/{name}: {err_s}")
                    report(
                        item.progress,
                        heartbeat=True,
                        status="downloading",
                        message=f"Server {short} lỗi → đổi server…",
                        server=short,
                        force_ui=True,
                    )
                    self._cleanup_partials(out_base, quality_name=name)
                    continue
                except StallTimeoutError as exc:
                    if self._stop_flag():
                        raise RuntimeError("stopped") from exc
                    # Stall on this host only — rotate, do not fail the whole job yet
                    errors.append(f"{short}/{name}: stall/timeout")
                    report(
                        item.progress,
                        heartbeat=True,
                        status="downloading",
                        message=f"Server {short} đứng yên → đổi server…",
                        server=short,
                        force_ui=True,
                    )
                    self._cleanup_partials(out_base, quality_name=name)
                    continue
                except Exception as exc:  # noqa: BLE001
                    if self._stop_flag():
                        raise RuntimeError("stopped") from exc
                    errors.append(f"{short}/{name}: {type(exc).__name__}: {exc}")
                    report(
                        item.progress,
                        heartbeat=True,
                        status="downloading",
                        message=f"Server {short} lỗi → đổi server…",
                        server=short,
                        force_ui=True,
                    )
                    self._cleanup_partials(out_base, quality_name=name)
                    continue

        hosts_txt = ", ".join(tried_hosts) if tried_hosts else ", ".join(
            short_server_name(h) for h in IWARA_CDN_HOSTS
        )
        detail = "; ".join(errors[:8])
        if len(errors) > 8:
            detail += f" … (+{len(errors) - 8})"
        raise RuntimeError(
            "HTTP 404 — Không tìm thấy file trên tất cả các server "
            f"({hosts_txt}). Đã thử {len(errors)} lượt. Chi tiết: {detail or 'n/a'}"
        )

    @staticmethod
    def _cleanup_partials(out_base: Path, *, quality_name: str = "") -> None:
        """Remove leftover .part files when switching CDN host mid-download."""
        patterns = [
            Path(str(out_base) + ".part"),
            Path(str(out_base) + ".mp4.part"),
            Path(str(out_base) + ".ytdl"),
        ]
        if quality_name:
            patterns.append(Path(f"{out_base}.{quality_name}.mp4.part"))
            patterns.append(Path(f"{out_base}.{quality_name}.part"))
        parent = out_base.parent
        stem = out_base.name
        try:
            if parent.is_dir():
                for p in parent.glob(f"{stem}*"):
                    name = p.name
                    if name.endswith((".part", ".ytdl", ".temp")):
                        try:
                            p.unlink(missing_ok=True)
                        except OSError:
                            pass
        except OSError:
            pass
        for p in patterns:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def _http_download_file(
        self,
        url: str,
        out_base: Path,
        *,
        report: Callable[..., None],
        should_abort: Callable[[], bool],
        quality_name: str = "",
        server: str = "",
    ) -> Path:
        """Stream a direct media URL to disk with progress + stall detection."""
        full = normalize_media_url(url)
        if not full:
            raise RuntimeError("URL tải trống")
        srv = server or host_of(full) or "?"
        if srv.endswith(".iwara.tv"):
            srv = srv.split(".")[0]

        # Guess extension from filename query or path
        ext = "mp4"
        try:
            path_part = urlparse(full).path
            if "." in path_part.rsplit("/", 1)[-1]:
                ext = path_part.rsplit(".", 1)[-1][:8] or "mp4"
            # Iwara often puts filename=xxx_Source.mp4 in query
            from urllib.parse import parse_qs

            qs = parse_qs(urlparse(full).query)
            fn = (qs.get("filename") or [""])[0]
            if "." in fn:
                ext = fn.rsplit(".", 1)[-1][:8] or ext
        except Exception:
            pass
        if quality_name and quality_name.lower() not in ("source", ""):
            out = Path(f"{out_base}.{quality_name}.{ext}")
        else:
            out = Path(f"{out_base}.{ext}")
        part = Path(str(out) + ".part")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.iwara.tv/",
            "Accept": "*/*",
        }

        # Prefer curl_cffi (TLS fingerprint), else httpx, else urllib
        session = None
        use_cffi = False
        try:
            from curl_cffi import requests as cffi_requests  # type: ignore

            try:
                session = cffi_requests.Session(impersonate="chrome131")
            except Exception:
                session = cffi_requests.Session(impersonate="chrome")
            use_cffi = True
        except Exception:
            try:
                import httpx

                session = httpx.Client(
                    headers=headers,
                    timeout=60.0,
                    follow_redirects=True,
                )
            except Exception:
                session = None

        resume_from = 0
        if part.is_file() and not self.force:
            try:
                resume_from = part.stat().st_size
            except OSError:
                resume_from = 0
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        try:
            if use_cffi and session is not None:
                r = session.get(full, headers=headers, stream=True, timeout=60)
                status = r.status_code
                if status in (404, 410):
                    raise RuntimeError(f"HTTP {status} Not Found @ {host_of(full)}")
                if status >= 400 and status != 206:
                    raise RuntimeError(f"HTTP {status} @ {host_of(full)}")
                total_hdr = r.headers.get("Content-Length") or r.headers.get(
                    "content-length"
                )
                total = int(total_hdr) + resume_from if total_hdr else 0
                mode = "ab" if resume_from and status == 206 else "wb"
                if mode == "wb":
                    resume_from = 0
                downloaded = resume_from
                last_t = time.time()
                last_bytes = downloaded
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        if should_abort():
                            if self._stop_flag():
                                raise RuntimeError("stopped")
                            raise StallTimeoutError(
                                f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                            )
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        dt = max(1e-3, now - last_t)
                        speed = (downloaded - last_bytes) / dt
                        last_t = now
                        last_bytes = downloaded
                        pct = min(0.99, downloaded / total) if total else max(
                            0.01, item_progress_guess(downloaded)
                        )
                        report(
                            pct,
                            heartbeat=True,
                            status="downloading",
                            speed=speed,
                            downloaded=downloaded,
                            total_bytes=total or 0,
                            message=f"CDN {srv}…",
                            server=srv,
                        )
            elif session is not None:
                # httpx
                with session.stream("GET", full, headers=headers) as r:
                    status = r.status_code
                    if status in (404, 410):
                        raise RuntimeError(f"HTTP {status} Not Found @ {host_of(full)}")
                    if status >= 400 and status != 206:
                        raise RuntimeError(f"HTTP {status} @ {host_of(full)}")
                    total_hdr = r.headers.get("Content-Length") or r.headers.get(
                        "content-length"
                    )
                    total = int(total_hdr) + resume_from if total_hdr else 0
                    mode = "ab" if resume_from and status == 206 else "wb"
                    if mode == "wb":
                        resume_from = 0
                    downloaded = resume_from
                    last_t = time.time()
                    last_bytes = downloaded
                    with open(part, mode) as f:
                        for chunk in r.iter_bytes(chunk_size=256 * 1024):
                            if should_abort():
                                if self._stop_flag():
                                    raise RuntimeError("stopped")
                                raise StallTimeoutError(
                                    f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                                )
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            dt = max(1e-3, now - last_t)
                            speed = (downloaded - last_bytes) / dt
                            last_t = now
                            last_bytes = downloaded
                            pct = (
                                min(0.99, downloaded / total)
                                if total
                                else max(0.01, item_progress_guess(downloaded))
                            )
                            report(
                                pct,
                                heartbeat=True,
                                status="downloading",
                                speed=speed,
                                downloaded=downloaded,
                                total_bytes=total or 0,
                                message=f"CDN {srv}…",
                                server=srv,
                            )
            else:
                import urllib.request

                req = urllib.request.Request(full, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status = getattr(resp, "status", 200) or 200
                    if status in (404, 410):
                        raise RuntimeError(f"HTTP {status} Not Found @ {host_of(full)}")
                    total_hdr = resp.headers.get("Content-Length")
                    total = int(total_hdr) if total_hdr else 0
                    downloaded = 0
                    last_t = time.time()
                    last_bytes = 0
                    with open(part, "wb") as f:
                        while True:
                            if should_abort():
                                if self._stop_flag():
                                    raise RuntimeError("stopped")
                                raise StallTimeoutError(
                                    f"Không có tiến trình trong {int(self.stall_timeout_sec)}s"
                                )
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            dt = max(1e-3, now - last_t)
                            speed = (downloaded - last_bytes) / dt
                            last_t = now
                            last_bytes = downloaded
                            pct = (
                                min(0.99, downloaded / total)
                                if total
                                else max(0.01, item_progress_guess(downloaded))
                            )
                            report(
                                pct,
                                heartbeat=True,
                                status="downloading",
                                speed=speed,
                                downloaded=downloaded,
                                total_bytes=total or 0,
                                message=f"CDN {srv}…",
                                server=srv,
                            )
        finally:
            try:
                if session is not None and hasattr(session, "close"):
                    session.close()
            except Exception:
                pass

        if not part.is_file() or part.stat().st_size <= 0:
            raise RuntimeError(f"File rỗng sau khi tải từ {host_of(full)}")

        # Reject tiny HTML error pages masquerading as video
        if part.stat().st_size < 4096:
            head = part.read_bytes()[:200].lower()
            if b"<html" in head or b"not found" in head or b"error" in head:
                try:
                    part.unlink(missing_ok=True)
                except Exception:
                    pass
                raise RuntimeError(f"HTTP body không phải video @ {host_of(full)}")

        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        part.replace(out)
        return out

    @staticmethod
    def _safe_filename(name: str) -> str:
        bad = '<>:"/\\|?*'
        for ch in bad:
            name = name.replace(ch, "_")
        return name[:180]


def item_progress_guess(downloaded: int) -> float:
    """Soft progress when total size unknown (caps at 0.5)."""
    # ~200 MB imaginary total
    return min(0.5, max(0.01, downloaded / (200 * 1024 * 1024)))
