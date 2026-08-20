"""Map download exceptions to user-facing cause + advice (VI)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorHelp:
    code: str
    title: str  # short, for status column
    detail: str  # what happened
    advice: str  # how to fix / tips

    def full_message(self) -> str:
        return f"{self.detail} | Cách xử lý: {self.advice}"

    def log_message(self) -> str:
        return f"{self.title}: {self.detail} → {self.advice}"


def diagnose_download_error(exc: BaseException | str) -> ErrorHelp:
    """Classify yt-dlp / network / app errors for the GUI."""
    msg = str(exc) if not isinstance(exc, str) else exc
    low = msg.lower()

    # --- Network / Iwara CDN ---
    if (
        "filesq.iwara.tv" in low
        or "files.iwara.tv" in low
        or ("curl: (28)" in low and "connect" in low)
        or "could not connect to server" in low
        or "failed to connect" in low
        or "connection timed out" in low
        or "connection refused" in low
        or "name or service not known" in low
        or "getaddrinfo failed" in low
        or "nodename nor servname" in low
    ):
        return ErrorHelp(
            code="net_cdn",
            title="Lỗi mạng CDN",
            detail=(
                "Không kết nối được máy chủ file Iwara "
                "(filesq.iwara.tv / files.iwara.tv) — timeout hoặc bị chặn."
            ),
            advice=(
                "Bật VPN (đổi server nếu vẫn lỗi); thử mạng khác (4G/WiFi); "
                "tắt tạm firewall/antivirus; chờ vài phút rồi bấm «Tải lại»; "
                "cập nhật yt-dlp: pip install -U yt-dlp"
            ),
        )

    if "curl: (28)" in low or "timed out" in low or "timeout" in low:
        return ErrorHelp(
            code="net_timeout",
            title="Timeout mạng",
            detail="Kết nối quá chậm hoặc đứt giữa chừng khi tải.",
            advice=(
                "Tăng delay giữa các file; tải 1 video mỗi lần; "
                "kiểm tra WiFi; dùng VPN ổn định hơn; bấm «Tải lại»"
            ),
        )

    if (
        "curl: (35)" in low
        or "ssl" in low
        or "certificate" in low
        or "handshake" in low
    ):
        return ErrorHelp(
            code="net_ssl",
            title="Lỗi SSL/HTTPS",
            detail="Không bắt tay HTTPS an toàn với server (SSL/TLS).",
            advice=(
                "Cập nhật Windows + yt-dlp; tắt proxy lạ; "
                "thử VPN; kiểm tra đồng hồ máy tính (ngày giờ sai làm hỏng SSL)"
            ),
        )

    if "403" in low or "forbidden" in low or "http error 403" in low:
        return ErrorHelp(
            code="http_403",
            title="Bị từ chối (403)",
            detail="Server từ chối tải (chặn IP, cần đăng nhập, hoặc rate-limit).",
            advice=(
                "Đăng nhập tài khoản Iwara trong Cài đặt; "
                "giảm tần suất tải (delay cao hơn); đổi VPN/IP; thử lại sau"
            ),
        )

    if (
        "đã thử hết các server" in low
        or "không tìm thấy file trên tất cả" in low
        or "không tìm thấy file để tải" in low
        or ("http 404" in low and "tất cả" in low)
    ):
        return ErrorHelp(
            code="all_cdn_failed",
            title="404 · hết server",
            detail=(
                "Không tìm thấy file — đã thử lần lượt tất cả server CDN "
                "(hime / mikoto / firefly / clara / files / filesq) "
                "và đều lỗi."
            ),
            advice=(
                "Video có thể đã bị xóa hoặc CDN down toàn bộ; "
                "thử VPN, đợi rồi «Tải lại»; mở link trên web kiểm tra"
            ),
        )

    if "404" in low or "not found" in low or "errors.notfound" in low:
        return ErrorHelp(
            code="http_404",
            title="Không tìm thấy (404)",
            detail=(
                "Server trả 404 — app sẽ tự đổi sang CDN khác ngay "
                "(mikoto/hime/firefly/…)."
            ),
            advice=(
                "Nếu vẫn lỗi sau khi thử hết server: bỏ qua video này "
                "hoặc quét lại list; thử VPN / «Tải lại» sau"
            ),
        )

    if "429" in low or "too many requests" in low or "rate" in low:
        return ErrorHelp(
            code="rate_limit",
            title="Bị giới hạn tần suất",
            detail="Gửi quá nhiều request — server yêu cầu chậm lại.",
            advice=(
                "Chờ 5–15 phút; tăng delay quét/tải; "
                "tạm dừng bớt; không mở nhiều tool tải cùng lúc"
            ),
        )

    # --- Auth / private ---
    if (
        "private" in low
        or "login" in low
        or "password" in low
        or "unauthorized" in low
        or "401" in low
        or "invalidlogin" in low
        or "needs_auth" in low
        or "sign in" in low
    ):
        return ErrorHelp(
            code="auth",
            title="Cần đăng nhập",
            detail="Video private / hạn chế khách, hoặc tài khoản sai.",
            advice=(
                "Vào Cài đặt → nhập email/mật khẩu Iwara → bấm «Đăng nhập» "
                "kiểm tra OK rồi «Tải lại» video"
            ),
        )

    # --- Stall / app ---
    if "không có tiến trình" in low or "stall" in low:
        return ErrorHelp(
            code="stall",
            title="Tải đứng yên",
            detail="Quá lâu không có tiến độ (mạng đơ hoặc server không gửi data).",
            advice=(
                "Bấm «Tải lại»; tăng timeout đứng yên trong Cài đặt; "
                "kiểm tra mạng/VPN; thử tải lại vào giờ khác"
            ),
        )

    if "yt-dlp" in low and ("not found" in low or "no module" in low or "chưa cài" in low):
        return ErrorHelp(
            code="no_ytdlp",
            title="Thiếu yt-dlp",
            detail="Chưa cài hoặc hỏng gói yt-dlp.",
            advice=(
                r"Mở CMD: cd D:\iwara-downloader rồi "
                r".\.venv\Scripts\python.exe -m pip install -U yt-dlp"
            ),
        )

    if "unable to download json metadata" in low or "json metadata" in low:
        return ErrorHelp(
            code="meta_fail",
            title="Không lấy được metadata",
            detail=(
                "yt-dlp không lấy được thông tin/link file từ Iwara "
                "(thường do mạng CDN hoặc API)."
            ),
            advice=(
                "Cập nhật yt-dlp; bật VPN; đăng nhập tài khoản; "
                "«Tải lại» sau vài phút"
            ),
        )

    if "no video formats" in low or "requested format" in low or "format is not available" in low:
        return ErrorHelp(
            code="no_format",
            title="Không có định dạng tải",
            detail="Video không có file nguồn tải được (chỉ preview/embed).",
            advice="Bỏ qua video này; thử mở trên web xem còn play được không",
        )

    if "disk" in low or "no space" in low or "errno 28" in low or "not enough space" in low:
        return ErrorHelp(
            code="disk_full",
            title="Hết dung lượng ổ đĩa",
            detail="Không còn chỗ trống để ghi file.",
            advice=(
                "Xóa bớt file; chọn thư mục lưu trên ổ khác "
                "(nút «Chọn thư mục…» trên màn hình chính)"
            ),
        )

    if "permission" in low or "access is denied" in low or "errno 13" in low:
        return ErrorHelp(
            code="permission",
            title="Không có quyền ghi file",
            detail="Windows chặn ghi vào thư mục lưu.",
            advice=(
                "Chọn thư mục khác (không phải Program Files); "
                "chạy app không cần admin nếu folder user; tắt anti-ransomware tạm"
            ),
        )

    if "stopped" in low:
        return ErrorHelp(
            code="stopped",
            title="Đã dừng",
            detail="Tác vụ bị dừng theo yêu cầu người dùng.",
            advice="Bấm «Tải đã chọn» hoặc «Tải lại» nếu muốn tiếp tục",
        )

    # --- Fallback ---
    short = msg.replace("\n", " ").strip()
    if len(short) > 220:
        short = short[:220] + "…"
    return ErrorHelp(
        code="unknown",
        title="Lỗi tải",
        detail=short or "Lỗi không xác định khi tải video.",
        advice=(
            "Xem log CMD chi tiết; cập nhật yt-dlp; thử VPN; "
            "«Tải lại» video; nếu lặp lại hãy chụp log để kiểm tra"
        ),
    )
