"""Simple i18n helpers for Vietnamese / English / Chinese."""

from __future__ import annotations

from typing import Callable

SUPPORTED_LANGS = ("vi", "en", "zh")

LANG_LABELS = {
    "vi": "Tiếng Việt",
    "en": "English",
    "zh": "中文",
}

# key -> {lang: text}
STRINGS: dict[str, dict[str, str]] = {
    "app_title": {
        "vi": "Iwara Downloader",
        "en": "Iwara Downloader",
        "zh": "Iwara 下载器",
    },
    "language": {
        "vi": "Ngôn ngữ",
        "en": "Language",
        "zh": "语言",
    },
    "tab_hashtag": {
        "vi": "Hashtag",
        "en": "Hashtag",
        "zh": "标签",
    },
    "tab_channel": {
        "vi": "Kênh",
        "en": "Channel",
        "zh": "频道",
    },
    "tab_settings": {
        "vi": "Cài đặt",
        "en": "Settings",
        "zh": "设置",
    },
    "tab_library": {
        "vi": "Thư viện kênh",
        "en": "Channel library",
        "zh": "频道库",
    },
    "library_hint": {
        "vi": "Quét tất cả thư mục tải (nhiều ổ). Kênh = tên folder. Làm mới so khớp list (private ghi chú). Update chỉ tải video public còn thiếu — lưu đúng ổ đang chứa kênh đó.",
        "en": "Scans all download folders (multi-drive). Channel = folder name. Refresh compares lists. Update only missing public videos — saves to the drive that already holds the channel.",
        "zh": "扫描全部下载目录（多盘）。频道=文件夹名。刷新比对列表。更新只下载缺少的公开视频——保存到该频道所在磁盘。",
    },
    "btn_library_refresh": {
        "vi": "Làm mới",
        "en": "Refresh",
        "zh": "刷新",
    },
    "btn_library_update_all": {
        "vi": "Update tất cả",
        "en": "Update all",
        "zh": "全部更新",
    },
    "btn_library_open": {
        "vi": "Xem / chọn",
        "en": "Open",
        "zh": "查看",
    },
    "btn_library_update_one": {
        "vi": "Update",
        "en": "Update",
        "zh": "更新",
    },
    "library_col_channel": {
        "vi": "Kênh",
        "en": "Channel",
        "zh": "频道",
    },
    "library_col_drive": {
        "vi": "Ổ",
        "en": "Drive",
        "zh": "盘",
    },
    "library_col_local": {
        "vi": "Trên máy",
        "en": "On disk",
        "zh": "本机",
    },
    "library_col_remote": {
        "vi": "Trên kênh",
        "en": "On channel",
        "zh": "频道总数",
    },
    "library_col_new": {
        "vi": "Còn thiếu",
        "en": "Missing",
        "zh": "缺少",
    },
    "library_col_actions": {
        "vi": "Thao tác",
        "en": "Actions",
        "zh": "操作",
    },
    "library_empty": {
        "vi": "Chưa có folder kênh nào trong các thư mục tải.",
        "en": "No channel folders in any download directory yet.",
        "zh": "各下载目录中尚无频道文件夹。",
    },
    "library_remote_unknown": {
        "vi": "?",
        "en": "?",
        "zh": "?",
    },
    "library_missing_zero": {
        "vi": "0",
        "en": "0",
        "zh": "0",
    },
    "library_missing_all_private": {
        "vi": "0 · toàn private ({n})",
        "en": "0 · all private ({n})",
        "zh": "0 · 全是私密（{n}）",
    },
    "library_missing_public": {
        "vi": "{n}",
        "en": "{n}",
        "zh": "{n}",
    },
    "library_missing_mixed": {
        "vi": "{n} (+{priv} private)",
        "en": "{n} (+{priv} private)",
        "zh": "{n}（+{priv} 私密）",
    },
    "library_summary": {
        "vi": "{channels} kênh · máy {local} video · thiếu tải ~{missing}",
        "en": "{channels} channels · {local} local · ~{missing} to download",
        "zh": "{channels} 个频道 · 本机 {local} · 可下载约 {missing}",
    },
    "library_summary_drives": {
        "vi": "{drives} · tổng {channels} kênh · máy {local} video · thiếu ~{missing}",
        "en": "{drives} · {channels} channels total · {local} local · ~{missing} missing",
        "zh": "{drives} · 共 {channels} 频道 · 本机 {local} · 缺约 {missing}",
    },
    "library_drive_piece": {
        "vi": "{drive} {n} kênh",
        "en": "{drive} {n} ch",
        "zh": "{drive} {n} 频道",
    },
    "download_roots_label": {
        "vi": "Thư mục tải (nhiều ổ)",
        "en": "Download folders (multi-drive)",
        "zh": "下载目录（多盘）",
    },
    "download_roots_hint": {
        "vi": "Thư mục chính = nơi lưu kênh mới. Thêm ổ khác để quét/update kênh đã có sẵn trên ổ đó. Update sẽ lưu video mới vào đúng folder kênh hiện có.",
        "en": "Primary = where new channels go. Add other drives to scan/update channels already there. Updates save into the existing channel folder.",
        "zh": "主目录=新频道保存位置。添加其他盘以扫描/更新已有频道。更新会保存到该频道已有文件夹。",
    },
    "download_root_primary": {
        "vi": "Chính",
        "en": "Primary",
        "zh": "主",
    },
    "download_root_row": {
        "vi": "{drive} · {channels} kênh · {videos} video · trống {free} GB{primary}",
        "en": "{drive} · {channels} ch · {videos} videos · {free} GB free{primary}",
        "zh": "{drive} · {channels} 频道 · {videos} 视频 · 剩余 {free} GB{primary}",
    },
    "download_root_primary_tag": {
        "vi": " · [CHÍNH — kênh mới]",
        "en": " · [PRIMARY — new channels]",
        "zh": " · [主 — 新频道]",
    },
    "btn_add_download_root": {
        "vi": "Thêm thư mục/ổ…",
        "en": "Add folder/drive…",
        "zh": "添加目录/磁盘…",
    },
    "btn_set_primary_root": {
        "vi": "Đặt làm chính",
        "en": "Set primary",
        "zh": "设为主目录",
    },
    "btn_remove_download_root": {
        "vi": "Gỡ",
        "en": "Remove",
        "zh": "移除",
    },
    "msg_root_added": {
        "vi": "Đã thêm thư mục tải: {path} ({drive})",
        "en": "Added download folder: {path} ({drive})",
        "zh": "已添加下载目录：{path}（{drive}）",
    },
    "msg_root_removed": {
        "vi": "Đã gỡ thư mục tải: {path}",
        "en": "Removed download folder: {path}",
        "zh": "已移除下载目录：{path}",
    },
    "msg_root_primary": {
        "vi": "Thư mục chính (kênh mới): {path} ({drive})",
        "en": "Primary folder (new channels): {path} ({drive})",
        "zh": "主下载目录（新频道）：{path}（{drive}）",
    },
    "msg_root_cannot_remove_last": {
        "vi": "Phải giữ ít nhất một thư mục tải.",
        "en": "Keep at least one download folder.",
        "zh": "至少保留一个下载目录。",
    },
    "msg_root_already": {
        "vi": "Thư mục này đã có trong danh sách.",
        "en": "This folder is already in the list.",
        "zh": "该目录已在列表中。",
    },
    "download_dir_primary": {
        "vi": "Lưu kênh mới vào (ổ chính)",
        "en": "Save new channels to (primary)",
        "zh": "新频道保存到（主目录）",
    },
    "msg_library_refreshing": {
        "vi": "Đang làm mới thống kê kênh…",
        "en": "Refreshing channel stats…",
        "zh": "正在刷新频道统计…",
    },
    "msg_library_refreshed": {
        "vi": "Đã làm mới {count} kênh.",
        "en": "Refreshed {count} channel(s).",
        "zh": "已刷新 {count} 个频道。",
    },
    "library_progress_idle": {
        "vi": "Chưa kiểm tra kênh",
        "en": "Not checked yet",
        "zh": "尚未检查频道",
    },
    "library_progress_checking": {
        "vi": "Đang kiểm tra {current}/{total} · {name}…",
        "en": "Checking {current}/{total} · {name}…",
        "zh": "正在检查 {current}/{total} · {name}…",
    },
    "library_progress_done": {
        "vi": "Đã kiểm tra xong {count} kênh",
        "en": "Checked {count} channel(s)",
        "zh": "已检查完 {count} 个频道",
    },
    "library_progress_stopped": {
        "vi": "Đã dừng kiểm tra ({current}/{total})",
        "en": "Check stopped ({current}/{total})",
        "zh": "已停止检查（{current}/{total}）",
    },
    "msg_library_update_all": {
        "vi": "Update tất cả: quét & tải video mới cho {count} kênh…",
        "en": "Update all: scanning & downloading new videos for {count} channel(s)…",
        "zh": "全部更新：正在为 {count} 个频道扫描并下载新视频…",
    },
    "msg_library_update_one": {
        "vi": "Update kênh {name}: quét video mới…",
        "en": "Updating {name}: scanning for new videos…",
        "zh": "正在更新 {name}：扫描新视频…",
    },
    "msg_library_open": {
        "vi": "Mở kênh {name} — tích chọn video mới rồi bấm Tải đã chọn.",
        "en": "Opened {name} — select new videos then Download selected.",
        "zh": "已打开 {name} — 勾选新视频后点下载所选。",
    },
    "msg_library_no_new": {
        "vi": "Kênh {name}: không có video mới so với máy.",
        "en": "Channel {name}: no new videos vs local.",
        "zh": "频道 {name}：相对本机无新视频。",
    },
    "msg_library_none": {
        "vi": "Không có kênh nào để update.",
        "en": "No channels to update.",
        "zh": "没有可更新的频道。",
    },
    "hashtag_label": {
        "vi": "Hashtag",
        "en": "Hashtag",
        "zh": "标签",
    },
    "channel_label": {
        "vi": "Tên kênh / username",
        "en": "Channel / username",
        "zh": "频道 / 用户名",
    },
    "channel_placeholder": {
        "vi": "vd: username hoặc nhiều kênh cách nhau bởi dấu phẩy: a, b, c",
        "en": "e.g. username or multi: a, b, c",
        "zh": "例如：用户名，或多个频道用逗号：a, b, c",
    },
    "status_private": {
        "vi": "Private · bỏ qua",
        "en": "Private · skip",
        "zh": "私密 · 可跳过",
    },
    "msg_skip_private_select": {
        "vi": "Bỏ qua {count} video private (có thể bỏ qua; tích thủ công nếu đã mua/friend).",
        "en": "Skipped {count} private video(s) (safe to skip; tick manually if purchased/friend).",
        "zh": "已跳过 {count} 个私密视频（可忽略；已购买/好友请手动勾选）。",
    },
    "msg_skip_private_download": {
        "vi": "Bỏ qua {count} video private (không tự tải).",
        "en": "Skipped {count} private video(s) (not auto-downloaded).",
        "zh": "已跳过 {count} 个私密视频（不自动下载）。",
    },
    "msg_multi_channel": {
        "vi": "Quét {count} kênh: {names}",
        "en": "Scanning {count} channels: {names}",
        "zh": "扫描 {count} 个频道：{names}",
    },
    "limit_label": {
        "vi": "Số video quét (0=∞)",
        "en": "Scan limit (0=∞)",
        "zh": "扫描数量（0=不限）",
    },
    "limit_unlimited": {
        "vi": "không giới hạn",
        "en": "unlimited",
        "zh": "不限",
    },
    "download_delay_label": {
        "vi": "Delay giữa các lượt (giây)",
        "en": "Delay between starts (sec)",
        "zh": "启动间隔（秒）",
    },
    "download_batch_label": {
        "vi": "Số luồng tải song song",
        "en": "Parallel download threads",
        "zh": "并行下载线程数",
    },
    "scan_delay_label": {
        "vi": "Delay quét API (giây; unlimited ≥1.5s)",
        "en": "API scan delay (sec; unlimited ≥1.5s)",
        "zh": "API 扫描间隔（秒；不限量≥1.5s）",
    },
    "selected_count": {
        "vi": "Đã chọn: {count}",
        "en": "Selected: {count}",
        "zh": "已选：{count}",
    },
    "msg_download_plan": {
        "vi": "Sẽ tải HẾT {selected} video đã chọn, tối đa {batch} file cùng lúc, delay {delay}s giữa các lượt.",
        "en": "Will download ALL {selected} selected, up to {batch} at once, {delay}s delay between starts.",
        "zh": "将下载全部 {selected} 个已选视频，最多同时 {batch} 个，启动间隔 {delay}s。",
    },
    "msg_waiting_delay": {
        "vi": "Chờ {sec}s trước file tiếp theo…",
        "en": "Waiting {sec}s before next file…",
        "zh": "等待 {sec}s 后继续…",
    },
    "login_email": {
        "vi": "Email",
        "en": "Email",
        "zh": "邮箱",
    },
    "login_password": {
        "vi": "Mật khẩu",
        "en": "Password",
        "zh": "密码",
    },
    "btn_login": {
        "vi": "Đăng nhập",
        "en": "Log in",
        "zh": "登录",
    },
    "login_checking": {
        "vi": "Đang kiểm tra tài khoản…",
        "en": "Checking account…",
        "zh": "正在验证账号…",
    },
    "login_ok": {
        "vi": "✓ Đăng nhập thành công: {name}",
        "en": "✓ Login successful: {name}",
        "zh": "✓ 登录成功：{name}",
    },
    "login_fail": {
        "vi": "✗ Đăng nhập thất bại: {error}",
        "en": "✗ Login failed: {error}",
        "zh": "✗ 登录失败：{error}",
    },
    "login_missing": {
        "vi": "✗ Vui lòng nhập email và mật khẩu.",
        "en": "✗ Please enter email and password.",
        "zh": "✗ 请输入邮箱和密码。",
    },
    "login_hint": {
        "vi": (
            "Bấm Đăng nhập để kiểm tra. Cần cho video private. "
            "Lỗi HTTP 522 = server/Cloudflare timeout (không phải sai mật khẩu) — thử VPN hoặc đợi."
        ),
        "en": (
            "Click Log in to verify. Needed for private videos. "
            "HTTP 522 = Cloudflare/server timeout (not wrong password) — try VPN or wait."
        ),
        "zh": (
            "点击登录验证。私有视频需要登录。"
            "HTTP 522 = Cloudflare/服务器超时（不是密码错误）— 可尝试 VPN 或稍后再试。"
        ),
    },
    "login_status_idle": {
        "vi": "Chưa đăng nhập",
        "en": "Not logged in",
        "zh": "未登录",
    },
    "btn_scan": {
        "vi": "Quét",
        "en": "Scan",
        "zh": "扫描",
    },
    "btn_stop": {
        "vi": "Dừng",
        "en": "Stop",
        "zh": "停止",
    },
    "btn_pause": {
        "vi": "Tạm dừng",
        "en": "Pause",
        "zh": "暂停",
    },
    "btn_resume": {
        "vi": "Tiếp tục",
        "en": "Resume",
        "zh": "继续",
    },
    "msg_paused": {
        "vi": "Đã tạm dừng. Bấm Tiếp tục để chạy tiếp.",
        "en": "Paused. Click Resume to continue.",
        "zh": "已暂停。点击继续以恢复。",
    },
    "msg_resumed": {
        "vi": "Đã tiếp tục.",
        "en": "Resumed.",
        "zh": "已继续。",
    },
    "progress_paused": {
        "vi": "Đang tạm dừng…",
        "en": "Paused…",
        "zh": "已暂停…",
    },
    "btn_select_all": {
        "vi": "Chọn tất cả",
        "en": "Select all",
        "zh": "全选",
    },
    "btn_deselect_all": {
        "vi": "Bỏ chọn",
        "en": "Deselect all",
        "zh": "取消全选",
    },
    "btn_download": {
        "vi": "Tải đã chọn",
        "en": "Download selected",
        "zh": "下载所选",
    },
    "btn_retry": {
        "vi": "Tải lại",
        "en": "Retry",
        "zh": "重试",
    },
    "btn_retry_failed": {
        "vi": "Tải lại lỗi/timeout",
        "en": "Retry failed/stalled",
        "zh": "重试失败/超时",
    },
    "col_progress": {
        "vi": "Tiến độ",
        "en": "Progress",
        "zh": "进度",
    },
    "col_size": {
        "vi": "Dung lượng",
        "en": "Size",
        "zh": "大小",
    },
    "col_action": {
        "vi": "Thao tác",
        "en": "Action",
        "zh": "操作",
    },
    "size_summary": {
        "vi": "Tổng list: {total} · Đã chọn: {selected} ({count} video)",
        "en": "List total: {total} · Selected: {selected} ({count} videos)",
        "zh": "列表合计：{total} · 已选：{selected}（{count} 个）",
    },
    "size_unknown_note": {
        "vi": "· một số video không có size từ API",
        "en": "· some sizes unknown from API",
        "zh": "· 部分视频 API 无大小",
    },
    "stall_timeout_label": {
        "vi": "Timeout đứng yên (giây)",
        "en": "Stall timeout (sec)",
        "zh": "停滞超时（秒）",
    },
    "status_stalled": {
        "vi": "Đứng yên / timeout",
        "en": "Stalled / timeout",
        "zh": "停滞 / 超时",
    },
    "msg_no_retryable": {
        "vi": "Không có video lỗi hoặc timeout để tải lại.",
        "en": "No failed or stalled videos to retry.",
        "zh": "没有可重试的失败/超时视频。",
    },
    "msg_retry_one": {
        "vi": "Tải lại: {title}",
        "en": "Retrying: {title}",
        "zh": "重试：{title}",
    },
    "msg_retry_batch": {
        "vi": "Tải lại {count} video lỗi/timeout…",
        "en": "Retrying {count} failed/stalled video(s)…",
        "zh": "正在重试 {count} 个失败/超时视频…",
    },
    "btn_browse": {
        "vi": "Chọn thư mục…",
        "en": "Browse…",
        "zh": "浏览…",
    },
    "btn_open_folder": {
        "vi": "Mở thư mục",
        "en": "Open folder",
        "zh": "打开文件夹",
    },
    "download_dir": {
        "vi": "Nơi lưu file tải",
        "en": "Save downloads to",
        "zh": "下载保存位置",
    },
    "msg_folder_set": {
        "vi": "Đã chọn thư mục lưu: {path}",
        "en": "Download folder set: {path}",
        "zh": "已设置下载目录：{path}",
    },
    "msg_folder_missing": {
        "vi": "Thư mục chưa tồn tại, sẽ tạo khi tải: {path}",
        "en": "Folder will be created on download: {path}",
        "zh": "下载时将创建目录：{path}",
    },
    "msg_open_file": {
        "vi": "Đang mở file: {path}",
        "en": "Opening: {path}",
        "zh": "正在打开：{path}",
    },
    "msg_file_not_local": {
        "vi": "Chưa có file trên máy. Hãy tải video trước (double-click chỉ mở file đã tải).",
        "en": "File not on disk yet. Download first (double-click opens local files only).",
        "zh": "本机尚无文件。请先下载（双击仅打开已下载文件）。",
    },
    "msg_open_file_fail": {
        "vi": "Không mở được file: {error}",
        "en": "Could not open file: {error}",
        "zh": "无法打开文件：{error}",
    },
    "demo_mode": {
        "vi": "Chế độ demo (dữ liệu giả, không gọi API)",
        "en": "Demo mode (mock data, no real API)",
        "zh": "演示模式（模拟数据，不调用真实 API）",
    },
    "col_select": {
        "vi": "Chọn",
        "en": "Sel",
        "zh": "选",
    },
    "col_thumb": {
        "vi": "Ảnh",
        "en": "Thumb",
        "zh": "封面",
    },
    "col_title": {
        "vi": "Tiêu đề",
        "en": "Title",
        "zh": "标题",
    },
    "col_author": {
        "vi": "Tác giả",
        "en": "Author",
        "zh": "作者",
    },
    "col_views": {
        "vi": "Lượt xem",
        "en": "Views",
        "zh": "播放",
    },
    "col_status": {
        "vi": "Trạng thái",
        "en": "Status",
        "zh": "状态",
    },
    "status_ready": {
        "vi": "Sẵn sàng",
        "en": "Ready",
        "zh": "就绪",
    },
    "status_queued": {
        "vi": "Trong hàng đợi",
        "en": "Queued",
        "zh": "排队中",
    },
    "status_downloading": {
        "vi": "Đang tải…",
        "en": "Downloading…",
        "zh": "下载中…",
    },
    "status_done": {
        "vi": "Hoàn tất",
        "en": "Done",
        "zh": "完成",
    },
    "status_error": {
        "vi": "Lỗi",
        "en": "Error",
        "zh": "错误",
    },
    "err_network_hint": {
        "vi": "Lỗi mạng tới server file Iwara (thường do timeout/chặn). Thử VPN hoặc Tải lại sau.",
        "en": "Network error reaching Iwara file servers (timeout/block). Try VPN or retry later.",
        "zh": "无法连接 Iwara 文件服务器（超时/封锁）。可试 VPN 或稍后重试。",
    },
    "status_skipped": {
        "vi": "Đã bỏ qua",
        "en": "Skipped",
        "zh": "已跳过",
    },
    "status_on_disk": {
        "vi": "Đã có trên máy",
        "en": "Already on disk",
        "zh": "本机已有",
    },
    "msg_scan_local": {
        "vi": "Thư mục: {folder} — đã có {already}, mới {new}.",
        "en": "Folder: {folder} — on disk {already}, new {new}.",
        "zh": "目录：{folder} — 已有 {already}，新增 {new}。",
    },
    "msg_skip_on_disk": {
        "vi": "Bỏ qua {count} video đã có trên máy.",
        "en": "Skipped {count} video(s) already on disk.",
        "zh": "已跳过 {count} 个本机已有视频。",
    },
    "btn_select_new": {
        "vi": "Chọn video mới",
        "en": "Select new only",
        "zh": "仅选新视频",
    },
    "log_title": {
        "vi": "Nhật ký",
        "en": "Log",
        "zh": "日志",
    },
    "progress_idle": {
        "vi": "Chưa có tác vụ",
        "en": "No task",
        "zh": "无任务",
    },
    "progress_scan": {
        "vi": "Đang quét…",
        "en": "Scanning…",
        "zh": "扫描中…",
    },
    "progress_download": {
        "vi": "Đã tải {ok}/{total} · Lỗi {err} · {percent}% · {speed}",
        "en": "Done {ok}/{total} · Failed {err} · {percent}% · {speed}",
        "zh": "已完成 {ok}/{total} · 失败 {err} · {percent}% · {speed}",
    },
    "progress_download_live": {
        "vi": "Đã tải {ok}/{total} · Lỗi {err} · {percent}% · {speed} · Đang: {title}",
        "en": "Done {ok}/{total} · Failed {err} · {percent}% · {speed} · Now: {title}",
        "zh": "已完成 {ok}/{total} · 失败 {err} · {percent}% · {speed} · 当前：{title}",
    },
    "progress_download_done": {
        "vi": "Hoàn tất · Đã tải {ok}/{total} · Lỗi {err} · {percent}%",
        "en": "Finished · Done {ok}/{total} · Failed {err} · {percent}%",
        "zh": "完成 · 已完成 {ok}/{total} · 失败 {err} · {percent}%",
    },
    "progress_ok_part": {
        "vi": "✓ Đã tải {ok}/{total}",
        "en": "✓ Done {ok}/{total}",
        "zh": "✓ 完成 {ok}/{total}",
    },
    "progress_err_part": {
        "vi": "✗ Lỗi {err}",
        "en": "✗ Failed {err}",
        "zh": "✗ 失败 {err}",
    },
    "progress_live_extra": {
        "vi": "· {speed} · Đang: {title}",
        "en": "· {speed} · Now: {title}",
        "zh": "· {speed} · 当前：{title}",
    },
    "progress_live_server": {
        "vi": "· {speed} · Server: {server} · {title}",
        "en": "· {speed} · Server: {server} · {title}",
        "zh": "· {speed} · 服务器：{server} · {title}",
    },
    "status_downloading_server": {
        "vi": "Đang tải… {pct}% · {server}",
        "en": "Downloading… {pct}% · {server}",
        "zh": "下载中… {pct}% · {server}",
    },
    "log_dl_live": {
        "vi": "… [{n}/{total}] {title} | {status} | {file_pct}% | {speed} | {size} | {server}",
        "en": "… [{n}/{total}] {title} | {status} | {file_pct}% | {speed} | {size} | {server}",
        "zh": "… [{n}/{total}] {title} | {status} | {file_pct}% | {speed} | {size} | {server}",
    },
    "progress_speed_extra": {
        "vi": "· {speed}",
        "en": "· {speed}",
        "zh": "· {speed}",
    },
    "progress_done_extra": {
        "vi": "· Hoàn tất",
        "en": "· Finished",
        "zh": "· 完成",
    },
    "log_dl_start": {
        "vi": "▶ [{n}/{total}] Bắt đầu: {title}",
        "en": "▶ [{n}/{total}] Start: {title}",
        "zh": "▶ [{n}/{total}] 开始：{title}",
    },
    "log_dl_ok": {
        "vi": "✓ [{n}/{total}] Xong: {title} ({speed})",
        "en": "✓ [{n}/{total}] Done: {title} ({speed})",
        "zh": "✓ [{n}/{total}] 完成：{title}（{speed}）",
    },
    "log_dl_err": {
        "vi": "✗ [{n}/{total}] {title}\n   Lỗi: {detail}\n   → Xử lý: {advice}",
        "en": "✗ [{n}/{total}] {title}\n   Error: {detail}\n   → Fix: {advice}",
        "zh": "✗ [{n}/{total}] {title}\n   错误：{detail}\n   → 处理：{advice}",
    },
    "log_dl_skip": {
        "vi": "⊘ [{n}/{total}] Bỏ qua (đã có): {title}",
        "en": "⊘ [{n}/{total}] Skipped (on disk): {title}",
        "zh": "⊘ [{n}/{total}] 跳过（已有）：{title}",
    },
    "msg_enter_hashtag": {
        "vi": "Vui lòng nhập hashtag.",
        "en": "Please enter a hashtag.",
        "zh": "请输入标签。",
    },
    "msg_enter_channel": {
        "vi": "Vui lòng nhập tên kênh.",
        "en": "Please enter a channel name.",
        "zh": "请输入频道名。",
    },
    "msg_no_selection": {
        "vi": "Chưa chọn video nào.",
        "en": "No videos selected.",
        "zh": "未选择任何视频。",
    },
    "msg_all_on_disk": {
        "vi": "Các video đã chọn đều đã có trên máy. Dùng «Chọn video mới» hoặc quét lại khi có video mới.",
        "en": "All selected videos are already on disk. Use «Select new only» or re-scan for new uploads.",
        "zh": "所选视频均已在本机。请用「仅选新视频」或重新扫描新内容。",
    },
    "msg_scan_done": {
        "vi": "Quét xong: {count} video.",
        "en": "Scan finished: {count} videos.",
        "zh": "扫描完成：{count} 个视频。",
    },
    "msg_download_done": {
        "vi": "Tải xong {ok} video, lỗi {err}.",
        "en": "Downloaded {ok}, failed {err}.",
        "zh": "下载完成 {ok}，失败 {err}。",
    },
    "msg_stopped": {
        "vi": "Đã dừng theo yêu cầu.",
        "en": "Stopped by user.",
        "zh": "已按用户要求停止。",
    },
    "msg_demo_note": {
        "vi": "⚠ DEMO đang BẬT → chỉ hiện Sample video giả. Vào Cài đặt BỎ TICK demo để lấy title/author thật từ iwara.",
        "en": "⚠ DEMO ON → fake Sample videos only. Uncheck demo in Settings for real titles/authors.",
        "zh": "⚠ 演示模式已开启 → 仅显示假数据。请在设置中取消演示以获取真实标题/作者。",
    },
    "msg_live_mode": {
        "vi": "Chế độ THẬT: quét API iwara.tv (title, author, views thật).",
        "en": "LIVE mode: scanning iwara.tv API (real titles, authors, views).",
        "zh": "真实模式：正在扫描 iwara.tv API（真实标题、作者、播放量）。",
    },
    "hashtag_placeholder": {
        "vi": "vd: mikumikudance, genshin_impact, koikatsu",
        "en": "e.g. mikumikudance, genshin_impact, koikatsu",
        "zh": "例如：mikumikudance、genshin_impact、koikatsu",
    },
    "settings_hint": {
        "vi": "Cài đặt lưu vào config.json. Tải thật cần tắt demo + có mạng. Đổi UI/GPU cần khởi động lại app.",
        "en": "Settings saved to config.json. Real download needs demo off + network. UI/GPU change needs restart.",
        "zh": "设置保存到 config.json。真实下载需关闭演示并联网。更改 UI/GPU 需重启。",
    },
    "ui_backend_label": {
        "vi": "Giao diện (UI)",
        "en": "UI engine",
        "zh": "界面引擎",
    },
    "ui_backend_tk": {
        "vi": "CPU · CustomTkinter (ổn định)",
        "en": "CPU · CustomTkinter (stable)",
        "zh": "CPU · CustomTkinter（稳定）",
    },
    "ui_backend_flet": {
        "vi": "GPU · Flet/Flutter (mượt, list lớn)",
        "en": "GPU · Flet/Flutter (smooth large lists)",
        "zh": "GPU · Flet/Flutter（大列表更流畅）",
    },
    "ui_gpu_label": {
        "vi": "GPU render UI (Flet)",
        "en": "UI GPU (Flet)",
        "zh": "UI GPU（Flet）",
    },
    "ui_gpu_pick_label": {
        "vi": "Chọn GPU cụ thể",
        "en": "Select specific GPU",
        "zh": "选择具体 GPU",
    },
    "ui_gpu_detected_label": {
        "vi": "GPU trên máy",
        "en": "GPUs on this PC",
        "zh": "本机 GPU",
    },
    "ui_gpu_none": {
        "vi": "(Không phát hiện GPU)",
        "en": "(No GPU detected)",
        "zh": "（未检测到 GPU）",
    },
    "ui_gpu_auto": {
        "vi": "Tự động (Windows quyết định)",
        "en": "Auto (Windows decides)",
        "zh": "自动（由 Windows 决定）",
    },
    "ui_gpu_high": {
        "vi": "Hiệu năng cao (ưu tiên GPU rời)",
        "en": "High performance (prefer discrete)",
        "zh": "高性能（优先独显）",
    },
    "ui_gpu_save": {
        "vi": "Tiết kiệm điện (ưu tiên GPU tích hợp)",
        "en": "Power saving (prefer integrated)",
        "zh": "节能（优先核显）",
    },
    "msg_gpu_applied": {
        "vi": "Đã chọn GPU: {name}\n{detail}\nKhởi động lại app để áp dụng hoàn toàn.",
        "en": "GPU selected: {name}\n{detail}\nRestart the app to fully apply.",
        "zh": "已选择 GPU：{name}\n{detail}\n请重启应用以完全生效。",
    },

    "list_page_size_label": {
        "vi": "Số video / trang (CPU UI)",
        "en": "Videos per page (CPU UI)",
        "zh": "每页视频数（CPU UI）",
    },
    "btn_clear_cache": {
        "vi": "Xóa cache",
        "en": "Clear cache",
        "zh": "清理缓存",
    },
    "msg_cache_cleared": {
        "vi": "Đã xóa cache: {files} file · giải phóng ~{mb} MB.",
        "en": "Cache cleared: {files} files · freed ~{mb} MB.",
        "zh": "已清理缓存：{files} 个文件 · 约释放 {mb} MB。",
    },
    "msg_cache_stats": {
        "vi": "Cache hiện: {files} file · {mb} MB",
        "en": "Cache now: {files} files · {mb} MB",
        "zh": "当前缓存：{files} 个文件 · {mb} MB",
    },
    "msg_ui_restart": {
        "vi": "Đã lưu. Khởi động lại app để áp dụng UI/GPU mới.",
        "en": "Saved. Restart the app to apply the new UI/GPU.",
        "zh": "已保存。请重启应用以应用新的 UI/GPU。",
    },
    "page_nav": {
        "vi": "Trang {page}/{pages} · hiển thị {start}–{end}/{total}",
        "en": "Page {page}/{pages} · showing {start}–{end}/{total}",
        "zh": "第 {page}/{pages} 页 · 显示 {start}–{end}/{total}",
    },
    "btn_page_prev": {
        "vi": "« Trước",
        "en": "« Prev",
        "zh": "« 上一页",
    },
    "btn_page_next": {
        "vi": "Sau »",
        "en": "Next »",
        "zh": "下一页 »",
    },
    "theme": {
        "vi": "Giao diện",
        "en": "Theme",
        "zh": "主题",
    },
    "theme_dark": {
        "vi": "Tối",
        "en": "Dark",
        "zh": "深色",
    },
    "theme_light": {
        "vi": "Sáng",
        "en": "Light",
        "zh": "浅色",
    },
    "found_count": {
        "vi": "Tìm thấy: {count}",
        "en": "Found: {count}",
        "zh": "找到：{count}",
    },
    "controls_title": {
        "vi": "Tuỳ chọn tải",
        "en": "Download options",
        "zh": "下载选项",
    },
}



class I18n:
    """Tiny translator with live-update listeners."""

    def __init__(self, lang: str = "vi") -> None:
        self._lang = lang if lang in SUPPORTED_LANGS else "vi"
        self._listeners: list[Callable[[], None]] = []

    @property
    def lang(self) -> str:
        return self._lang

    def set_lang(self, lang: str) -> None:
        if lang not in SUPPORTED_LANGS:
            return
        if lang == self._lang:
            return
        self._lang = lang
        for cb in list(self._listeners):
            cb()

    def on_change(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def t(self, key: str, **kwargs: object) -> str:
        entry = STRINGS.get(key, {})
        text = entry.get(self._lang) or entry.get("en") or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError):
                return text
        return text
