"""Iwara listing client (hashtag / channel) — real API by default."""

from __future__ import annotations

import random
import time
from typing import Any, Callable
from urllib.parse import quote

from .models import ScanResult, VideoItem, VideoStatus

API_BASES = (
    "https://api.iwara.tv",
    "https://apiq.iwara.tv",
)
PAGE_SIZE = 32
SITE = "https://www.iwara.tv"
# Soft ceiling when scan limit is 0 (unlimited) — avoids runaway memory
UNLIMITED_SAFETY_CAP = 10_000
# Minimum pause between API pages when scanning unlimited (limit=0)
UNLIMITED_MIN_PAGE_DELAY = 1.5
# Login / API can be slow behind Cloudflare (522 = origin timeout)
REQUEST_TIMEOUT_SEC = 45
LOGIN_RETRIES = 3

# Common short names → real tag ids on iwara
TAG_ALIASES = {
    "mmd": "mikumikudance",
    "mmd_dance": "mikumikudance",
    "miku_miku_dance": "mikumikudance",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": SITE,
    "Referer": f"{SITE}/",
}


class IwaraAPIError(RuntimeError):
    pass


def format_api_http_error(status: int, url: str, text: str = "") -> str:
    """Human-readable API/Cloudflare errors (VI-friendly)."""
    snippet = (text or "").strip().replace("\n", " ")
    if len(snippet) > 120:
        snippet = snippet[:120] + "…"
    # Cloudflare edge codes
    if status == 522:
        return (
            f"HTTP 522 (Cloudflare timeout) — máy chủ Iwara API không trả lời "
            f"({url}). Không phải sai mật khẩu. Thử VPN, mạng khác, hoặc đợi vài phút."
        )
    if status in (520, 521, 523, 524, 525, 526):
        return (
            f"HTTP {status} (Cloudflare/API lỗi) — {url}. "
            "Server Iwara đang lỗi hoặc bị chặn từ mạng của bạn. Thử VPN / đợi rồi thử lại."
        )
    if status == 403:
        return (
            f"HTTP 403 Forbidden — {url}. "
            "Có thể bị Cloudflare chặn (IP/VPN). Đổi VPN server hoặc thử lại sau."
        )
    if status == 401:
        return (
            f"HTTP 401 Unauthorized — email/mật khẩu sai hoặc token hết hạn. "
            f"({url})"
        )
    if status == 429:
        return (
            f"HTTP 429 Too Many Requests — bị rate-limit. "
            "Giảm số luồng, tăng delay, đợi vài phút."
        )
    if status >= 500:
        return (
            f"HTTP {status} — server Iwara lỗi tạm thời ({url}). "
            "Đợi rồi thử lại; không phải do app."
        )
    if snippet and not snippet.lower().startswith("<!doctype"):
        return f"HTTP {status} {url}: {snippet}"
    return f"HTTP {status} {url}"


def is_transient_api_error(msg: str) -> bool:
    """True if error is network/CDN — safe to retry, do not cache as bad credentials."""
    low = (msg or "").lower()
    keys = (
        "522",
        "520",
        "521",
        "523",
        "524",
        "525",
        "timeout",
        "timed out",
        "cloudflare",
        "connection",
        "connect",
        "reset",
        "unreachable",
        "temporarily",
        "502",
        "503",
        "504",
        "429",
    )
    return any(k in low for k in keys)


