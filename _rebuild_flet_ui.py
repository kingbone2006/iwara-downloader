# -*- coding: utf-8 -*-
"""Replace IwaraApp._build + _on_tab_change with a stable CTk-like Flet layout."""
from pathlib import Path

path = Path(__file__).resolve().parent / "app" / "ui" / "app_flet.py"
text = path.read_text(encoding="utf-8")

start = text.index("    def _build(self) -> None:")
# replace through _switch_to_channel_tab method end, keep _refresh_texts
end = text.index("    def _refresh_texts(self) -> None:")

new = r'''    def _build(self) -> None:
        """
        CTk-like layout (matches user's CustomTkinter screenshots):
          [Header]
          [Tab buttons]
          [Active panel only — fixed height, no expand fight]
          [Action bar]
          [Folder + batch/delay/stall]
          [Column headers + page nav]
          [Video ListView — only expanding region]
          [Progress]
          [Log fixed height]
        """
        t = self.t
        self._tab_index = 0
        self._page = 0
        self._page_size = max(
            20, min(200, int(getattr(self.config_data, "list_page_size", 60) or 60))
        )

        # ---------- header ----------
        self.lang_dd = ft.Dropdown(
            width=160,
            dense=True,
            value=self.i18n.lang,
            options=[
                ft.DropdownOption(key=c, text=LANG_LABELS[c]) for c in SUPPORTED_LANGS
            ],
            on_select=self._on_language_change,
            text_size=13,
        )
        self.lbl_title = ft.Text(
            t("app_title"), size=18, weight=ft.FontWeight.BOLD, expand=True
        )
        self.hdr_lang_label = ft.Text(t("language"), size=13, color=_MUTED)
        header = ft.Row(
            [self.lbl_title, self.hdr_lang_label, self.lang_dd],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # ---------- tab strip (SegmentedButton = reliable, no TabBarView bugs) ----------
        self.tab_seg = ft.SegmentedButton(
            selected={"0"},
            allow_multiple_selection=False,
            segments=[
                ft.Segment(value="0", label=ft.Text(t("tab_hashtag"))),
                ft.Segment(value="1", label=ft.Text(t("tab_channel"))),
                ft.Segment(value="2", label=ft.Text(t("tab_library"))),
                ft.Segment(value="3", label=ft.Text(t("tab_settings"))),
            ],
            on_change=self._on_seg_tab,
        )
        # Keep old attrs for _refresh_texts compatibility
        self.tab_bar = self.tab_seg
        self.tabs = self.tab_seg  # selected_index shim via property methods

        # ---------- hashtag panel ----------
        self.hashtag_query = ft.TextField(
            hint_text=t("hashtag_placeholder"),
            expand=True,
            dense=True,
            height=42,
            on_submit=lambda _e: self._start_scan("hashtag"),
        )
        self.hashtag_limit = ft.TextField(
            value=str(self.config_data.scan_limit),
            width=64,
            dense=True,
            height=42,
            text_align=ft.TextAlign.CENTER,
        )
        self.btn_scan_hashtag = ft.FilledButton(
            t("btn_scan"),
            height=42,
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
                        ft.Text(t("hashtag_label"), width=90, size=13),
                        self.hashtag_query,
                        ft.Text(t("limit_label"), size=12, width=120),
                        self.hashtag_limit,
                        self.btn_scan_hashtag,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.hashtag_hint,
            ],
            spacing=4,
            tight=True,
        )

        # ---------- channel panel ----------
        self.channel_query = ft.TextField(
            hint_text=t("channel_placeholder"),
            expand=True,
            dense=True,
            height=42,
            on_submit=lambda _e: self._start_scan("channel"),
        )
        self.channel_limit = ft.TextField(
            value=str(self.config_data.scan_limit),
            width=64,
            dense=True,
            height=42,
            text_align=ft.TextAlign.CENTER,
        )
        self.btn_scan_channel = ft.FilledButton(
            t("btn_scan"),
            height=42,
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
                        ft.Text(t("channel_label"), width=130, size=13),
                        self.channel_query,
                        ft.Text(t("limit_label"), size=12, width=120),
                        self.channel_limit,
                        self.btn_scan_channel,
                    ],
                    spacing=8,
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
                ft.Text(t("library_col_channel"), expand=3, size=12, weight=ft.FontWeight.W_600),
                ft.Text(t("library_col_local"), width=64, size=12, weight=ft.FontWeight.W_600),
                ft.Text(t("library_col_remote"), width=64, size=12, weight=ft.FontWeight.W_600),
                ft.Text(t("library_col_new"), expand=2, size=12, weight=ft.FontWeight.W_600),
                ft.Text(t("library_col_actions"), width=180, size=12, weight=ft.FontWeight.W_600),
            ],
            spacing=6,
        )
        self.lib_list = ft.ListView(spacing=3, padding=2, height=220)
        self.panel_library = ft.Column(
            [
                ft.Row(
                    [self.btn_lib_refresh, self.btn_lib_update_all, self.lbl_lib_summary],
                    spacing=10,
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

        # ---------- settings panel (CTk form rows) ----------
        self.entry_dl_dir = ft.TextField(
            value=self.config_data.download_dir,
            expand=True,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_dl_batch = ft.TextField(
            value=str(self.config_data.download_batch),
            width=80,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_dl_delay = ft.TextField(
            value=str(self.config_data.download_delay_sec),
            width=80,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_stall = ft.TextField(
            value=str(int(self.config_data.stall_timeout_sec)),
            width=80,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_scan_delay = ft.TextField(
            value=str(self.config_data.scan_delay_sec),
            width=80,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.theme_dd = ft.Dropdown(
            width=140,
            dense=True,
            value=self.config_data.theme,
            options=[
                ft.DropdownOption(key="dark", text=t("theme_dark")),
                ft.DropdownOption(key="light", text=t("theme_light")),
            ],
            on_select=self._on_theme_change,
        )
        self.demo_sw = ft.Checkbox(
            label=t("demo_mode"),
            value=self.config_data.demo_mode,
            on_change=lambda _e: self._save_settings_from_ui(),
        )
        self.ui_backend_dd = ft.Dropdown(
            width=300,
            dense=True,
            value=self.config_data.ui_backend or "flet",
            options=[
                ft.DropdownOption(key="tk", text=t("ui_backend_tk")),
                ft.DropdownOption(key="flet", text=t("ui_backend_flet")),
            ],
            on_select=self._on_ui_backend_change,
        )
        self.ui_gpu_dd = ft.Dropdown(
            width=300,
            dense=True,
            value=self.config_data.ui_gpu or GPU_AUTO,
            options=[
                ft.DropdownOption(key=GPU_AUTO, text=t("ui_gpu_auto")),
                ft.DropdownOption(key=GPU_HIGH, text=t("ui_gpu_high")),
                ft.DropdownOption(key=GPU_SAVE, text=t("ui_gpu_save")),
            ],
            on_select=self._on_ui_gpu_change,
        )
        self.entry_page_size = ft.TextField(
            value=str(getattr(self.config_data, "list_page_size", 60)),
            width=80,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        st = cache_stats()
        self.lbl_cache_status = ft.Text(
            t("msg_cache_stats", files=st["files"], mb=st["mb"]),
            size=12,
            color=_MUTED,
        )
        self.btn_clear_cache = ft.Button(
            t("btn_clear_cache"),
            bgcolor=_AMBER,
            color=ft.Colors.WHITE,
            height=36,
            on_click=lambda _e: self._on_clear_cache(),
        )
        self.entry_email = ft.TextField(
            value=self.config_data.email, expand=True, dense=True, height=40
        )
        self.entry_password = ft.TextField(
            value=self.config_data.password,
            password=True,
            can_reveal_password=True,
            expand=True,
            dense=True,
            height=40,
        )
        self.btn_login = ft.FilledButton(
            t("btn_login"), bgcolor=_BLUE, height=36, on_click=lambda _e: self._on_login_click()
        )
        self.lbl_login_status = ft.Text(t("login_status_idle"), size=13, color=_MUTED)
        self.lbl_login_hint = ft.Text(t("login_hint"), size=11, color=_MUTED)
        self.lbl_settings_hint = ft.Text(t("settings_hint"), size=11, color=_MUTED)

        self.panel_settings = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(t("download_dir"), width=190, size=13),
                        self.entry_dl_dir,
                        ft.FilledButton(
                            t("btn_browse"),
                            height=36,
                            on_click=lambda _e: self.page.run_task(self._browse_dir),
                        ),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("download_batch_label"), width=190, size=13), self.entry_dl_batch],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("download_delay_label"), width=190, size=13), self.entry_dl_delay],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("stall_timeout_label"), width=190, size=13), self.entry_stall],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("scan_delay_label"), width=190, size=13), self.entry_scan_delay],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("theme"), width=190, size=13), self.theme_dd],
                    spacing=8,
                ),
                self.demo_sw,
                ft.Row(
                    [ft.Text(t("ui_backend_label"), width=190, size=13), self.ui_backend_dd],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("ui_gpu_label"), width=190, size=13), self.ui_gpu_dd],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("list_page_size_label"), width=190, size=13), self.entry_page_size],
                    spacing=8,
                ),
                ft.Row([self.btn_clear_cache, self.lbl_cache_status], spacing=12),
                ft.Row(
                    [ft.Text(t("login_email"), width=190, size=13), self.entry_email],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text(t("login_password"), width=190, size=13), self.entry_password],
                    spacing=8,
                ),
                ft.Row([self.btn_login, self.lbl_login_status], spacing=12),
                self.lbl_login_hint,
                self.lbl_settings_hint,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            height=300,
            tight=True,
        )

        # Host for active top panel (swapped by tab)
        self.panel_host = ft.Container(
            content=self.panel_hashtag,
            padding=ft.Padding.symmetric(horizontal=4, vertical=6),
        )

        # ---------- shared bottom (always visible) ----------
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
            t("btn_pause"),
            bgcolor="#6b5b2e",
            color=ft.Colors.WHITE,
            height=34,
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
        self.btn_download = ft.FilledButton(
            t("btn_download"),
            bgcolor=_GREEN,
            height=34,
            on_click=lambda _e: self._start_download(),
        )
        self.action_bar = ft.Row(
            [
                self.lbl_found,
                self.lbl_selected,
                self.lbl_size_summary,
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
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.entry_dl_dir_bar = ft.TextField(
            value=self.config_data.download_dir,
            expand=True,
            dense=True,
            height=40,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_dl_batch_bar = ft.TextField(
            value=str(self.config_data.download_batch),
            width=56,
            dense=True,
            height=40,
            text_align=ft.TextAlign.CENTER,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_dl_delay_bar = ft.TextField(
            value=str(self.config_data.download_delay_sec),
            width=56,
            dense=True,
            height=40,
            text_align=ft.TextAlign.CENTER,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.entry_stall_bar = ft.TextField(
            value=str(int(self.config_data.stall_timeout_sec)),
            width=56,
            dense=True,
            height=40,
            text_align=ft.TextAlign.CENTER,
            on_blur=lambda _e: self._save_settings_from_ui(),
        )
        self.folder_bar = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(t("download_dir"), size=13, weight=ft.FontWeight.W_600, width=100),
                        self.entry_dl_dir_bar,
                        ft.FilledButton(
                            t("btn_browse"),
                            height=36,
                            on_click=lambda _e: self.page.run_task(self._browse_dir),
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
                ),
                ft.Row(
                    [
                        ft.Text(t("download_batch_label"), size=12),
                        self.entry_dl_batch_bar,
                        ft.Text(t("download_delay_label"), size=12),
                        self.entry_dl_delay_bar,
                        ft.Text(t("stall_timeout_label"), size=12),
                        self.entry_stall_bar,
                    ],
                    spacing=10,
                    wrap=True,
                ),
            ],
            spacing=6,
            tight=True,
        )

        self.header_row = ft.Container(
            content=ft.Row(
                [
                    ft.Text(t("col_select"), width=36, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_thumb"), width=thumb_mod.THUMB_W, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_title"), expand=3, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_author"), expand=2, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_size"), width=70, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_progress"), expand=2, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_status"), expand=2, size=12, weight=ft.FontWeight.W_600),
                    ft.Text(t("col_action"), width=72, size=12, weight=ft.FontWeight.W_600),
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
        self.page_bar = ft.Row([self.btn_page_prev, self.btn_page_next, self.lbl_page], spacing=8)

        self.list_view = ft.ListView(expand=True, spacing=2, padding=2, auto_scroll=False)

        self.lbl_prog_ok = ft.Text(
            t("progress_idle"), size=13, color=_MUTED, weight=ft.FontWeight.BOLD
        )
        self.lbl_prog_err = ft.Text("", size=13, color=_ERR, weight=ft.FontWeight.BOLD)
        self.lbl_progress = ft.Text("", size=12, color=_MUTED, expand=True)
        self.lbl_progress_pct = ft.Text("0%", size=13, weight=ft.FontWeight.BOLD, width=44)
        self.seg_ok = ft.Container(bgcolor=_GREEN, expand=0, height=10, border_radius=4)
        self.seg_err = ft.Container(bgcolor=_RED, expand=0, height=10, border_radius=4)
        self.seg_rest = ft.Container(
            bgcolor=ft.Colors.OUTLINE_VARIANT, expand=1, height=10, border_radius=4
        )
        self.seg_ok.visible = False
        self.seg_err.visible = False
        self.progress_seg = ft.Row(
            [self.seg_ok, self.seg_err, self.seg_rest], spacing=0, height=10, expand=True
        )
        self.progress_block = ft.Column(
            [
                ft.Row(
                    [self.lbl_prog_ok, self.lbl_prog_err, self.lbl_progress, self.lbl_progress_pct],
                    spacing=8,
                ),
                self.progress_seg,
            ],
            spacing=3,
            tight=True,
        )

        self.lbl_log = ft.Text(t("log_title"), size=13, weight=ft.FontWeight.W_600)
        self.log_box = ft.TextField(
            multiline=True,
            min_lines=2,
            max_lines=3,
            read_only=True,
            text_size=12,
            height=72,
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
                shim._app._tab_index = int(v)
                try:
                    shim._app.tab_seg.selected = {str(int(v))}
                except Exception:
                    pass
                shim._app._show_tab(int(v))

        self.tabs = _TabsShim(self)

        # Root: only list_view expands
        self.page.add(
            ft.Column(
                [
                    header,
                    ft.Row([self.tab_seg], alignment=ft.MainAxisAlignment.START),
                    self.panel_host,
                    ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                    self.action_bar,
                    self.folder_bar,
                    self.header_row,
                    self.page_bar,
                    self.list_view,
                    self.progress_block,
                    self.lbl_log,
                    self.log_box,
                ],
                expand=True,
                spacing=6,
            )
        )
        self._show_tab(0)
        self._update_page_label()

    def _on_seg_tab(self, e: ft.ControlEvent) -> None:
        try:
            sel = e.control.selected
            if isinstance(sel, (set, list, tuple)) and sel:
                idx = int(next(iter(sel)))
            else:
                idx = int(sel or 0)
        except Exception:
            idx = 0
        self._tab_index = idx
        self._show_tab(idx)
        self.page.update()

    def _show_tab(self, idx: int) -> None:
        """Swap top panel only; bottom list chrome stays (like CTk)."""
        self._tab_index = idx
        if idx == 0:
            self.panel_host.content = self.panel_hashtag
            self.btn_select_new.visible = False
        elif idx == 1:
            self.panel_host.content = self.panel_channel
            self.btn_select_new.visible = True
        elif idx == 2:
            self.panel_host.content = self.panel_library
            self.btn_select_new.visible = self._scan_source == "channel"
            self._library_on_enter()
        else:
            self.panel_host.content = self.panel_settings
            self.btn_select_new.visible = False

    def _on_tab_change(self, _e: ft.ControlEvent | None = None) -> None:
        # Back-compat if something still fires this
        idx = getattr(self, "_tab_index", 0)
        self._show_tab(idx)
        try:
            self.page.update()
        except Exception:
            pass

    def _switch_to_channel_tab(self) -> None:
        self._tab_index = 1
        try:
            self.tab_seg.selected = {"1"}
        except Exception:
            pass
        self._show_tab(1)
        self.btn_select_new.visible = True

'''

# Fix the invalid "srow :=" leftover if any - the new code is clean
path.write_text(text[:start] + new + text[end:], encoding="utf-8")
print("patched OK")
