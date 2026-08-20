"""CustomTkinter main window (stable desktop UI) — VI / EN / ZH.

Flet/GPU was tried but did not reliably show a window on double-click for this PC.
This UI uses Tk/CustomTkinter which opens consistently. Core download logic is unchanged.
Optional Flet build kept at app/ui/app_flet.py.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

from app.core.api import IwaraAPI
from app.core.cache_util import cache_stats, clear_cache
from app.core.config import AppConfig
from app.core.database import HistoryDB
from app.core.debug_log import console_error, console_log
from app.core.downloader import Downloader
from app.core.errors import diagnose_download_error
from app.core.gpu_pref import (
    GPU_AUTO,
    GPU_HIGH,
    GPU_SAVE,
    apply_windows_gpu_preference,
    build_gpu_options,
    format_gpu_list,
    list_gpus,
    parse_gpu_selection,
)
from app.core.models import VideoItem, VideoStatus, format_bytes, format_speed
from app.core.storage import (
    LocalChannelInfo,
    annotate_local_status,
    find_local_file,
    find_local_file_in_roots,
    list_local_channels_multi,
    path_drive,
    resolve_channel_dir,
    summarize_download_roots,
    target_download_dir,
)
from app.core import thumbnails as thumb_mod
from app.i18n import I18n, LANG_LABELS, SUPPORTED_LANGS

_GREEN = "#2d9f5c"
_RED = "#8b2e2e"
_AMBER = "#8a6d1d"
_BLUE = "#1f538d"
_OK = "#3cb371"
_ERR = "#e05c5c"
_PAUSE = "#e0a84c"


class IwaraDownloaderApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = AppConfig.load()
        self.i18n = I18n(self.config_data.language)
        self.history = HistoryDB()

        self._videos: list[VideoItem] = []
        self._row_vars: list[tk.BooleanVar] = []
        self._row_checks: list[ctk.CTkCheckBox] = []
        self._row_bars: list[ctk.CTkProgressBar] = []
        self._row_pcts: list[ctk.CTkLabel] = []
        self._row_status: list[ctk.CTkLabel] = []
        self._row_retry: list[ctk.CTkButton] = []
        self._row_thumbs: list[ctk.CTkLabel] = []
        self._thumb_images: dict[str, ctk.CTkImage] = {}
        self._thumb_row_index: dict[str, int] = {}
        self._list_generation = 0
        self._scan_source = ""
        self._scan_query = ""
        self._library_channels: list[LocalChannelInfo] = []
        self._library_rows: list[dict[str, Any]] = []
        self._library_auto_select_new = False
        self._library_checking = False
        self._library_remote_checked = False
        # video_id → current CDN/server label while downloading
        self._dl_server_by_id: dict[str, str] = {}
        # Paged list rendering (only ~page_size widgets at a time → much less lag)
        self._page = 0
        self._page_size = max(20, min(200, int(self.config_data.list_page_size or 60)))
        self._stop = threading.Event()
        self._pause_gate = threading.Event()
        self._pause_gate.set()
        self._busy = False
        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        ctk.set_appearance_mode(self.config_data.theme)
        ctk.set_default_color_theme("blue")

        self.title(self.i18n.t("app_title"))
        self.geometry("1200x820+80+60")
        self.minsize(1000, 660)
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(600, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass

        self._build_ui()
        self.i18n.on_change(self._refresh_texts)
        self.after(80, self._poll_queue)
        self._log(
            self.i18n.t("msg_demo_note")
            if self.config_data.demo_mode
            else self.i18n.t("msg_live_mode")
        )
        self._log("UI: CustomTkinter (stable desktop)")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(
            top, text=self.i18n.t("app_title"), font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_title.grid(row=0, column=0, padx=16, pady=12, sticky="w")

        lang_frame = ctk.CTkFrame(top, fg_color="transparent")
        lang_frame.grid(row=0, column=1, padx=16, pady=12, sticky="e")
        self.lbl_lang = ctk.CTkLabel(lang_frame, text=self.i18n.t("language"))
        self.lbl_lang.pack(side="left", padx=(0, 8))
        self.lang_var = ctk.StringVar(value=LANG_LABELS[self.i18n.lang])
        self.lang_menu = ctk.CTkOptionMenu(
            lang_frame,
            variable=self.lang_var,
            values=[LANG_LABELS[c] for c in SUPPORTED_LANGS],
            command=self._on_language_change,
            width=130,
        )
        self.lang_menu.pack(side="left")

        self.tabs = ctk.CTkTabview(self, command=self._on_tab_change)
        self.tabs.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.tab_hashtag = self.tabs.add(self.i18n.t("tab_hashtag"))
        self.tab_channel = self.tabs.add(self.i18n.t("tab_channel"))
        self.tab_library = self.tabs.add(self.i18n.t("tab_library"))
        self.tab_settings = self.tabs.add(self.i18n.t("tab_settings"))
        self._tab_keys = ["tab_hashtag", "tab_channel", "tab_library", "tab_settings"]
        self._build_scan_tab(self.tab_hashtag, "hashtag")
        self._build_scan_tab(self.tab_channel, "channel")
        self._build_library_tab()
        self._build_settings_tab()

        bottom = ctk.CTkFrame(self)
        bottom.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        bottom.grid_columnconfigure(0, weight=1)
        # list_frame is row 4 (set below after page bar)
        self.bottom = bottom

        # Action bar
        ab = ctk.CTkFrame(bottom, fg_color="transparent")
        ab.grid(row=0, column=0, sticky="ew", pady=(8, 4), padx=4)
        self.lbl_found = ctk.CTkLabel(ab, text=self.i18n.t("found_count", count=0))
        self.lbl_found.pack(side="left", padx=4)
        self.lbl_selected = ctk.CTkLabel(ab, text=self.i18n.t("selected_count", count=0))
        self.lbl_selected.pack(side="left", padx=8)
        self.lbl_size_summary = ctk.CTkLabel(
            ab,
            text=self.i18n.t("size_summary", total="—", selected="—", count=0),
            text_color="gray70",
        )
        self.lbl_size_summary.pack(side="left", padx=8)

        self.btn_select_all = ctk.CTkButton(
            ab, text=self.i18n.t("btn_select_all"), width=110, command=self._select_all
        )
        self.btn_select_all.pack(side="left", padx=4)
        self.btn_deselect_all = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_deselect_all"),
            width=110,
            command=self._deselect_all,
            fg_color="gray40",
            hover_color="gray30",
        )
        self.btn_deselect_all.pack(side="left", padx=4)
        self.btn_select_new = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_select_new"),
            width=120,
            command=self._select_new_only,
            fg_color="#2d5a7b",
            hover_color="#244a66",
        )
        self.btn_retry_failed = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_retry_failed"),
            width=150,
            command=self._retry_failed,
            fg_color=_AMBER,
            hover_color="#6e5617",
        )
        self.btn_retry_failed.pack(side="left", padx=8)
        self.btn_download = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_download"),
            width=140,
            command=self._start_download,
            fg_color=_GREEN,
            hover_color="#248a4e",
            text_color="#ffffff",
        )
        self.btn_download.pack(side="right", padx=4)
        self.btn_stop = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_stop"),
            width=90,
            command=self._request_stop,
            fg_color=_RED,
            hover_color="#6e2424",
            state="disabled",
        )
        self.btn_stop.pack(side="right", padx=4)
        self.btn_pause = ctk.CTkButton(
            ab,
            text=self.i18n.t("btn_pause"),
            width=100,
            command=self._toggle_pause,
            fg_color="#6b5b2e",
            hover_color="#564820",
            state="disabled",
        )
        self.btn_pause.pack(side="right", padx=4)

        # Folder / tuning
        opts = ctk.CTkFrame(bottom)
        opts.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 6))
        opts.grid_columnconfigure(1, weight=1)
        self.lbl_dl_dir_bar = ctk.CTkLabel(
            opts,
            text=self.i18n.t("download_dir_primary"),
            font=ctk.CTkFont(weight="bold"),
        )
        self.lbl_dl_dir_bar.grid(row=0, column=0, padx=(10, 6), pady=(8, 4), sticky="w")
        self.entry_dl_dir_bar = ctk.CTkEntry(opts, height=32)
        self.entry_dl_dir_bar.insert(0, self.config_data.download_dir)
        self.entry_dl_dir_bar.grid(row=0, column=1, padx=4, pady=(8, 4), sticky="ew")
        self.entry_dl_dir_bar.bind("<FocusOut>", lambda _e: self._save_settings_from_ui())
        self.btn_browse_bar = ctk.CTkButton(
            opts, text=self.i18n.t("btn_browse"), width=120, command=self._browse_dir, fg_color=_BLUE
        )
        self.btn_browse_bar.grid(row=0, column=2, padx=4, pady=(8, 4))
        self.btn_add_root_bar = ctk.CTkButton(
            opts,
            text=self.i18n.t("btn_add_download_root"),
            width=140,
            command=self._add_download_root,
            fg_color=_BLUE,
        )
        self.btn_add_root_bar.grid(row=0, column=3, padx=4, pady=(8, 4))
        self.btn_open_folder = ctk.CTkButton(
            opts,
            text=self.i18n.t("btn_open_folder"),
            width=110,
            command=self._open_download_folder,
            fg_color="gray40",
        )
        self.btn_open_folder.grid(row=0, column=4, padx=(4, 10), pady=(8, 4))

        self.lbl_roots_bar = ctk.CTkLabel(
            opts, text="", text_color="gray60", anchor="w", wraplength=900
        )
        self.lbl_roots_bar.grid(
            row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 4)
        )

        tune = ctk.CTkFrame(opts, fg_color="transparent")
        tune.grid(row=2, column=0, columnspan=5, sticky="w", padx=10, pady=(0, 8))
        self.lbl_dl_batch = ctk.CTkLabel(tune, text=self.i18n.t("download_batch_label"))
        self.lbl_dl_batch.pack(side="left", padx=(0, 4))
        self.entry_dl_batch = ctk.CTkEntry(tune, width=44)
        self.entry_dl_batch.insert(0, str(self.config_data.download_batch))
        self.entry_dl_batch.pack(side="left", padx=2)
        self.entry_dl_batch.bind("<FocusOut>", lambda _e: self._save_settings_from_ui())
        self.lbl_dl_delay = ctk.CTkLabel(tune, text=self.i18n.t("download_delay_label"))
        self.lbl_dl_delay.pack(side="left", padx=(12, 4))
        self.entry_dl_delay = ctk.CTkEntry(tune, width=50)
        self.entry_dl_delay.insert(0, str(self.config_data.download_delay_sec))
        self.entry_dl_delay.pack(side="left", padx=2)
        self.entry_dl_delay.bind("<FocusOut>", lambda _e: self._save_settings_from_ui())
        self.lbl_stall = ctk.CTkLabel(tune, text=self.i18n.t("stall_timeout_label"))
        self.lbl_stall.pack(side="left", padx=(12, 4))
        self.entry_stall = ctk.CTkEntry(tune, width=50)
        self.entry_stall.insert(0, str(int(self.config_data.stall_timeout_sec)))
        self.entry_stall.pack(side="left", padx=2)
        self.entry_stall.bind("<FocusOut>", lambda _e: self._save_settings_from_ui())

        # List header
        weights = (0, 0, 3, 2, 0, 3, 2, 0)
        hdr = ctk.CTkFrame(bottom, height=32)
        hdr.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))
        for col, w in enumerate(weights):
            hdr.grid_columnconfigure(col, weight=w)
        self.hdr_sel = ctk.CTkLabel(hdr, text=self.i18n.t("col_select"), width=36)
        self.hdr_sel.grid(row=0, column=0, padx=4)
        self.hdr_thumb = ctk.CTkLabel(hdr, text=self.i18n.t("col_thumb"), width=thumb_mod.THUMB_W)
        self.hdr_thumb.grid(row=0, column=1, padx=4)
        self.hdr_title = ctk.CTkLabel(hdr, text=self.i18n.t("col_title"), anchor="w")
        self.hdr_title.grid(row=0, column=2, sticky="ew", padx=4)
        self.hdr_author = ctk.CTkLabel(hdr, text=self.i18n.t("col_author"), anchor="w")
        self.hdr_author.grid(row=0, column=3, sticky="ew", padx=4)
        self.hdr_size = ctk.CTkLabel(hdr, text=self.i18n.t("col_size"), width=72, anchor="e")
        self.hdr_size.grid(row=0, column=4, padx=4)
        self.hdr_progress = ctk.CTkLabel(hdr, text=self.i18n.t("col_progress"), anchor="w")
        self.hdr_progress.grid(row=0, column=5, sticky="ew", padx=4)
        self.hdr_status = ctk.CTkLabel(hdr, text=self.i18n.t("col_status"), anchor="w")
        self.hdr_status.grid(row=0, column=6, sticky="ew", padx=4)
        self.hdr_action = ctk.CTkLabel(hdr, text=self.i18n.t("col_action"), width=80)
        self.hdr_action.grid(row=0, column=7, padx=4)

        # Page navigation (keeps only one page of widgets for smooth scrolling)
        page_bar = ctk.CTkFrame(bottom, fg_color="transparent")
        page_bar.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 2))
        self.btn_page_prev = ctk.CTkButton(
            page_bar,
            text=self.i18n.t("btn_page_prev"),
            width=90,
            height=28,
            command=self._page_prev,
            fg_color="gray40",
        )
        self.btn_page_prev.pack(side="left", padx=4)
        self.lbl_page = ctk.CTkLabel(
            page_bar, text="", text_color="gray70", anchor="w"
        )
        self.lbl_page.pack(side="left", padx=8)
        self.btn_page_next = ctk.CTkButton(
            page_bar,
            text=self.i18n.t("btn_page_next"),
            width=90,
            height=28,
            command=self._page_next,
            fg_color="gray40",
        )
        self.btn_page_next.pack(side="left", padx=4)

        self.list_frame = ctk.CTkScrollableFrame(bottom, height=300)
        self.list_frame.grid(row=4, column=0, sticky="nsew", padx=4, pady=4)
        bottom.grid_rowconfigure(4, weight=1)
        for col, w in enumerate(weights):
            self.list_frame.grid_columnconfigure(col, weight=w)

        try:
            ph = thumb_mod.placeholder_pil()
            self._placeholder_img = ctk.CTkImage(
                light_image=ph, dark_image=ph, size=(thumb_mod.THUMB_W, thumb_mod.THUMB_H)
            )
        except Exception:
            self._placeholder_img = None

        # Progress
        pf = ctk.CTkFrame(bottom, fg_color="transparent")
        pf.grid(row=5, column=0, sticky="ew", padx=4, pady=(4, 0))
        pf.grid_columnconfigure(0, weight=1)
        self.lbl_prog_ok = ctk.CTkLabel(
            pf, text=self.i18n.t("progress_idle"), anchor="w", text_color="gray70",
            font=ctk.CTkFont(weight="bold"),
        )
        self.lbl_prog_ok.grid(row=0, column=0, sticky="w")
        self.lbl_prog_err = ctk.CTkLabel(pf, text="", anchor="w", text_color=_ERR, font=ctk.CTkFont(weight="bold"))
        self.lbl_prog_err.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.lbl_progress = ctk.CTkLabel(pf, text="", anchor="w", text_color="gray70")
        self.lbl_progress.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.lbl_progress_pct = ctk.CTkLabel(pf, text="0%", width=48, anchor="e", font=ctk.CTkFont(weight="bold"))
        self.lbl_progress_pct.grid(row=0, column=3, sticky="e")

        self.progress = ctk.CTkProgressBar(bottom, height=14)
        self.progress.grid(row=6, column=0, sticky="ew", padx=4, pady=(4, 0))
        self.progress.set(0)

        self.lbl_log = ctk.CTkLabel(bottom, text=self.i18n.t("log_title"), anchor="w")
        self.lbl_log.grid(row=7, column=0, sticky="w", padx=8, pady=(8, 0))
        self.log_box = ctk.CTkTextbox(bottom, height=130)
        self.log_box.grid(row=8, column=0, sticky="ew", padx=4, pady=(4, 8))
        self.log_box.configure(state="disabled")

        self._update_select_new_visibility()
        # Initial local library list only (remote check when user opens tab)
        self.after(200, self._library_load_local)

    def _build_library_tab(self) -> None:
        tab = self.tab_library
        tab.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(12, 4))
        self.btn_lib_refresh = ctk.CTkButton(
            bar,
            text=self.i18n.t("btn_library_refresh"),
            width=110,
            command=lambda: self._library_refresh(auto=False),
            fg_color=_BLUE,
        )
        self.btn_lib_refresh.pack(side="left", padx=4)
        self.btn_lib_update_all = ctk.CTkButton(
            bar,
            text=self.i18n.t("btn_library_update_all"),
            width=140,
            command=self._library_update_all,
            fg_color=_GREEN,
            hover_color="#185734",
        )
        self.btn_lib_update_all.pack(side="left", padx=4)
        self.lbl_lib_summary = ctk.CTkLabel(
            bar, text="", text_color="gray70", anchor="w"
        )
        self.lbl_lib_summary.pack(side="left", padx=12)

        self.lbl_lib_hint = ctk.CTkLabel(
            tab,
            text=self.i18n.t("library_hint"),
            text_color="gray60",
            anchor="w",
            wraplength=1000,
        )
        self.lbl_lib_hint.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 2))

        # Progress while checking remote counts
        prog_frame = ctk.CTkFrame(tab, fg_color="transparent")
        prog_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        prog_frame.grid_columnconfigure(0, weight=1)
        self.lbl_lib_progress = ctk.CTkLabel(
            prog_frame,
            text=self.i18n.t("library_progress_idle"),
            text_color="gray70",
            anchor="w",
        )
        self.lbl_lib_progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.lbl_lib_progress_pct = ctk.CTkLabel(
            prog_frame, text="0%", width=44, anchor="e", text_color="gray70"
        )
        self.lbl_lib_progress_pct.grid(row=0, column=1, sticky="e")
        self.lib_progress = ctk.CTkProgressBar(prog_frame, height=10)
        self.lib_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.lib_progress.set(0)

        # Header + scrollable channel list (channel · drive · local · remote · missing · actions)
        weights = (3, 1, 1, 1, 1, 2)
        hdr = ctk.CTkFrame(tab, height=28)
        hdr.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 0))
        for col, w in enumerate(weights):
            hdr.grid_columnconfigure(col, weight=w)
        self.hdr_lib_ch = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_channel"), anchor="w"
        )
        self.hdr_lib_ch.grid(row=0, column=0, sticky="ew", padx=6)
        self.hdr_lib_drive = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_drive"), width=56
        )
        self.hdr_lib_drive.grid(row=0, column=1, padx=4)
        self.hdr_lib_local = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_local"), width=70
        )
        self.hdr_lib_local.grid(row=0, column=2, padx=4)
        self.hdr_lib_remote = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_remote"), width=70
        )
        self.hdr_lib_remote.grid(row=0, column=3, padx=4)
        self.hdr_lib_new = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_new"), width=150, anchor="w"
        )
        self.hdr_lib_new.grid(row=0, column=4, padx=4, sticky="w")
        self.hdr_lib_act = ctk.CTkLabel(
            hdr, text=self.i18n.t("library_col_actions"), width=180
        )
        self.hdr_lib_act.grid(row=0, column=5, padx=4)

        self.lib_list = ctk.CTkScrollableFrame(tab, height=160)
        self.lib_list.grid(row=4, column=0, sticky="nsew", padx=8, pady=(2, 10))
        tab.grid_rowconfigure(4, weight=1)
        for col, w in enumerate(weights):
            self.lib_list.grid_columnconfigure(col, weight=w)

    def _build_scan_tab(self, parent: ctk.CTkFrame, mode: str) -> None:
        parent.grid_columnconfigure(1, weight=1)
        lbl = ctk.CTkLabel(
            parent,
            text=self.i18n.t("hashtag_label" if mode == "hashtag" else "channel_label"),
        )
        lbl.grid(row=0, column=0, padx=12, pady=(16, 8), sticky="w")
        setattr(self, f"_{mode}_label", lbl)
        entry = ctk.CTkEntry(
            parent,
            placeholder_text=self.i18n.t(
                "hashtag_placeholder" if mode == "hashtag" else "channel_placeholder"
            ),
            height=36,
        )
        entry.grid(row=0, column=1, padx=8, pady=(16, 8), sticky="ew")
        setattr(self, f"_{mode}_entry", entry)
        limit_lbl = ctk.CTkLabel(parent, text=self.i18n.t("limit_label"))
        limit_lbl.grid(row=0, column=2, padx=8, pady=(16, 8))
        setattr(self, f"_{mode}_limit_label", limit_lbl)
        limit = ctk.CTkEntry(parent, width=70)
        limit.insert(0, str(self.config_data.scan_limit))
        limit.grid(row=0, column=3, padx=4, pady=(16, 8))
        setattr(self, f"_{mode}_limit", limit)
        btn = ctk.CTkButton(
            parent,
            text=self.i18n.t("btn_scan"),
            width=100,
            command=lambda m=mode: self._start_scan(m),
        )
        btn.grid(row=0, column=4, padx=12, pady=(16, 8))
        setattr(self, f"_{mode}_scan_btn", btn)
        hint = ctk.CTkLabel(
            parent,
            text=self.i18n.t("msg_demo_note") if self.config_data.demo_mode else "",
            text_color="gray60",
            anchor="w",
            wraplength=1000,
        )
        hint.grid(row=1, column=0, columnspan=5, padx=12, pady=(0, 12), sticky="w")
        setattr(self, f"_{mode}_hint", hint)

    def _build_settings_tab(self) -> None:
        tab = self.tab_settings
        tab.grid_columnconfigure(1, weight=1)
        r = 0
        self.lbl_dl_dir = ctk.CTkLabel(tab, text=self.i18n.t("download_dir_primary"))
        self.lbl_dl_dir.grid(row=r, column=0, padx=12, pady=(16, 8), sticky="w")
        self.entry_dl_dir = ctk.CTkEntry(tab)
        self.entry_dl_dir.insert(0, self.config_data.download_dir)
        self.entry_dl_dir.grid(row=r, column=1, padx=8, pady=(16, 8), sticky="ew")
        self.btn_browse = ctk.CTkButton(
            tab, text=self.i18n.t("btn_browse"), width=120, command=self._browse_dir
        )
        self.btn_browse.grid(row=r, column=2, padx=12, pady=(16, 8))

        r = 1
        self.lbl_roots = ctk.CTkLabel(tab, text=self.i18n.t("download_roots_label"))
        self.lbl_roots.grid(row=r, column=0, padx=12, pady=(4, 2), sticky="nw")
        roots_box = ctk.CTkFrame(tab)
        roots_box.grid(row=r, column=1, columnspan=2, padx=8, pady=(4, 2), sticky="ew")
        self.lbl_roots_hint = ctk.CTkLabel(
            roots_box,
            text=self.i18n.t("download_roots_hint"),
            text_color="gray60",
            anchor="w",
            wraplength=520,
            justify="left",
        )
        self.lbl_roots_hint.pack(fill="x", padx=6, pady=(4, 2))
        self.roots_list = ctk.CTkScrollableFrame(roots_box, height=110)
        self.roots_list.pack(fill="both", expand=True, padx=4, pady=2)
        self.btn_add_root = ctk.CTkButton(
            roots_box,
            text=self.i18n.t("btn_add_download_root"),
            width=160,
            command=self._add_download_root,
            fg_color=_BLUE,
        )
        self.btn_add_root.pack(anchor="w", padx=6, pady=(2, 6))
        self._refresh_roots_list_ui()

        r = 2
        self.lbl_dl_batch_set = ctk.CTkLabel(tab, text=self.i18n.t("download_batch_label"))
        self.lbl_dl_batch_set.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_dl_batch_set = ctk.CTkEntry(tab, width=80)
        self.entry_dl_batch_set.insert(0, str(self.config_data.download_batch))
        self.entry_dl_batch_set.grid(row=r, column=1, padx=8, pady=8, sticky="w")

        r = 3
        self.lbl_dl_delay_set = ctk.CTkLabel(tab, text=self.i18n.t("download_delay_label"))
        self.lbl_dl_delay_set.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_dl_delay_set = ctk.CTkEntry(tab, width=80)
        self.entry_dl_delay_set.insert(0, str(self.config_data.download_delay_sec))
        self.entry_dl_delay_set.grid(row=r, column=1, padx=8, pady=8, sticky="w")

        r = 4
        self.lbl_stall_set = ctk.CTkLabel(tab, text=self.i18n.t("stall_timeout_label"))
        self.lbl_stall_set.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_stall_set = ctk.CTkEntry(tab, width=80)
        self.entry_stall_set.insert(0, str(int(self.config_data.stall_timeout_sec)))
        self.entry_stall_set.grid(row=r, column=1, padx=8, pady=8, sticky="w")

        r = 5
        self.lbl_scan_delay = ctk.CTkLabel(tab, text=self.i18n.t("scan_delay_label"))
        self.lbl_scan_delay.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_scan_delay = ctk.CTkEntry(tab, width=80)
        self.entry_scan_delay.insert(0, str(self.config_data.scan_delay_sec))
        self.entry_scan_delay.grid(row=r, column=1, padx=8, pady=8, sticky="w")

        r = 6
        self.lbl_theme = ctk.CTkLabel(tab, text=self.i18n.t("theme"))
        self.lbl_theme.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.theme_var = ctk.StringVar(
            value=self.i18n.t("theme_dark")
            if self.config_data.theme == "dark"
            else self.i18n.t("theme_light")
        )
        self.theme_menu = ctk.CTkOptionMenu(
            tab,
            variable=self.theme_var,
            values=[self.i18n.t("theme_dark"), self.i18n.t("theme_light")],
            command=self._on_theme_change,
            width=140,
        )
        self.theme_menu.grid(row=r, column=1, padx=8, pady=8, sticky="w")

        r = 7
        self.demo_var = ctk.BooleanVar(value=self.config_data.demo_mode)
        self.chk_demo = ctk.CTkCheckBox(
            tab,
            text=self.i18n.t("demo_mode"),
            variable=self.demo_var,
            command=self._save_settings_from_ui,
        )
        self.chk_demo.grid(row=r, column=0, columnspan=2, padx=12, pady=8, sticky="w")

        # --- UI backend / GPU / cache ---
        r = 8
        self.lbl_ui_backend = ctk.CTkLabel(tab, text=self.i18n.t("ui_backend_label"))
        self.lbl_ui_backend.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self._ui_backend_labels = {
            "tk": self.i18n.t("ui_backend_tk"),
            "flet": self.i18n.t("ui_backend_flet"),
        }
        self.ui_backend_var = ctk.StringVar(
            value=self._ui_backend_labels.get(
                self.config_data.ui_backend, self._ui_backend_labels["tk"]
            )
        )
        self.ui_backend_menu = ctk.CTkOptionMenu(
            tab,
            variable=self.ui_backend_var,
            values=list(self._ui_backend_labels.values()),
            command=self._on_ui_backend_change,
            width=280,
        )
        self.ui_backend_menu.grid(row=r, column=1, columnspan=2, padx=8, pady=8, sticky="w")

        r = 9
        self.lbl_ui_gpu = ctk.CTkLabel(tab, text=self.i18n.t("ui_gpu_pick_label"))
        self.lbl_ui_gpu.grid(row=r, column=0, padx=12, pady=8, sticky="nw")
        self._gpus = list_gpus()
        self._ui_gpu_options = build_gpu_options(
            self.i18n.t("ui_gpu_auto"),
            self.i18n.t("ui_gpu_high"),
            self.i18n.t("ui_gpu_save"),
            self._gpus,
        )
        # label -> key reverse map for OptionMenu
        self._ui_gpu_labels = {k: lab for k, lab in self._ui_gpu_options}
        self._ui_gpu_label_to_key = {lab: k for k, lab in self._ui_gpu_options}
        cur_key = self.config_data.ui_gpu or GPU_AUTO
        if cur_key not in self._ui_gpu_labels:
            cur_key = GPU_AUTO
        self.ui_gpu_var = ctk.StringVar(value=self._ui_gpu_labels[cur_key])
        self.ui_gpu_menu = ctk.CTkOptionMenu(
            tab,
            variable=self.ui_gpu_var,
            values=[lab for _k, lab in self._ui_gpu_options],
            command=self._on_ui_gpu_change,
            width=420,
        )
        self.ui_gpu_menu.grid(row=r, column=1, columnspan=2, padx=8, pady=8, sticky="w")
        r += 1
        det = format_gpu_list(self._gpus) if self._gpus else self.i18n.t("ui_gpu_none")
        self.lbl_gpu_detected = ctk.CTkLabel(
            tab,
            text=f"{self.i18n.t('ui_gpu_detected_label')}:\n{det}",
            text_color="gray70",
            anchor="w",
            justify="left",
        )
        self.lbl_gpu_detected.grid(
            row=r, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="w"
        )

        r += 1
        self.lbl_page_size = ctk.CTkLabel(tab, text=self.i18n.t("list_page_size_label"))
        self.lbl_page_size.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_page_size = ctk.CTkEntry(tab, width=80)
        self.entry_page_size.insert(0, str(self._page_size))
        self.entry_page_size.grid(row=r, column=1, padx=8, pady=8, sticky="w")
        self.entry_page_size.bind("<FocusOut>", lambda _e: self._save_settings_from_ui())

        r += 1
        self.btn_clear_cache = ctk.CTkButton(
            tab,
            text=self.i18n.t("btn_clear_cache"),
            width=140,
            command=self._on_clear_cache,
            fg_color=_AMBER,
            hover_color="#6e5617",
        )
        self.btn_clear_cache.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        st = cache_stats()
        self.lbl_cache_status = ctk.CTkLabel(
            tab,
            text=self.i18n.t(
                "msg_cache_stats", files=st["files"], mb=st["mb"]
            ),
            text_color="gray70",
            anchor="w",
        )
        self.lbl_cache_status.grid(row=r, column=1, columnspan=2, padx=8, pady=8, sticky="w")

        r += 1
        self.lbl_email = ctk.CTkLabel(tab, text=self.i18n.t("login_email"))
        self.lbl_email.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_email = ctk.CTkEntry(tab)
        self.entry_email.insert(0, self.config_data.email)
        self.entry_email.grid(row=r, column=1, padx=8, pady=8, sticky="ew")

        r += 1
        self.lbl_password = ctk.CTkLabel(tab, text=self.i18n.t("login_password"))
        self.lbl_password.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.entry_password = ctk.CTkEntry(tab, show="*")
        self.entry_password.insert(0, self.config_data.password)
        self.entry_password.grid(row=r, column=1, padx=8, pady=8, sticky="ew")

        r += 1
        self.btn_login = ctk.CTkButton(
            tab,
            text=self.i18n.t("btn_login"),
            width=140,
            command=self._on_login_click,
            fg_color=_BLUE,
        )
        self.btn_login.grid(row=r, column=0, padx=12, pady=8, sticky="w")
        self.lbl_login_status = ctk.CTkLabel(
            tab,
            text=self.i18n.t("login_status_idle"),
            anchor="w",
            text_color="gray70",
            wraplength=520,
            justify="left",
        )
        self.lbl_login_status.grid(row=r, column=1, columnspan=2, padx=8, pady=8, sticky="w")

        r += 1
        self.lbl_login_hint = ctk.CTkLabel(
            tab, text=self.i18n.t("login_hint"), text_color="gray60", wraplength=700, anchor="w"
        )
        self.lbl_login_hint.grid(row=r, column=0, columnspan=3, padx=12, pady=4, sticky="w")
        r += 1
        self.lbl_settings_hint = ctk.CTkLabel(
            tab, text=self.i18n.t("settings_hint"), text_color="gray60", wraplength=700, anchor="w"
        )
        self.lbl_settings_hint.grid(row=r, column=0, columnspan=3, padx=12, pady=4, sticky="w")

    # ------------------------------------------------------------------ i18n
    def _on_tab_change(self) -> None:
        self._update_select_new_visibility()
        try:
            if self.tabs.get() == self.i18n.t("tab_library"):
                self._library_on_enter()
        except Exception:
            pass

    def _library_on_enter(self) -> None:
        """When opening Library tab: load local list; auto-check remote only once."""
        if not self._library_channels:
            self._library_load_local()
        # Auto remote check only the first time this session — later use «Làm mới»
        if self._library_remote_checked or self._busy or self._library_checking:
            return
        # Small delay so tab UI paints first, then start API checks
        self.after(150, self._library_auto_refresh_if_active)

    def _library_auto_refresh_if_active(self) -> None:
        """Start remote check only if Library tab is still selected (first visit)."""
        try:
            if self.tabs.get() != self.i18n.t("tab_library"):
                return
        except Exception:
            return
        if self._library_remote_checked or self._busy or self._library_checking:
            return
        self._library_refresh(auto=True)

    def _is_channel_tab_active(self) -> bool:
        try:
            return self.tabs.get() == self.i18n.t("tab_channel")
        except Exception:
            return False

    def _is_library_context(self) -> bool:
        """True when main list is showing a channel opened from library or channel tab."""
        return self._scan_source == "channel"

    def _update_select_new_visibility(self) -> None:
        try:
            self.btn_select_new.pack_forget()
        except Exception:
            pass
        if self._is_channel_tab_active() or self._is_library_context():
            self.btn_select_new.pack(side="left", padx=4, before=self.btn_retry_failed)

    def _on_language_change(self, label: str) -> None:
        code = next((c for c, name in LANG_LABELS.items() if name == label), "vi")
        self.i18n.set_lang(code)
        self.config_data.language = code
        self.config_data.save()

    def _on_theme_change(self, label: str) -> None:
        mode = "dark" if label == self.i18n.t("theme_dark") else "light"
        self.config_data.theme = mode
        ctk.set_appearance_mode(mode)
        self.config_data.save()

    def _on_ui_backend_change(self, _label: str) -> None:
        self._save_settings_from_ui()
        messagebox.showinfo(self.i18n.t("app_title"), self.i18n.t("msg_ui_restart"))
        self._log(self.i18n.t("msg_ui_restart"))

    def _on_ui_gpu_change(self, _label: str) -> None:
        self._save_settings_from_ui()
        sel = self.config_data.ui_gpu or GPU_AUTO
        ok, msg = apply_windows_gpu_preference(sel)
        self._log(msg if ok else f"GPU: {msg}")
        name = self.config_data.ui_gpu_adapter or sel
        messagebox.showinfo(
            self.i18n.t("app_title"),
            self.i18n.t(
                "msg_gpu_applied", name=name, detail=msg if ok else str(msg)
            ),
        )

    def _on_clear_cache(self) -> None:
        result = clear_cache(thumbs=True, memory=True)
        freed_mb = round(result["freed_bytes"] / (1024 * 1024), 2)
        msg = self.i18n.t(
            "msg_cache_cleared", files=result["removed"], mb=freed_mb
        )
        st = cache_stats()
        try:
            self.lbl_cache_status.configure(
                text=self.i18n.t(
                    "msg_cache_stats", files=st["files"], mb=st["mb"]
                )
            )
        except Exception:
            pass
        # Drop in-UI thumb images so they re-fetch
        self._thumb_images.clear()
        self._rebuild_page()
        self._log(msg)
        messagebox.showinfo(self.i18n.t("app_title"), msg)

    def _refresh_texts(self) -> None:
        t = self.i18n.t
        self.title(t("app_title"))
        self.lbl_title.configure(text=t("app_title"))
        self.lbl_lang.configure(text=t("language"))
        try:
            for idx, key in enumerate(self._tab_keys):
                name = t(key)
                old = self.tabs._name_list[idx]  # type: ignore[attr-defined]
                if old != name:
                    frame = self.tabs._tab_dict[old]  # type: ignore[attr-defined]
                    del self.tabs._tab_dict[old]  # type: ignore[attr-defined]
                    self.tabs._tab_dict[name] = frame  # type: ignore[attr-defined]
                    self.tabs._name_list[idx] = name  # type: ignore[attr-defined]
                    btn = self.tabs._segmented_button._buttons_dict.pop(old)  # type: ignore[attr-defined]
                    self.tabs._segmented_button._buttons_dict[name] = btn  # type: ignore[attr-defined]
                    btn.configure(text=name)
        except Exception:
            pass
        self._hashtag_label.configure(text=t("hashtag_label"))
        self._channel_label.configure(text=t("channel_label"))
        self._hashtag_entry.configure(placeholder_text=t("hashtag_placeholder"))
        self._channel_entry.configure(placeholder_text=t("channel_placeholder"))
        self._hashtag_limit_label.configure(text=t("limit_label"))
        self._channel_limit_label.configure(text=t("limit_label"))
        self._hashtag_scan_btn.configure(text=t("btn_scan"))
        self._channel_scan_btn.configure(text=t("btn_scan"))
        note = t("msg_demo_note") if self.config_data.demo_mode else ""
        self._hashtag_hint.configure(text=note)
        self._channel_hint.configure(text=note)
        try:
            self.btn_lib_refresh.configure(text=t("btn_library_refresh"))
            self.btn_lib_update_all.configure(text=t("btn_library_update_all"))
            self.lbl_lib_hint.configure(text=t("library_hint"))
            self.hdr_lib_ch.configure(text=t("library_col_channel"))
            try:
                self.hdr_lib_drive.configure(text=t("library_col_drive"))
            except Exception:
                pass
            self.hdr_lib_local.configure(text=t("library_col_local"))
            self.hdr_lib_remote.configure(text=t("library_col_remote"))
            self.hdr_lib_new.configure(text=t("library_col_new"))
            self.hdr_lib_act.configure(text=t("library_col_actions"))
            if not self._library_checking:
                self.lbl_lib_progress.configure(text=t("library_progress_idle"))
            self._library_render_rows()
        except Exception:
            pass
        self.lbl_dl_dir.configure(text=t("download_dir_primary"))
        self.lbl_dl_dir_bar.configure(text=t("download_dir_primary"))
        self.btn_browse.configure(text=t("btn_browse"))
        self.btn_browse_bar.configure(text=t("btn_browse"))
        try:
            self.lbl_roots.configure(text=t("download_roots_label"))
            self.lbl_roots_hint.configure(text=t("download_roots_hint"))
            self.btn_add_root.configure(text=t("btn_add_download_root"))
            self.btn_add_root_bar.configure(text=t("btn_add_download_root"))
            self._refresh_roots_list_ui()
        except Exception:
            pass
        self.btn_open_folder.configure(text=t("btn_open_folder"))
        self.lbl_dl_batch.configure(text=t("download_batch_label"))
        self.lbl_dl_batch_set.configure(text=t("download_batch_label"))
        self.lbl_dl_delay.configure(text=t("download_delay_label"))
        self.lbl_dl_delay_set.configure(text=t("download_delay_label"))
        self.lbl_stall.configure(text=t("stall_timeout_label"))
        self.lbl_stall_set.configure(text=t("stall_timeout_label"))
        self.lbl_scan_delay.configure(text=t("scan_delay_label"))
        self.lbl_theme.configure(text=t("theme"))
        self.chk_demo.configure(text=t("demo_mode"))
        self.lbl_email.configure(text=t("login_email"))
        self.lbl_password.configure(text=t("login_password"))
        self.btn_login.configure(text=t("btn_login"))
        self.lbl_login_hint.configure(text=t("login_hint"))
        self.lbl_settings_hint.configure(text=t("settings_hint"))
        dark_lbl, light_lbl = t("theme_dark"), t("theme_light")
        self.theme_menu.configure(values=[dark_lbl, light_lbl])
        self.theme_var.set(dark_lbl if self.config_data.theme == "dark" else light_lbl)
        self.btn_select_all.configure(text=t("btn_select_all"))
        self.btn_deselect_all.configure(text=t("btn_deselect_all"))
        self.btn_select_new.configure(text=t("btn_select_new"))
        self.btn_retry_failed.configure(text=t("btn_retry_failed"))
        self.btn_download.configure(text=t("btn_download"))
        self.btn_stop.configure(text=t("btn_stop"))
        self._sync_pause_button()
        try:
            self.btn_page_prev.configure(text=t("btn_page_prev"))
            self.btn_page_next.configure(text=t("btn_page_next"))
            self._update_page_label()
            self.lbl_ui_backend.configure(text=t("ui_backend_label"))
            self.lbl_ui_gpu.configure(text=t("ui_gpu_pick_label"))
            self.lbl_page_size.configure(text=t("list_page_size_label"))
            self.btn_clear_cache.configure(text=t("btn_clear_cache"))
            self._ui_backend_labels = {
                "tk": t("ui_backend_tk"),
                "flet": t("ui_backend_flet"),
            }
            self.ui_backend_menu.configure(values=list(self._ui_backend_labels.values()))
            self.ui_backend_var.set(
                self._ui_backend_labels.get(
                    self.config_data.ui_backend, self._ui_backend_labels["tk"]
                )
            )
            self._gpus = list_gpus()
            self._ui_gpu_options = build_gpu_options(
                t("ui_gpu_auto"),
                t("ui_gpu_high"),
                t("ui_gpu_save"),
                self._gpus,
            )
            self._ui_gpu_labels = {k: lab for k, lab in self._ui_gpu_options}
            self._ui_gpu_label_to_key = {lab: k for k, lab in self._ui_gpu_options}
            self.ui_gpu_menu.configure(values=[lab for _k, lab in self._ui_gpu_options])
            cur_key = self.config_data.ui_gpu or GPU_AUTO
            if cur_key not in self._ui_gpu_labels:
                cur_key = GPU_AUTO
            self.ui_gpu_var.set(self._ui_gpu_labels[cur_key])
            det = format_gpu_list(self._gpus) if self._gpus else t("ui_gpu_none")
            self.lbl_gpu_detected.configure(
                text=f"{t('ui_gpu_detected_label')}:\n{det}"
            )
        except Exception:
            pass
        self.hdr_sel.configure(text=t("col_select"))
        self.hdr_thumb.configure(text=t("col_thumb"))
        self.hdr_title.configure(text=t("col_title"))
        self.hdr_author.configure(text=t("col_author"))
        self.hdr_size.configure(text=t("col_size"))
        self.hdr_progress.configure(text=t("col_progress"))
        self.hdr_status.configure(text=t("col_status"))
        self.hdr_action.configure(text=t("col_action"))
        self.lbl_log.configure(text=t("log_title"))
        self.lbl_found.configure(text=t("found_count", count=len(self._videos)))
        self._update_selected_label()
        self._update_select_new_visibility()
        for i, item in enumerate(self._videos):
            self._update_row_status(i, item)

    # ------------------------------------------------------------------ helpers
    def _log(self, message: str) -> None:
        try:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass
        console_log(message)

    def _sync_download_dir_entries(self, path: str) -> None:
        path = (path or "").strip()
        for entry in (self.entry_dl_dir_bar, self.entry_dl_dir):
            if entry.get() != path:
                entry.delete(0, "end")
                entry.insert(0, path)

    def _all_roots(self) -> list[str]:
        return self.config_data.all_download_dirs()

    def _update_roots_bar_summary(self) -> None:
        if not hasattr(self, "lbl_roots_bar"):
            return
        try:
            infos = summarize_download_roots(
                self._all_roots(),
                primary=self.config_data.download_dir,
            )
            if not infos:
                self.lbl_roots_bar.configure(text="")
                return
            pieces: list[str] = []
            for info in infos:
                piece = self.i18n.t(
                    "library_drive_piece",
                    drive=info.drive or "?",
                    n=info.channel_count,
                )
                if info.is_primary:
                    piece = f"★ {piece}"
                free = f" · {info.free_gb:.0f}GB" if info.free_gb >= 0 else ""
                pieces.append(f"{piece}{free}")
            self.lbl_roots_bar.configure(text="  ·  ".join(pieces))
        except Exception:
            try:
                self.lbl_roots_bar.configure(text="")
            except Exception:
                pass

    def _refresh_roots_list_ui(self) -> None:
        if not hasattr(self, "roots_list"):
            return
        for child in self.roots_list.winfo_children():
            child.destroy()
        infos = summarize_download_roots(
            self._all_roots(),
            primary=self.config_data.download_dir,
        )
        for info in infos:
            free_s = f"{info.free_gb:.0f}" if info.free_gb >= 0 else "?"
            primary_tag = (
                self.i18n.t("download_root_primary_tag") if info.is_primary else ""
            )
            row_txt = self.i18n.t(
                "download_root_row",
                drive=info.drive or "?",
                channels=info.channel_count,
                videos=info.video_count,
                free=free_s,
                primary=primary_tag,
            )
            frame = ctk.CTkFrame(self.roots_list)
            frame.pack(fill="x", padx=2, pady=3)
            ctk.CTkLabel(
                frame, text=row_txt, anchor="w", font=ctk.CTkFont(weight="bold")
            ).pack(fill="x", padx=6, pady=(4, 0))
            ctk.CTkLabel(
                frame, text=info.path, anchor="w", text_color="gray60"
            ).pack(fill="x", padx=6)
            act = ctk.CTkFrame(frame, fg_color="transparent")
            act.pack(fill="x", padx=4, pady=(2, 4))
            if not info.is_primary:
                ctk.CTkButton(
                    act,
                    text=self.i18n.t("btn_set_primary_root"),
                    width=110,
                    height=26,
                    command=lambda p=info.path: self._set_primary_root(p),
                    fg_color=_BLUE,
                ).pack(side="left", padx=2)
            ctk.CTkButton(
                act,
                text=self.i18n.t("btn_remove_download_root"),
                width=60,
                height=26,
                command=lambda p=info.path: self._remove_download_root(p),
                fg_color=_RED,
                hover_color="#6b2222",
            ).pack(side="left", padx=2)
        self._update_roots_bar_summary()

    def _set_primary_root(self, path: str) -> None:
        self.config_data.set_primary_download_dir(path, keep_old=True)
        self.config_data.save()
        self._sync_download_dir_entries(self.config_data.download_dir)
        self._refresh_roots_list_ui()
        self._log(
            self.i18n.t(
                "msg_root_primary",
                path=path,
                drive=path_drive(path),
            )
        )

    def _remove_download_root(self, path: str) -> None:
        ok = self.config_data.remove_download_dir(path)
        if not ok:
            messagebox.showinfo(
                self.i18n.t("app_title"),
                self.i18n.t("msg_root_cannot_remove_last"),
            )
            return
        self.config_data.save()
        self._sync_download_dir_entries(self.config_data.download_dir)
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(self.i18n.t("msg_root_removed", path=path))
        try:
            self._library_load_local()
        except Exception:
            pass

    def _add_download_root(self) -> None:
        initial = self.entry_dl_dir_bar.get().strip() or self.config_data.download_dir
        path = filedialog.askdirectory(
            title=self.i18n.t("btn_add_download_root"),
            initialdir=initial if Path(initial).exists() else str(Path.home()),
        )
        if not path:
            return
        added = self.config_data.add_download_dir(path)
        if not added:
            messagebox.showinfo(
                self.i18n.t("app_title"), self.i18n.t("msg_root_already")
            )
            return
        self.config_data.save()
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(
            self.i18n.t(
                "msg_root_added",
                path=path,
                drive=path_drive(path),
            )
        )
        try:
            self._library_load_local()
        except Exception:
            pass

    def _browse_dir(self) -> None:
        initial = self.entry_dl_dir_bar.get().strip() or self.config_data.download_dir
        path = filedialog.askdirectory(
            title=self.i18n.t("download_dir_primary"),
            initialdir=initial if Path(initial).exists() else str(Path.home()),
        )
        if not path:
            return
        self.config_data.set_primary_download_dir(path, keep_old=True)
        self.config_data.save()
        self._sync_download_dir_entries(path)
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(
            self.i18n.t(
                "msg_root_primary",
                path=path,
                drive=path_drive(path),
            )
        )
        try:
            self._library_load_local()
        except Exception:
            pass

    def _open_download_folder(self) -> None:
        path = self.entry_dl_dir_bar.get().strip() or self.config_data.download_dir
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(self.i18n.t("app_title"), str(exc))

    def _resolve_local_path(self, item: VideoItem) -> Path | None:
        if item.local_path and Path(item.local_path).is_file():
            return Path(item.local_path)
        hist = self.history.get_path(item.id)
        if hist and Path(hist).is_file():
            item.local_path = hist
            return Path(hist)
        found = find_local_file(self._item_target_dir(item), item.id)
        if found and found.is_file():
            item.local_path = str(found)
            return found
        found = find_local_file_in_roots(
            self._all_roots(),
            item.id,
            channel=item.channel or "",
        )
        if found and found.is_file():
            item.local_path = str(found)
            return found
        return None

    def _open_video_file(self, index: int) -> None:
        if index < 0 or index >= len(self._videos):
            return
        item = self._videos[index]
        path = self._resolve_local_path(item)
        if path is None:
            self._log(self.i18n.t("msg_file_not_local"))
            messagebox.showinfo(self.i18n.t("app_title"), self.i18n.t("msg_file_not_local"))
            return
        try:
            os.startfile(str(path))  # noqa: S606
            self._log(self.i18n.t("msg_open_file", path=str(path)))
            if item.status not in (VideoStatus.ON_DISK, VideoStatus.DONE):
                item.status = VideoStatus.ON_DISK
                item.progress = 1.0
                self._update_row_status(index, item)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                self.i18n.t("app_title"),
                self.i18n.t("msg_open_file_fail", error=str(exc)),
            )

    def _bind_double_open(self, widget: Any, index: int) -> None:
        try:
            widget.bind("<Double-Button-1>", lambda _e, idx=index: self._open_video_file(idx))
        except Exception:
            pass

    def _save_settings_from_ui(self) -> None:
        delay_raw = self.entry_dl_delay.get().strip() or self.entry_dl_delay_set.get().strip()
        stall_raw = self.entry_stall.get().strip() or self.entry_stall_set.get().strip()
        batch_raw = (
            self.entry_dl_batch.get().strip()
            if hasattr(self, "entry_dl_batch")
            else ""
        ) or (
            self.entry_dl_batch_set.get().strip()
            if hasattr(self, "entry_dl_batch_set")
            else ""
        )
        try:
            dl_delay = max(0.0, float(delay_raw or "0"))
        except ValueError:
            dl_delay = self.config_data.download_delay_sec
        try:
            stall = max(15.0, float(stall_raw or "90"))
        except ValueError:
            stall = self.config_data.stall_timeout_sec
        try:
            scan_delay = max(0.0, float(self.entry_scan_delay.get().strip() or "0"))
        except ValueError:
            scan_delay = self.config_data.scan_delay_sec
        try:
            batch = max(1, min(16, int(batch_raw or "3")))
        except ValueError:
            batch = max(1, min(16, int(self.config_data.download_batch or 3)))
        chosen = (
            self.entry_dl_dir_bar.get().strip()
            or self.entry_dl_dir.get().strip()
            or self.config_data.download_dir
        )
        if chosen and chosen != self.config_data.download_dir:
            self.config_data.set_primary_download_dir(chosen, keep_old=True)
        else:
            self.config_data.download_dir = chosen
        self._sync_download_dir_entries(self.config_data.download_dir)
        try:
            self._refresh_roots_list_ui()
        except Exception:
            pass
        self.config_data.download_batch = batch
        self.config_data.download_delay_sec = dl_delay
        self.config_data.stall_timeout_sec = stall
        self.config_data.scan_delay_sec = scan_delay
        self.config_data.demo_mode = bool(self.demo_var.get())
        self.config_data.email = self.entry_email.get().strip()
        self.config_data.password = self.entry_password.get()
        # Page size
        try:
            ps = int(self.entry_page_size.get().strip() or "60")
            ps = max(20, min(200, ps))
        except ValueError:
            ps = self._page_size
        if ps != self._page_size:
            self._page_size = ps
            self.config_data.list_page_size = ps
            self._rebuild_page()
        else:
            self.config_data.list_page_size = ps
        # UI backend / GPU from menus
        try:
            be_label = self.ui_backend_var.get()
            for code, lab in self._ui_backend_labels.items():
                if lab == be_label:
                    self.config_data.ui_backend = code
                    break
            gpu_label = self.ui_gpu_var.get()
            key = self._ui_gpu_label_to_key.get(gpu_label)
            if key is None:
                for code, lab in self._ui_gpu_labels.items():
                    if lab == gpu_label:
                        key = code
                        break
            if key:
                self.config_data.ui_gpu = key
                _mode, adapter_name = parse_gpu_selection(
                    key, getattr(self, "_gpus", None)
                )
                self.config_data.ui_gpu_adapter = adapter_name
        except Exception:
            pass
        self.config_data.save()
        for entry, value in (
            (self.entry_dl_batch, str(batch)),
            (self.entry_dl_batch_set, str(batch)),
            (self.entry_dl_delay, str(dl_delay)),
            (self.entry_dl_delay_set, str(dl_delay)),
            (self.entry_stall, str(int(stall))),
            (self.entry_stall_set, str(int(stall))),
            (self.entry_scan_delay, str(scan_delay)),
        ):
            if entry.get() != value:
                entry.delete(0, "end")
                entry.insert(0, value)
        note = self.i18n.t("msg_demo_note") if self.config_data.demo_mode else ""
        self._hashtag_hint.configure(text=note)
        self._channel_hint.configure(text=note)

    def _parse_scan_limit(self, mode: str) -> int:
        raw = getattr(self, f"_{mode}_limit").get().strip()
        try:
            val = int(raw)
        except ValueError:
            val = self.config_data.scan_limit
        val = 0 if val < 0 else min(val, 10_000)
        self.config_data.scan_limit = val
        self.config_data.save()
        return val

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        st = "disabled" if busy else "normal"
        for w in (
            self._hashtag_scan_btn,
            self._channel_scan_btn,
            self.btn_download,
            self.btn_select_all,
            self.btn_deselect_all,
            self.btn_select_new,
            self.btn_retry_failed,
            self.btn_lib_refresh,
            self.btn_lib_update_all,
        ):
            try:
                w.configure(state=st)
            except Exception:
                pass
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if not busy:
            self._pause_gate.set()
        self._sync_pause_button()
        start, end = self._page_slice()
        for local, abs_i in enumerate(range(start, end)):
            if local >= len(self._row_retry):
                break
            can = abs_i < len(self._videos) and self._videos[abs_i].can_retry() and not busy
            try:
                self._row_retry[local].configure(state="normal" if can else "disabled")
            except Exception:
                pass
        # Disable per-row library action buttons while busy
        for row in self._library_rows:
            for key in ("btn_open", "btn_update"):
                b = row.get(key)
                if b is not None:
                    try:
                        b.configure(state=st)
                    except Exception:
                        pass

    def _pause_wait(self) -> None:
        while not self._pause_gate.is_set():
            if self._stop.is_set():
                return
            time.sleep(0.12)

    def _sync_pause_button(self) -> None:
        """Show «Tạm dừng» while running, «Tiếp tục» while paused."""
        paused = bool(self._busy) and not self._pause_gate.is_set()
        label = (
            self.i18n.t("btn_resume") if paused else self.i18n.t("btn_pause")
        )
        # Green when resume is available; amber when pause is available
        fg = "#2d7a4f" if paused else "#6b5b2e"
        hover = "#246640" if paused else "#564820"
        try:
            self.btn_pause.configure(
                text=label,
                fg_color=fg,
                hover_color=hover,
                state="normal" if self._busy else "disabled",
            )
        except Exception:
            pass

    def _toggle_pause(self) -> None:
        if not self._busy:
            return
        if self._pause_gate.is_set():
            # Running → pause: button becomes «Tiếp tục»
            self._pause_gate.clear()
            self.lbl_progress.configure(
                text="· " + self.i18n.t("progress_paused"), text_color=_PAUSE
            )
            self._log(self.i18n.t("msg_paused"))
        else:
            # Paused → resume: button becomes «Tạm dừng»
            self._pause_gate.set()
            self._log(self.i18n.t("msg_resumed"))
        self._sync_pause_button()

    def _request_stop(self) -> None:
        self._stop.set()
        self._pause_gate.set()
        self._sync_pause_button()
        self._log(self.i18n.t("msg_stopped"))

    def _selected_count(self) -> int:
        return sum(1 for v in self._videos if v.selected)

    def _update_selected_label(self) -> None:
        self.lbl_selected.configure(
            text=self.i18n.t("selected_count", count=self._selected_count())
        )
        total_b = sel_b = sel_n = 0
        unknown = 0
        for item in self._videos:
            if item.size_bytes > 0:
                total_b += item.size_bytes
            else:
                unknown += 1
            if item.selected:
                sel_n += 1
                if item.size_bytes > 0:
                    sel_b += item.size_bytes
        text = self.i18n.t(
            "size_summary",
            total=format_bytes(total_b if total_b else None),
            selected=format_bytes(sel_b if sel_b else None),
            count=sel_n,
        )
        if unknown:
            text += " " + self.i18n.t("size_unknown_note")
        self.lbl_size_summary.configure(text=text)

    def _on_check_toggle(self, index: int) -> None:
        # index is absolute video index
        if 0 <= index < len(self._videos):
            local = self._row_local_index(index)
            if local is not None and local < len(self._row_vars):
                self._videos[index].selected = bool(self._row_vars[local].get())
        self._update_selected_label()

    def _bulk_set_selection(self, pred: Callable[[int, VideoItem], bool]) -> None:
        for i, item in enumerate(self._videos):
            item.selected = bool(pred(i, item))
        # Sync checkboxes on the visible page only
        start, end = self._page_slice()
        for local, abs_i in enumerate(range(start, end)):
            want = self._videos[abs_i].selected
            if local < len(self._row_vars):
                self._row_vars[local].set(want)
            if local < len(self._row_checks):
                try:
                    if want:
                        self._row_checks[local].select()
                    else:
                        self._row_checks[local].deselect()
                except Exception:
                    pass
        self._update_selected_label()

    def _select_all(self) -> None:
        """Select all except private (must tick private manually if purchased)."""
        skipped_priv = sum(
            1
            for v in self._videos
            if v.is_private or v.status == VideoStatus.PRIVATE
        )
        self._bulk_set_selection(lambda _i, item: item.auto_selectable())
        if skipped_priv:
            self._log(self.i18n.t("msg_skip_private_select", count=skipped_priv))

    def _deselect_all(self) -> None:
        self._bulk_set_selection(lambda _i, _v: False)

    def _select_new_only(self) -> None:
        def pred(_i: int, item: VideoItem) -> bool:
            if not item.auto_selectable():
                return False
            return item.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)

        skipped_priv = sum(
            1
            for v in self._videos
            if (v.is_private or v.status == VideoStatus.PRIVATE)
            and v.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)
        )
        self._bulk_set_selection(pred)
        if skipped_priv:
            self._log(self.i18n.t("msg_skip_private_select", count=skipped_priv))

    def _item_target_dir(self, item: VideoItem | None = None) -> Path:
        """Download folder for one video (per-channel when multi-channel scan)."""
        roots = self._all_roots()
        primary = self.config_data.download_dir
        if item and self._scan_source == "channel" and item.channel:
            return resolve_channel_dir(
                item.channel, roots, primary=primary, create=True
            )
        return self._current_target_dir()

    def _current_target_dir(self) -> Path:
        # Multi-channel query "a,b,c" is not a real folder — fall back to root
        q = self._scan_query or ""
        roots = self._all_roots()
        primary = self.config_data.download_dir
        if self._scan_source == "channel":
            if "," in q or "，" in q:
                root = Path(primary)
                root.mkdir(parents=True, exist_ok=True)
                return root
            if q.strip():
                return resolve_channel_dir(
                    q.strip(), roots, primary=primary, create=True
                )
        return target_download_dir(
            primary,
            source=self._scan_source,
            query=self._scan_query,
        )

    def _page_count(self) -> int:
        n = len(self._videos)
        if n <= 0:
            return 1
        return max(1, (n + self._page_size - 1) // self._page_size)

    def _page_slice(self) -> tuple[int, int]:
        start = self._page * self._page_size
        end = min(start + self._page_size, len(self._videos))
        return start, end

    def _update_page_label(self) -> None:
        total = len(self._videos)
        pages = self._page_count()
        if total == 0:
            self.lbl_page.configure(
                text=self.i18n.t(
                    "page_nav", page=1, pages=1, start=0, end=0, total=0
                )
            )
            self.btn_page_prev.configure(state="disabled")
            self.btn_page_next.configure(state="disabled")
            return
        start, end = self._page_slice()
        self.lbl_page.configure(
            text=self.i18n.t(
                "page_nav",
                page=self._page + 1,
                pages=pages,
                start=start + 1,
                end=end,
                total=total,
            )
        )
        self.btn_page_prev.configure(
            state="normal" if self._page > 0 else "disabled"
        )
        self.btn_page_next.configure(
            state="normal" if self._page < pages - 1 else "disabled"
        )

    def _page_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._rebuild_page()

    def _page_next(self) -> None:
        if self._page < self._page_count() - 1:
            self._page += 1
            self._rebuild_page()

    def _destroy_page_widgets(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._row_vars.clear()
        self._row_checks.clear()
        self._row_bars.clear()
        self._row_pcts.clear()
        self._row_status.clear()
        self._row_retry.clear()
        self._row_thumbs.clear()
        self._thumb_row_index.clear()

    def _rebuild_page(self) -> None:
        """Render only current page of videos (absolute indices preserved)."""
        self._destroy_page_widgets()
        pages = self._page_count()
        if self._page >= pages:
            self._page = max(0, pages - 1)
        start, end = self._page_slice()
        for abs_i in range(start, end):
            self._create_row_widget(abs_i, self._videos[abs_i])
        self._update_page_label()
        self.lbl_found.configure(text=self.i18n.t("found_count", count=len(self._videos)))
        self._update_selected_label()
        # Load thumbs only for visible page
        if start < end:
            gen = self._list_generation
            batch = self._videos[start:end]
            self.after(20, lambda b=list(batch), g=gen: self._queue_thumbnails(b, g))

    def _clear_list(self) -> None:
        self._list_generation += 1
        self._page = 0
        self._videos.clear()
        self._destroy_page_widgets()
        self._thumb_images.clear()
        self.lbl_found.configure(text=self.i18n.t("found_count", count=0))
        self._update_page_label()
        self._update_selected_label()

    def _queue_thumbnails(self, items: list[VideoItem], generation: int) -> None:
        for item in items:
            if not item.thumbnail_url and not item.thumbnail_alts:
                continue

            def on_thumb(vid: str, pil_img: object | None, gen: int = generation) -> None:
                self._ui_queue.put(("thumb", {"id": vid, "img": pil_img, "gen": gen}))

            thumb_mod.fetch_async(
                item.id,
                item.thumbnail_url,
                on_thumb,
                alt_urls=list(item.thumbnail_alts or []),
            )

    def _apply_thumb(self, video_id: str, pil_img: object | None, generation: int) -> None:
        if generation != self._list_generation or pil_img is None:
            return
        try:
            ctk_img = ctk.CTkImage(
                light_image=pil_img,  # type: ignore[arg-type]
                dark_image=pil_img,  # type: ignore[arg-type]
                size=(thumb_mod.THUMB_W, thumb_mod.THUMB_H),
            )
        except Exception:
            return
        self._thumb_images[video_id] = ctk_img
        i = self._thumb_row_index.get(video_id)
        if i is not None and 0 <= i < len(self._row_thumbs):
            try:
                self._row_thumbs[i].configure(image=ctk_img, text="")
            except Exception:
                pass

    def _progress_color(self, item: VideoItem) -> str | None:
        if item.status in (VideoStatus.DONE, VideoStatus.ON_DISK):
            return _GREEN
        if item.status in (VideoStatus.ERROR, VideoStatus.STALLED):
            return _RED
        if item.status == VideoStatus.DOWNLOADING:
            return _BLUE
        if item.status == VideoStatus.QUEUED:
            return "#555555"
        return None

    def _create_row_widget(self, abs_i: int, item: VideoItem) -> None:
        """Create widgets for one video at absolute index (page-local grid row)."""
        local_row = len(self._row_vars)
        var = tk.BooleanVar(master=self, value=bool(item.selected))
        self._row_vars.append(var)
        cb = ctk.CTkCheckBox(
            self.list_frame,
            text="",
            variable=var,
            width=28,
            command=lambda idx=abs_i: self._on_check_toggle(idx),
        )
        cb.grid(row=local_row, column=0, padx=6, pady=6)
        self._row_checks.append(cb)
        thumb_lbl = ctk.CTkLabel(
            self.list_frame,
            text="",
            width=thumb_mod.THUMB_W,
            height=thumb_mod.THUMB_H,
            image=self._placeholder_img,
        )
        thumb_lbl.grid(row=local_row, column=1, padx=4, pady=4)
        self._row_thumbs.append(thumb_lbl)
        self._bind_double_open(thumb_lbl, abs_i)
        title = ctk.CTkLabel(
            self.list_frame, text=item.title, anchor="w", wraplength=320, justify="left"
        )
        title.grid(row=local_row, column=2, sticky="ew", padx=4, pady=4)
        self._bind_double_open(title, abs_i)
        author = ctk.CTkLabel(self.list_frame, text=item.author, anchor="w")
        author.grid(row=local_row, column=3, sticky="ew", padx=4, pady=4)
        self._bind_double_open(author, abs_i)
        size_lbl = ctk.CTkLabel(self.list_frame, text=item.size_label(), width=72, anchor="e")
        size_lbl.grid(row=local_row, column=4, padx=4, pady=4)
        self._bind_double_open(size_lbl, abs_i)
        prog_cell = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        prog_cell.grid(row=local_row, column=5, sticky="ew", padx=4, pady=4)
        prog_cell.grid_columnconfigure(0, weight=1)
        bar = ctk.CTkProgressBar(prog_cell, height=12)
        bar.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        bar.set(max(0.0, min(1.0, item.progress)))
        pct = ctk.CTkLabel(prog_cell, text=f"{int(item.progress * 100)}%", width=42)
        pct.grid(row=0, column=1)
        self._row_bars.append(bar)
        self._row_pcts.append(pct)
        status = ctk.CTkLabel(
            self.list_frame, text=self.i18n.t(item.status_key()), anchor="w"
        )
        status.grid(row=local_row, column=6, sticky="ew", padx=4, pady=4)
        self._row_status.append(status)
        self._bind_double_open(status, abs_i)
        if item.status in (VideoStatus.ON_DISK, VideoStatus.DONE):
            try:
                status.configure(text_color=_OK)
            except Exception:
                pass
        elif item.status == VideoStatus.PRIVATE or item.is_private:
            try:
                status.configure(text_color=_PAUSE)
            except Exception:
                pass
        retry = ctk.CTkButton(
            self.list_frame,
            text=self.i18n.t("btn_retry"),
            width=72,
            height=26,
            fg_color=_AMBER,
            command=lambda idx=abs_i: self._retry_one(idx),
            state="normal" if item.can_retry() and not self._busy else "disabled",
        )
        retry.grid(row=local_row, column=7, padx=4, pady=4)
        self._row_retry.append(retry)
        self._thumb_row_index[item.id] = local_row
        # Apply cached thumb if already loaded
        cached = self._thumb_images.get(item.id)
        if cached is not None:
            try:
                thumb_lbl.configure(image=cached, text="")
            except Exception:
                pass
        self._update_row_status(abs_i, item)

    def _append_scan_batch(self, batch: list[VideoItem], page: int) -> None:
        if not batch:
            return
        # Annotate per channel folder when multi-channel (each item has .channel)
        by_folder: dict[str, list[VideoItem]] = {}
        for item in batch:
            folder = str(self._item_target_dir(item))
            by_folder.setdefault(folder, []).append(item)
        for folder, group in by_folder.items():
            extra: list[Path] = []
            try:
                label = Path(folder).name
                for r in self._all_roots():
                    other = Path(r) / label
                    if other.is_dir() and other.resolve() != Path(folder).resolve():
                        extra.append(other)
            except OSError:
                extra = []
            annotate_local_status(
                group, Path(folder), self.history, extra_folders=extra or None
            )
        for item in batch:
            self._videos.append(item)
        # Stay on last page so new videos appear; only rebuild current page widgets
        last_page = self._page_count() - 1
        if self._page != last_page:
            self._page = last_page
        self._rebuild_page()
        priv_n = sum(1 for v in batch if v.is_private or v.status == VideoStatus.PRIVATE)
        extra = f", private {priv_n}" if priv_n else ""
        self._log(
            f"+ API page {page}: +{len(batch)} video{extra} (tổng {len(self._videos)})"
        )

    def _row_local_index(self, abs_index: int) -> int | None:
        """Map absolute video index → widget slot on current page, or None."""
        start, end = self._page_slice()
        if abs_index < start or abs_index >= end:
            return None
        return abs_index - start

    def _update_row_status(self, index: int, item: VideoItem) -> None:
        if index < 0 or index >= len(self._videos):
            return
        local = self._row_local_index(index)
        if local is None:
            return
        if local < len(self._row_bars):
            self._row_bars[local].set(max(0.0, min(1.0, item.progress)))
            color = self._progress_color(item)
            if color:
                self._row_bars[local].configure(progress_color=color)
        if local < len(self._row_pcts):
            self._row_pcts[local].configure(text=f"{int(item.progress * 100)}%")
        if local < len(self._row_status):
            text = self.i18n.t(item.status_key())
            if item.status == VideoStatus.DOWNLOADING:
                pct = int(item.progress * 100)
                srv = self._dl_server_by_id.get(item.id, "")
                if srv:
                    text = self.i18n.t(
                        "status_downloading_server", pct=pct, server=srv
                    )
                else:
                    text = f"{text} {pct}%"
            elif item.status == VideoStatus.ON_DISK:
                text = self.i18n.t("status_on_disk")
            elif item.status == VideoStatus.PRIVATE or item.is_private:
                text = self.i18n.t("status_private")
            elif item.status in (VideoStatus.ERROR, VideoStatus.STALLED) and item.error:
                text = diagnose_download_error(item.error).title
            color = None
            if item.status in (VideoStatus.ERROR, VideoStatus.STALLED):
                color = _ERR
            elif item.status in (VideoStatus.DONE, VideoStatus.ON_DISK):
                color = _OK
            elif item.status == VideoStatus.PRIVATE or item.is_private:
                color = _PAUSE
            try:
                if color:
                    self._row_status[local].configure(text=text, text_color=color)
                else:
                    self._row_status[local].configure(text=text)
            except Exception:
                self._row_status[local].configure(text=text)
        if local < len(self._row_retry):
            self._row_retry[local].configure(
                state="normal" if item.can_retry() and not self._busy else "disabled",
                text=self.i18n.t("btn_retry"),
            )

    # ------------------------------------------------------------------ library
    def _library_load_local(self) -> None:
        """Scan all download roots for channels with local files (no API)."""
        self._save_settings_from_ui()
        # Preserve remote stats for same usernames when reloading local counts
        prev = {c.username: c for c in self._library_channels}
        channels = list_local_channels_multi(self._all_roots())
        for ch in channels:
            old = prev.get(ch.username)
            if old and old.remote_count >= 0:
                ch.remote_count = old.remote_count
                ch.display_name = old.display_name or ch.display_name
                ch.error = old.error
                ch.missing_public = old.missing_public
                ch.missing_private = old.missing_private
        self._library_channels = channels
        self._library_render_rows()
        self._library_update_summary()
        self._update_roots_bar_summary()

    def _library_missing_label(self, ch: LocalChannelInfo) -> tuple[str, str]:
        """
        Text + color for «Còn thiếu» column.
        If every gap is private → annotate so user need not open the channel.
        """
        unk = self.i18n.t("library_remote_unknown")
        if ch.remote_count < 0 and ch.missing_public < 0:
            return unk, "gray70"
        # Prefer gap stats from full list scan
        if ch.missing_public >= 0 and ch.missing_private >= 0:
            mp, mpr = ch.missing_public, ch.missing_private
            if mp == 0 and mpr == 0:
                return self.i18n.t("library_missing_zero"), _OK
            if mp == 0 and mpr > 0:
                return (
                    self.i18n.t("library_missing_all_private", n=mpr),
                    _OK,
                )
            if mp > 0 and mpr > 0:
                return (
                    self.i18n.t("library_missing_mixed", n=mp, priv=mpr),
                    _PAUSE,
                )
            return self.i18n.t("library_missing_public", n=mp), _PAUSE
        # Fallback: crude remote − local
        n = max(0, int(ch.remote_count) - int(ch.local_count))
        if n == 0:
            return self.i18n.t("library_missing_zero"), _OK
        return self.i18n.t("library_missing_public", n=n), _PAUSE

    def _library_update_summary(self) -> None:
        ch = self._library_channels
        local = sum(c.local_count for c in ch)
        missing = 0
        any_remote = False
        for c in ch:
            if c.missing_public >= 0:
                any_remote = True
                missing += c.missing_public
            elif c.remote_count >= 0:
                any_remote = True
                missing += max(0, c.remote_count - c.local_count)
        if not ch:
            self.lbl_lib_summary.configure(text=self.i18n.t("library_empty"))
        else:
            miss_txt = str(missing) if any_remote else "?"
            drive_counts: dict[str, int] = {}
            for c in ch:
                parts = [
                    p.strip()
                    for p in (c.drive or path_drive(c.folder) or "?").split("+")
                    if p.strip()
                ] or ["?"]
                for part in parts:
                    drive_counts[part] = drive_counts.get(part, 0) + 1
            drive_parts = [
                self.i18n.t("library_drive_piece", drive=d, n=n)
                for d, n in sorted(drive_counts.items())
            ]
            self.lbl_lib_summary.configure(
                text=self.i18n.t(
                    "library_summary_drives",
                    drives=" · ".join(drive_parts),
                    channels=len(ch),
                    local=local,
                    missing=miss_txt,
                )
            )

    def _library_set_progress(
        self,
        *,
        current: int = 0,
        total: int = 0,
        name: str = "",
        done: bool = False,
        stopped: bool = False,
    ) -> None:
        try:
            if done:
                self.lbl_lib_progress.configure(
                    text=self.i18n.t("library_progress_done", count=current),
                    text_color=_OK,
                )
                self.lib_progress.set(1.0)
                self.lbl_lib_progress_pct.configure(text="100%")
                self.lib_progress.configure(progress_color=_GREEN)
            elif stopped:
                self.lbl_lib_progress.configure(
                    text=self.i18n.t(
                        "library_progress_stopped", current=current, total=total
                    ),
                    text_color=_PAUSE,
                )
                ratio = (current / total) if total else 0.0
                self.lib_progress.set(ratio)
                self.lbl_lib_progress_pct.configure(text=f"{int(ratio * 100)}%")
                self.lib_progress.configure(progress_color=_PAUSE)
            elif total > 0:
                short = name if len(name) <= 28 else name[:26] + "…"
                self.lbl_lib_progress.configure(
                    text=self.i18n.t(
                        "library_progress_checking",
                        current=current,
                        total=total,
                        name=short or "…",
                    ),
                    text_color="gray70",
                )
                # Show progress for the channel currently being checked
                ratio = max(0.0, min(1.0, (current - 0.15) / total)) if current else 0.0
                ratio = max(0.0, min(0.99, ratio))
                self.lib_progress.set(ratio)
                self.lbl_lib_progress_pct.configure(text=f"{int(round(ratio * 100))}%")
                self.lib_progress.configure(progress_color=_BLUE)
            else:
                self.lbl_lib_progress.configure(
                    text=self.i18n.t("library_progress_idle"),
                    text_color="gray70",
                )
                self.lib_progress.set(0)
                self.lbl_lib_progress_pct.configure(text="0%")
        except Exception:
            pass

    def _library_apply_row(self, ch: LocalChannelInfo) -> None:
        """Update one library row in place (avoid full rebuild = less lag)."""
        unk = self.i18n.t("library_remote_unknown")
        remote_txt = unk if ch.remote_count < 0 else str(ch.remote_count)
        new_txt, new_color = self._library_missing_label(ch)
        name = ch.display_name or ch.username
        if ch.error:
            name = f"{name} ⚠"

        for row in self._library_rows:
            if row.get("username") != ch.username:
                continue
            try:
                row["lbl_name"].configure(text=name)
                if "lbl_drive" in row:
                    row["lbl_drive"].configure(
                        text=ch.drive or path_drive(ch.folder) or "?"
                    )
                row["lbl_local"].configure(text=str(ch.local_count))
                row["lbl_remote"].configure(text=remote_txt)
                row["lbl_new"].configure(text=new_txt, text_color=new_color)
            except Exception:
                pass
            return
        # Row missing — full rebuild once
        self._library_render_rows()

    def _library_render_rows(self) -> None:
        for child in self.lib_list.winfo_children():
            child.destroy()
        self._library_rows.clear()
        unk = self.i18n.t("library_remote_unknown")
        busy_or_check = self._busy or self._library_checking
        for i, ch in enumerate(self._library_channels):
            remote_txt = unk if ch.remote_count < 0 else str(ch.remote_count)
            new_txt, new_color = self._library_missing_label(ch)
            name = ch.display_name or ch.username
            if ch.error:
                name = f"{name} ⚠"
            drive_lbl = ch.drive or path_drive(ch.folder) or "?"

            lbl_name = ctk.CTkLabel(
                self.lib_list, text=name, anchor="w", wraplength=280, justify="left"
            )
            lbl_name.grid(row=i, column=0, sticky="ew", padx=6, pady=4)
            lbl_drive = ctk.CTkLabel(self.lib_list, text=drive_lbl, width=56)
            lbl_drive.grid(row=i, column=1, padx=4, pady=4)
            lbl_local = ctk.CTkLabel(
                self.lib_list, text=str(ch.local_count), width=70
            )
            lbl_local.grid(row=i, column=2, padx=4, pady=4)
            lbl_remote = ctk.CTkLabel(self.lib_list, text=remote_txt, width=70)
            lbl_remote.grid(row=i, column=3, padx=4, pady=4)
            lbl_new = ctk.CTkLabel(
                self.lib_list,
                text=new_txt,
                width=150,
                anchor="w",
                text_color=new_color,
            )
            lbl_new.grid(row=i, column=4, padx=4, pady=4, sticky="w")

            act = ctk.CTkFrame(self.lib_list, fg_color="transparent")
            act.grid(row=i, column=5, padx=4, pady=4, sticky="e")
            btn_open = ctk.CTkButton(
                act,
                text=self.i18n.t("btn_library_open"),
                width=90,
                height=28,
                command=lambda u=ch.username: self._library_open_channel(u),
                fg_color=_BLUE,
            )
            btn_open.pack(side="left", padx=2)
            btn_upd = ctk.CTkButton(
                act,
                text=self.i18n.t("btn_library_update_one"),
                width=80,
                height=28,
                command=lambda u=ch.username: self._library_update_one(u),
                fg_color=_GREEN,
                hover_color="#185734",
            )
            btn_upd.pack(side="left", padx=2)
            if busy_or_check:
                btn_open.configure(state="disabled")
                btn_upd.configure(state="disabled")

            self._library_rows.append(
                {
                    "username": ch.username,
                    "lbl_name": lbl_name,
                    "lbl_drive": lbl_drive,
                    "lbl_local": lbl_local,
                    "lbl_remote": lbl_remote,
                    "lbl_new": lbl_new,
                    "btn_open": btn_open,
                    "btn_update": btn_upd,
                }
            )

    def _library_refresh(self, *, auto: bool = False) -> None:
        """
        Fetch remote video counts for each local channel.
        auto=True: silent start when opening Library tab (no empty popup spam).
        """
        if self._busy or self._library_checking:
            return
        self._save_settings_from_ui()
        self._library_load_local()
        if not self._library_channels:
            if not auto:
                messagebox.showinfo(
                    self.i18n.t("app_title"), self.i18n.t("library_empty")
                )
            self._library_set_progress(done=False)
            return

        # Delay between channel API calls to avoid rate-limit / UI lag
        try:
            page_delay = float(self.config_data.scan_delay_sec)
        except (TypeError, ValueError):
            page_delay = 1.0
        # Minimum pause so UI stays responsive and API isn't hammered
        check_delay = max(0.8, min(3.0, page_delay if page_delay > 0 else 1.0))

        self._stop.clear()
        self._pause_gate.set()
        self._library_checking = True
        self._set_busy(True)
        self._log(self.i18n.t("msg_library_refreshing"))
        channels = list(self._library_channels)
        total = len(channels)
        self._library_set_progress(current=0, total=total, name="")

        def worker() -> None:
            # Quiet logs during bulk check (only errors) to reduce UI churn
            api = IwaraAPI(
                demo_mode=self.config_data.demo_mode,
                scan_delay_sec=self.config_data.scan_delay_sec,
                stop_flag=self._stop.is_set,
                pause_wait=self._pause_wait,
                email=self.config_data.email,
                password=self.config_data.password,
                on_log=lambda _m: None,
            )
            updated: list[LocalChannelInfo] = []
            stopped = False
            for i, ch in enumerate(channels, start=1):
                if self._stop.is_set():
                    stopped = True
                    break
                self._pause_wait()
                if self._stop.is_set():
                    stopped = True
                    break

                self._ui_queue.put(
                    (
                        "library_progress",
                        {
                            "current": i,
                            "total": total,
                            "name": ch.display_name or ch.username,
                        },
                    )
                )

                try:
                    # Include files from every drive that holds this channel
                    local_ids = ch.local_video_ids()
                    # Full list walk: total + public/private gaps vs disk
                    gap = api.get_channel_gap_stats(ch.username, local_ids)
                    ch.remote_count = int(gap.get("total") or 0)
                    ch.display_name = str(
                        gap.get("display_name") or ch.username
                    )
                    ch.missing_public = int(gap.get("missing_public") or 0)
                    ch.missing_private = int(gap.get("missing_private") or 0)
                    ch.error = ""
                except Exception as exc:  # noqa: BLE001
                    ch.error = str(exc)
                    self._ui_queue.put(("log", f"⚠ {ch.username}: {exc}"))

                updated.append(ch)
                self._ui_queue.put(("library_row", ch))

                # Delay before next channel (except last) — keeps UI/API calm
                if i < total and not self._stop.is_set():
                    end_t = time.time() + check_delay
                    while time.time() < end_t:
                        self._pause_wait()
                        if self._stop.is_set():
                            stopped = True
                            break
                        time.sleep(0.08)
                    if stopped:
                        break

            self._ui_queue.put(
                (
                    "library_done",
                    {
                        "count": len(updated),
                        "channels": updated,
                        "stopped": stopped,
                        "total": total,
                    },
                )
            )
            self._ui_queue.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def _library_open_channel(self, username: str) -> None:
        """Scan full channel into main list; user selects new videos to download."""
        if self._busy:
            return
        self._save_settings_from_ui()
        try:
            self._channel_entry.delete(0, "end")
            self._channel_entry.insert(0, username)
            self.tabs.set(self.i18n.t("tab_channel"))
        except Exception:
            pass
        # Unlimited scan so user sees all new vs local
        try:
            self._channel_limit.delete(0, "end")
            self._channel_limit.insert(0, "0")
        except Exception:
            pass
        self._log(self.i18n.t("msg_library_open", name=username))
        # After scan finishes, auto-select new (handled in scan_done)
        self._library_auto_select_new = True
        self._start_scan("channel")

    def _library_update_one(self, username: str) -> None:
        if self._busy:
            return
        self._library_update_channels([username])

    def _library_update_all(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        if not self._library_channels:
            self._library_load_local()
        names = [c.username for c in self._library_channels]
        if not names:
            messagebox.showinfo(self.i18n.t("app_title"), self.i18n.t("msg_library_none"))
            return
        self._library_update_channels(names)

    def _library_update_channels(self, usernames: list[str]) -> None:
        """Scan each channel (unlimited) and download all videos not on disk."""
        if self._busy or not usernames:
            return
        self._save_settings_from_ui()
        self._stop.clear()
        self._pause_gate.set()
        self._set_busy(True)
        self._library_auto_select_new = False
        self._log(
            self.i18n.t("msg_library_update_all", count=len(usernames))
            if len(usernames) > 1
            else self.i18n.t("msg_library_update_one", name=usernames[0])
        )

        delay = self.config_data.download_delay_sec
        stall = self.config_data.stall_timeout_sec
        try:
            workers = max(1, min(16, int(self.config_data.download_batch)))
        except (TypeError, ValueError):
            workers = 1
        demo = self.config_data.demo_mode
        email = self.config_data.email
        password = self.config_data.password
        scan_delay = self.config_data.scan_delay_sec
        base_dir = self.config_data.download_dir
        roots = list(self._all_roots())

        def worker() -> None:
            total_ok = total_err = total_new = 0
            for uname in usernames:
                if self._stop.is_set():
                    break
                self._pause_wait()
                self._ui_queue.put(
                    ("log", self.i18n.t("msg_library_update_one", name=uname))
                )
                self._ui_queue.put(
                    (
                        "scan_meta",
                        {"source": "channel", "query": uname},
                    )
                )
                # Clear list when showing multi-channel update progress
                self._ui_queue.put(("library_clear_list", None))

                collected: list[VideoItem] = []

                def on_batch(page_items: list, page: int) -> None:
                    collected.extend(page_items)
                    self._ui_queue.put(
                        ("scan_batch", {"items": page_items, "page": page})
                    )

                api = IwaraAPI(
                    demo_mode=demo,
                    scan_delay_sec=scan_delay,
                    stop_flag=self._stop.is_set,
                    pause_wait=self._pause_wait,
                    email=email,
                    password=password,
                    on_log=lambda m: self._ui_queue.put(("log", m)),
                    on_batch=on_batch,
                    on_meta=lambda s, q: self._ui_queue.put(
                        ("scan_meta", {"source": s, "query": q})
                    ),
                )
                try:
                    result = api.search_by_channel(uname, limit=0)
                    items = result.items or collected
                    query = result.query or uname
                except Exception as exc:  # noqa: BLE001
                    self._ui_queue.put(("log", f"ERROR {uname}: {exc}"))
                    continue

                target = resolve_channel_dir(
                    query, roots, primary=base_dir, create=True
                )
                extra: list[Path] = []
                try:
                    label = target.name
                    for r in roots:
                        other = Path(r) / label
                        if other.is_dir() and other.resolve() != target.resolve():
                            extra.append(other)
                except OSError:
                    extra = []
                annotate_local_status(
                    items, target, self.history, extra_folders=extra or None
                )
                priv_skip = [
                    v
                    for v in items
                    if (v.is_private or v.status == VideoStatus.PRIVATE)
                    and v.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)
                ]
                new_items = [
                    v
                    for v in items
                    if v.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)
                    and not (v.is_private or v.status == VideoStatus.PRIVATE)
                    and v.auto_selectable()
                ]
                if priv_skip:
                    self._ui_queue.put(
                        (
                            "log",
                            self.i18n.t(
                                "msg_skip_private_download", count=len(priv_skip)
                            ),
                        )
                    )
                total_new += len(new_items)
                if not new_items:
                    self._ui_queue.put(
                        (
                            "log",
                            self.i18n.t("msg_library_no_new", name=uname),
                        )
                    )
                    continue

                self._ui_queue.put(
                    (
                        "log",
                        f"→ {uname}: {len(new_items)} video mới → tải…",
                    )
                )
                # Mark selected for UI (never auto-queue private)
                for v in new_items:
                    v.selected = True
                    v.status = VideoStatus.QUEUED

                # Download using same queue machinery via a mini inline pool
                from app.core.storage import index_local_files

                file_index = index_local_files(target)
                index_lock = threading.Lock()
                stats = {"ok": 0, "err": 0}
                counter_lock = threading.Lock()
                n_total = len(new_items)
                self._ui_queue.put(
                    (
                        "progress",
                        {
                            "ok": 0,
                            "err": 0,
                            "total": n_total,
                            "ratio": 0.0,
                            "speed": 0.0,
                            "title": uname,
                            "file_pct": 0.0,
                        },
                    )
                )

                def download_one(n: int, item: VideoItem) -> str:
                    if self._stop.is_set():
                        return "stop"

                    def on_progress(v: VideoItem) -> None:
                        # find row by id if present
                        for idx, existing in enumerate(self._videos):
                            if existing.id == v.id:
                                self._ui_queue.put(("row_update", (idx, v)))
                                break

                    def on_live(info: dict) -> None:
                        with counter_lock:
                            o, e = stats["ok"], stats["err"]
                        self._ui_queue.put(
                            (
                                "progress",
                                {
                                    "ok": o,
                                    "err": e,
                                    "total": n_total,
                                    "ratio": (o + e + float(info.get("file_pct") or 0))
                                    / max(1, n_total),
                                    "speed": float(info.get("speed") or 0),
                                    "title": str(info.get("title") or item.title),
                                    "file_pct": float(info.get("file_pct") or 0),
                                },
                            )
                        )

                    dl = Downloader(
                        target,
                        demo_mode=demo,
                        history=self.history,
                        stop_flag=self._stop.is_set,
                        pause_wait=self._pause_wait,
                        on_progress=on_progress,
                        on_live=on_live,
                        email=email,
                        password=password,
                        stall_timeout_sec=stall,
                        force=False,
                        channel=query,
                    )
                    with index_lock:
                        dl._file_index = file_index  # type: ignore[attr-defined]
                    try:
                        result_item = dl.download(item)
                    except Exception as exc:  # noqa: BLE001
                        help_ = diagnose_download_error(exc)
                        item.status = VideoStatus.ERROR
                        item.error = help_.full_message()
                        result_item = item
                    kind = "err"
                    if result_item.status in (VideoStatus.DONE, VideoStatus.ON_DISK):
                        kind = "ok"
                    elif result_item.status in (
                        VideoStatus.ERROR,
                        VideoStatus.STALLED,
                    ):
                        kind = "err"
                    else:
                        return "stop"
                    with counter_lock:
                        if kind == "ok":
                            stats["ok"] += 1
                        else:
                            stats["err"] += 1
                        o, e = stats["ok"], stats["err"]
                    self._ui_queue.put(
                        (
                            "dl_end",
                            {
                                "n": n,
                                "total": n_total,
                                "ok": o,
                                "err": e,
                                "title": item.title,
                                "kind": kind if kind == "ok" else "err",
                                "error": item.error,
                                "err_title": diagnose_download_error(
                                    item.error or ""
                                ).title
                                if kind != "ok"
                                else "",
                                "err_detail": diagnose_download_error(
                                    item.error or ""
                                ).detail
                                if kind != "ok"
                                else "",
                                "err_advice": diagnose_download_error(
                                    item.error or ""
                                ).advice
                                if kind != "ok"
                                else "",
                            },
                        )
                    )
                    return kind

                with ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="lib-dl"
                ) as pool:
                    futs = []
                    for n, item in enumerate(new_items, start=1):
                        self._pause_wait()
                        if self._stop.is_set():
                            break
                        futs.append(pool.submit(download_one, n, item))
                        if n < n_total and delay > 0 and not self._stop.is_set():
                            end_t = time.time() + delay
                            while time.time() < end_t:
                                self._pause_wait()
                                if self._stop.is_set():
                                    break
                                time.sleep(0.1)
                    for fut in as_completed(futs):
                        try:
                            fut.result()
                        except Exception as exc:  # noqa: BLE001
                            console_error("library download worker", exc)

                total_ok += stats["ok"]
                total_err += stats["err"]

            self._ui_queue.put(
                (
                    "download_done",
                    {
                        "ok": total_ok,
                        "err": total_err,
                        "current": total_ok + total_err,
                        "total": total_ok + total_err,
                    },
                )
            )
            self._ui_queue.put(
                (
                    "log",
                    self.i18n.t(
                        "msg_download_done", ok=total_ok, err=total_err
                    )
                    + f" (new≈{total_new})",
                )
            )
            # Refresh library local counts
            self._ui_queue.put(("library_reload", None))
            self._ui_queue.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ workers
    def _start_scan(self, mode: str) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        query = getattr(self, f"_{mode}_entry").get().strip()
        if not query:
            key = "msg_enter_hashtag" if mode == "hashtag" else "msg_enter_channel"
            messagebox.showwarning(self.i18n.t("app_title"), self.i18n.t(key))
            return
        limit = self._parse_scan_limit(mode)
        self._stop.clear()
        self._pause_gate.set()
        self._set_busy(True)
        self._clear_list()
        self._scan_source = mode
        if mode == "channel":
            # Keep full multi-channel string; per-item .channel drives folders
            names = IwaraAPI.split_channel_names(query)
            self._scan_query = ",".join(names) if names else query.strip().lstrip("@")
        else:
            self._scan_query = query.strip().lstrip("#@")
        self.progress.set(0)
        self.lbl_prog_ok.configure(text=self.i18n.t("progress_scan"), text_color="gray70")
        self.lbl_prog_err.configure(text="")
        self.lbl_progress.configure(text="")
        self.lbl_progress_pct.configure(text="…")
        limit_txt = self.i18n.t("limit_unlimited") if limit <= 0 else str(limit)
        self._log(f"→ {mode}: {query} (scan_limit={limit_txt})")
        if mode == "channel":
            names = IwaraAPI.split_channel_names(query)
            if len(names) > 1:
                self._log(
                    self.i18n.t(
                        "msg_multi_channel",
                        count=len(names),
                        names=", ".join(names),
                    )
                )

        def worker() -> None:
            def on_log(msg: str) -> None:
                self._ui_queue.put(("log", msg))

            def on_batch(page_items: list, page: int) -> None:
                self._ui_queue.put(("scan_batch", {"items": page_items, "page": page}))

            def on_meta(source: str, resolved_query: str) -> None:
                # For multi-channel keep joined query on scan_done; meta still
                # updates so single-channel annotate uses the right folder.
                self._ui_queue.put(("scan_meta", {"source": source, "query": resolved_query}))

            api = IwaraAPI(
                demo_mode=self.config_data.demo_mode,
                scan_delay_sec=self.config_data.scan_delay_sec,
                stop_flag=self._stop.is_set,
                pause_wait=self._pause_wait,
                email=self.config_data.email,
                password=self.config_data.password,
                on_log=on_log,
                on_batch=on_batch,
                on_meta=on_meta,
            )
            try:
                if mode == "hashtag":
                    result = api.search_by_hashtag(query, limit=limit)
                else:
                    result = api.search_by_channel(query, limit=limit)
                self._ui_queue.put(
                    (
                        "scan_done",
                        {
                            "count": len(result.items),
                            "source": result.source,
                            "query": result.query,
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._ui_queue.put(("error", str(exc)))
            finally:
                self._ui_queue.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def _collect_selected(
        self, *, skip_on_disk: bool = True
    ) -> tuple[list[tuple[int, VideoItem]], int, int]:
        selected: list[tuple[int, VideoItem]] = []
        checked = skipped = 0
        for i, item in enumerate(self._videos):
            if not item.selected:
                continue
            checked += 1
            if skip_on_disk and item.status in (VideoStatus.ON_DISK, VideoStatus.DONE):
                skipped += 1
                continue
            selected.append((i, item))
        if skipped:
            self._log(self.i18n.t("msg_skip_on_disk", count=skipped))
        return selected, checked, skipped

    def _start_download(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        selected, checked, skipped = self._collect_selected(skip_on_disk=True)
        if not selected:
            if checked == 0:
                msg = self.i18n.t("msg_no_selection")
            elif skipped > 0 and skipped == checked:
                msg = self.i18n.t("msg_all_on_disk")
            else:
                msg = self.i18n.t("msg_no_selection")
            messagebox.showinfo(self.i18n.t("app_title"), msg)
            return
        self._run_download_queue(selected, force=False)

    def _retry_one(self, index: int) -> None:
        if self._busy or index < 0 or index >= len(self._videos):
            return
        item = self._videos[index]
        if not item.can_retry():
            return
        self._save_settings_from_ui()
        self._log(self.i18n.t("msg_retry_one", title=item.title[:60]))
        self.history.remove(item.id)
        item.progress = 0.0
        item.error = ""
        item.status = VideoStatus.QUEUED
        self._update_row_status(index, item)
        self._run_download_queue([(index, item)], force=True)

    def _retry_failed(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        jobs: list[tuple[int, VideoItem]] = []
        for i, item in enumerate(self._videos):
            if item.can_retry():
                self.history.remove(item.id)
                item.progress = 0.0
                item.error = ""
                item.status = VideoStatus.QUEUED
                self._update_row_status(i, item)
                jobs.append((i, item))
        if not jobs:
            messagebox.showinfo(self.i18n.t("app_title"), self.i18n.t("msg_no_retryable"))
            return
        self._log(self.i18n.t("msg_retry_batch", count=len(jobs)))
        self._run_download_queue(jobs, force=True)

    def _run_download_queue(
        self, selected: list[tuple[int, VideoItem]], *, force: bool
    ) -> None:
        delay = self.config_data.download_delay_sec
        stall = self.config_data.stall_timeout_sec
        try:
            workers = max(1, min(16, int(self.config_data.download_batch)))
        except (TypeError, ValueError):
            workers = 1
        base_dir = self.config_data.download_dir
        roots = list(self._all_roots())
        scan_source = self._scan_source
        scan_query = self._scan_query
        group = scan_query if scan_source in ("channel", "hashtag") else ""
        from app.core.storage import index_local_files

        # Pre-index each target folder (multi-channel → many folders)
        folder_indexes: dict[str, dict] = {}
        index_lock = threading.Lock()

        def folder_for(item: VideoItem) -> Path:
            if scan_source == "channel" and item.channel:
                return resolve_channel_dir(
                    item.channel, roots, primary=base_dir, create=True
                )
            if scan_source == "channel" and scan_query and "," not in scan_query:
                return resolve_channel_dir(
                    scan_query, roots, primary=base_dir, create=True
                )
            if scan_source == "hashtag":
                return target_download_dir(
                    base_dir, source="hashtag", query=scan_query
                )
            # Fallback
            return Path(base_dir)

        for _idx, it in selected:
            fp = str(folder_for(it))
            if fp not in folder_indexes:
                folder_indexes[fp] = index_local_files(Path(fp))

        self._stop.clear()
        self._pause_gate.set()
        self._set_busy(True)
        total = len(selected)
        self.progress.set(0)
        self._set_overall_progress(ok=0, err=0, total=total, ratio=0.0, title="", done=False)
        self._log(
            self.i18n.t(
                "msg_download_plan",
                selected=total,
                batch=workers,
                delay=delay,
            )
        )
        folders_preview = sorted({str(folder_for(it)) for _i, it in selected})
        if len(folders_preview) == 1:
            self._log(f"→ {folders_preview[0]} (stall={int(stall)}s, threads={workers})")
        else:
            self._log(
                f"→ {len(folders_preview)} thư mục kênh (stall={int(stall)}s, threads={workers})"
            )

        def worker() -> None:
            # Shared counters — updated immediately when EACH file finishes
            # (not delayed until as_completed drains the whole pool).
            stats = {"ok": 0, "err": 0}
            counter_lock = threading.Lock()
            last_log_t = 0.0
            log_lock = threading.Lock()

            for idx, item in selected:
                # Keep PRIVATE label until download actually starts if user ticked it
                item.status = VideoStatus.QUEUED
                item.error = ""
                self._ui_queue.put(("row_update", (idx, item)))

            def snapshot() -> tuple[int, int, float]:
                with counter_lock:
                    o, e = stats["ok"], stats["err"]
                ratio = (o + e) / total if total else 1.0
                return o, e, ratio

            def push_progress(
                *,
                title: str = "",
                speed: float = 0.0,
                file_pct: float = 0.0,
                force_ratio: float | None = None,
            ) -> None:
                o, e, ratio = snapshot()
                if force_ratio is not None:
                    ratio = force_ratio
                elif title and total > 0:
                    ratio = min(1.0, (o + e + max(0.0, min(1.0, file_pct))) / total)
                self._ui_queue.put(
                    (
                        "progress",
                        {
                            "ok": o,
                            "err": e,
                            "total": total,
                            "ratio": ratio,
                            "speed": speed,
                            "title": title,
                            "file_pct": file_pct,
                        },
                    )
                )

            def finish_one(kind: str, n: int, item: VideoItem) -> None:
                """Bump ok/err + notify UI immediately (realtime bar)."""
                with counter_lock:
                    if kind in ("ok", "skip"):
                        stats["ok"] += 1
                    elif kind == "err":
                        stats["err"] += 1
                    cur_ok, cur_err = stats["ok"], stats["err"]
                    ratio = (cur_ok + cur_err) / total if total else 1.0

                if kind in ("ok", "skip"):
                    self._ui_queue.put(
                        (
                            "dl_end",
                            {
                                "n": n,
                                "total": total,
                                "ok": cur_ok,
                                "err": cur_err,
                                "title": item.title,
                                "kind": kind,
                            },
                        )
                    )
                elif kind == "err":
                    help_ = diagnose_download_error(item.error or item.status.value)
                    self._ui_queue.put(
                        (
                            "dl_end",
                            {
                                "n": n,
                                "total": total,
                                "ok": cur_ok,
                                "err": cur_err,
                                "title": item.title,
                                "kind": "err",
                                "error": help_.full_message(),
                                "err_title": help_.title,
                                "err_detail": help_.detail,
                                "err_advice": help_.advice,
                            },
                        )
                    )
                # Always refresh overall bar right after counters change
                self._ui_queue.put(
                    (
                        "progress",
                        {
                            "ok": cur_ok,
                            "err": cur_err,
                            "total": total,
                            "ratio": ratio,
                            "speed": 0.0,
                            "title": "",
                            "file_pct": 0.0,
                        },
                    )
                )

            def download_one(n: int, idx: int, item: VideoItem) -> str:
                """Download one file; returns kind ok|skip|err|stop."""
                nonlocal last_log_t
                self._pause_wait()
                if self._stop.is_set():
                    return "stop"

                o, e, _ = snapshot()
                self._ui_queue.put(
                    (
                        "dl_start",
                        {
                            "n": n,
                            "total": total,
                            "title": item.title,
                            "ok": o,
                            "err": e,
                        },
                    )
                )

                def on_progress(v: VideoItem, row: int = idx) -> None:
                    self._ui_queue.put(("row_update", (row, v)))

                def on_live(info: dict, n_file: int = n) -> None:
                    nonlocal last_log_t
                    now = time.time()
                    with log_lock:
                        # Log sparsely — text widget inserts freeze UI if too frequent
                        log_it = (now - last_log_t) >= 2.5 or info.get("status") in (
                            "starting",
                            "done",
                            "error",
                            "finishing",
                        )
                        if log_it:
                            last_log_t = now
                    o2, e2, _ = snapshot()
                    file_pct = float(info.get("file_pct") or 0)
                    speed = float(info.get("speed") or 0)
                    title = str(info.get("title") or item.title)
                    server = str(info.get("server") or "")
                    # Live bar always carries latest ok/err (not stale capture)
                    self._ui_queue.put(
                        (
                            "dl_live",
                            {
                                "n": n_file,
                                "total": total,
                                "ok": o2,
                                "err": e2,
                                "title": title,
                                "id": item.id,
                                "status": info.get("status") or "downloading",
                                "speed": speed,
                                "file_pct": file_pct,
                                "downloaded": int(info.get("downloaded") or 0),
                                "total_bytes": int(info.get("total_bytes") or 0),
                                "server": server,
                                "message": str(info.get("message") or ""),
                                "log": log_it,
                            },
                        )
                    )

                target = folder_for(item)
                ch_key = item.channel or (
                    group if scan_source == "channel" and "," not in (group or "") else ""
                )
                with index_lock:
                    idx_map = folder_indexes.setdefault(
                        str(target), index_local_files(target)
                    )
                downloader = Downloader(
                    target,
                    demo_mode=self.config_data.demo_mode,
                    history=self.history,
                    stop_flag=self._stop.is_set,
                    pause_wait=self._pause_wait,
                    on_progress=on_progress,
                    on_live=on_live,
                    email=self.config_data.email,
                    password=self.config_data.password,
                    stall_timeout_sec=stall,
                    force=force,
                    channel=ch_key or group,
                )
                with index_lock:
                    downloader._file_index = idx_map  # type: ignore[attr-defined]
                try:
                    result = downloader.download(item)
                    # Keep file index in sync for same-channel later jobs
                    if result.local_path:
                        with index_lock:
                            idx_map[item.id] = Path(result.local_path)
                except Exception as exc:  # noqa: BLE001
                    help_ = diagnose_download_error(exc)
                    item.status = VideoStatus.ERROR
                    item.error = help_.full_message()
                    result = item
                    self._ui_queue.put(("row_update", (idx, item)))

                if result.status in (VideoStatus.DONE, VideoStatus.ON_DISK):
                    kind = "ok" if result.status == VideoStatus.DONE else "skip"
                elif result.status in (VideoStatus.ERROR, VideoStatus.STALLED):
                    kind = "err"
                else:
                    kind = "stop"

                if kind != "stop":
                    finish_one(kind, n, item)
                return kind

            futures = []
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="dl"
            ) as pool:
                for n, (idx, item) in enumerate(selected, start=1):
                    self._pause_wait()
                    if self._stop.is_set():
                        break
                    futures.append(pool.submit(download_one, n, idx, item))
                    if n < total and delay > 0 and not self._stop.is_set():
                        self._ui_queue.put(("delay", delay))
                        end_t = time.time() + delay
                        while time.time() < end_t:
                            self._pause_wait()
                            if self._stop.is_set():
                                break
                            time.sleep(0.1)

                # Wait for stragglers; counters already updated inside download_one
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001
                        help_ = diagnose_download_error(exc)
                        console_error("download worker crashed", exc)
                        # Best-effort: count as error if something blew up outside finish_one
                        dummy = selected[0][1] if selected else VideoItem(id="?", title="?")
                        dummy.status = VideoStatus.ERROR
                        dummy.error = help_.full_message()
                        finish_one("err", 0, dummy)

            o, e, _ = snapshot()
            self._ui_queue.put(
                ("download_done", {"ok": o, "err": e, "current": o + e, "total": total})
            )
            self._ui_queue.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_login_click(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        email = self.entry_email.get().strip()
        password = self.entry_password.get()
        if not email or not password:
            self.lbl_login_status.configure(text=self.i18n.t("login_missing"), text_color=_ERR)
            self._log(self.i18n.t("login_missing"))
            return
        self.btn_login.configure(state="disabled")
        self.lbl_login_status.configure(text=self.i18n.t("login_checking"), text_color=_PAUSE)
        self._log(self.i18n.t("login_checking"))

        def worker() -> None:
            api = IwaraAPI(demo_mode=False, email=email, password=password)
            try:
                ok, detail = api.test_login()
                self._ui_queue.put(("login_result", {"ok": ok, "detail": detail}))
            except Exception as exc:  # noqa: BLE001
                self._ui_queue.put(("login_result", {"ok": False, "detail": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _set_overall_progress(
        self,
        *,
        ok: int,
        err: int,
        total: int,
        ratio: float,
        speed: float = 0.0,
        title: str = "",
        done: bool = False,
        file_pct: float = 0.0,
        server: str = "",
    ) -> None:
        total = max(0, int(total))
        ok = max(0, int(ok))
        err = max(0, int(err))
        finished = ok + err
        if not done and title and total > 0:
            ratio = min(1.0, (finished + max(0.0, min(1.0, file_pct))) / total)
        else:
            ratio = max(0.0, min(1.0, float(ratio)))
        percent = int(round(ratio * 100))
        speed_txt = format_speed(speed) if speed and speed > 0 else "—"
        ok_text = self.i18n.t("progress_ok_part", ok=ok, total=total)
        err_text = self.i18n.t("progress_err_part", err=err)
        if done:
            extra = self.i18n.t("progress_done_extra")
        elif title:
            short = title if len(title) <= 28 else title[:26] + "…"
            if server:
                extra = self.i18n.t(
                    "progress_live_server",
                    speed=speed_txt,
                    server=server,
                    title=short,
                )
            else:
                extra = self.i18n.t(
                    "progress_live_extra", speed=speed_txt, title=short
                )
        else:
            extra = self.i18n.t("progress_speed_extra", speed=speed_txt)
        try:
            self.lbl_prog_ok.configure(text=ok_text, text_color=_OK)
            # Always refresh error counter (realtime) — red when any failures
            self.lbl_prog_err.configure(
                text=err_text,
                text_color=_ERR if err > 0 else "gray70",
            )
            self.lbl_progress.configure(text=extra, text_color="gray70")
            self.lbl_progress_pct.configure(text=f"{percent}%")
            self.progress.set(ratio)
            if done:
                if err == 0 and ok > 0:
                    self.progress.configure(progress_color=_GREEN)
                elif ok == 0 and err > 0:
                    self.progress.configure(progress_color=_RED)
                elif err > 0:
                    self.progress.configure(progress_color=_PAUSE)
            elif err > 0:
                # Mid-run: hint that some files already failed
                self.progress.configure(progress_color=_BLUE)
            # Do NOT call update_idletasks() here — it freezes the UI under load
        except Exception:
            pass

    def _poll_queue(self) -> None:
        """Drain UI queue with coalescing so progress floods don't freeze Tk."""
        # kind → last payload (progress/live/row coalesce)
        latest_progress: Any = None
        latest_live: dict[Any, Any] = {}  # n or id → payload
        latest_row: dict[int, Any] = {}
        ordered: list[tuple[str, Any]] = []
        drained = 0
        max_drain = 120
        try:
            while drained < max_drain:
                kind, payload = self._ui_queue.get_nowait()
                drained += 1
                if kind == "progress":
                    latest_progress = payload
                elif kind == "dl_live":
                    key = payload.get("n") if isinstance(payload, dict) else id(payload)
                    latest_live[key] = payload
                elif kind == "row_update":
                    try:
                        idx = int(payload[0])
                    except Exception:
                        ordered.append((kind, payload))
                        continue
                    latest_row[idx] = payload
                else:
                    ordered.append((kind, payload))
        except queue.Empty:
            pass

        for kind, payload in ordered:
            try:
                self._handle(kind, payload)
            except Exception as exc:  # noqa: BLE001
                console_error(f"UI handle {kind}", exc)

        for payload in latest_row.values():
            try:
                self._handle("row_update", payload)
            except Exception as exc:  # noqa: BLE001
                console_error("UI row_update", exc)

        for payload in latest_live.values():
            try:
                self._handle("dl_live", payload)
            except Exception as exc:  # noqa: BLE001
                console_error("UI dl_live", exc)

        if latest_progress is not None:
            try:
                self._handle("progress", latest_progress)
            except Exception as exc:  # noqa: BLE001
                console_error("UI progress", exc)

        # Faster poll when busy (more responsive stop/pause), calmer when idle
        self.after(50 if self._busy else 100, self._poll_queue)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "scan_meta":
            if payload.get("source"):
                self._scan_source = str(payload["source"])
            if payload.get("query"):
                q = str(payload["query"]).strip().lstrip("#@")
                # Multi-channel: do not replace joined "a,b,c" with a single name
                # mid-scan (each VideoItem.channel already carries the folder key).
                if self._scan_source == "channel" and (
                    "," in (self._scan_query or "") or "，" in (self._scan_query or "")
                ):
                    pass
                else:
                    self._scan_query = q
        elif kind == "scan_batch":
            self._append_scan_batch(payload.get("items") or [], int(payload.get("page") or 0))
        elif kind == "scan_done":
            count = int(payload.get("count") or len(self._videos)) if isinstance(payload, dict) else len(self._videos)
            if isinstance(payload, dict):
                if payload.get("source"):
                    self._scan_source = str(payload["source"])
                if payload.get("query"):
                    self._scan_query = str(payload["query"])
            folder = self._current_target_dir()
            already = sum(1 for v in self._videos if v.status == VideoStatus.ON_DISK)
            priv = sum(
                1
                for v in self._videos
                if (v.is_private or v.status == VideoStatus.PRIVATE)
                and v.status != VideoStatus.ON_DISK
            )
            new = sum(
                1
                for v in self._videos
                if v.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)
                and not (v.is_private or v.status == VideoStatus.PRIVATE)
            )
            self._log(self.i18n.t("msg_scan_done", count=count))
            self._log(
                self.i18n.t(
                    "msg_scan_local",
                    folder=str(folder),
                    already=already,
                    new=new,
                )
            )
            if priv:
                self._log(self.i18n.t("msg_skip_private_download", count=priv))
            self.progress.set(1)
            self.lbl_progress_pct.configure(text="100%")
            if getattr(self, "_library_auto_select_new", False):
                self._library_auto_select_new = False
                self._select_new_only()
                self._update_select_new_visibility()
            self._update_selected_label()
        elif kind == "library_progress":
            self._library_set_progress(
                current=int(payload.get("current") or 0),
                total=int(payload.get("total") or 0),
                name=str(payload.get("name") or ""),
            )
        elif kind == "library_row":
            # Live update one channel stats during refresh (no full rebuild)
            ch: LocalChannelInfo = payload
            for i, existing in enumerate(self._library_channels):
                if existing.username == ch.username:
                    self._library_channels[i] = ch
                    break
            else:
                self._library_channels.append(ch)
            self._library_apply_row(ch)
            self._library_update_summary()
        elif kind == "library_done":
            self._library_checking = False
            channels = payload.get("channels") if isinstance(payload, dict) else None
            if channels is not None:
                self._library_channels = list(channels)
            stopped = bool(payload.get("stopped")) if isinstance(payload, dict) else False
            n = (
                int(payload.get("count") or len(self._library_channels))
                if isinstance(payload, dict)
                else len(self._library_channels)
            )
            total = (
                int(payload.get("total") or n)
                if isinstance(payload, dict)
                else n
            )
            self._library_render_rows()
            self._library_update_summary()
            if stopped:
                self._library_set_progress(
                    current=n, total=total, stopped=True
                )
                self._log(
                    self.i18n.t(
                        "library_progress_stopped", current=n, total=total
                    )
                )
            else:
                self._library_remote_checked = True
                self._library_set_progress(current=n, done=True)
                self._log(self.i18n.t("msg_library_refreshed", count=n))
        elif kind == "library_reload":
            self._library_load_local()
        elif kind == "library_clear_list":
            self._clear_list()
        elif kind == "log":
            self._log(str(payload))
        elif kind == "thumb":
            self._apply_thumb(payload["id"], payload.get("img"), payload.get("gen", 0))
        elif kind == "row_update":
            idx, item = payload
            if 0 <= idx < len(self._videos):
                self._videos[idx] = item
                self._update_row_status(idx, item)
        elif kind == "dl_start":
            self._log(
                self.i18n.t(
                    "log_dl_start",
                    n=payload.get("n"),
                    total=payload.get("total"),
                    title=payload.get("title") or "",
                )
            )
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            # Keep cumulative ok/err; do not reset bar to 0% when a new parallel job starts
            finished = ok + err
            ratio = (finished / total) if total else 0.0
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=ratio,
                title=str(payload.get("title") or ""),
            )
        elif kind == "dl_live":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            file_pct = float(payload.get("file_pct") or 0)
            server = str(payload.get("server") or "")
            vid = str(payload.get("id") or "")
            if vid and server:
                self._dl_server_by_id[vid] = server
            # Update matching row % + server without full log spam
            if vid:
                for i, v in enumerate(self._videos):
                    if v.id == vid:
                        v.progress = max(v.progress, file_pct)
                        if v.status == VideoStatus.DOWNLOADING or payload.get(
                            "status"
                        ) == "downloading":
                            v.status = VideoStatus.DOWNLOADING
                        self._update_row_status(i, v)
                        break
            finished = ok + err
            ratio = min(1.0, (finished + file_pct) / total) if total else file_pct
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=ratio,
                speed=float(payload.get("speed") or 0),
                title=str(payload.get("title") or ""),
                file_pct=file_pct,
                server=server,
            )
            if payload.get("log"):
                done_b = int(payload.get("downloaded") or 0)
                tot_b = int(payload.get("total_bytes") or 0)
                size_txt = (
                    f"{format_bytes(done_b)}/{format_bytes(tot_b)}"
                    if tot_b > 0
                    else (format_bytes(done_b) if done_b else "—")
                )
                self._log(
                    self.i18n.t(
                        "log_dl_live",
                        n=payload.get("n"),
                        total=total,
                        title=str(payload.get("title") or "")[:50],
                        status=payload.get("status") or "downloading",
                        file_pct=int(round(file_pct * 100)),
                        speed=format_speed(float(payload.get("speed") or 0)),
                        size=size_txt,
                        server=server or "—",
                    )
                )
        elif kind == "dl_end":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            n = int(payload.get("n") or 0)
            title = str(payload.get("title") or "")
            kind_end = payload.get("kind")
            # Drop server label when file finishes
            for i, v in enumerate(self._videos):
                if v.title == title or (
                    title and v.title.startswith(title[:40])
                ):
                    self._dl_server_by_id.pop(v.id, None)
                    break
            if kind_end == "ok":
                self._log(self.i18n.t("log_dl_ok", n=n, total=total, title=title[:60], speed="—"))
            elif kind_end == "skip":
                self._log(self.i18n.t("log_dl_skip", n=n, total=total, title=title[:60]))
            else:
                self._log(
                    self.i18n.t(
                        "log_dl_err",
                        n=n,
                        total=total,
                        title=f"[{payload.get('err_title') or 'Lỗi'}] {title[:50]}",
                        detail=str(payload.get("err_detail") or payload.get("error") or "")[:300],
                        advice=str(payload.get("err_advice") or "—")[:300],
                    )
                )
            ratio = (ok + err) / total if total else 1.0
            self._set_overall_progress(ok=ok, err=err, total=total, ratio=ratio, done=False)
        elif kind == "progress":
            self._set_overall_progress(
                ok=int(payload.get("ok") or 0),
                err=int(payload.get("err") or 0),
                total=int(payload.get("total") or 0),
                ratio=float(payload.get("ratio") or 0),
                speed=float(payload.get("speed") or 0),
                title=str(payload.get("title") or ""),
                file_pct=float(payload.get("file_pct") or 0),
            )
        elif kind == "delay":
            self._log(self.i18n.t("msg_waiting_delay", sec=payload))
        elif kind == "download_done":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or (ok + err))
            self._set_overall_progress(ok=ok, err=err, total=total, ratio=1.0, done=True)
            self._log(self.i18n.t("msg_download_done", ok=ok, err=err))
            for i, item in enumerate(self._videos):
                self._update_row_status(i, item)
        elif kind == "login_result":
            self.btn_login.configure(state="normal")
            ok = bool(payload.get("ok"))
            detail = str(payload.get("detail") or "")
            if ok:
                text = self.i18n.t("login_ok", name=detail)
                self.lbl_login_status.configure(text=text, text_color=_OK)
                self._log(text)
                messagebox.showinfo(self.i18n.t("app_title"), text)
            else:
                if detail == "missing_credentials":
                    text = self.i18n.t("login_missing")
                else:
                    err = detail.replace("Đăng nhập thất bại: ", "")
                    # Keep full network advice (522 etc.) in dialog; shorten status line only
                    text = self.i18n.t("login_fail", error=err)
                short = text if len(text) <= 220 else text[:220] + "…"
                self.lbl_login_status.configure(text=short, text_color=_ERR)
                self._log(text)
                messagebox.showerror(self.i18n.t("app_title"), text)
        elif kind == "error":
            self._log(f"ERROR: {payload}")
            console_error(str(payload))
            messagebox.showerror(self.i18n.t("app_title"), str(payload))
        elif kind == "idle":
            self._library_checking = False
            self._set_busy(False)
            if self._stop.is_set():
                self.lbl_prog_ok.configure(text=self.i18n.t("progress_idle"), text_color="gray70")
                self.lbl_prog_err.configure(text="")
                self.lbl_progress.configure(text="")


def run_app() -> None:
    console_log("Starting CustomTkinter UI…")
    app = IwaraDownloaderApp()
    app.mainloop()
