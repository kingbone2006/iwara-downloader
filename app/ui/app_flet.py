"""Flet (Flutter/GPU) main window — VI / EN / ZH.

Renders with Flutter's GPU compositor for smooth scrolling and button animations.
Business logic stays in app.core.*; this module is UI only.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import flet as ft

from app.core.api import IwaraAPI
from app.core.cache_util import cache_stats, clear_cache
from app.core.config import AppConfig
from app.core.database import HistoryDB
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
    index_local_files,
    list_local_channels_multi,
    path_drive,
    resolve_channel_dir,
    summarize_download_roots,
    target_download_dir,
)
from app.core.debug_log import console_error, console_log
from app.core import thumbnails as thumb_mod
from app.i18n import I18n, LANG_LABELS, SUPPORTED_LANGS


# --------------------------------------------------------------------------- theme
_GREEN = "#2d9f5c"
_GREEN_HOVER = "#248a4e"
_GREEN_DISABLED = "#3d6b50"
_RED = "#8b2e2e"
_AMBER = "#8a6d1d"
_BLUE = "#1f538d"
_OK = "#3cb371"
_ERR = "#e05c5c"
_PAUSE = "#e0a84c"
_MUTED = "#9e9e9e"


@dataclass
class RowUI:
    check: ft.Checkbox
    thumb: ft.Image
    title: ft.Text
    author: ft.Text
    size: ft.Text
    bar: ft.ProgressBar
    pct: ft.Text
    status: ft.Text
    retry: ft.Button
    root: ft.Control
    index: int = 0


class IwaraApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.config_data = AppConfig.load()
        self.i18n = I18n(self.config_data.language)
        self.history = HistoryDB()

        self._videos: list[VideoItem] = []
        self._rows: list[RowUI] = []
        self._list_generation = 0
        self._scan_source = ""
        self._scan_query = ""
        self._stop = threading.Event()
        self._pause_gate = threading.Event()
        self._pause_gate.set()
        self._busy = False
        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thumb_row_index: dict[str, int] = {}
        self._placeholder_src = self._make_placeholder_src()
        self._library_channels: list[LocalChannelInfo] = []
        self._library_checking = False
        self._library_remote_checked = False
        self._library_auto_select_new = False
        self._dl_server_by_id: dict[str, str] = {}

        self._setup_page()
        self._build()
        self._apply_theme()
        self._log(
            self.i18n.t("msg_demo_note")
            if self.config_data.demo_mode
            else self.i18n.t("msg_live_mode")
        )
        self._log("UI: Flet / Flutter (GPU)")
        page.run_task(self._poll_loop)
        # Load local library folders once
        try:
            self._library_load_local()
        except Exception:
            pass

    # ------------------------------------------------------------------ setup
    def _setup_page(self) -> None:
        p = self.page
        p.title = self.i18n.t("app_title")
        try:
            # Fixed on-screen position so window never spawns off-monitor
            p.window.width = 1200
            p.window.height = 820
            p.window.min_width = 980
            p.window.min_height = 640
            p.window.left = 80
            p.window.top = 60
            p.window.visible = True
            p.window.maximized = False
            p.window.minimized = False
            p.window.skip_task_bar = False
            p.window.always_on_top = True  # brief pin so user sees it; cleared after focus
            p.window.focused = True
        except Exception as exc:
            console_error("window setup", exc)
        p.padding = 12
        p.spacing = 8
        p.bgcolor = "#121212" if self.config_data.theme == "dark" else "#f5f5f5"
        p.theme_mode = (
            ft.ThemeMode.DARK
            if self.config_data.theme == "dark"
            else ft.ThemeMode.LIGHT
        )
        p.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE)
        p.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE)
        self.file_picker = ft.FilePicker()
        try:
            p.services.append(self.file_picker)
        except Exception:
            # Older layout fallback
            if hasattr(p, "overlay"):
                p.overlay.append(self.file_picker)  # type: ignore[arg-type]

    def _make_placeholder_src(self) -> str:
        path = thumb_mod.CACHE_DIR / "_placeholder.jpg"
        try:
            if not path.exists():
                img = thumb_mod.placeholder_pil()
                img.save(path, format="JPEG", quality=85)
            return str(path)
        except Exception:
            return ""

    def _apply_theme(self) -> None:
        dark = self.config_data.theme == "dark"
        self.page.theme_mode = ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT
        try:
            self.page.bgcolor = "#121212" if dark else "#f5f5f5"
        except Exception:
            pass

    def t(self, key: str, **kw: Any) -> str:
        return self.i18n.t(key, **kw)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        """
        Layout mirrors CustomTkinter:
          header → tab strip → active panel (content-sized)
          action bar → folder bar → col headers → page nav
          video ListView (only expand) → progress → log
        Side labels only — never TextField.label (avoids Material float overlap).
        """
        t = self.t
        self._tab_index = 0
        self._page = 0
        self._page_size = max(
            20, min(200, int(getattr(self.config_data, "list_page_size", 60) or 60))
        )

        def side_lbl(text: str, w: int | None = None) -> ft.Text:
            """Non-floating label; width only when we need a fixed form column."""
            kw: dict[str, Any] = {"value": text, "size": 13}
            if w is not None:
                kw["width"] = w
            return ft.Text(**kw)

        def tf(
            *,
            value: str = "",
            hint: str = "",
            width: int | None = None,
            expand: bool = False,
            password: bool = False,
            center: bool = False,
            on_submit: Callable | None = None,
            on_blur: Callable | None = None,
        ) -> ft.TextField:
            """Outline field without floating label; fixed height=40 always."""
            return ft.TextField(
                value=value,
                hint_text=hint or None,
                label=None,
                width=width,
                # expand only when in a Row — parent Column must NOT be scrollable
                expand=True if (expand and width is None) else None,
                height=40,
                dense=True,
                text_size=13,
                password=password,
                can_reveal_password=password,
                text_align=ft.TextAlign.CENTER if center else ft.TextAlign.LEFT,
                content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=8,
                filled=True,
                bgcolor="#2a2a2a" if self.config_data.theme == "dark" else "#ffffff",
                on_submit=on_submit,
                on_blur=on_blur,
            )

        # ---------- header ----------
        self.lang_dd = ft.Dropdown(
            width=200,
            menu_width=200,
            dense=True,
            value=self.i18n.lang,
            options=[
                ft.DropdownOption(key=c, text=LANG_LABELS[c]) for c in SUPPORTED_LANGS
            ],
            on_select=self._on_language_change,
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        self.lbl_title = ft.Text(
            t("app_title"), size=18, weight=ft.FontWeight.BOLD, expand=True
        )
        self.hdr_lang_label = ft.Text(t("language"), size=13, color=_MUTED)
        header = ft.Row(
            [
                self.lbl_title,
                ft.Row(
                    [self.hdr_lang_label, self.lang_dd],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # ---------- tab strip (CTkTabview-like buttons) ----------
        def _mk_tab_btn(idx: int, label: str) -> ft.Container:
            return ft.Container(
                content=ft.Text(label, size=13, weight=ft.FontWeight.W_600),
                padding=ft.Padding.symmetric(horizontal=18, vertical=9),
                border_radius=8,
                ink=True,
                on_click=lambda _e, i=idx: self._click_tab(i),
                data=idx,
            )

        self._tab_btns = [
            _mk_tab_btn(0, t("tab_hashtag")),
            _mk_tab_btn(1, t("tab_channel")),
            _mk_tab_btn(2, t("tab_library")),
            _mk_tab_btn(3, t("tab_settings")),
        ]
        self.tab_strip = ft.Container(
            content=ft.Row(self._tab_btns, spacing=4),
            padding=ft.Padding.only(bottom=2),
        )
        self.tab_seg = self.tab_strip

        # ---------- hashtag panel (single compact row like CTk) ----------
        self.lbl_hashtag = side_lbl(t("hashtag_label"))
        self.hashtag_query = tf(
            hint=t("hashtag_placeholder"),
            expand=True,
            on_submit=lambda _e: self._start_scan("hashtag"),
        )
        self.lbl_hashtag_limit = side_lbl(t("limit_label"))
        self.hashtag_limit = tf(
            value=str(self.config_data.scan_limit), width=72, center=True
        )
        self.btn_scan_hashtag = ft.FilledButton(
            t("btn_scan"),
            height=40,
            on_click=lambda _e: self._start_scan("hashtag"),
        )
        self.hashtag_hint = ft.Text(
            t("msg_demo_note") if self.config_data.demo_mode else "",
            size=11,
            color=_MUTED,
        )
        self.panel_hashtag = ft.Column(
            [
                ft.Row(
                    [
                        self.lbl_hashtag,
                        self.hashtag_query,
                        self.lbl_hashtag_limit,
                        self.hashtag_limit,
                        self.btn_scan_hashtag,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.hashtag_hint,
            ],
            spacing=4,
            tight=True,
        )

        # ---------- channel panel ----------
        self.lbl_channel = side_lbl(t("channel_label"))
        self.channel_query = tf(
            hint=t("channel_placeholder"),
            expand=True,
            on_submit=lambda _e: self._start_scan("channel"),
        )
        self.lbl_channel_limit = side_lbl(t("limit_label"))
        self.channel_limit = tf(
            value=str(self.config_data.scan_limit), width=72, center=True
        )
        self.btn_scan_channel = ft.FilledButton(
            t("btn_scan"),
            height=40,
            on_click=lambda _e: self._start_scan("channel"),
        )
        self.channel_hint = ft.Text(
            t("msg_demo_note") if self.config_data.demo_mode else "",
            size=11,
            color=_MUTED,
        )
        self.panel_channel = ft.Column(
            [
                ft.Row(
                    [
                        self.lbl_channel,
                        self.channel_query,
                        self.lbl_channel_limit,
                        self.channel_limit,
                        self.btn_scan_channel,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.channel_hint,
            ],
            spacing=4,
            tight=True,
        )

        # ---------- library panel ----------
        self.btn_lib_refresh = ft.FilledButton(
            t("btn_library_refresh"),
            icon=ft.Icons.REFRESH,
            height=36,
            on_click=lambda _e: self._library_refresh(auto=False),
        )
        self.btn_lib_update_all = ft.FilledButton(
            t("btn_library_update_all"),
            icon=ft.Icons.DOWNLOAD,
            bgcolor=_GREEN,
            height=36,
            on_click=lambda _e: self._library_update_all(),
        )
        self.lbl_lib_summary = ft.Text("", size=12, color=_MUTED, expand=True)
        self.lbl_lib_hint = ft.Text(t("library_hint"), size=11, color=_MUTED)
        self.lbl_lib_progress = ft.Text(t("library_progress_idle"), size=12, color=_MUTED)
        self.lbl_lib_progress_pct = ft.Text("0%", size=12, width=42)
        self.lib_progress = ft.ProgressBar(value=0, bar_height=8, border_radius=4)
        self.lib_hdr = ft.Row(
            [
                ft.Text(
                    t("library_col_channel"),
                    expand=3,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    t("library_col_drive"),
                    width=72,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    t("library_col_local"),
                    width=64,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    t("library_col_remote"),
                    width=64,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    t("library_col_new"),
                    expand=2,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    t("library_col_actions"),
                    width=180,
                    size=12,
                    weight=ft.FontWeight.W_600,
                ),
            ],
            spacing=6,
        )
        self.lib_list = ft.ListView(spacing=3, padding=2, height=160)
        self.panel_library = ft.Column(
            [
                ft.Row(
                    [self.btn_lib_refresh, self.btn_lib_update_all, self.lbl_lib_summary],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.lbl_lib_hint,
                ft.Row([self.lbl_lib_progress, self.lbl_lib_progress_pct], spacing=8),
                self.lib_progress,
                self.lib_hdr,
                self.lib_list,
            ],
            spacing=6,
            tight=True,
        )

        # ---------- settings panel (fixed label column, no float labels) ----------
        LW = 220  # label column width — fits longest VI strings

        def set_row(lbl: ft.Text, *ctrls: ft.Control) -> ft.Row:
            return ft.Row(
                [lbl, *ctrls],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

        self.lbl_set_dl_dir = side_lbl(t("download_dir_primary"), LW)
        self.entry_dl_dir = tf(
            value=self.config_data.download_dir,
            expand=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.btn_browse_set = ft.FilledButton(
            t("btn_browse"),
            height=36,
            on_click=lambda _e: self.page.run_task(self._browse_dir),
        )
        self.lbl_set_roots = side_lbl(t("download_roots_label"), LW)
        self.lbl_roots_hint = ft.Text(t("download_roots_hint"), size=11, color=_MUTED)
        self.roots_list = ft.Column(spacing=4, tight=True)
        self.btn_add_root = ft.FilledButton(
            t("btn_add_download_root"),
            height=36,
            icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=lambda _e: self.page.run_task(self._add_download_root),
        )
        self._refresh_roots_list_ui()

        self.lbl_set_batch = side_lbl(t("download_batch_label"), LW)
        self.entry_dl_batch = tf(
            value=str(self.config_data.download_batch),
            width=88,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_set_delay = side_lbl(t("download_delay_label"), LW)
        self.entry_dl_delay = tf(
            value=str(self.config_data.download_delay_sec),
            width=88,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_set_stall = side_lbl(t("stall_timeout_label"), LW)
        self.entry_stall = tf(
            value=str(int(self.config_data.stall_timeout_sec)),
            width=88,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_set_scan_delay = side_lbl(t("scan_delay_label"), LW)
        self.entry_scan_delay = tf(
            value=str(self.config_data.scan_delay_sec),
            width=88,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_set_theme = side_lbl(t("theme"), LW)
        self.theme_dd = ft.Dropdown(
            width=160,
            menu_width=160,
            dense=True,
            value=self.config_data.theme,
            options=[
                ft.DropdownOption(key="dark", text=t("theme_dark")),
                ft.DropdownOption(key="light", text=t("theme_light")),
            ],
            on_select=self._on_theme_change,
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        self.demo_sw = ft.Checkbox(
            label=t("demo_mode"),
            value=self.config_data.demo_mode,
            on_change=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_set_ui_backend = side_lbl(t("ui_backend_label"), LW)
        self.ui_backend_dd = ft.Dropdown(
            width=320,
            menu_width=320,
            dense=True,
            value=self.config_data.ui_backend or "flet",
            options=[
                ft.DropdownOption(key="tk", text=t("ui_backend_tk")),
                ft.DropdownOption(key="flet", text=t("ui_backend_flet")),
            ],
            on_select=self._on_ui_backend_change,
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        self.lbl_set_ui_gpu = side_lbl(t("ui_gpu_pick_label"), LW)
        self._gpus = list_gpus()
        self._gpu_opts = build_gpu_options(
            t("ui_gpu_auto"),
            t("ui_gpu_high"),
            t("ui_gpu_save"),
            self._gpus,
        )
        gpu_val = self.config_data.ui_gpu or GPU_AUTO
        valid_keys = {k for k, _ in self._gpu_opts}
        if gpu_val not in valid_keys:
            gpu_val = GPU_AUTO
        self.ui_gpu_dd = ft.Dropdown(
            width=420,
            menu_width=480,
            dense=True,
            value=gpu_val,
            options=[
                ft.DropdownOption(key=k, text=lab) for k, lab in self._gpu_opts
            ],
            on_select=self._on_ui_gpu_change,
            text_size=12,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        detected_txt = format_gpu_list(self._gpus)
        if not self._gpus:
            detected_txt = t("ui_gpu_none")
        self.lbl_gpu_detected = ft.Text(
            f"{t('ui_gpu_detected_label')}:\n{detected_txt}",
            size=11,
            color=_MUTED,
            selectable=True,
        )
        self.lbl_set_page_size = side_lbl(t("list_page_size_label"), LW)
        self.entry_page_size = tf(
            value=str(getattr(self.config_data, "list_page_size", 60)),
            width=88,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        st = cache_stats()
        self.lbl_cache_status = ft.Text(
            t("msg_cache_stats", files=st["files"], mb=st["mb"]),
            size=12,
            color=_MUTED,
            expand=True,
        )
        self.btn_clear_cache = ft.Button(
            t("btn_clear_cache"),
            bgcolor=_AMBER,
            color=ft.Colors.WHITE,
            height=36,
            on_click=lambda _e: self._on_clear_cache(),
        )
        self.lbl_set_email = side_lbl(t("login_email"), LW)
        self.entry_email = tf(value=self.config_data.email, expand=True)
        self.lbl_set_password = side_lbl(t("login_password"), LW)
        self.entry_password = tf(
            value=self.config_data.password, expand=True, password=True
        )
        self.btn_login = ft.FilledButton(
            t("btn_login"),
            bgcolor=_BLUE,
            height=36,
            on_click=lambda _e: self._on_login_click(),
        )
        self.lbl_login_status = ft.Text(t("login_status_idle"), size=13, color=_MUTED)
        self.lbl_login_hint = ft.Text(t("login_hint"), size=11, color=_MUTED)
        self.lbl_settings_hint = ft.Text(t("settings_hint"), size=11, color=_MUTED)

        self.panel_settings = ft.Column(
            [
                set_row(self.lbl_set_dl_dir, self.entry_dl_dir, self.btn_browse_set),
                self.lbl_set_roots,
                self.lbl_roots_hint,
                self.roots_list,
                ft.Row([self.btn_add_root], spacing=8),
                set_row(self.lbl_set_batch, self.entry_dl_batch),
                set_row(self.lbl_set_delay, self.entry_dl_delay),
                set_row(self.lbl_set_stall, self.entry_stall),
                set_row(self.lbl_set_scan_delay, self.entry_scan_delay),
                set_row(self.lbl_set_theme, self.theme_dd),
                ft.Container(content=self.demo_sw, padding=ft.Padding.only(left=0)),
                set_row(self.lbl_set_ui_backend, self.ui_backend_dd),
                set_row(self.lbl_set_ui_gpu, self.ui_gpu_dd),
                ft.Container(
                    content=self.lbl_gpu_detected,
                    padding=ft.Padding.only(left=0, bottom=4),
                ),
                set_row(self.lbl_set_page_size, self.entry_page_size),
                ft.Row([self.btn_clear_cache, self.lbl_cache_status], spacing=12),
                set_row(self.lbl_set_email, self.entry_email),
                set_row(self.lbl_set_password, self.entry_password),
                ft.Row([self.btn_login, self.lbl_login_status], spacing=12),
                self.lbl_login_hint,
                self.lbl_settings_hint,
            ],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        )

        # Host for active top panel — height adapts per tab
        self.panel_host = ft.Container(
            content=self.panel_hashtag,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        # ---------- shared bottom chrome (always visible, like CTk bottom frame) ----------
        self.lbl_found = ft.Text(t("found_count", count=0), size=13)
        self.lbl_selected = ft.Text(t("selected_count", count=0), size=13)
        self.lbl_size_summary = ft.Text(
            t("size_summary", total="—", selected="—", count=0), size=12, color=_MUTED
        )
        self.btn_select_all = ft.FilledButton(
            t("btn_select_all"), height=34, on_click=lambda _e: self._select_all()
        )
        self.btn_deselect_all = ft.Button(
            t("btn_deselect_all"),
            bgcolor="#555555",
            color=ft.Colors.WHITE,
            height=34,
            on_click=lambda _e: self._deselect_all(),
        )
        self.btn_select_new = ft.FilledButton(
            t("btn_select_new"),
            visible=False,
            bgcolor="#2d5a7b",
            height=34,
            on_click=lambda _e: self._select_new_only(),
        )
        self.btn_retry_failed = ft.Button(
            t("btn_retry_failed"),
            bgcolor=_AMBER,
            color=ft.Colors.WHITE,
            height=34,
            on_click=lambda _e: self._retry_failed(),
        )
        self.btn_pause = ft.Button(
            content=ft.Text(
                t("btn_pause"),
                color=ft.Colors.WHITE,
                weight=ft.FontWeight.W_600,
                size=13,
            ),
            icon=ft.Icons.PAUSE,
            bgcolor="#6b5b2e",
            color=ft.Colors.WHITE,
            height=38,
            disabled=True,
            on_click=lambda _e: self._toggle_pause(),
        )
        self.btn_stop = ft.Button(
            t("btn_stop"),
            bgcolor=_RED,
            color=ft.Colors.WHITE,
            height=34,
            disabled=True,
            on_click=lambda _e: self._request_stop(),
        )
        # Solid Button (not FilledButton) — Material Filled looks washed/dim on dark theme
        self.btn_download = ft.Button(
            content=ft.Text(
                t("btn_download"),
                color=ft.Colors.WHITE,
                weight=ft.FontWeight.W_600,
                size=13,
            ),
            bgcolor=_GREEN,
            color=ft.Colors.WHITE,
            height=38,
            elevation=2,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: _GREEN,
                    ft.ControlState.HOVERED: _GREEN_HOVER,
                    ft.ControlState.DISABLED: _GREEN_DISABLED,
                },
                color={
                    ft.ControlState.DEFAULT: "#ffffff",
                    ft.ControlState.HOVERED: "#ffffff",
                    ft.ControlState.DISABLED: "#e8e8e8",
                },
                overlay_color={
                    ft.ControlState.HOVERED: "transparent",
                    ft.ControlState.PRESSED: "transparent",
                },
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                elevation={
                    ft.ControlState.DEFAULT: 2,
                    ft.ControlState.DISABLED: 0,
                },
            ),
            on_click=lambda _e: self._start_download(),
        )
        # Two rows — NEVER put expand=True spacer inside wrap=True Row
        # (Flet/Flutter turns that into a full-window gray slab).
        self.action_bar = ft.Column(
            [
                ft.Row(
                    [
                        self.lbl_found,
                        self.lbl_selected,
                        self.lbl_size_summary,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        self.btn_select_all,
                        self.btn_deselect_all,
                        self.btn_select_new,
                        self.btn_retry_failed,
                        ft.Container(expand=True),
                        self.btn_pause,
                        self.btn_stop,
                        self.btn_download,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=4,
            tight=True,
        )

        self.lbl_dl_dir_bar = side_lbl(t("download_dir_primary"))
        self.entry_dl_dir_bar = tf(
            value=self.config_data.download_dir,
            expand=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_roots_bar = ft.Text(
            "",
            size=11,
            color=_MUTED,
            expand=True,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.lbl_dl_batch_bar = side_lbl(t("download_batch_label"))
        self.entry_dl_batch_bar = tf(
            value=str(self.config_data.download_batch),
            width=56,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_dl_delay_bar = side_lbl(t("download_delay_label"))
        self.entry_dl_delay_bar = tf(
            value=str(self.config_data.download_delay_sec),
            width=56,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.lbl_stall_bar = side_lbl(t("stall_timeout_label"))
        self.entry_stall_bar = tf(
            value=str(int(self.config_data.stall_timeout_sec)),
            width=56,
            center=True,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.folder_bar = ft.Column(
            [
                ft.Row(
                    [
                        self.lbl_dl_dir_bar,
                        self.entry_dl_dir_bar,
                        ft.FilledButton(
                            t("btn_browse"),
                            height=36,
                            on_click=lambda _e: self.page.run_task(self._browse_dir),
                        ),
                        ft.FilledButton(
                            t("btn_add_download_root"),
                            height=36,
                            on_click=lambda _e: self.page.run_task(self._add_download_root),
                        ),
                        ft.Button(
                            t("btn_open_folder"),
                            bgcolor="#555555",
                            color=ft.Colors.WHITE,
                            height=36,
                            on_click=lambda _e: self._open_download_folder(),
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [self.lbl_roots_bar],
                    spacing=8,
                ),
                ft.Row(
                    [
                        self.lbl_dl_batch_bar,
                        self.entry_dl_batch_bar,
                        self.lbl_dl_delay_bar,
                        self.entry_dl_delay_bar,
                        self.lbl_stall_bar,
                        self.entry_stall_bar,
                    ],
                    spacing=10,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=6,
            tight=True,
        )

        self.header_row = ft.Container(
            content=ft.Row(
                [
                    ft.Text(
                        t("col_select"), width=36, size=12, weight=ft.FontWeight.W_600
                    ),
                    ft.Text(
                        t("col_thumb"),
                        width=thumb_mod.THUMB_W,
                        size=12,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Text(
                        t("col_title"), expand=3, size=12, weight=ft.FontWeight.W_600
                    ),
                    ft.Text(
                        t("col_author"), expand=2, size=12, weight=ft.FontWeight.W_600
                    ),
                    ft.Text(t("col_size"), width=70, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(
                        t("col_progress"), expand=2, size=12, weight=ft.FontWeight.W_600
                    ),
                    ft.Text(
                        t("col_status"), expand=2, size=12, weight=ft.FontWeight.W_600
                    ),
                    ft.Text(
                        t("col_action"), width=72, size=12, weight=ft.FontWeight.W_600
                    ),
                ],
                spacing=6,
            ),
            padding=ft.Padding.symmetric(horizontal=4, vertical=2),
        )
        self.hdr_texts = self.header_row.content.controls  # type: ignore[union-attr]

        self.btn_page_prev = ft.Button(
            t("btn_page_prev"),
            bgcolor="#555555",
            color=ft.Colors.WHITE,
            height=30,
            disabled=True,
            on_click=lambda _e: self._page_prev(),
        )
        self.btn_page_next = ft.Button(
            t("btn_page_next"),
            bgcolor="#555555",
            color=ft.Colors.WHITE,
            height=30,
            disabled=True,
            on_click=lambda _e: self._page_next(),
        )
        self.lbl_page = ft.Text("", size=12, color=_MUTED, expand=True)
        self.page_bar = ft.Row(
            [self.btn_page_prev, self.btn_page_next, self.lbl_page], spacing=8
        )

        self.list_empty = ft.Text(
            t("progress_idle"),
            size=13,
            color=_MUTED,
            text_align=ft.TextAlign.CENTER,
        )
        # Fixed-height list (like CTkScrollableFrame). Avoid Column+expand fights
        # that painted a full-window gray slab and hid folder/progress/log.
        self._list_height = 260
        self.list_view = ft.ListView(
            height=self._list_height,
            spacing=2,
            padding=4,
            auto_scroll=False,
            controls=[
                ft.Container(
                    content=self.list_empty,
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.symmetric(vertical=20, horizontal=12),
                )
            ],
        )
        self.list_shell = ft.Container(
            content=self.list_view,
            height=self._list_height + 8,
            border=ft.Border.all(1, "#3a3a3a"),
            border_radius=8,
            padding=4,
            bgcolor="#1a1a1a",
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        self.lbl_prog_ok = ft.Text(
            t("progress_idle"), size=13, color=_MUTED, weight=ft.FontWeight.BOLD
        )
        self.lbl_prog_err = ft.Text("", size=13, color=_ERR, weight=ft.FontWeight.BOLD)
        self.lbl_progress = ft.Text("", size=12, color=_MUTED, expand=True)
        self.lbl_progress_pct = ft.Text(
            "0%", size=13, weight=ft.FontWeight.BOLD, width=44
        )
        self.seg_ok = ft.Container(bgcolor=_GREEN, expand=0, height=10, border_radius=4)
        self.seg_err = ft.Container(bgcolor=_RED, expand=0, height=10, border_radius=4)
        self.seg_rest = ft.Container(
            bgcolor=ft.Colors.OUTLINE_VARIANT, expand=1, height=10, border_radius=4
        )
        self.seg_ok.visible = False
        self.seg_err.visible = False
        # Do NOT set expand on this Row — inside a Column that steals vertical space
        # and collapses folder/list chrome into a gray slab.
        self.progress_seg = ft.Row(
            [self.seg_ok, self.seg_err, self.seg_rest],
            spacing=0,
            height=10,
        )
        self.progress_block = ft.Column(
            [
                ft.Row(
                    [
                        self.lbl_prog_ok,
                        self.lbl_prog_err,
                        self.lbl_progress,
                        self.lbl_progress_pct,
                    ],
                    spacing=8,
                ),
                self.progress_seg,
            ],
            spacing=3,
            tight=True,
        )

        self.lbl_log = ft.Text(t("log_title"), size=13, weight=ft.FontWeight.W_600)
        # Full window width + taller — default Column center kept the box tiny
        self.log_box = ft.TextField(
            multiline=True,
            min_lines=4,
            max_lines=8,
            read_only=True,
            text_size=12,
            height=130,
            label=None,
            filled=True,
            bgcolor="#1a1a1a" if self.config_data.theme == "dark" else "#f0f0f0",
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=8,
        )

        # Shim for code that reads tabs.selected_index / tab_bar.tabs[i].label
        class _TabLabel:
            def __init__(self, label: str = "") -> None:
                self.label = label

        class _TabBarShim:
            def __init__(self) -> None:
                self.tabs = [_TabLabel() for _ in range(4)]

        self.tab_bar = _TabBarShim()
        for i, key in enumerate(
            ["tab_hashtag", "tab_channel", "tab_library", "tab_settings"]
        ):
            self.tab_bar.tabs[i].label = t(key)

        class _TabsShim:
            def __init__(shim, app: "IwaraApp") -> None:
                shim._app = app
                shim.height = 0

            @property
            def selected_index(shim) -> int:
                return int(getattr(shim._app, "_tab_index", 0))

            @selected_index.setter
            def selected_index(shim, v: int) -> None:
                shim._app._click_tab(int(v))

        self.tabs = _TabsShim(self)

        # Non-scroll root with fixed list height. Scrollable Column + TextField(expand)
        # was painting a full-window gray Material fill over folder/progress/log.
        self.page.add(
            ft.Column(
                [
                    header,
                    self.tab_strip,
                    self.panel_host,
                    ft.Divider(height=1, thickness=1, color=ft.Colors.OUTLINE_VARIANT),
                    self.action_bar,
                    self.folder_bar,
                    self.header_row,
                    self.page_bar,
                    self.list_shell,
                    self.progress_block,
                    self.lbl_log,
                    self.log_box,
                ],
                expand=True,
                spacing=5,
                # Stretch children to full window width (log box was content-sized)
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        )
        self.top_chrome = None
        self.bottom_chrome = None
        self._click_tab(0)
        self._update_page_label()

    def _paint_tab_strip(self) -> None:
        """Highlight active tab button (CTkTabview selected look)."""
        for i, btn in enumerate(self._tab_btns):
            active = i == self._tab_index
            try:
                btn.bgcolor = _BLUE if active else ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)
                if isinstance(btn.content, ft.Text):
                    btn.content.color = ft.Colors.WHITE if active else None
                    btn.content.weight = (
                        ft.FontWeight.BOLD if active else ft.FontWeight.W_600
                    )
            except Exception:
                pass

    def _click_tab(self, idx: int) -> None:
        self._tab_index = int(idx)
        self._paint_tab_strip()
        self._show_tab(self._tab_index)
        try:
            self.page.update()
        except Exception:
            pass

    def _show_tab(self, idx: int) -> None:
        """Swap top panel only; bottom list chrome stays (like CTk)."""
        self._tab_index = idx
        # Bound height only for tall panels so list still gets room
        if idx == 0:
            self.panel_host.content = self.panel_hashtag
            self.panel_host.height = None
            self._set_list_height(280)
            self.btn_select_new.visible = False
        elif idx == 1:
            self.panel_host.content = self.panel_channel
            self.panel_host.height = None
            self._set_list_height(280)
            self.btn_select_new.visible = True
        elif idx == 2:
            self.panel_host.content = self.panel_library
            self.panel_host.height = 220
            self._set_list_height(160)
            self.btn_select_new.visible = self._scan_source == "channel"
            self._library_on_enter()
        else:
            self.panel_host.content = self.panel_settings
            # Show full settings form (UI backend / GPU / login) with scroll
            self.panel_host.height = 360
            self._set_list_height(120)
            self.btn_select_new.visible = False

    def _set_list_height(self, h: int) -> None:
        """Resize video list region (fixed height — avoids expand gray-slab bugs)."""
        try:
            self._list_height = int(h)
            self.list_view.height = self._list_height
            self.list_shell.height = self._list_height + 8
        except Exception:
            pass

    def _on_tab_change(self, _e: ft.ControlEvent | None = None) -> None:
        self._click_tab(getattr(self, "_tab_index", 0))

    def _switch_to_channel_tab(self) -> None:
        self._click_tab(1)
        self.btn_select_new.visible = True

    def _on_language_change(self, e: ft.ControlEvent) -> None:
        code = str(getattr(e.control, "value", None) or "vi")
        self.i18n.set_lang(code)
        self.config_data.language = code
        self.config_data.save()
        self._refresh_texts()
        try:
            self.page.update()
        except Exception:
            pass

    def _on_theme_change(self, e: ft.ControlEvent) -> None:
        mode = str(getattr(e.control, "value", None) or "dark")
        self.config_data.theme = mode
        self.config_data.save()
        self._apply_theme()
        try:
            self.page.update()
        except Exception:
            pass

    def _refresh_texts(self) -> None:
        t = self.t
        self.page.title = t("app_title")
        self.lbl_title.value = t("app_title")
        self.hdr_lang_label.value = t("language")

        tab_keys = ["tab_hashtag", "tab_channel", "tab_library", "tab_settings"]
        for i, key in enumerate(tab_keys):
            try:
                if i < len(self.tab_bar.tabs):
                    self.tab_bar.tabs[i].label = t(key)
            except Exception:
                pass
        try:
            for i, key in enumerate(tab_keys):
                if i < len(self._tab_btns):
                    btn = self._tab_btns[i]
                    if isinstance(btn.content, ft.Text):
                        btn.content.value = t(key)
            self._paint_tab_strip()
        except Exception:
            pass

        # Side labels + hints (never assign TextField.label)
        try:
            self.lbl_hashtag.value = t("hashtag_label")
            self.lbl_hashtag_limit.value = t("limit_label")
            self.lbl_channel.value = t("channel_label")
            self.lbl_channel_limit.value = t("limit_label")
            self.hashtag_query.hint_text = t("hashtag_placeholder")
            self.channel_query.hint_text = t("channel_placeholder")
            self.btn_scan_hashtag.text = t("btn_scan")
            self.btn_scan_channel.text = t("btn_scan")
            note = t("msg_demo_note") if self.config_data.demo_mode else ""
            self.hashtag_hint.value = note
            self.channel_hint.value = note

            self.lbl_set_dl_dir.value = t("download_dir_primary")
            self.lbl_set_roots.value = t("download_roots_label")
            self.lbl_roots_hint.value = t("download_roots_hint")
            self.btn_add_root.text = t("btn_add_download_root")
            self.lbl_set_batch.value = t("download_batch_label")
            self.lbl_set_delay.value = t("download_delay_label")
            self.lbl_set_stall.value = t("stall_timeout_label")
            self.lbl_set_scan_delay.value = t("scan_delay_label")
            self.lbl_set_theme.value = t("theme")
            self.lbl_set_ui_backend.value = t("ui_backend_label")
            self.lbl_set_ui_gpu.value = t("ui_gpu_pick_label")
            self._gpus = list_gpus()
            self._gpu_opts = build_gpu_options(
                t("ui_gpu_auto"),
                t("ui_gpu_high"),
                t("ui_gpu_save"),
                self._gpus,
            )
            cur = self.ui_gpu_dd.value or self.config_data.ui_gpu or GPU_AUTO
            self.ui_gpu_dd.options = [
                ft.DropdownOption(key=k, text=lab) for k, lab in self._gpu_opts
            ]
            keys = {k for k, _ in self._gpu_opts}
            self.ui_gpu_dd.value = cur if cur in keys else GPU_AUTO
            det = format_gpu_list(self._gpus) if self._gpus else t("ui_gpu_none")
            self.lbl_gpu_detected.value = f"{t('ui_gpu_detected_label')}:\n{det}"
            self.lbl_set_page_size.value = t("list_page_size_label")
            self.lbl_set_email.value = t("login_email")
            self.lbl_set_password.value = t("login_password")
            self.lbl_dl_dir_bar.value = t("download_dir_primary")
            self.lbl_dl_batch_bar.value = t("download_batch_label")
            self.lbl_dl_delay_bar.value = t("download_delay_label")
            self.lbl_stall_bar.value = t("stall_timeout_label")

            self.theme_dd.options = [
                ft.DropdownOption(key="dark", text=t("theme_dark")),
                ft.DropdownOption(key="light", text=t("theme_light")),
            ]
            self.demo_sw.label = t("demo_mode")
            self.btn_login.text = t("btn_login")
            self.lbl_login_hint.value = t("login_hint")
            self.lbl_settings_hint.value = t("settings_hint")
            self.btn_lib_refresh.text = t("btn_library_refresh")
            self.btn_lib_update_all.text = t("btn_library_update_all")
            self.lbl_lib_hint.value = t("library_hint")
            self.btn_page_prev.text = t("btn_page_prev")
            self.btn_page_next.text = t("btn_page_next")
            self.btn_clear_cache.text = t("btn_clear_cache")
            self.btn_browse_set.text = t("btn_browse")
            self.lang_dd.options = [
                ft.DropdownOption(key=c, text=LANG_LABELS[c]) for c in SUPPORTED_LANGS
            ]
            self._refresh_roots_list_ui()
            self._update_roots_bar_summary()
        except Exception:
            pass

        self.btn_select_all.text = t("btn_select_all")
        self.btn_deselect_all.text = t("btn_deselect_all")
        self.btn_select_new.text = t("btn_select_new")
        self.btn_retry_failed.text = t("btn_retry_failed")
        try:
            if isinstance(self.btn_download.content, ft.Text):
                self.btn_download.content.value = t("btn_download")
            else:
                self.btn_download.text = t("btn_download")
        except Exception:
            self.btn_download.text = t("btn_download")
        self.btn_stop.text = t("btn_stop")
        self._sync_pause_button()

        labels = [
            t("col_select"),
            t("col_thumb"),
            t("col_title"),
            t("col_author"),
            t("col_size"),
            t("col_progress"),
            t("col_status"),
            t("col_action"),
        ]
        for ctrl, text in zip(self.hdr_texts, labels):
            if isinstance(ctrl, ft.Text):
                ctrl.value = text

        # library headers
        try:
            lib_labels = [
                t("library_col_channel"),
                t("library_col_drive"),
                t("library_col_local"),
                t("library_col_remote"),
                t("library_col_new"),
                t("library_col_actions"),
            ]
            for ctrl, text in zip(self.lib_hdr.controls, lib_labels):
                if isinstance(ctrl, ft.Text):
                    ctrl.value = text
        except Exception:
            pass

        self.lbl_log.value = t("log_title")
        self.lbl_found.value = t("found_count", count=len(self._videos))
        self._update_selected_label()
        for i, item in enumerate(self._videos):
            self._update_row_status(i, item)
        if not self._busy:
            self.lbl_prog_ok.value = t("progress_idle")
            self.lbl_prog_ok.color = _MUTED
            try:
                self.list_empty.value = t("progress_idle")
            except Exception:
                pass
        self.page.update()

    # ------------------------------------------------------------------ helpers
    def _log(self, message: str) -> None:
        try:
            prev = self.log_box.value or ""
            lines = (prev + message + "\n").splitlines()
            # Keep last ~200 lines
            self.log_box.value = "\n".join(lines[-200:]) + "\n"
        except Exception:
            pass
        console_log(message)

    def _alert(self, message: str, *, error: bool = False) -> None:
        dlg = ft.AlertDialog(
            title=ft.Text(self.t("app_title")),
            content=ft.Text(message),
            actions=[
                ft.TextButton(
                    "OK",
                    on_click=lambda _e: self.page.pop_dialog(),
                )
            ],
        )
        try:
            self.page.show_dialog(dlg)
        except Exception:
            self._log(("ERROR: " if error else "") + message)

    def _sync_download_dir_entries(self, path: str) -> None:
        path = (path or "").strip()
        for entry in (self.entry_dl_dir_bar, self.entry_dl_dir):
            if entry.value != path:
                entry.value = path

    def _all_roots(self) -> list[str]:
        return self.config_data.all_download_dirs()

    def _update_roots_bar_summary(self) -> None:
        """Compact multi-drive channel counts under the primary path bar."""
        if not hasattr(self, "lbl_roots_bar"):
            return
        try:
            infos = summarize_download_roots(
                self._all_roots(),
                primary=self.config_data.download_dir,
            )
            if not infos:
                self.lbl_roots_bar.value = ""
                return
            pieces: list[str] = []
            for info in infos:
                piece = self.t(
                    "library_drive_piece",
                    drive=info.drive or "?",
                    n=info.channel_count,
                )
                if info.is_primary:
                    piece = f"★ {piece}"
                free = f" · {info.free_gb:.0f}GB" if info.free_gb >= 0 else ""
                pieces.append(f"{piece}{free}")
            self.lbl_roots_bar.value = "  ·  ".join(pieces)
        except Exception:
            try:
                self.lbl_roots_bar.value = ""
            except Exception:
                pass

    def _refresh_roots_list_ui(self) -> None:
        """Rebuild the multi-drive list in Settings."""
        if not hasattr(self, "roots_list"):
            return
        self.roots_list.controls.clear()
        infos = summarize_download_roots(
            self._all_roots(),
            primary=self.config_data.download_dir,
        )
        for info in infos:
            free_s = f"{info.free_gb:.0f}" if info.free_gb >= 0 else "?"
            primary_tag = (
                self.t("download_root_primary_tag") if info.is_primary else ""
            )
            row_txt = self.t(
                "download_root_row",
                drive=info.drive or "?",
                channels=info.channel_count,
                videos=info.video_count,
                free=free_s,
                primary=primary_tag,
            )
            path_txt = info.path
            btns: list[ft.Control] = []
            if not info.is_primary:
                btns.append(
                    ft.OutlinedButton(
                        self.t("btn_set_primary_root"),
                        height=30,
                        on_click=lambda _e, p=info.path: self._set_primary_root(p),
                    )
                )
            btns.append(
                ft.Button(
                    self.t("btn_remove_download_root"),
                    height=30,
                    bgcolor=_RED,
                    color=ft.Colors.WHITE,
                    on_click=lambda _e, p=info.path: self._remove_download_root(p),
                )
            )
            self.roots_list.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                row_txt,
                                size=12,
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Text(
                                path_txt,
                                size=11,
                                color=_MUTED,
                                selectable=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(btns, spacing=6),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                )
            )
        self._update_roots_bar_summary()

    def _set_primary_root(self, path: str) -> None:
        self.config_data.set_primary_download_dir(path, keep_old=True)
        self.config_data.save()
        self._sync_download_dir_entries(self.config_data.download_dir)
        self._refresh_roots_list_ui()
        self._log(
            self.t(
                "msg_root_primary",
                path=path,
                drive=path_drive(path),
            )
        )
        try:
            self.page.update()
        except Exception:
            pass

    def _remove_download_root(self, path: str) -> None:
        ok = self.config_data.remove_download_dir(path)
        if not ok:
            self._alert(self.t("msg_root_cannot_remove_last"))
            return
        self.config_data.save()
        self._sync_download_dir_entries(self.config_data.download_dir)
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(self.t("msg_root_removed", path=path))
        try:
            self._library_load_local()
        except Exception:
            pass
        try:
            self.page.update()
        except Exception:
            pass

    async def _add_download_root(self) -> None:
        initial = (
            (self.entry_dl_dir_bar.value or "").strip()
            or self.config_data.download_dir
            or str(Path.home())
        )
        path: str | None = None
        try:
            path = await self.file_picker.get_directory_path(
                dialog_title=self.t("btn_add_download_root"),
                initial_directory=initial if Path(initial).exists() else str(Path.home()),
            )
        except Exception as exc:
            console_error("FilePicker failed, fallback to tk", exc)
            path = self._browse_dir_tk(initial)
        if not path:
            return
        added = self.config_data.add_download_dir(path)
        if not added:
            # already listed — still ok if it's primary
            if path_drive(path):
                self._alert(self.t("msg_root_already"))
            return
        self.config_data.save()
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(
            self.t(
                "msg_root_added",
                path=path,
                drive=path_drive(path),
            )
        )
        try:
            self._library_load_local()
        except Exception:
            pass
        try:
            self.page.update()
        except Exception:
            pass

    async def _browse_dir(self) -> None:
        """Pick primary download folder for NEW channels (keeps old roots)."""
        initial = (
            (self.entry_dl_dir_bar.value or "").strip()
            or (self.entry_dl_dir.value or "").strip()
            or self.config_data.download_dir
            or str(Path.home())
        )
        path: str | None = None
        try:
            path = await self.file_picker.get_directory_path(
                dialog_title=self.t("download_dir_primary"),
                initial_directory=initial if Path(initial).exists() else str(Path.home()),
            )
        except Exception as exc:
            console_error("FilePicker failed, fallback to tk", exc)
            path = self._browse_dir_tk(initial)
        if not path:
            return
        self.config_data.set_primary_download_dir(path, keep_old=True)
        self.config_data.save()
        self._sync_download_dir_entries(path)
        self._refresh_roots_list_ui()
        self._library_remote_checked = False
        self._log(
            self.t(
                "msg_root_primary",
                path=path,
                drive=path_drive(path),
            )
        )
        try:
            self._library_load_local()
        except Exception:
            pass
        self.page.update()

    def _browse_dir_tk(self, initial: str) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(
                title=self.t("download_dir_primary"),
                initialdir=initial if Path(initial).exists() else str(Path.home()),
            )
            root.destroy()
            return path or None
        except Exception:
            return None

    def _open_download_folder(self) -> None:
        path = (
            (self.entry_dl_dir_bar.value or "").strip()
            or self.config_data.download_dir
        )
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            self._alert(str(exc), error=True)

    def _resolve_local_path(self, item: VideoItem) -> Path | None:
        if item.local_path:
            p = Path(item.local_path)
            if p.is_file():
                return p
        hist = self.history.get_path(item.id)
        if hist:
            p = Path(hist)
            if p.is_file():
                item.local_path = str(p)
                return p
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
            self._log(self.t("msg_file_not_local"))
            self._alert(self.t("msg_file_not_local"))
            return
        try:
            os.startfile(str(path))  # noqa: S606
            self._log(self.t("msg_open_file", path=str(path)))
            if item.status not in (VideoStatus.ON_DISK, VideoStatus.DONE):
                item.status = VideoStatus.ON_DISK
                item.progress = 1.0
                self._update_row_status(index, item)
                self.page.update()
        except Exception as exc:  # noqa: BLE001
            self._alert(self.t("msg_open_file_fail", error=str(exc)), error=True)

    def _save_settings_from_ui(self) -> None:
        delay_raw = (
            (self.entry_dl_delay_bar.value or "").strip()
            or (self.entry_dl_delay.value or "").strip()
        )
        stall_raw = (
            (self.entry_stall_bar.value or "").strip()
            or (self.entry_stall.value or "").strip()
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
            scan_delay = max(
                0.0, float((self.entry_scan_delay.value or "").strip() or "0")
            )
        except ValueError:
            scan_delay = self.config_data.scan_delay_sec

        bar_path = (self.entry_dl_dir_bar.value or "").strip()
        settings_path = (self.entry_dl_dir.value or "").strip()
        chosen = bar_path or settings_path or self.config_data.download_dir
        # Changing the primary path via text field keeps previous roots
        if chosen and chosen != self.config_data.download_dir:
            self.config_data.set_primary_download_dir(chosen, keep_old=True)
        else:
            self.config_data.download_dir = chosen
        self._sync_download_dir_entries(self.config_data.download_dir)
        try:
            self._refresh_roots_list_ui()
        except Exception:
            pass
        try:
            batch = max(
                1,
                min(16, int((self.entry_dl_batch.value or "3").strip() or "3")),
            )
        except ValueError:
            batch = max(1, min(16, int(self.config_data.download_batch or 3)))
        # Prefer bar fields if present
        try:
            batch_bar = (self.entry_dl_batch_bar.value or "").strip()
            if batch_bar:
                batch = max(1, min(16, int(batch_bar)))
        except Exception:
            pass
        self.config_data.download_batch = batch
        self.config_data.download_delay_sec = dl_delay
        self.config_data.stall_timeout_sec = stall
        self.config_data.scan_delay_sec = scan_delay
        self.config_data.demo_mode = bool(self.demo_sw.value)
        self.config_data.email = (self.entry_email.value or "").strip()
        self.config_data.password = self.entry_password.value or ""
        try:
            be = (self.ui_backend_dd.value or "flet").strip().lower()
            if be in ("tk", "flet"):
                self.config_data.ui_backend = be
            gpu_sel = (self.ui_gpu_dd.value or GPU_AUTO).strip()
            mode, adapter_name = parse_gpu_selection(gpu_sel, getattr(self, "_gpus", None))
            # Keep adapter:N key so the dropdown restores the exact device
            low = gpu_sel.lower()
            if low.startswith("adapter:") or low in (GPU_AUTO, GPU_HIGH, GPU_SAVE):
                self.config_data.ui_gpu = low
            else:
                self.config_data.ui_gpu = mode
            self.config_data.ui_gpu_adapter = adapter_name
            ps = int((self.entry_page_size.value or "60").strip() or "60")
            ps = max(20, min(200, ps))
            if ps != getattr(self, "_page_size", 60):
                self._page_size = ps
                self._rebuild_page()
            self.config_data.list_page_size = ps
        except Exception:
            pass
        self.config_data.save()
        try:
            self.entry_dl_batch.value = str(batch)
            self.entry_dl_batch_bar.value = str(batch)
        except Exception:
            pass

        for entry, value in (
            (self.entry_dl_delay, str(dl_delay)),
            (self.entry_dl_delay_bar, str(dl_delay)),
            (self.entry_stall, str(int(stall))),
            (self.entry_stall_bar, str(int(stall))),
            (self.entry_scan_delay, str(scan_delay)),
        ):
            if entry.value != value:
                entry.value = value

        note = self.t("msg_demo_note") if self.config_data.demo_mode else ""
        self.hashtag_hint.value = note
        self.channel_hint.value = note
        try:
            self.page.update()
        except Exception:
            pass

    def _parse_scan_limit(self, mode: str) -> int:
        field = self.hashtag_limit if mode == "hashtag" else self.channel_limit
        raw = (field.value or "").strip()
        try:
            val = int(raw)
        except ValueError:
            val = self.config_data.scan_limit
        if val < 0:
            val = 0
        elif val > 10_000:
            val = 10_000
        self.config_data.scan_limit = val
        self.config_data.save()
        # keep both limit fields in sync
        self.hashtag_limit.value = str(val)
        self.channel_limit.value = str(val)
        return val

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (
            self.btn_scan_hashtag,
            self.btn_scan_channel,
            self.btn_select_all,
            self.btn_deselect_all,
            self.btn_select_new,
            self.btn_retry_failed,
        ):
            btn.disabled = busy
        # Download stays visually solid; only truly disabled while busy
        self.btn_download.disabled = busy
        self.btn_download.opacity = 0.92 if busy else 1.0
        try:
            # Keep green look even when disabled (avoid gray Material wash-out)
            if isinstance(self.btn_download.content, ft.Text):
                self.btn_download.content.color = ft.Colors.WHITE
            self.btn_download.bgcolor = _GREEN_DISABLED if busy else _GREEN
        except Exception:
            pass
        self.btn_stop.disabled = not busy
        try:
            self.btn_stop.opacity = 1.0 if busy else 0.55
        except Exception:
            pass
        if not busy:
            self._pause_gate.set()
        self._sync_pause_button()
        for i, row in enumerate(self._rows):
            can = i < len(self._videos) and self._videos[i].can_retry() and not busy
            row.retry.disabled = not can
        try:
            self.page.update()
        except Exception:
            pass

    def _sync_pause_button(self) -> None:
        """Show «Tạm dừng» while running, «Tiếp tục» while paused (clickable)."""
        paused = bool(self._busy) and not self._pause_gate.is_set()
        label = self.t("btn_resume") if paused else self.t("btn_pause")
        try:
            if isinstance(self.btn_pause.content, ft.Text):
                self.btn_pause.content.value = label
                self.btn_pause.content.color = ft.Colors.WHITE
            else:
                self.btn_pause.text = label
        except Exception:
            try:
                self.btn_pause.text = label
            except Exception:
                pass
        try:
            self.btn_pause.icon = (
                ft.Icons.PLAY_ARROW if paused else ft.Icons.PAUSE
            )
        except Exception:
            pass
        # Green = resume (action), amber = pause
        try:
            self.btn_pause.bgcolor = "#2d7a4f" if paused else "#6b5b2e"
            self.btn_pause.color = ft.Colors.WHITE
            self.btn_pause.disabled = not self._busy
            self.btn_pause.opacity = 1.0 if self._busy else 0.55
        except Exception:
            pass

    def _pause_wait(self) -> None:
        while not self._pause_gate.is_set():
            if self._stop.is_set():
                return
            time.sleep(0.12)

    def _toggle_pause(self) -> None:
        if not self._busy:
            return
        if self._pause_gate.is_set():
            # Running → pause: label becomes «Tiếp tục»
            self._pause_gate.clear()
            self.lbl_progress.value = "· " + self.t("progress_paused")
            self.lbl_progress.color = _PAUSE
            self._log(self.t("msg_paused"))
        else:
            # Paused → resume: label becomes «Tạm dừng»
            self._pause_gate.set()
            self.lbl_progress.color = _MUTED
            self._log(self.t("msg_resumed"))
        self._sync_pause_button()
        try:
            self.page.update()
        except Exception:
            pass

    def _request_stop(self) -> None:
        self._stop.set()
        self._pause_gate.set()
        self._sync_pause_button()
        self._log(self.t("msg_stopped"))
        try:
            self.page.update()
        except Exception:
            pass

    def _selected_count(self) -> int:
        return sum(1 for v in self._videos if v.selected)

    def _update_selected_label(self) -> None:
        self.lbl_selected.value = self.t(
            "selected_count", count=self._selected_count()
        )
        self._update_size_summary()

    def _update_size_summary(self) -> None:
        total_bytes = 0
        selected_bytes = 0
        selected_n = 0
        unknown = 0
        for item in self._videos:
            if item.size_bytes > 0:
                total_bytes += item.size_bytes
            else:
                unknown += 1
            if item.selected:
                selected_n += 1
                if item.size_bytes > 0:
                    selected_bytes += item.size_bytes
        text = self.t(
            "size_summary",
            total=format_bytes(total_bytes if total_bytes else None),
            selected=format_bytes(selected_bytes if selected_bytes else None),
            count=selected_n,
        )
        if unknown:
            text += " " + self.t("size_unknown_note")
        self.lbl_size_summary.value = text

    def _on_check_toggle(self, index: int, e: ft.ControlEvent) -> None:
        if 0 <= index < len(self._videos):
            self._videos[index].selected = bool(e.control.value)
        self._update_selected_label()
        self.page.update()

    def _bulk_set_selection(self, pred: Callable[[int, VideoItem], bool]) -> None:
        for i, item in enumerate(self._videos):
            item.selected = bool(pred(i, item))
        start, end = self._page_slice()
        for local, abs_i in enumerate(range(start, end)):
            if local < len(self._rows):
                self._rows[local].check.value = self._videos[abs_i].selected
        self._update_selected_label()
        self.page.update()

    def _select_all(self) -> None:
        self._bulk_set_selection(lambda _i, item: item.auto_selectable())

    def _deselect_all(self) -> None:
        self._bulk_set_selection(lambda _i, _v: False)

    def _select_new_only(self) -> None:
        def pred(_i: int, item: VideoItem) -> bool:
            if not item.auto_selectable():
                return False
            return item.status not in (VideoStatus.ON_DISK, VideoStatus.DONE)

        self._bulk_set_selection(pred)

    def _on_ui_backend_change(self, _e: ft.ControlEvent) -> None:
        self._save_settings_from_ui()
        self._alert(self.t("msg_ui_restart"))
        self._log(self.t("msg_ui_restart"))

    def _on_ui_gpu_change(self, _e: ft.ControlEvent) -> None:
        self._save_settings_from_ui()
        sel = self.config_data.ui_gpu or GPU_AUTO
        ok, msg = apply_windows_gpu_preference(sel)
        self._log(msg if ok else f"GPU: {msg}")
        name = self.config_data.ui_gpu_adapter or sel
        self._alert(
            self.t("msg_gpu_applied", name=name, detail=msg if ok else str(msg))
        )

    def _on_clear_cache(self) -> None:
        result = clear_cache(thumbs=True, memory=True)
        freed_mb = round(result["freed_bytes"] / (1024 * 1024), 2)
        msg = self.t(
            "msg_cache_cleared", files=result["removed"], mb=freed_mb
        )
        st = cache_stats()
        self.lbl_cache_status.value = self.t(
            "msg_cache_stats", files=st["files"], mb=st["mb"]
        )
        self._log(msg)
        self._alert(msg)
        try:
            self.page.update()
        except Exception:
            pass

    def _item_target_dir(self, item: VideoItem | None = None) -> Path:
        roots = self._all_roots()
        primary = self.config_data.download_dir
        if item and self._scan_source == "channel" and item.channel:
            return resolve_channel_dir(
                item.channel, roots, primary=primary, create=True
            )
        return self._current_target_dir()

    def _current_target_dir(self) -> Path:
        q = self._scan_query or ""
        roots = self._all_roots()
        primary = self.config_data.download_dir
        if self._scan_source == "channel":
            # Multi-channel scan → root only (per-item uses _item_target_dir)
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
            self.lbl_page.value = self.t(
                "page_nav", page=1, pages=1, start=0, end=0, total=0
            )
            self.btn_page_prev.disabled = True
            self.btn_page_next.disabled = True
            return
        start, end = self._page_slice()
        self.lbl_page.value = self.t(
            "page_nav",
            page=self._page + 1,
            pages=pages,
            start=start + 1,
            end=end,
            total=total,
        )
        self.btn_page_prev.disabled = self._page <= 0
        self.btn_page_next.disabled = self._page >= pages - 1

    def _page_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._rebuild_page()
            self.page.update()

    def _page_next(self) -> None:
        if self._page < self._page_count() - 1:
            self._page += 1
            self._rebuild_page()
            self.page.update()

    def _rebuild_page(self) -> None:
        """Render only current page into ListView (match CTk paging)."""
        self.list_view.controls.clear()
        self._rows.clear()
        self._thumb_row_index.clear()
        pages = self._page_count()
        if self._page >= pages:
            self._page = max(0, pages - 1)
        start, end = self._page_slice()
        for abs_i in range(start, end):
            self._add_list_row_at(abs_i, self._videos[abs_i])
        self._update_page_label()
        self.lbl_found.value = self.t("found_count", count=len(self._videos))
        self._update_selected_label()
        if start < end:
            self._queue_thumbnails(self._videos[start:end], self._list_generation)

    def _clear_list(self) -> None:
        self._list_generation += 1
        self._page = 0
        self._videos.clear()
        self._rows.clear()
        self._thumb_row_index.clear()
        self.list_view.controls.clear()
        self.lbl_found.value = self.t("found_count", count=0)
        self._update_page_label()
        self._update_selected_label()

    def _queue_thumbnails(self, items: list[VideoItem], generation: int) -> None:
        for item in items:
            if not item.thumbnail_url and not item.thumbnail_alts:
                continue

            def on_thumb(
                vid: str,
                pil_img: object | None,
                gen: int = generation,
            ) -> None:
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
        path = thumb_mod.cache_path(video_id)
        try:
            if not path.exists() or path.stat().st_size < 100:
                pil_img.save(path, format="JPEG", quality=85)  # type: ignore[union-attr]
        except Exception:
            # still try memory cache path from fetch
            if path.exists():
                pass
            else:
                return
        local = self._thumb_row_index.get(video_id)
        if local is None:
            start, end = self._page_slice()
            for j in range(start, end):
                if self._videos[j].id == video_id:
                    local = j - start
                    break
        if local is not None and 0 <= local < len(self._rows):
            self._rows[local].thumb.src = str(path)
            self._rows[local].thumb.key = f"{video_id}-{path.stat().st_mtime_ns}"

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

    def _add_list_row(self, item: VideoItem) -> int:
        """Append video and (re)build current page."""
        self._videos.append(item)
        return len(self._videos) - 1

    def _add_list_row_at(self, abs_i: int, item: VideoItem) -> None:
        """Create row widgets for absolute video index on the current page."""
        i = abs_i  # absolute index for callbacks
        local = len(self._rows)

        check = ft.Checkbox(
            value=bool(item.selected),
            width=40,
            on_change=lambda e, idx=i: self._on_check_toggle(idx, e),
        )
        thumb = ft.Image(
            src=self._placeholder_src or None,
            width=thumb_mod.THUMB_W,
            height=thumb_mod.THUMB_H,
            fit=ft.BoxFit.COVER,
            border_radius=6,
        )
        title = ft.Text(item.title, expand=3, size=13, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        author = ft.Text(item.author, expand=2, size=12, color=_MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        size = ft.Text(item.size_label(), width=72, size=12, text_align=ft.TextAlign.RIGHT)
        bar = ft.ProgressBar(
            value=max(0.0, min(1.0, item.progress)),
            expand=True,
            color=self._progress_color(item) or _BLUE,
            bgcolor=ft.Colors.OUTLINE_VARIANT,
            bar_height=8,
            border_radius=4,
        )
        pct = ft.Text(f"{int(item.progress * 100)}%", width=40, size=11)
        status_color = None
        status_text = self.t(item.status_key())
        if item.status in (VideoStatus.ON_DISK, VideoStatus.DONE):
            status_color = _OK
        elif item.is_private or item.status == VideoStatus.PRIVATE:
            status_color = _PAUSE
            status_text = self.t("status_private")
        status = ft.Text(
            status_text,
            expand=2,
            size=12,
            color=status_color,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        retry = ft.Button(
            self.t("btn_retry"),
            bgcolor=_AMBER,
            color=ft.Colors.WHITE,
            height=32,
            disabled=not (item.can_retry() and not self._busy),
            on_click=lambda _e, idx=i: self._retry_one(idx),
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )

        def on_double(_e: ft.ControlEvent, idx: int = i) -> None:
            self._open_video_file(idx)

        row = ft.Container(
            content=ft.Row(
                [
                    check,
                    thumb,
                    title,
                    author,
                    size,
                    ft.Row([bar, pct], expand=2, spacing=6),
                    status,
                    retry,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            border_radius=10,
            ink=True,
            on_click=None,  # single click reserved for checkbox
            data=i,
            animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT),
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
        )
        # double-tap open via GestureDetector
        g = ft.GestureDetector(
            content=row,
            on_double_tap=lambda _e, idx=i: self._open_video_file(idx),
        )

        ui = RowUI(
            check=check,
            thumb=thumb,
            title=title,
            author=author,
            size=size,
            bar=bar,
            pct=pct,
            status=status,
            retry=retry,
            root=g,
            index=i,
        )
        self._rows.append(ui)
        self.list_view.controls.append(g)
        self._thumb_row_index[item.id] = local

    def _append_scan_batch(self, batch: list[VideoItem], page: int) -> None:
        if not batch:
            return
        by_folder: dict[str, list[VideoItem]] = {}
        for item in batch:
            folder = str(self._item_target_dir(item))
            by_folder.setdefault(folder, []).append(item)
        for folder, group in by_folder.items():
            # Also look for the same channel folder on other drives
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
            self._add_list_row(item)
        # Stay on last page so new items appear (like CTk)
        last_page = self._page_count() - 1
        if self._page != last_page:
            self._page = last_page
        self._rebuild_page()
        priv_n = sum(
            1 for v in batch if v.is_private or v.status == VideoStatus.PRIVATE
        )
        extra = f", private {priv_n}" if priv_n else ""
        self._log(
            f"+ API page {page}: +{len(batch)} video{extra} "
            f"(tổng list {len(self._videos)})"
        )

    def _update_row_status(self, index: int, item: VideoItem) -> None:
        # Map absolute video index → page-local row
        start, end = self._page_slice()
        if index < start or index >= end:
            return
        local = index - start
        if local < 0 or local >= len(self._rows):
            return
        row = self._rows[local]
        row.bar.value = max(0.0, min(1.0, item.progress))
        color = self._progress_color(item)
        if color:
            row.bar.color = color
        row.pct.value = f"{int(item.progress * 100)}%"

        text = self.t(item.status_key())
        if item.status == VideoStatus.DOWNLOADING:
            pct = int(item.progress * 100)
            srv = self._dl_server_by_id.get(item.id, "")
            if srv:
                text = self.t("status_downloading_server", pct=pct, server=srv)
            else:
                text = f"{text} {pct}%"
        elif item.status == VideoStatus.ON_DISK:
            text = self.t("status_on_disk")
        elif item.is_private or item.status == VideoStatus.PRIVATE:
            text = self.t("status_private")
        elif item.status in (VideoStatus.ERROR, VideoStatus.STALLED) and item.error:
            help_ = diagnose_download_error(item.error)
            text = help_.title
        scolor = None
        if item.status in (VideoStatus.ERROR, VideoStatus.STALLED):
            scolor = _ERR
        elif item.status in (VideoStatus.DONE, VideoStatus.ON_DISK):
            scolor = _OK
        elif item.is_private or item.status == VideoStatus.PRIVATE:
            scolor = _PAUSE
        row.status.value = text
        row.status.color = scolor
        row.retry.disabled = not (item.can_retry() and not self._busy)
        row.retry.text = self.t("btn_retry")

    # ------------------------------------------------------------------ workers
    def _start_scan(self, mode: str) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        query = (
            (self.hashtag_query.value or "").strip()
            if mode == "hashtag"
            else (self.channel_query.value or "").strip()
        )
        if not query:
            key = "msg_enter_hashtag" if mode == "hashtag" else "msg_enter_channel"
            self._alert(self.t(key))
            return

        limit = self._parse_scan_limit(mode)
        self._stop.clear()
        self._pause_gate.set()
        self._set_busy(True)
        self._clear_list()
        self._scan_source = mode
        if mode == "channel":
            names = IwaraAPI.split_channel_names(query)
            self._scan_query = ",".join(names) if names else query.strip().lstrip("@")
            if len(names) > 1:
                self._log(
                    self.t(
                        "msg_multi_channel",
                        count=len(names),
                        names=", ".join(names),
                    )
                )
        else:
            self._scan_query = query.strip().lstrip("#@")
        self._update_segment_bar(ok=0, err=0, total=0)
        self.lbl_prog_ok.value = self.t("progress_scan")
        self.lbl_prog_ok.color = _MUTED
        self.lbl_prog_err.value = ""
        self.lbl_progress.value = ""
        self.lbl_progress_pct.value = "…"
        limit_txt = self.t("limit_unlimited") if limit <= 0 else str(limit)
        self._log(f"→ {mode}: {query} (scan_limit={limit_txt})")
        self._log("List sẽ được thêm dần theo từng trang…")
        self.page.update()

        def worker() -> None:
            def on_log(msg: str) -> None:
                self._ui_queue.put(("log", msg))

            def on_batch(page_items: list, page: int) -> None:
                self._ui_queue.put(
                    ("scan_batch", {"items": page_items, "page": page})
                )

            def on_meta(source: str, resolved_query: str) -> None:
                # Keep joined multi-channel query; item.channel drives folders
                self._ui_queue.put(
                    ("scan_meta", {"source": source, "query": resolved_query})
                )

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
                if self.config_data.demo_mode:
                    self._ui_queue.put(("log", self.t("msg_demo_note")))
                else:
                    self._ui_queue.put(("log", self.t("msg_live_mode")))
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
        checked = 0
        skipped_local = 0
        for i, item in enumerate(self._videos):
            if not item.selected:
                continue
            checked += 1
            if skip_on_disk and item.status in (
                VideoStatus.ON_DISK,
                VideoStatus.DONE,
            ):
                skipped_local += 1
                continue
            selected.append((i, item))
        if skipped_local:
            self._log(self.t("msg_skip_on_disk", count=skipped_local))
        return selected, checked, skipped_local

    def _start_download(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        selected, checked, skipped_local = self._collect_selected(skip_on_disk=True)
        if not selected:
            if checked == 0:
                msg = self.t("msg_no_selection")
            elif skipped_local > 0 and skipped_local == checked:
                msg = self.t("msg_all_on_disk")
            else:
                msg = self.t("msg_no_selection")
            self._alert(msg)
            return
        self._run_download_queue(selected, force=False)

    def _retry_one(self, index: int) -> None:
        if self._busy or index < 0 or index >= len(self._videos):
            return
        item = self._videos[index]
        if not item.can_retry():
            return
        self._save_settings_from_ui()
        self._log(self.t("msg_retry_one", title=item.title[:60]))
        self.history.remove(item.id)
        item.progress = 0.0
        item.error = ""
        item.status = VideoStatus.QUEUED
        self._update_row_status(index, item)
        self.page.update()
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
            self._alert(self.t("msg_no_retryable"))
            return
        self._log(self.t("msg_retry_batch", count=len(jobs)))
        self.page.update()
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
            return Path(base_dir)

        folder_indexes: dict[str, dict] = {}
        index_lock = threading.Lock()
        for _idx, it in selected:
            fp = str(folder_for(it))
            if fp not in folder_indexes:
                folder_indexes[fp] = index_local_files(Path(fp))

        self._stop.clear()
        self._pause_gate.set()
        self._set_busy(True)
        total = len(selected)
        self._update_segment_bar(ok=0, err=0, total=0)
        self._set_overall_progress(
            ok=0, err=0, total=total, ratio=0.0, speed=0.0, title="", done=False
        )
        self._log(
            self.t(
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
        self.page.update()

        def worker() -> None:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            stats = {"ok": 0, "err": 0}
            counter_lock = threading.Lock()
            last_log_t = 0.0
            log_lock = threading.Lock()

            for idx, item in selected:
                item.status = VideoStatus.QUEUED
                item.error = ""
                self._ui_queue.put(("row_update", (idx, item)))

            def snapshot() -> tuple[int, int]:
                with counter_lock:
                    return stats["ok"], stats["err"]

            def finish_one(kind: str, n: int, item: VideoItem) -> None:
                with counter_lock:
                    if kind in ("ok", "skip"):
                        stats["ok"] += 1
                    elif kind == "err":
                        stats["err"] += 1
                    o, e = stats["ok"], stats["err"]
                if kind == "err":
                    help_ = diagnose_download_error(item.error or "")
                    self._ui_queue.put(
                        (
                            "dl_end",
                            {
                                "n": n,
                                "total": total,
                                "ok": o,
                                "err": e,
                                "title": item.title,
                                "kind": "err",
                                "error": help_.full_message(),
                                "err_title": help_.title,
                                "err_detail": help_.detail,
                                "err_advice": help_.advice,
                            },
                        )
                    )
                else:
                    self._ui_queue.put(
                        (
                            "dl_end",
                            {
                                "n": n,
                                "total": total,
                                "ok": o,
                                "err": e,
                                "title": item.title,
                                "kind": kind,
                            },
                        )
                    )

            def download_one(n: int, idx: int, item: VideoItem) -> str:
                nonlocal last_log_t
                self._pause_wait()
                if self._stop.is_set():
                    return "stop"
                o, e = snapshot()
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
                        log_it = (now - last_log_t) >= 2.5 or info.get("status") in (
                            "starting",
                            "done",
                            "error",
                            "finishing",
                        )
                        if log_it:
                            last_log_t = now
                    o2, e2 = snapshot()
                    self._ui_queue.put(
                        (
                            "dl_live",
                            {
                                "n": n_file,
                                "total": total,
                                "ok": o2,
                                "err": e2,
                                "title": info.get("title") or item.title,
                                "id": item.id,
                                "status": info.get("status") or "downloading",
                                "speed": float(info.get("speed") or 0),
                                "file_pct": float(info.get("file_pct") or 0),
                                "downloaded": int(info.get("downloaded") or 0),
                                "total_bytes": int(info.get("total_bytes") or 0),
                                "server": str(info.get("server") or ""),
                                "message": info.get("message") or "",
                                "log": log_it,
                            },
                        )
                    )

                target = folder_for(item)
                ch_key = item.channel or (
                    group
                    if scan_source == "channel" and "," not in (group or "")
                    else ""
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
                    return "stop"
                finish_one(kind, n, item)
                return kind

            futures = []
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="flet-dl"
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
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001
                        console_error("flet download worker", exc)

            o, e = snapshot()
            self._ui_queue.put(
                (
                    "download_done",
                    {
                        "ok": o,
                        "err": e,
                        "current": o + e,
                        "total": total,
                        "percent": int(
                            round(((o + e) / total) * 100) if total else 100
                        ),
                    },
                )
            )
            self._ui_queue.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ library
    def _library_on_enter(self) -> None:
        if not self._library_channels:
            self._library_load_local()
        if self._library_remote_checked or self._busy or self._library_checking:
            return
        # First visit only
        self._library_refresh(auto=True)

    def _library_load_local(self) -> None:
        self._save_settings_from_ui()
        prev = {c.username: c for c in self._library_channels}
        # Scan every managed download root (multi-drive)
        channels = list_local_channels_multi(self._all_roots())
        for ch in channels:
            old = prev.get(ch.username)
            if old and old.remote_count >= 0:
                ch.remote_count = old.remote_count
                ch.display_name = old.display_name or ch.display_name
                ch.missing_public = old.missing_public
                ch.missing_private = old.missing_private
                ch.error = old.error
        self._library_channels = channels
        self._library_render_list()
        self._update_roots_bar_summary()

    def _library_missing_label(self, ch: LocalChannelInfo) -> str:
        if ch.missing_public >= 0 and ch.missing_private >= 0:
            if ch.missing_public == 0 and ch.missing_private == 0:
                return self.t("library_missing_zero")
            if ch.missing_public == 0 and ch.missing_private > 0:
                return self.t(
                    "library_missing_all_private", n=ch.missing_private
                )
            if ch.missing_public > 0 and ch.missing_private > 0:
                return self.t(
                    "library_missing_mixed",
                    n=ch.missing_public,
                    priv=ch.missing_private,
                )
            return self.t("library_missing_public", n=ch.missing_public)
        if ch.remote_count < 0:
            return self.t("library_remote_unknown")
        n = max(0, ch.remote_count - ch.local_count)
        return self.t("library_missing_public", n=n) if n else self.t("library_missing_zero")

    def _library_render_list(self) -> None:
        self.lib_list.controls.clear()
        local_sum = sum(c.local_count for c in self._library_channels)
        miss_sum = 0
        any_gap = False
        for c in self._library_channels:
            if c.missing_public >= 0:
                any_gap = True
                miss_sum += c.missing_public
            elif c.remote_count >= 0:
                any_gap = True
                miss_sum += max(0, c.remote_count - c.local_count)
        # Per-drive channel counts for summary (multi-drive channels count once per drive)
        drive_counts: dict[str, int] = {}
        for c in self._library_channels:
            parts = [
                p.strip()
                for p in (c.drive or path_drive(c.folder) or "?").split("+")
                if p.strip()
            ] or ["?"]
            for part in parts:
                drive_counts[part] = drive_counts.get(part, 0) + 1
        if not self._library_channels:
            self.lbl_lib_summary.value = self.t("library_empty")
        else:
            drive_parts = [
                self.t("library_drive_piece", drive=d, n=n)
                for d, n in sorted(drive_counts.items())
            ]
            self.lbl_lib_summary.value = self.t(
                "library_summary_drives",
                drives=" · ".join(drive_parts),
                channels=len(self._library_channels),
                local=local_sum,
                missing=str(miss_sum) if any_gap else "?",
            )
        for ch in self._library_channels:
            remote = (
                self.t("library_remote_unknown")
                if ch.remote_count < 0
                else str(ch.remote_count)
            )
            miss = self._library_missing_label(ch)
            name = ch.display_name or ch.username
            if ch.error:
                name = f"{name} ⚠"
            drive_lbl = ch.drive or path_drive(ch.folder) or "?"
            miss_color = _OK
            if miss not in ("0", "?", self.t("library_missing_zero")) and "private" not in miss.lower():
                miss_color = _PAUSE
            elif "private" in miss.lower() or "toàn private" in miss.lower():
                miss_color = _OK
            row = ft.Container(
                content=ft.Row(
                    [
                        ft.Text(name, expand=3, size=13, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(drive_lbl, width=64, size=12, text_align=ft.TextAlign.CENTER),
                        ft.Text(str(ch.local_count), width=56, size=12, text_align=ft.TextAlign.CENTER),
                        ft.Text(remote, width=56, size=12, text_align=ft.TextAlign.CENTER),
                        ft.Text(miss, expand=2, size=12, color=miss_color, max_lines=1),
                        ft.Row(
                            [
                                ft.OutlinedButton(
                                    self.t("btn_library_open"),
                                    height=32,
                                    on_click=lambda _e, u=ch.username: self._library_open_channel(u),
                                ),
                                ft.FilledButton(
                                    self.t("btn_library_update_one"),
                                    height=32,
                                    bgcolor=_GREEN,
                                    on_click=lambda _e, u=ch.username: self._library_update_one(u),
                                ),
                            ],
                            width=200,
                            spacing=6,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            )
            self.lib_list.controls.append(row)

    def _library_refresh(self, *, auto: bool = False) -> None:
        if self._busy or self._library_checking:
            return
        self._save_settings_from_ui()
        self._library_load_local()
        if not self._library_channels:
            if not auto:
                self._alert(self.t("library_empty"))
            return
        try:
            page_delay = float(self.config_data.scan_delay_sec)
        except (TypeError, ValueError):
            page_delay = 1.0
        check_delay = max(0.8, min(3.0, page_delay if page_delay > 0 else 1.0))
        self._stop.clear()
        self._pause_gate.set()
        self._library_checking = True
        self._set_busy(True)
        self._log(self.t("msg_library_refreshing"))
        channels = list(self._library_channels)
        total = len(channels)

        def worker() -> None:
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
                    gap = api.get_channel_gap_stats(ch.username, local_ids)
                    ch.remote_count = int(gap.get("total") or 0)
                    ch.display_name = str(gap.get("display_name") or ch.username)
                    ch.missing_public = int(gap.get("missing_public") or 0)
                    ch.missing_private = int(gap.get("missing_private") or 0)
                    ch.error = ""
                except Exception as exc:  # noqa: BLE001
                    ch.error = str(exc)
                    self._ui_queue.put(("log", f"⚠ {ch.username}: {exc}"))
                updated.append(ch)
                self._ui_queue.put(("library_row", ch))
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
        if self._busy:
            return
        self.channel_query.value = username
        self.channel_limit.value = "0"
        self._switch_to_channel_tab()
        self._log(self.t("msg_library_open", name=username))
        self._library_auto_select_new = True
        self.page.update()
        self._start_scan("channel")

    def _library_update_one(self, username: str) -> None:
        if self._busy:
            return
        self._log(self.t("msg_library_update_one", name=username))
        self.channel_query.value = username
        self.channel_limit.value = "0"
        self._library_auto_select_new = True
        self._switch_to_channel_tab()
        self.page.update()
        self._start_scan("channel")

    def _library_update_all(self) -> None:
        if self._busy:
            return
        if not self._library_channels:
            self._library_load_local()
        names = [c.username for c in self._library_channels]
        if not names:
            self._alert(self.t("msg_library_none"))
            return
        self.channel_query.value = ", ".join(names)
        self.channel_limit.value = "0"
        self._library_auto_select_new = True
        self._switch_to_channel_tab()
        self._log(self.t("msg_library_update_all", count=len(names)))
        self.page.update()
        self._start_scan("channel")

    def _on_login_click(self) -> None:
        if self._busy:
            return
        self._save_settings_from_ui()
        email = (self.entry_email.value or "").strip()
        password = self.entry_password.value or ""
        if not email or not password:
            self.lbl_login_status.value = self.t("login_missing")
            self.lbl_login_status.color = _ERR
            self._log(self.t("login_missing"))
            self.page.update()
            return

        self.btn_login.disabled = True
        self.lbl_login_status.value = self.t("login_checking")
        self.lbl_login_status.color = _PAUSE
        self._log(self.t("login_checking"))
        self.page.update()

        def worker() -> None:
            api = IwaraAPI(demo_mode=False, email=email, password=password)
            try:
                ok, detail = api.test_login()
                self._ui_queue.put(("login_result", {"ok": ok, "detail": detail}))
            except Exception as exc:  # noqa: BLE001
                self._ui_queue.put(
                    ("login_result", {"ok": False, "detail": str(exc)})
                )

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ progress UI
    def _update_segment_bar(
        self, *, ok: int, err: int, total: int, file_pct: float = 0.0
    ) -> None:
        total = max(1, int(total)) if (ok or err or file_pct) else 0
        if total <= 0:
            self.seg_ok.visible = False
            self.seg_err.visible = False
            self.seg_rest.expand = 1
            return

        ok_r = max(0.0, ok / total)
        partial = max(0.0, min(1.0, file_pct)) / total if file_pct > 0 else 0.0
        green_r = min(1.0, ok_r + partial)
        err_r = max(0.0, min(1.0 - green_r, err / total))
        rest_r = max(0.0, 1.0 - green_r - err_r)

        # Use flex expand proportional (scale *100)
        def flex(r: float) -> int:
            return max(1, int(round(r * 1000))) if r > 0.001 else 0

        g, er, rs = flex(green_r), flex(err_r), flex(rest_r)
        self.seg_ok.visible = g > 0
        self.seg_err.visible = er > 0
        self.seg_ok.expand = g if g else 0
        self.seg_err.expand = er if er else 0
        self.seg_rest.expand = rs if rs else 1
        self.seg_rest.visible = True

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

        ok_text = self.t("progress_ok_part", ok=ok, total=total)
        err_text = self.t("progress_err_part", err=err)
        if done:
            extra = self.t("progress_done_extra")
        elif title:
            short = title if len(title) <= 28 else title[:26] + "…"
            if server:
                extra = self.t(
                    "progress_live_server",
                    speed=speed_txt,
                    server=server,
                    title=short,
                )
            else:
                extra = self.t("progress_live_extra", speed=speed_txt, title=short)
        else:
            extra = self.t("progress_speed_extra", speed=speed_txt)

        if done:
            if err == 0 and ok > 0:
                pct_color = _OK
            elif ok == 0 and err > 0:
                pct_color = _ERR
            elif err > 0:
                pct_color = _PAUSE
            else:
                pct_color = None
        else:
            pct_color = None

        self.lbl_prog_ok.value = ok_text
        self.lbl_prog_ok.color = _OK
        self.lbl_prog_err.value = err_text
        self.lbl_prog_err.color = _ERR
        self.lbl_progress.value = extra
        self.lbl_progress.color = _MUTED
        self.lbl_progress_pct.value = f"{percent}%"
        self.lbl_progress_pct.color = pct_color
        self._update_segment_bar(
            ok=ok,
            err=err,
            total=total if total else 1,
            file_pct=0.0 if done else file_pct,
        )

    # ------------------------------------------------------------------ queue poll
    async def _poll_loop(self) -> None:
        while True:
            dirty = False
            try:
                while True:
                    kind, payload = self._ui_queue.get_nowait()
                    self._handle_event(kind, payload)
                    dirty = True
            except queue.Empty:
                pass
            if dirty:
                try:
                    self.page.update()
                except Exception:
                    pass
            await asyncio.sleep(0.08)

    def _handle_event(self, kind: str, payload: Any) -> None:
        if kind == "scan_meta":
            if payload.get("source"):
                self._scan_source = str(payload["source"])
            if payload.get("query"):
                q = str(payload["query"]).strip().lstrip("#@")
                if self._scan_source == "channel" and (
                    "," in (self._scan_query or "") or "，" in (self._scan_query or "")
                ):
                    pass
                else:
                    self._scan_query = q
        elif kind == "scan_batch":
            batch = payload.get("items") or []
            page = int(payload.get("page") or 0)
            self._append_scan_batch(batch, page)
        elif kind == "scan_done":
            if isinstance(payload, dict):
                count = int(
                    payload.get("count")
                    or len(payload.get("items") or [])
                    or len(self._videos)
                )
                if payload.get("source"):
                    self._scan_source = str(payload["source"])
                if payload.get("query"):
                    self._scan_query = str(payload["query"])
            else:
                count = len(self._videos)
            folder = self._current_target_dir()
            already = sum(
                1 for v in self._videos if v.status == VideoStatus.ON_DISK
            )
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
            self._log(self.t("msg_scan_done", count=count))
            self._log(
                self.t(
                    "msg_scan_local",
                    folder=str(folder),
                    already=already,
                    new=new,
                )
            )
            if priv:
                self._log(self.t("msg_skip_private_download", count=priv))
            if getattr(self, "_library_auto_select_new", False):
                self._library_auto_select_new = False
                self._select_new_only()
            self._update_selected_label()
            self._update_segment_bar(ok=1, err=0, total=1, file_pct=0.0)
            self.lbl_progress_pct.value = "100%"
        elif kind == "library_progress":
            cur = int(payload.get("current") or 0)
            tot = int(payload.get("total") or 0)
            name = str(payload.get("name") or "")
            self.lbl_lib_progress.value = self.t(
                "library_progress_checking",
                current=cur,
                total=tot,
                name=name[:28],
            )
            ratio = min(0.99, max(0.0, (cur - 0.2) / tot)) if tot else 0
            self.lib_progress.value = ratio
            try:
                self.lbl_lib_progress_pct.value = f"{int(ratio * 100)}%"
            except Exception:
                pass
        elif kind == "library_row":
            ch: LocalChannelInfo = payload
            for i, existing in enumerate(self._library_channels):
                if existing.username == ch.username:
                    self._library_channels[i] = ch
                    break
            else:
                self._library_channels.append(ch)
            self._library_render_list()
        elif kind == "library_done":
            self._library_checking = False
            channels = payload.get("channels") if isinstance(payload, dict) else None
            if channels is not None:
                self._library_channels = list(channels)
            self._library_render_list()
            n = int(payload.get("count") or len(self._library_channels))
            stopped = bool(payload.get("stopped")) if isinstance(payload, dict) else False
            if stopped:
                self.lbl_lib_progress.value = self.t(
                    "library_progress_stopped",
                    current=n,
                    total=int(payload.get("total") or n),
                )
            else:
                self._library_remote_checked = True
                self.lbl_lib_progress.value = self.t(
                    "library_progress_done", count=n
                )
                self.lib_progress.value = 1.0
                try:
                    self.lbl_lib_progress_pct.value = "100%"
                except Exception:
                    pass
                self._log(self.t("msg_library_refreshed", count=n))
        elif kind == "library_reload":
            self._library_load_local()
        elif kind == "log":
            self._log(str(payload))
        elif kind == "thumb":
            self._apply_thumb(
                payload["id"], payload.get("img"), payload.get("gen", 0)
            )
        elif kind == "row_update":
            idx, item = payload
            if 0 <= idx < len(self._videos):
                self._videos[idx] = item
                self._update_row_status(idx, item)
        elif kind == "dl_start":
            self._log(
                self.t(
                    "log_dl_start",
                    n=payload.get("n"),
                    total=payload.get("total"),
                    title=payload.get("title") or "",
                )
            )
            self._set_overall_progress(
                ok=int(payload.get("ok") or 0),
                err=int(payload.get("err") or 0),
                total=int(payload.get("total") or 0),
                ratio=0.0,
                speed=0.0,
                title=str(payload.get("title") or ""),
                file_pct=0.0,
                done=False,
            )
        elif kind == "dl_live":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            n = int(payload.get("n") or 0)
            speed = float(payload.get("speed") or 0)
            file_pct = float(payload.get("file_pct") or 0)
            title = str(payload.get("title") or "")
            status = str(payload.get("status") or "downloading")
            server = str(payload.get("server") or "")
            vid = str(payload.get("id") or "")
            if vid and server:
                self._dl_server_by_id[vid] = server
            if vid:
                for i, v in enumerate(self._videos):
                    if v.id == vid:
                        v.progress = max(v.progress, file_pct)
                        if status == "downloading":
                            v.status = VideoStatus.DOWNLOADING
                        self._update_row_status(i, v)
                        break
            finished = ok + err
            ratio = (
                min(1.0, (finished + file_pct) / total) if total else file_pct
            )
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=ratio,
                speed=speed,
                title=title,
                file_pct=file_pct,
                done=False,
                server=server,
            )
            if payload.get("log"):
                done_b = int(payload.get("downloaded") or 0)
                tot_b = int(payload.get("total_bytes") or 0)
                if tot_b > 0:
                    size_txt = f"{format_bytes(done_b)}/{format_bytes(tot_b)}"
                elif done_b > 0:
                    size_txt = format_bytes(done_b)
                else:
                    size_txt = "—"
                self._log(
                    self.t(
                        "log_dl_live",
                        n=n,
                        total=total,
                        title=title[:50],
                        status=status,
                        file_pct=int(round(file_pct * 100)),
                        speed=format_speed(speed),
                        size=size_txt,
                        server=str(payload.get("server") or "—"),
                    )
                )
        elif kind == "dl_end":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            n = int(payload.get("n") or 0)
            title = str(payload.get("title") or "")
            kind_end = payload.get("kind")
            if kind_end == "ok":
                self._log(
                    self.t(
                        "log_dl_ok",
                        n=n,
                        total=total,
                        title=title[:60],
                        speed="—",
                    )
                )
            elif kind_end == "skip":
                self._log(
                    self.t(
                        "log_dl_skip",
                        n=n,
                        total=total,
                        title=title[:60],
                    )
                )
            else:
                detail = str(payload.get("err_detail") or payload.get("error") or "")
                advice = str(payload.get("err_advice") or "")
                err_title = str(payload.get("err_title") or "Lỗi")
                self._log(
                    self.t(
                        "log_dl_err",
                        n=n,
                        total=total,
                        title=f"[{err_title}] {title[:50]}",
                        detail=detail[:300],
                        advice=advice[:300] if advice else "—",
                    )
                )
            ratio = (ok + err) / total if total else 1.0
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=ratio,
                speed=0.0,
                title="",
                done=False,
            )
        elif kind == "progress":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or 0)
            ratio = float(
                payload.get("ratio")
                if payload.get("ratio") is not None
                else ((ok + err) / total if total else 0)
            )
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=ratio,
                speed=float(payload.get("speed") or 0),
                title=str(payload.get("title") or ""),
                file_pct=float(payload.get("file_pct") or 0),
                done=False,
            )
        elif kind == "delay":
            self._log(self.t("msg_waiting_delay", sec=payload))
        elif kind == "download_done":
            ok = int(payload.get("ok") or 0)
            err = int(payload.get("err") or 0)
            total = int(payload.get("total") or (ok + err))
            current = int(payload.get("current") or (ok + err))
            self._set_overall_progress(
                ok=ok,
                err=err,
                total=total,
                ratio=1.0
                if total and current >= total
                else (current / total if total else 1.0),
                speed=0.0,
                title="",
                done=True,
            )
            self._log(self.t("msg_download_done", ok=ok, err=err))
            for i, item in enumerate(self._videos):
                self._update_row_status(i, item)
        elif kind == "login_result":
            self.btn_login.disabled = False
            ok = bool(payload.get("ok"))
            detail = str(payload.get("detail") or "")
            if ok:
                text = self.t("login_ok", name=detail)
                self.lbl_login_status.value = text
                self.lbl_login_status.color = _OK
                self._log(text)
                self._alert(text)
            else:
                if detail == "missing_credentials":
                    text = self.t("login_missing")
                else:
                    err = detail.replace("Đăng nhập thất bại: ", "")
                    if len(err) > 160:
                        err = err[:160] + "…"
                    text = self.t("login_fail", error=err)
                self.lbl_login_status.value = text
                self.lbl_login_status.color = _ERR
                self._log(text)
                self._alert(text, error=True)
        elif kind == "error":
            self._log(f"ERROR: {payload}")
            console_error(str(payload))
            self._alert(str(payload), error=True)
        elif kind == "idle":
            self._library_checking = False
            self._set_busy(False)
            if self._stop.is_set():
                self.lbl_prog_ok.value = self.t("progress_idle")
                self.lbl_prog_ok.color = _MUTED
                self.lbl_prog_err.value = ""
                self.lbl_progress.value = ""


async def _focus_window(page: ft.Page) -> None:
    """Bring to front, then release always-on-top so it does not stay sticky."""
    try:
        page.window.visible = True
        page.window.minimized = False
        page.window.left = 80
        page.window.top = 60
        page.window.focused = True
        page.window.always_on_top = True
        page.update()
        await page.window.to_front()
        await asyncio.sleep(0.8)
        # Unpin after user has had a chance to see the window
        page.window.always_on_top = False
        page.update()
    except Exception as exc:
        console_error("focus window", exc)


def main(page: ft.Page) -> None:
    try:
        app = IwaraApp(page)
        page.run_task(_focus_window, page)
        console_log("GUI ready — check taskbar if window is behind other apps.")
        _ = app
    except Exception as exc:  # noqa: BLE001
        console_error("UI failed to build", exc)
        try:
            page.controls.clear()
            page.add(
                ft.Text("Iwara Downloader — UI error", size=20, weight=ft.FontWeight.BOLD),
                ft.Text(str(exc), selectable=True),
                ft.Text("See last_error.txt in the app folder.", color=_MUTED),
            )
            page.update()
        except Exception:
            pass
        _write_crash(exc)
        raise


def _write_crash(exc: BaseException) -> None:
    try:
        from app.core.config import APP_DIR
        import traceback

        path = APP_DIR / "last_error.txt"
        path.write_text(
            traceback.format_exc() + "\n" + repr(exc),
            encoding="utf-8",
        )
        console_log(f"Crash log written: {path}")
    except Exception:
        pass


def run_app() -> None:
    console_log("Starting Flet (Flutter/GPU) UI…")
    # Mark backend as flet so settings stay consistent
    try:
        cfg = AppConfig.load()
        if cfg.ui_backend != "flet":
            cfg.ui_backend = "flet"
            cfg.save()
        apply_windows_gpu_preference(cfg.ui_gpu or GPU_AUTO)
    except Exception:
        pass
    # Force native desktop window (not browser)
    ft.app(
        target=main,
        view=ft.AppView.FLET_APP,
        name="Iwara Downloader",
    )
