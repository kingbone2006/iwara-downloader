"""Download and cache video thumbnails for the GUI.

Simple fixed small pool (not CPU-scaled) so list UI stays responsive.
"""

from __future__ import annotations

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .config import APP_DIR

CACHE_DIR = APP_DIR / "cache" / "thumbs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

THUMB_W = 120
THUMB_H = 68

# Fixed small pool — no multi-core auto-tuning
_MAX_WORKERS = 3
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="thumb")

_lock = threading.Lock()
_memory: dict[str, bytes] = {}


def cache_path(video_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in video_id)[:80]
    return CACHE_DIR / f"{safe}.jpg"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.iwara.tv/",
        "Origin": "https://www.iwara.tv",
    }


def _http_get_bytes(url: str) -> bytes | None:
    headers = _headers()
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore

        r = cffi_requests.get(
            url, headers=headers, impersonate="chrome", timeout=25
        )
        if r.status_code == 200 and r.content and len(r.content) > 100:
            ctype = (r.headers.get("content-type") or "").lower()
            if "json" in ctype or "html" in ctype:
                return None
            return r.content
    except Exception:
        pass
    try:
        import httpx

        r = httpx.get(url, headers=headers, timeout=25.0, follow_redirects=True)
        if r.status_code == 200 and r.content and len(r.content) > 100:
            ctype = (r.headers.get("content-type") or "").lower()
            if "json" in ctype or "html" in ctype:
                return None
            return r.content
    except Exception:
        return None
    return None


def candidate_thumbnail_urls(
    *,
    file_id: str = "",
    thumb_index: int = 0,
    custom_id: str = "",
    video_id: str = "",
) -> list[str]:
    urls: list[str] = []
    idx = max(0, min(int(thumb_index), 99))

    def add(u: str) -> None:
        if u and u not in urls:
            urls.append(u)

    if custom_id:
        add(f"https://files.iwara.tv/image/thumbnail/{custom_id}/thumbnail-00.jpg")
        add(f"https://i.iwara.tv/image/thumbnail/{custom_id}/thumbnail-00.jpg")
        add(f"https://files.iwara.tv/image/original/{custom_id}/thumbnail-00.jpg")

    if file_id:
        add(
            f"https://files.iwara.tv/image/thumbnail/{file_id}/"
            f"thumbnail-{idx:02d}.jpg"
        )
        add(f"https://files.iwara.tv/image/thumbnail/{file_id}/thumbnail-00.jpg")
        add(
            f"https://i.iwara.tv/image/thumbnail/{file_id}/"
            f"thumbnail-{idx:02d}.jpg"
        )
        add(f"https://i.iwara.tv/image/thumbnail/{file_id}/thumbnail-00.jpg")

    return urls


def fetch_thumbnail_bytes(
    url: str,
    video_id: str = "",
    *,
    alt_urls: list[str] | None = None,
) -> bytes | None:
    if video_id:
        path = cache_path(video_id)
        if path.exists() and path.stat().st_size > 100:
            data = path.read_bytes()
            with _lock:
                _memory[video_id] = data
            return data
        with _lock:
            if video_id in _memory:
                return _memory[video_id]

    to_try: list[str] = []
    if url:
        to_try.append(url)
    if alt_urls:
        for u in alt_urls:
            if u and u not in to_try:
                to_try.append(u)

    data: bytes | None = None
    for u in to_try:
        data = _http_get_bytes(u)
        if data:
            break

    if data and video_id:
        try:
            cache_path(video_id).write_bytes(data)
        except OSError:
            pass
        with _lock:
            _memory[video_id] = data
    return data


def load_pil_image(data: bytes):
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img = img.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    return img


def placeholder_pil():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (THUMB_W, THUMB_H), (45, 45, 50))
    draw = ImageDraw.Draw(img)
    draw.rectangle((2, 2, THUMB_W - 3, THUMB_H - 3), outline=(80, 80, 90))
    cx, cy = THUMB_W // 2, THUMB_H // 2
    draw.polygon(
        [(cx - 10, cy - 12), (cx - 10, cy + 12), (cx + 14, cy)],
        fill=(120, 120, 130),
    )
    return img


def fetch_async(
    video_id: str,
    url: str,
    on_done: Callable[[str, object | None], None],
    *,
    alt_urls: list[str] | None = None,
) -> None:
    def run() -> None:
        try:
            if not url and not alt_urls:
                on_done(video_id, placeholder_pil())
                return
            data = fetch_thumbnail_bytes(url, video_id, alt_urls=alt_urls)
            if not data:
                on_done(video_id, placeholder_pil())
                return
            on_done(video_id, load_pil_image(data))
        except Exception:
            try:
                on_done(video_id, placeholder_pil())
            except Exception:
                on_done(video_id, None)

    try:
        _executor.submit(run)
    except Exception:
        threading.Thread(target=run, daemon=True).start()
