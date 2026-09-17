# Iwara Downloader 🎬

[**Tiếng Việt**](#tiếng-việt) | [**English**](#english)

---

## Tiếng Việt

### 📖 Giới thiệu
~~ Tool làm đem bán, chán rồi đem share


**Iwara Downloader** là một ứng dụng máy tính mạnh mẽ, hiện đại và tiện lợi dùng để tìm kiếm, quản lý và tải video từ Iwara hàng loạt theo **kênh (tác giả)** hoặc **hashtag**. Ứng dụng hỗ trợ giao diện đồ họa (GUI) tăng tốc phần cứng bằng GPU thông qua Flet (Flutter engine) và có chế độ dự phòng bằng CustomTkinter.

---

### ✨ Tính năng nổi bật

- 🏷️ **Tải theo Hashtag:** Tìm kiếm và tải toàn bộ hoặc chọn lọc video gắn hashtag cụ thể.
- 👤 **Tải theo Kênh (Channel / User):** Quét và tải danh sách video của bất kỳ tác giả nào.
- 📚 **Thư viện Kênh (Channel Library):** 
  - Tự động quét và quản lý thư mục tác giả phân bổ trên **nhiều ổ đĩa khác nhau** (Multi-drive support).
  - So khớp với dữ liệu trên Iwara, phát hiện video còn thiếu hoặc video đã bị ẩn/xóa.
  - Cập nhật một chạm (**Update all** / **Update channel**) chỉ tải các video mới còn thiếu.
- ⚡ **Tải đa luồng & Tránh nghẽn:** Tùy chỉnh số luồng tải đồng thời, độ trễ giữa các request và thời gian chờ (stall timeout) để tránh bị chặn IP/rate limit.
- 🔐 **Hỗ trợ Đăng nhập:** Đăng nhập tài khoản Iwara để tải các nội dung yêu cầu quyền thành viên hoặc bị giới hạn.
- 🖼️ **Thumbnail Cache & Lịch sử:** Tải trước thumbnail để duyệt video trực quan, lưu lịch sử tải vào SQLite (`history.db`) để chống tải trùng lặp.
- 🚀 **Giao diện GPU Flet / CustomTkinter:** Giao diện Flet hiện đại, mượt mà với khả năng chọn card đồ họa (GPU Adapter), tự động chuyển sang CustomTkinter nếu hệ thống không hỗ trợ.
- 🌐 **Đa ngôn ngữ:** Hỗ trợ Tiếng Việt (`vi`), Tiếng Anh (`en`), Tiếng Trung (`zh`).

---

### 📂 Cấu trúc thư mục

```
iwara-downloader/
├── app/
│   ├── core/            # Core logic (API, Downloader, CDN, DB, Config, GPU, Cache)
│   ├── i18n/            # Hệ thống đa ngôn ngữ (locales)
│   └── ui/              # Giao diện người dùng (Flet GPU & CustomTkinter)
├── cache/               # Thư mục cache ảnh thumbnail
├── downloads/           # Thư mục mặc định chứa video tải về
├── config.example.json  # File cấu hình mẫu
├── history.db           # SQLite database lưu lịch sử video đã tải (tự tạo)
├── main.py              # Entry point khởi chạy ứng dụng
├── requirements.txt     # Danh sách thư viện Python cần thiết
└── run.bat              # Script khởi chạy và tự động cài đặt trên Windows
```

---

### 🛠️ Yêu cầu hệ thống

- **Hệ điều hành:** Windows 10/11 (64-bit).
- **Python:** Phiên bản Python 3.10 trở lên (khuyên dùng Python 3.11 - 3.13 từ [python.org](https://www.python.org/)).

---

### 🚀 Hướng dẫn cài đặt & Khởi chạy

#### Cách 1: Khởi chạy nhanh bằng `run.bat` (Khuyên dùng trên Windows)

1. Nhấp đúp chuột vào file **`run.bat`**.
2. Script sẽ tự động:
   - Tạo môi trường ảo Python (`.venv`) nếu chưa có.
   - Nâng cấp `pip` và cài đặt đầy đủ các thư viện trong `requirements.txt`.
   - Khởi động ứng dụng giao diện GUI.

#### Cách 2: Khởi chạy thủ công qua Terminal / Command Prompt

```powershell
# 1. Di chuyển vào thư mục dự án
cd F:\iwara-downloader

# 2. Tạo môi trường ảo Python
python -m venv .venv

# 3. Kích hoạt môi trường ảo
# Trên PowerShell:
.\.venv\Scripts\Activate.ps1
# Hoặc trên Command Prompt (cmd):
.\.venv\Scripts\activate.bat

# 4. Cài đặt các thư viện phụ thuộc
pip install --upgrade pip
pip install -r requirements.txt

# 5. Khởi chạy ứng dụng
python main.py
```

---

### ⚙️ Cấu hình (`config.json`)

Khi khởi chạy lần đầu, chương trình sẽ tự động tạo file `config.json`. Bạn có thể chỉnh sửa trực tiếp trên giao diện **Cài đặt** hoặc sửa file:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `language` | `"vi"` | Ngôn ngữ giao diện (`"vi"`, `"en"`, `"zh"`) |
| `theme` | `"dark"` | Giao diện (`"dark"` hoặc `"light"`) |
| `download_dir` | `"./downloads"` | Thư mục tải về mặc định cho kênh mới |
| `download_dirs` | `[]` | Danh sách các ổ đĩa / thư mục bổ sung chứa thư viện kênh |
| `scan_limit` | `32` | Số lượng video tối đa lấy về mỗi lần quét (0 = không giới hạn) |
| `download_batch`| `3` | Số luồng tải video song song (1 - 16) |
| `download_delay_sec` | `3.0` | Khoảng nghỉ giữa các lần tải video (giây) |
| `scan_delay_sec` | `1.5` | Khoảng nghỉ giữa các trang API khi quét (giây) |
| `stall_timeout_sec` | `90.0`| Thời gian tối đa cho phép tải bị treo trước khi thử lại |
| `ui_backend` | `"flet"` | Giao diện đồ họa (`"flet"` dùng GPU, `"tk"` dùng CustomTkinter) |
| `ui_gpu` | `"auto"` | Chế độ GPU cho Flet (`"auto"`, `"high_performance"`, `"adapter:0"`, ...) |

---

<br>

---

## English

### 📖 Overview

**Iwara Downloader** is a modern, feature-rich desktop application designed for scanning, managing, and batch downloading videos from Iwara by **Channel (Author)** or **Hashtag**. It features a GPU-accelerated graphical interface powered by Flet (Flutter engine) alongside a robust CustomTkinter fallback.

---

### ✨ Key Features

- 🏷️ **Hashtag Downloader:** Discover and batch-download videos matching specific tags.
- 👤 **Channel Downloader:** Fetch and download entire video libraries from any creator/channel.
- 📚 **Multi-Drive Channel Library:**
  - Automatically indexes author folders distributed across **multiple drives/partitions**.
  - Cross-references local archives against the Iwara API to identify missing, private, or deleted videos.
  - One-click incremental updates (**Update all** / **Update channel**) to grab only new uploads.
- ⚡ **Multi-threaded & Rate-Limit Safe:** Configurable concurrent workers, request pacing delays, and stall timeouts to prevent IP throttling.
- 🔐 **Account Authentication:** Log in with your Iwara account credentials to access restricted and member-only videos.
- 🖼️ **Thumbnail Previews & Deduplication:** Pre-caches thumbnails for smooth browsing; uses SQLite (`history.db`) to guarantee no duplicate downloads.
- 🚀 **GPU-Accelerated GUI:** Modern Flutter/Flet interface with GPU adapter selection, automatically falling back to CPU-rendered CustomTkinter if needed.
- 🌐 **Internationalization (i18n):** Native support for English (`en`), Vietnamese (`vi`), and Chinese (`zh`).

---

### 📂 Project Structure

```
iwara-downloader/
├── app/
│   ├── core/            # Core business logic (API, Downloader, CDN, DB, Config, GPU, Cache)
│   ├── i18n/            # Internationalization & localized strings
│   └── ui/              # User interfaces (Flet GPU & CustomTkinter)
├── cache/               # Cached video thumbnails
├── downloads/           # Default directory for downloaded videos
├── config.example.json  # Configuration template
├── history.db           # SQLite database storing download history (auto-created)
├── main.py              # Application entry point
├── requirements.txt     # Python dependencies
└── run.bat              # One-click startup script for Windows
```

---

### 🛠️ System Requirements

- **Operating System:** Windows 10/11 (64-bit).
- **Python:** Python 3.10 or higher (Python 3.11 - 3.13 recommended from [python.org](https://www.python.org/)).

---

### 🚀 Installation & Getting Started

#### Method 1: Quick Launch via `run.bat` (Recommended for Windows)

1. Double-click the **`run.bat`** file.
2. The launcher will automatically:
   - Create a Python virtual environment (`.venv`) if not present.
   - Upgrade `pip` and install all dependencies from `requirements.txt`.
   - Launch the GUI application.

#### Method 2: Manual Installation via Terminal / Command Prompt

```powershell
# 1. Navigate to the project directory
cd F:\iwara-downloader

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
# On PowerShell:
.\.venv\Scripts\Activate.ps1
# On Command Prompt (cmd):
.\.venv\Scripts\activate.bat

# 4. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Run the application
python main.py
```

---

### ⚙️ Configuration Reference (`config.json`)

On initial launch, a `config.json` file is automatically generated. You can configure options via the in-app **Settings** tab or by modifying `config.json`:

| Key | Default | Description |
|---|---|---|
| `language` | `"vi"` | UI Language (`"vi"`, `"en"`, `"zh"`) |
| `theme` | `"dark"` | Color theme (`"dark"` or `"light"`) |
| `download_dir` | `"./downloads"` | Primary download directory for new channels |
| `download_dirs` | `[]` | List of additional multi-drive root directories to scan |
| `scan_limit` | `32` | Max items retrieved during a scan (0 = unlimited) |
| `download_batch`| `3` | Number of concurrent download workers (1 - 16) |
| `download_delay_sec` | `3.0` | Delay between consecutive download requests in seconds |
| `scan_delay_sec` | `1.5` | Delay between API pagination requests in seconds |
| `stall_timeout_sec` | `90.0`| Max seconds allowed for an unresponsive download before retry |
| `ui_backend` | `"flet"` | UI renderer (`"flet"` for GPU, `"tk"` for CustomTkinter) |
| `ui_gpu` | `"auto"` | GPU adapter preference for Flet |

---

### 📝 License & Disclaimer

This project is created for personal and educational archival purposes. Please respect content creators and adhere to the terms of service of the target platform.
