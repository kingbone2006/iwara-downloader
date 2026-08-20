"""Iwara media CDN host fallbacks.

Download links from the API point at a single host (e.g. hime.iwara.tv).
When that host returns 404 / is down, the same path often works on another
mirror (mikoto, firefly, clara, …). This module rewrites hosts and orders
try-lists.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Known Iwara video CDN hosts (community + live API samples).
# Order: prefer commonly working hosts first; original host is always tried first.
IWARA_CDN_HOSTS: tuple[str, ...] = (
    "hime.iwara.tv",
    "mikoto.iwara.tv",
    "firefly.iwara.tv",
    "clara.iwara.tv",
    "files.iwara.tv",
    "filesq.iwara.tv",
)

_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)?iwara\.tv$",
    re.IGNORECASE,
)


def normalize_media_url(url: str) -> str:
    """Ensure scheme for //host/... URLs."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "https://" + u.lstrip("/")


def host_of(url: str) -> str:
    try:
        return (urlparse(normalize_media_url(url)).hostname or "").lower()
    except Exception:
        return ""


def rewrite_host(url: str, new_host: str) -> str:
    """Replace hostname, keep path/query/fragment."""
    full = normalize_media_url(url)
    if not full or not new_host:
        return full
    parts = urlparse(full)
    # urlparse with netloc
    new_netloc = new_host
    if parts.port and ":" not in new_host:
        # drop explicit port when swapping named hosts
        pass
    rebuilt = parts._replace(netloc=new_netloc, scheme=parts.scheme or "https")
    return urlunparse(rebuilt)


def candidate_download_urls(url: str, extra_hosts: list[str] | None = None) -> list[str]:
    """
    Build ordered unique URLs: original host first, then known CDN mirrors.
    Only rewrites hosts that look like *.iwara.tv (or empty → all hosts).
    """
    full = normalize_media_url(url)
    if not full:
        return []

    original = host_of(full)
    hosts: list[str] = []
    if original:
        hosts.append(original)

    for h in IWARA_CDN_HOSTS:
        if h not in hosts:
            hosts.append(h)
    if extra_hosts:
        for h in extra_hosts:
            h = (h or "").strip().lower()
            if h and h not in hosts:
                hosts.append(h)

    out: list[str] = []
    seen: set[str] = set()
    for h in hosts:
        # If original is not an iwara host, still try original once then mirrors
        if original and not _HOST_RE.match(original) and h != original:
            # only swap when original already is an iwara CDN
            continue
        candidate = rewrite_host(full, h) if h != original else full
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)

    # If original was non-iwara, still return original only
    if not out and full:
        out.append(full)
    return out


def is_cdn_retryable_error(msg: str) -> bool:
    """True if error suggests trying another CDN host immediately."""
    low = (msg or "").lower()
    keys = (
        "404",
        "410",
        "not found",
        "errors.notfound",
        "http error 404",
        "unable to download",
        "failed to open",
        "connection timed out",
        "connection refused",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "incomplete",
        "unexpected eof",
        "could not connect",
        "failed to connect",
        "getaddrinfo failed",
        "name or service not known",
        "nodename nor servname",
        "ssl",
        "curl: (28)",
        "curl: (7)",
        "curl: (6)",
        "curl: (18)",
        "curl: (35)",
        "curl: (56)",
        "403",
        "forbidden",
        "502",
        "503",
        "504",
        "522",
        "520",
        "không có tiến trình",  # stall on one host → try another
        "stall",
        "timed out",
        "timeout",
        "reset by peer",
        "remote end closed",
        "empty",
        "file rỗng",
        "không phải video",
        "filesq.iwara",
        "files.iwara",
        "mikoto.iwara",
        "hime.iwara",
        "firefly.iwara",
        "clara.iwara",
    )
    return any(k in low for k in keys)


def short_server_name(host: str) -> str:
    """mikoto.iwara.tv → mikoto"""
    h = (host or "").strip().lower()
    if h.endswith(".iwara.tv"):
        return h.split(".")[0]
    return h or "?"


def is_hard_not_found(msg: str) -> bool:
    """Video metadata itself missing (not a CDN mirror issue)."""
    low = (msg or "").lower()
    return (
        "errors.notfound" in low
        or "video may need login" in low
        or "this video is unplayable" in low
        or "private video" in low
    )