class IwaraAPI:
    def __init__(
        self,
        *,
        demo_mode: bool = False,
        scan_delay_sec: float = 1.0,
        stop_flag: Callable[[], bool] | None = None,
        pause_wait: Callable[[], None] | None = None,
        email: str = "",
        password: str = "",
        on_log: Callable[[str], None] | None = None,
        on_batch: Callable[[list, int], None] | None = None,
        on_meta: Callable[[str, str], None] | None = None,
    ) -> None:
        self.demo_mode = demo_mode
        self.scan_delay_sec = max(0.0, float(scan_delay_sec))
        self._stop_flag = stop_flag or (lambda: False)
        self._pause_wait = pause_wait or (lambda: None)
        self.email = (email or "").strip()
        self.password = password or ""
        self._token: str | None = None
        self._login_error: str | None = None
        self._base = API_BASES[0]
        # on_batch(page_items, page_index) — stream results to UI incrementally
        self._on_batch = on_batch
        # on_meta(source, query) — resolved folder key before first page
        self._on_meta = on_meta
        if on_log is not None:
            self._on_log = on_log
        else:
            from .debug_log import console_log

            self._on_log = console_log
        self._session = self._make_session()

    def _log(self, msg: str) -> None:
        try:
            self._on_log(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------ public
    def search_by_hashtag(self, tag: str, limit: int = 32) -> ScanResult:
        raw_tag = tag.strip().lstrip("#")
        if not raw_tag:
            return ScanResult(items=[], source="hashtag", query=raw_tag)

        if self.demo_mode:
            self._log("[DEMO] Dùng dữ liệu giả — tắt Demo trong Cài đặt để lấy video thật.")
            return self._mock_scan(source="hashtag", query=raw_tag, limit=limit)

        resolved, suggestions = self.resolve_tag(raw_tag)
        if resolved != raw_tag.lower().replace(" ", "_"):
            self._log(
                f"Hashtag '{raw_tag}' → tag id '{resolved}'"
                + (f" (gợi ý: {', '.join(suggestions[:6])})" if suggestions else "")
            )
        else:
            self._log(f"Đang quét tag: {resolved}")
        if self._on_meta is not None:
            try:
                self._on_meta("hashtag", resolved)
            except Exception:
                pass

        items = self._paginate_videos(
            extra_params={"tags": resolved},
            limit=limit,
            label=f"tag:{resolved}",
        )
        if not items and suggestions:
            # try next suggestions if first yields empty (rare)
            for alt in suggestions:
                if alt == resolved:
                    continue
                self._log(f"Thử tag khác: {alt}")
                items = self._paginate_videos(
                    extra_params={"tags": alt},
                    limit=limit,
                    label=f"tag:{alt}",
                )
                if items:
                    resolved = alt
                    break

        return ScanResult(items=items, source="hashtag", query=resolved)

    @staticmethod
    def split_channel_names(value: str) -> list[str]:
        """Split 'a, b, c' or 'a，b' into unique normalized usernames (order kept)."""
        raw = (value or "").strip()
        if not raw:
            return []
        # Support ASCII comma and full-width Chinese comma
        parts = raw.replace("，", ",").split(",")
        seen: set[str] = set()
        out: list[str] = []
        for part in parts:
            name = IwaraAPI._normalize_username(part)
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out

    def search_by_channel(self, username: str, limit: int = 32) -> ScanResult:
        """
        Scan one channel, or several if names are comma-separated
        (e.g. ``calbii, vectorcell, lanniao``). Limit applies per channel.
        """
        names = self.split_channel_names(username)
        if not names:
            return ScanResult(items=[], source="channel", query="")

        if len(names) == 1:
            return self._search_one_channel(names[0], limit=limit)

        # Multi-channel: sequential scan, stream batches for each
        all_items: list[VideoItem] = []
        self._log(f"Quét {len(names)} kênh: {', '.join(names)}")
        for i, name in enumerate(names, start=1):
            self._pause_wait()
            if self._stop_flag():
                self._log(f"Đã dừng multi-channel ({len(all_items)} video).")
                break
            self._log(f"—— Kênh {i}/{len(names)}: {name} ——")
            try:
                part = self._search_one_channel(name, limit=limit)
            except IwaraAPIError as exc:
                self._log(f"⚠ Bỏ qua kênh {name}: {exc}")
                continue
            all_items.extend(part.items)
            # Small breathe between channels
            if i < len(names) and not self._stop_flag() and self.scan_delay_sec > 0:
                self._sleep_interruptible(max(0.5, float(self.scan_delay_sec)))

        joined = ",".join(names)
        self._log(f"Multi-channel xong: {len(all_items)} video từ {len(names)} kênh.")
        return ScanResult(items=all_items, source="channel", query=joined)

    def _search_one_channel(self, username: str, limit: int = 32) -> ScanResult:
        username = self._normalize_username(username)
        if not username:
            return ScanResult(items=[], source="channel", query=username)

        if self.demo_mode:
            self._log("[DEMO] Dùng dữ liệu giả — tắt Demo trong Cài đặt để lấy kênh thật.")
            result = self._mock_scan(source="channel", query=username, limit=limit)
            for it in result.items:
                it.channel = username
            return result

        self._log(f"Đang lấy profile: {username}")
        profile = self._get_json(f"/profile/{quote(username)}")
        user = profile.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            raise IwaraAPIError(f"Không tìm thấy kênh: {username}")

        display = user.get("name") or user.get("username") or username
        self._log(f"Kênh: {display} (id={user_id})")
        if self._on_meta is not None:
            try:
                self._on_meta("channel", username)
            except Exception:
                pass

        items = self._paginate_videos(
            extra_params={"user": user_id},
            limit=limit,
            label=f"user:{username}",
            default_author=str(display),
            channel_key=username,
        )
        return ScanResult(items=items, source="channel", query=username)

    def get_channel_summary(self, username: str) -> dict[str, Any]:
        """
        Channel stats: display name + total video count on Iwara.

        Note: Iwara's /videos ``count`` field is NOT a reliable total
        (with limit=1 it often returns 2). We binary-search the last page.
        """
        username = self._normalize_username(username)
        if not username:
            raise IwaraAPIError("Tên kênh trống.")

        if self.demo_mode:
            return {
                "username": username,
                "display_name": f"[DEMO] {username}",
                "total": 42,
                "user_id": "demo",
            }

        profile = self._get_json(f"/profile/{quote(username)}")
        user = profile.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            raise IwaraAPIError(f"Không tìm thấy kênh: {username}")
        display = user.get("name") or user.get("username") or username

        total = self._count_user_videos(str(user_id))

        return {
            "username": username,
            "display_name": str(display),
            "total": max(0, total),
            "user_id": str(user_id),
        }

    def get_channel_gap_stats(
        self,
        username: str,
        local_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        Profile + full video list walk to compute totals and gaps vs local files.

        Returns display_name, total, missing_public, missing_private.
        ``missing_public`` = remote videos not on disk and not private (worth DL).
        ``missing_private`` = remote not on disk but private (safe to skip).
        """
        username = self._normalize_username(username)
        if not username:
            raise IwaraAPIError("Tên kênh trống.")
        local_ids = local_ids or set()

        if self.demo_mode:
            # Fake a few private gaps for UI testing
            total = 42
            matched = min(len(local_ids), 30)
            miss = max(0, total - matched)
            miss_priv = min(3, miss)
            miss_pub = max(0, miss - miss_priv)
            return {
                "username": username,
                "display_name": f"[DEMO] {username}",
                "total": total,
                "user_id": "demo",
                "missing_public": miss_pub,
                "missing_private": miss_priv,
            }

        profile = self._get_json(f"/profile/{quote(username)}")
        user = profile.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            raise IwaraAPIError(f"Không tìm thấy kênh: {username}")
        display = user.get("name") or user.get("username") or username

        limit = 50
        page = 0
        total = 0
        missing_public = 0
        missing_private = 0
        # Slightly faster than full scan delay — still yield to stop/pause
        page_pause = max(0.15, min(0.6, float(self.scan_delay_sec) * 0.35))

        while page < 5000:
            self._pause_wait()
            if self._stop_flag():
                break
            data = self._get_json(
                "/videos",
                {
                    "sort": "date",
                    "rating": "all",
                    "page": page,
                    "limit": limit,
                    "user": user_id,
                },
            )
            if isinstance(data, list):
                results = data
            elif isinstance(data, dict):
                results = data.get("results") or []
            else:
                results = []
            if not results:
                break

            for raw in results:
                if not isinstance(raw, dict):
                    continue
                vid = str(raw.get("id") or "").strip()
                if not vid:
                    continue
                total += 1
                if vid in local_ids:
                    continue
                if bool(raw.get("private")):
                    missing_private += 1
                else:
                    missing_public += 1

            if len(results) < limit:
                break
            page += 1
            if page_pause > 0 and not self._stop_flag():
                self._sleep_interruptible(page_pause)

        return {
            "username": username,
            "display_name": str(display),
            "total": max(0, total),
            "user_id": str(user_id),
            "missing_public": max(0, missing_public),
            "missing_private": max(0, missing_private),
        }

    def _count_user_videos(self, user_id: str) -> int:
        """
        Count public videos for a user id.

        Iwara caps page size at ~50 and its ``count`` field is unreliable
        (often ``limit+1`` / ``(page+1)*limit+1``, NOT the real total).
        Strategy: binary-search the last non-empty page, then
        total = page * limit + len(results).
        """
        limit = 50  # API hard-cap
        base = {
            "sort": "date",
            "rating": "all",
            "user": user_id,
            "limit": limit,
        }

        def page_len(page: int) -> int:
            self._pause_wait()
            if self._stop_flag():
                return -1
            data = self._get_json("/videos", {**base, "page": int(page)})
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                return len(data.get("results") or [])
            return 0

        n0 = page_len(0)
        if n0 <= 0:
            return 0
        if n0 < limit:
            return n0

        # Exponential search for an upper bound (empty or partial page)
        last_full = 0
        hi = 1
        while hi <= 5000:
            if self._stop_flag():
                return (last_full + 1) * limit
            n = page_len(hi)
            if n < 0:
                return (last_full + 1) * limit
            if n == 0:
                break
            if n < limit:
                return hi * limit + n
            last_full = hi
            nxt = hi * 2
            if nxt == hi:
                break
            hi = nxt
        else:
            return (last_full + 1) * limit

        # Binary search last content page in (last_full, hi)
        left = last_full + 1
        right = hi - 1
        while left <= right:
            if self._stop_flag():
                break
            mid = (left + right) // 2
            n = page_len(mid)
            if n < 0:
                break
            if n == 0:
                right = mid - 1
            elif n < limit:
                return mid * limit + n
            else:
                last_full = mid
                left = mid + 1

        # Pages 0..last_full are full; next is empty
        return (last_full + 1) * limit

    def get_video_file_sources(self, video_id: str) -> list[dict[str, Any]]:
        """
        Resolve downloadable formats for a video id via API fileUrl.
        Returns list of {name, url, type} sorted best-first (Source > 540 > 360).
        """
        import hashlib
        from urllib.parse import parse_qs, urlparse

        video_id = (video_id or "").strip()
        if not video_id:
            raise IwaraAPIError("Video id trống.")

        data = self._get_json(f"/video/{quote(video_id)}")
        if not isinstance(data, dict):
            raise IwaraAPIError(f"Phản hồi video không hợp lệ: {video_id}")

        errmsg = data.get("message")
        if errmsg:
            raise IwaraAPIError(f"Iwara: {errmsg}")

        file_url = data.get("fileUrl")
        if not file_url:
            if data.get("embedUrl"):
                raise IwaraAPIError("Video chỉ có embed, không có file nguồn.")
            raise IwaraAPIError("Không tìm thấy fileUrl — video không thể tải.")

        up = urlparse(str(file_url))
        q = parse_qs(up.query)
        paths = up.path.rstrip("/").split("/")
        expires = (q.get("expires") or [None])[0]
        if not expires or not paths[-1]:
            raise IwaraAPIError("fileUrl thiếu expires/path — không tạo được X-Version.")

        # Same salt as yt-dlp iwara extractor
        x_version = hashlib.sha1(
            "_".join(
                (paths[-1], str(expires), "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN")
            ).encode()
        ).hexdigest()

        headers = {
            **BROWSER_HEADERS,
            "X-Version": x_version,
            "Referer": f"{SITE}/",
        }
        # Prefer authenticated media when logged in
        headers.update(self._auth_headers())

        last_err: Exception | None = None
        body: Any = None
        for base_host in (None,):  # fileUrl already absolute; single request
            try:
                if self._is_cffi():
                    r = self._session.get(
                        str(file_url), headers=headers, timeout=REQUEST_TIMEOUT_SEC
                    )
                    status = r.status_code
                    try:
                        body = r.json()
                    except Exception:
                        body = None
                    text = r.text or ""
                else:
                    r = self._session.get(
                        str(file_url), headers=headers, timeout=REQUEST_TIMEOUT_SEC
                    )
                    status = r.status_code
                    try:
                        body = r.json()
                    except Exception:
                        body = None
                    text = r.text or ""
                if status >= 400:
                    last_err = IwaraAPIError(
                        format_api_http_error(status, str(file_url), text)
                    )
                    raise last_err
                break
            except IwaraAPIError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = IwaraAPIError(f"Không lấy được danh sách file: {exc}")
                raise last_err from exc

        if not isinstance(body, list):
            raise IwaraAPIError(
                f"Danh sách file không hợp lệ cho {video_id}: {type(body).__name__}"
            )

        pref = {"Source": 100, "540": 80, "360": 60, "preview": 10}
        formats: list[dict[str, Any]] = []
        for raw in body:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "")
            src = raw.get("src") or {}
            if not isinstance(src, dict):
                src = {}
            url = src.get("download") or src.get("view") or ""
            if not url:
                continue
            formats.append(
                {
                    "name": name,
                    "url": str(url),
                    "type": str(raw.get("type") or "video/mp4"),
                    "score": pref.get(name, 40),
                }
            )
        formats.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
        if not formats:
            raise IwaraAPIError(f"Không có định dạng tải cho video {video_id}.")
        return formats

    def resolve_tag(self, tag: str) -> tuple[str, list[str]]:
        """Map user input to a valid iwara tag id + suggestion list."""
        normalized = tag.strip().lstrip("#").lower().replace(" ", "_")
        if not normalized:
            raise IwaraAPIError("Hashtag trống.")

        if normalized in TAG_ALIASES:
            aliased = TAG_ALIASES[normalized]
            suggestions = self._autocomplete_tags(normalized)
            if aliased not in suggestions:
                suggestions = [aliased] + suggestions
            return aliased, suggestions

        suggestions = self._autocomplete_tags(normalized)

        # Exact id match in suggestions
        for sid in suggestions:
            if sid.lower() == normalized:
                return sid, suggestions

        # Probe: does this tag id work on /videos?
        if self._tag_works(normalized):
            return normalized, suggestions

        # Prefer category-like or closest suggestion
        if suggestions:
            return suggestions[0], suggestions

        raise IwaraAPIError(
            f"Không tìm thấy tag hợp lệ cho '{tag}'. "
            "Thử id tag trên iwara (vd: mikumikudance, genshin_impact, koikatsu)."
        )

    def _autocomplete_tags(self, query: str) -> list[str]:
        try:
            data = self._get_json("/autocomplete/tags", {"query": query})
            results = data.get("results") or []
            return [str(r["id"]) for r in results if r.get("id")]
        except IwaraAPIError as exc:
            # Login failures must surface; other errors → empty suggestions
            if self._login_error or "Đăng nhập" in str(exc):
                raise
            return []
        except Exception:
            return []

    def _tag_works(self, tag_id: str) -> bool:
        try:
            data = self._get_json(
                "/videos",
                {
                    "sort": "date",
                    "rating": "all",
                    "page": 0,
                    "limit": 1,
                    "tags": tag_id,
                },
            )
            return isinstance(data, dict) and "results" in data
        except IwaraAPIError as exc:
            if self._login_error or "Đăng nhập" in str(exc):
                raise
            return False

    # ----------------------------------------------------------------- session
    def _make_session(self) -> Any:
        try:
            from curl_cffi import requests as cffi_requests  # type: ignore

            # Prefer recent Chrome TLS fingerprint (Cloudflare friendlier)
            try:
                s = cffi_requests.Session(impersonate="chrome131")
            except Exception:
                s = cffi_requests.Session(impersonate="chrome")
            s.headers.update(BROWSER_HEADERS)
            return s
        except Exception:
            try:
                import httpx

                return httpx.Client(
                    headers=BROWSER_HEADERS,
                    timeout=float(REQUEST_TIMEOUT_SEC),
                    follow_redirects=True,
                )
            except Exception as exc:  # pragma: no cover
                raise IwaraAPIError(
                    "Cần cài httpx hoặc curl_cffi: pip install -r requirements.txt"
                ) from exc

    def test_login(self) -> tuple[bool, str]:
        """Try login with current credentials. Returns (ok, message)."""
        email = (self.email or "").strip()
        password = self.password or ""
        if not email or not password:
            return False, "missing_credentials"
        # Force a fresh attempt
        self._token = None
        self._login_error = None

        last_err = ""
        for attempt in range(1, LOGIN_RETRIES + 1):
            self._log(f"Đăng nhập API… (lần {attempt}/{LOGIN_RETRIES})")
            try:
                data = self._request(
                    "POST",
                    "/user/login",
                    json_body={"email": email, "password": password},
                    skip_auth=True,
                    timeout=REQUEST_TIMEOUT_SEC,
                )
            except IwaraAPIError as exc:
                last_err = str(exc)
                self._log(f"Login attempt {attempt} lỗi: {last_err[:160]}")
                if is_transient_api_error(last_err) and attempt < LOGIN_RETRIES:
                    time.sleep(1.2 * attempt)
                    continue
                # Only sticky-cache hard credential failures — not 522/timeout
                if not is_transient_api_error(last_err):
                    self._login_error = last_err
                return False, last_err

            token = None
            if isinstance(data, dict):
                token = data.get("token") or data.get("accessToken") or data.get("access_token")
            if not token:
                msg = (
                    (data.get("message") if isinstance(data, dict) else None)
                    or (data.get("error") if isinstance(data, dict) else None)
                    or str(data)
                )
                last_err = str(msg)
                # Wrong password etc.
                if not is_transient_api_error(last_err):
                    self._login_error = last_err
                return False, last_err

            self._token = str(token)
            self._login_error = None
            display = email
            try:
                me = self._request("GET", "/user", skip_auth=False)
                if isinstance(me, dict):
                    user = me.get("user") or me
                    display = (
                        user.get("name")
                        or user.get("username")
                        or email
                    )
            except Exception:
                pass
            self._log(f"Đăng nhập API thành công ({display}).")
            return True, str(display)

        return False, last_err or "Đăng nhập thất bại (không rõ lỗi)."

    def _ensure_login(self) -> None:
        """Login once if credentials are set. Must not call through authenticated _request."""
        if self._token:
            return
        # Sticky error only for bad credentials — allow retry after network 522
        if self._login_error and not is_transient_api_error(self._login_error):
            raise IwaraAPIError(self._login_error)
        if not self.email or not self.password:
            return
        ok, msg = self.test_login()
        if not ok:
            raise IwaraAPIError(f"Đăng nhập thất bại: {msg}")

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _is_cffi(self) -> bool:
        return "curl_cffi" in type(self._session).__module__

    def _ordered_bases(self) -> tuple[str, ...]:
        """Prefer last working base, then the rest."""
        if self._base and self._base in API_BASES:
            rest = [b for b in API_BASES if b != self._base]
            return (self._base, *rest)
        return API_BASES

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        skip_auth: bool = False,
        timeout: float | None = None,
    ) -> Any:
        # Login endpoint must skip this, otherwise infinite recursion.
        if not skip_auth:
            self._ensure_login()
        timeout = float(timeout if timeout is not None else REQUEST_TIMEOUT_SEC)
        last_err: Exception | None = None
        for base in self._ordered_bases():
            url = f"{base}{path}"
            try:
                headers = {**BROWSER_HEADERS}
                if json_body is not None:
                    headers["Content-Type"] = "application/json"
                if not skip_auth:
                    headers.update(self._auth_headers())
                if self._is_cffi():
                    r = self._session.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                        timeout=timeout,
                    )
                    status = r.status_code
                    text = r.text or ""
                    try:
                        body = r.json()
                    except Exception:
                        body = None
                else:
                    r = self._session.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                        timeout=timeout,
                    )
                    status = r.status_code
                    text = r.text or ""
                    try:
                        body = r.json()
                    except Exception:
                        body = None

                if status >= 400:
                    last_err = IwaraAPIError(
                        format_api_http_error(status, url, text)
                    )
                    # Try next mirror on server/Cloudflare errors
                    if status >= 500 or status in (403, 429):
                        continue
                    # 401/400 on login = real auth failure, stop early
                    raise last_err
                self._base = base
                return body if body is not None else {}
            except IwaraAPIError as exc:
                last_err = exc
                # Hard auth errors: do not try other bases with same bad password
                msg = str(exc)
                if "HTTP 401" in msg or "HTTP 400" in msg:
                    raise
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = IwaraAPIError(
                    f"Kết nối lỗi {url}: {type(exc).__name__}: {exc}"
                )
                continue
        raise IwaraAPIError(str(last_err) if last_err else "API request failed")

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    # ----------------------------------------------------------------- listing
    def _paginate_videos(
        self,
        *,
        extra_params: dict[str, Any],
        limit: int,
        label: str,
        default_author: str = "",
        channel_key: str = "",
    ) -> list[VideoItem]:
        items: list[VideoItem] = []
        page = 0
        # 0 or negative → unlimited (until API empty / user Stop)
        unlimited = int(limit) <= 0
        if unlimited:
            cap = UNLIMITED_SAFETY_CAP
            # Always pace unlimited scans so UI/API don't freeze or get rate-limited
            page_delay = max(float(self.scan_delay_sec), UNLIMITED_MIN_PAGE_DELAY)
            self._log(
                f"{label}: quét không giới hạn (0), "
                f"delay {page_delay:.1f}s giữa các trang…"
            )
        else:
            cap = max(1, min(int(limit), UNLIMITED_SAFETY_CAP))
            page_delay = max(0.0, float(self.scan_delay_sec))

        while len(items) < cap:
            self._pause_wait()
            if self._stop_flag():
                self._log(f"{label}: đã dừng theo yêu cầu ({len(items)} video).")
                break
            page_limit = min(PAGE_SIZE, cap - len(items))
            params = {
                "sort": "date",
                "rating": "all",
                "page": page,
                "limit": page_limit,
                **extra_params,
            }
            self._log(f"API {label} page={page}… ({len(items)} video)")
            data = self._get_json("/videos", params=params)
            if isinstance(data, list):
                results = data
            elif isinstance(data, dict):
                results = data.get("results") or []
            else:
                results = []
            if not results:
                break

            page_items: list[VideoItem] = []
            for raw in results:
                if self._stop_flag() or len(items) + len(page_items) >= cap:
                    break
                item = self._parse_video(raw, channel_key=channel_key)
                if item:
                    if default_author and (
                        not item.author or item.author == "unknown"
                    ):
                        item.author = default_author
                    if channel_key and not item.channel:
                        item.channel = channel_key
                    page_items.append(item)

            if page_items:
                items.extend(page_items)
                # Stream page to UI immediately (avoid one huge list dump)
                if self._on_batch is not None:
                    try:
                        self._on_batch(list(page_items), page)
                    except Exception:
                        pass

            # Do NOT trust data["count"] — Iwara often returns a fake total
            # (e.g. limit+1) instead of the real channel size.
            if len(results) < page_limit:
                break
            page += 1
            # Delay before next page (mandatory for unlimited)
            if len(items) < cap and page_delay > 0:
                self._log(f"Chờ {page_delay:.1f}s trước trang tiếp…")
                self._sleep_interruptible(page_delay)
            elif unlimited and len(items) < cap:
                # Fallback breathe even if delay misconfigured
                self._sleep_interruptible(UNLIMITED_MIN_PAGE_DELAY)

        if unlimited and len(items) >= UNLIMITED_SAFETY_CAP:
            self._log(
                f"Dừng ở {UNLIMITED_SAFETY_CAP} video (giới hạn an toàn). "
                "Bấm Dừng sớm hơn nếu cần."
            )
        self._log(f"Lấy được {len(items)} video ({label}).")
        return items

    def _parse_video(
        self, raw: dict[str, Any], *, channel_key: str = ""
    ) -> VideoItem | None:
        vid = raw.get("id")
        if not vid:
            return None
        user = raw.get("user") or {}
        # Prefer display name, then username
        author = (
            user.get("name")
            or user.get("username")
            or user.get("id")
            or "unknown"
        )
        # Folder key: explicit channel scan username, else uploader username
        ch = (channel_key or "").strip() or str(user.get("username") or "").strip()
        title = raw.get("title") or raw.get("slug") or str(vid)
        views = raw.get("numViews") or raw.get("views") or 0
        try:
            views_i = int(views)
        except (TypeError, ValueError):
            views_i = 0
        size_b, dur_s = self._file_size_duration(raw)
        thumb_url, thumb_alts = self._thumbnail_meta(raw)
        is_private = bool(raw.get("private"))
        status = VideoStatus.PRIVATE if is_private else VideoStatus.READY
        return VideoItem(
            id=str(vid),
            title=str(title),
            author=str(author),
            views=views_i,
            url=f"{SITE}/video/{vid}",
            thumbnail_url=thumb_url,
            thumbnail_alts=thumb_alts,
            size_bytes=size_b,
            duration_sec=dur_s,
            selected=False,
            status=status,
            channel=ch,
            is_private=is_private,
        )

    @staticmethod
    def _file_size_duration(raw: dict[str, Any]) -> tuple[int, int]:
        """Extract file size (bytes) and duration (sec) from list payload."""
        file_info = raw.get("file")
        size_b = 0
        dur_s = 0
        if isinstance(file_info, dict):
            try:
                size_b = int(file_info.get("size") or 0)
            except (TypeError, ValueError):
                size_b = 0
            try:
                dur_s = int(float(file_info.get("duration") or 0))
            except (TypeError, ValueError):
                dur_s = 0
        if size_b <= 0:
            try:
                size_b = int(raw.get("fileSize") or raw.get("size") or 0)
            except (TypeError, ValueError):
                size_b = 0
        return max(0, size_b), max(0, dur_s)

    @staticmethod
    def _thumbnail_meta(raw: dict[str, Any]) -> tuple[str, list[str]]:
        """
        Return (primary_url, alt_urls) for a video list item.
        Channel listings sometimes need fallbacks (custom cover, i.iwara.tv).
        """
        from .thumbnails import candidate_thumbnail_urls

        file_info = raw.get("file")
        file_id = ""
        if isinstance(file_info, dict):
            file_id = str(file_info.get("id") or "")
        elif isinstance(file_info, str):
            file_id = file_info
        if not file_id:
            file_id = str(raw.get("fileId") or "")

        custom = raw.get("customThumbnail")
        custom_id = ""
        if isinstance(custom, dict):
            custom_id = str(custom.get("id") or "")
        elif isinstance(custom, str):
            custom_id = custom

        idx = raw.get("thumbnail")
        try:
            idx_i = int(idx) if idx is not None else 0
        except (TypeError, ValueError):
            idx_i = 0

        urls = candidate_thumbnail_urls(
            file_id=file_id,
            thumb_index=idx_i,
            custom_id=custom_id,
            video_id=str(raw.get("id") or ""),
        )
        if not urls:
            return "", []
        return urls[0], urls[1:]

    @staticmethod
    def _thumbnail_url(raw: dict[str, Any]) -> str:
        primary, _alts = IwaraAPI._thumbnail_meta(raw)
        return primary

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._pause_wait()
            if self._stop_flag():
                return
            time.sleep(0.1)

    @staticmethod
    def _normalize_username(value: str) -> str:
        value = value.strip()
        if "iwara.tv" in value:
            parts = value.rstrip("/").split("/")
            for key in ("profile", "users"):
                if key in parts:
                    idx = parts.index(key)
                    if idx + 1 < len(parts):
                        return parts[idx + 1]
        return value.lstrip("@")

    def _mock_scan(self, source: str, query: str, limit: int) -> ScanResult:
        items: list[VideoItem] = []
        # Demo: 0 → 80 fake items; otherwise up to 40
        if int(limit) <= 0:
            n = 80
        else:
            n = max(1, min(int(limit), 40))
        batch: list[VideoItem] = []
        page = 0
        for i in range(n):
            if self._stop_flag():
                break
            self._pause_wait()
            time.sleep(0.03)
            vid = f"{source[:1]}{abs(hash(query)) % 10000:04d}{i:03d}"
            batch.append(
                VideoItem(
                    id=vid,
                    title=f"[DEMO] [{query}] Sample video #{i + 1}",
                    author=f"[DEMO] {query if source == 'channel' else f'creator_{(i % 5) + 1}'}",
                    views=random.randint(100, 50_000),
                    url=f"{SITE}/video/{vid}",
                    size_bytes=random.randint(20_000_000, 200_000_000),
                    selected=False,
                )
            )
            # Stream every 8 items in demo mode
            if len(batch) >= 8 or i == n - 1:
                items.extend(batch)
                if self._on_batch is not None:
                    try:
                        self._on_batch(list(batch), page)
                    except Exception:
                        pass
                batch = []
                page += 1
        return ScanResult(items=items, source=source, query=query)
