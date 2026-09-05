"""主窗口 UI - 现代版（CustomTkinter）

设计语言：深色卡片式界面，圆角控件、强调色渐变、清晰的信息层级。
支持浅/深色主题切换；视频封面缩略图异步加载；图集识别与批量图片下载。
"""

import os
import re
import queue
import threading
import concurrent.futures
from pathlib import Path
from typing import Optional, List, Dict, Any

import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import customtkinter as ctk
from PIL import Image

from core.extractor import VideoExtractor
from core.downloader import DownloadManager, DownloadTask
from utils.helpers import (
    format_size, format_speed, format_duration, format_eta,
    load_settings, save_settings, is_url,
)

# ── 设计令牌 ──────────────────────────────────────────────────
DARK = {
    "bg": "#0e1116",
    "card": "#161b22",
    "card2": "#1c222c",
    "border": "#2a313c",
    "accent": "#6c5ce7",
    "accent2": "#3b82f6",
    "text": "#e6e9ef",
    "sub": "#8b93a3",
    "success": "#2dd972",
    "warning": "#f5b545",
    "danger": "#f4574d",
}
LIGHT = {
    "bg": "#f3f5f9",
    "card": "#ffffff",
    "card2": "#f6f8fb",
    "border": "#e3e7ee",
    "accent": "#5b4be0",
    "accent2": "#2f7df6",
    "text": "#1b2430",
    "sub": "#67707f",
    "success": "#16a34a",
    "warning": "#d97706",
    "danger": "#dc2626",
}

FONT_FAMILY = "Microsoft YaHei UI"


def _palette(appearance: str) -> dict:
    return LIGHT if appearance == "Light" else DARK


class MainWindow:
    """主窗口"""

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("视频爬虫 · Modern")
        self.root.geometry("1280x820")
        self.root.minsize(1040, 700)

        # 设置
        self.settings_path = str(Path.home() / ".video_crawler" / "settings.json")
        self.settings = load_settings(self.settings_path)
        self._override_setting_defaults()

        # 核心引擎
        self._cookies_file = self.settings.get("cookies_file") or None
        self._proxy = self.settings.get("proxy") or None
        self.extractor = VideoExtractor(cookies_file=self._cookies_file,
                                        proxy=self._proxy)
        self.download_manager = DownloadManager(
            extractor=self.extractor,
            max_concurrent=int(self.settings.get("max_concurrent", 3)),
        )

        # 状态
        self._video_items: List[dict] = []
        self._video_meta: Dict[int, dict] = {}
        self._selected_indices: set = set()
        self._extracting = False
        self._current_quality = self.settings.get(
            "default_quality", "bestvideo+bestaudio/best")
        self._search_mode = False
        self._thumb_cache: Dict[str, ctk.CTkImage] = {}
        self._queue_rows: Dict[int, dict] = {}
        self._list_added = False

        # 外观
        ctk.set_appearance_mode(self.settings.get("appearance", "Dark"))
        ctk.set_default_color_theme("blue")

        self._build_menu()
        self._build_layout()
        self._bind_events()

        # 静默 CustomTkinter 销毁含 CTkButton 树时已知的 focus TclError 噪音
        # （主题切换会重建界面，销毁时挂起的 focus 回调指向已销毁 canvas）
        def _report(exc, val, tb):
            msg = str(val)
            if "invalid command name" in msg and "ctkcanvas" in msg:
                return
            import traceback
            traceback.print_exception(exc, val, tb)

        self.root.report_callback_exception = _report

        # 轮询
        self._poll_downloads()
        self._poll_progress_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _override_setting_defaults(self):
        """兼容旧设置文件中不存在的键"""
        s = self.settings
        s.setdefault("appearance", "Dark")
        s.setdefault("max_concurrent", 3)
        s.setdefault("download_dir", str(Path(__file__).resolve().parent.parent /
                                         "downloads"))

    # ── 菜单 ──────────────────────────────────────────────────
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="导入 URL 文件...",
                              command=self._import_urls_from_file)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="下载目录...", command=self._change_download_dir)
        settings_menu.add_command(label="Cookie 文件...", command=self._import_cookie)
        settings_menu.add_command(label="一键获取 Cookie（从浏览器）",
                                  command=self._auto_get_cookie)
        settings_menu.add_command(label="代理设置...", command=self._set_proxy)
        settings_menu.add_separator()
        settings_menu.add_command(label="首选项...", command=self._show_settings_dialog)
        menubar.add_cascade(label="设置", menu=settings_menu)

        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label="清除已完成任务", command=self._clear_completed)
        tool_menu.add_command(label="全部取消", command=self._cancel_all)
        menubar.add_cascade(label="工具", menu=tool_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

    # ── 布局构建 ──────────────────────────────────────────────
    def _build_layout(self):
        self._pal = _palette(ctk.get_appearance_mode())
        self.root.configure(fg_color=self._pal["bg"])

        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=(14, 8))
        self._content = outer

        self._build_header(outer)
        self._build_input_card(outer)
        self._build_main_area(outer)
        self._build_queue_area(outer)
        self._build_statusbar(outer)

    def _build_header(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(left, text="视频爬虫", font=(FONT_FAMILY, 22, "bold"),
                     text_color=self._pal["text"]).pack(anchor="w")
        ctk.CTkLabel(left, text="抖音 · 图集 · 哔哩哔哩 · YouTube · 1000+ 站点",
                     font=(FONT_FAMILY, 11),
                     text_color=self._pal["sub"]).pack(anchor="w")

        ctk.CTkButton(header, text="☀ 浅色" if ctk.get_appearance_mode() == "Dark"
                      else "🌙 深色",
                      width=88, height=32,
                      fg_color=self._pal["card"], hover_color=self._pal["card2"],
                      border_width=1, border_color=self._pal["border"],
                      text_color=self._pal["text"],
                      font=(FONT_FAMILY, 12),
                      command=self._toggle_theme).pack(side="right")

    def _build_input_card(self, parent):
        card = ctk.CTkFrame(parent, fg_color=self._pal["card"],
                            corner_radius=14,
                            border_width=1, border_color=self._pal["border"])
        card.pack(fill="x", pady=(0, 12))

        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(14, 6))

        # 模式分段切换
        self._mode_seg = ctk.CTkSegmentedButton(
            row1, values=["链接提取", "关键词搜索"], width=150, height=40,
            font=(FONT_FAMILY, 12), command=self._on_mode_change,
            selected_color=self._pal["accent"], selected_hover_color=self._pal["accent"],
            fg_color=self._pal["card2"], unselected_color=self._pal["card2"],
            unselected_hover_color=self._pal["border"],
            text_color=self._pal["text"], corner_radius=10)
        self._mode_seg.set("链接提取")
        self._mode_seg.pack(side="left", padx=(0, 10))

        # 输入框
        self._url_entry = ctk.CTkEntry(
            row1, placeholder_text="粘贴抖音/视频链接（支持整段分享文字）...",
            height=40, corner_radius=10, font=(FONT_FAMILY, 13),
            fg_color=self._pal["card2"], border_color=self._pal["border"],
            border_width=1, text_color=self._pal["text"])
        self._url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # 提取按钮
        self._extract_btn = ctk.CTkButton(
            row1, text="提 取", height=40, width=110, corner_radius=10,
            font=(FONT_FAMILY, 13, "bold"),
            fg_color=self._pal["accent"], hover_color=self._pal["accent2"],
            command=self._extract_videos)
        self._extract_btn.pack(side="left")

        # 第二行：平台 + 目录 + cookie + 代理
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(6, 14))

        self._platform_combo = ctk.CTkComboBox(
            row2, values=["YouTube", "B站搜索", "全网剧集"], width=110,
            height=30, font=(FONT_FAMILY, 11), state="readonly",
            fg_color=self._pal["card2"], border_color=self._pal["border"],
            button_color=self._pal["accent"], text_color=self._pal["text"])
        self._platform_combo.set("YouTube")
        # 链接模式下默认隐藏，切到搜索时显示

        ctk.CTkLabel(row2, text="下载到:",
                     font=(FONT_FAMILY, 11), text_color=self._pal["sub"]).pack(
            side="left", padx=(0, 4))
        self._dir_entry = ctk.CTkEntry(
            row2, width=330, height=30, corner_radius=8,
            font=(FONT_FAMILY, 11),
            placeholder_text="D:/pachong/video_crawler/downloads",
            fg_color=self._pal["card2"], border_color=self._pal["border"],
            text_color=self._pal["text"])
        self._dir_entry.insert(0, self.settings.get(
            "download_dir", "D:/pachong/video_crawler/downloads"))
        self._dir_entry.pack(side="left", padx=(0, 6))

        ctk.CTkButton(row2, text="浏览", width=52, height=30, corner_radius=8,
                      font=(FONT_FAMILY, 11),
                      fg_color=self._pal["card2"],
                      hover_color=self._pal["border"],
                      border_width=1, border_color=self._pal["border"],
                      text_color=self._pal["text"],
                      command=self._change_download_dir).pack(side="left", padx=(0, 4))
        ctk.CTkButton(row2, text="打开目录", width=64, height=30, corner_radius=8,
                      font=(FONT_FAMILY, 11),
                      fg_color=self._pal["card2"],
                      hover_color=self._pal["border"],
                      border_width=1, border_color=self._pal["border"],
                      text_color=self._pal["text"],
                      command=self._open_download_dir).pack(side="left", padx=(0, 12))

        self._cookie_btn = ctk.CTkButton(row2, text=self._cookie_label_text(),
                                         width=118, height=30, corner_radius=8,
                                         font=(FONT_FAMILY, 11),
                                         fg_color=self._pal["card2"],
                                         hover_color=self._pal["border"],
                                         border_width=1,
                                         border_color=self._pal["border"],
                                         text_color=self._pal["text"],
                                         command=self._import_cookie)
        self._cookie_btn.pack(side="left", padx=(0, 6))

        self._proxy_btn = ctk.CTkButton(row2, text=self._proxy_label_text(),
                                        width=118, height=30, corner_radius=8,
                                        font=(FONT_FAMILY, 11),
                                        fg_color=self._pal["card2"],
                                        hover_color=self._pal["border"],
                                        border_width=1,
                                        border_color=self._pal["border"],
                                        text_color=self._pal["text"],
                                        command=self._set_proxy)
        self._proxy_btn.pack(side="left")

    def _cookie_label_text(self) -> str:
        return "Cookie 已导入" if self._cookies_file else "导入 Cookie"

    def _proxy_label_text(self) -> str:
        return f"代理 {self._proxy[:26]}" if self._proxy else "设置代理"

    def _build_main_area(self, parent):
        main = ctk.CTkFrame(parent, fg_color="transparent")
        main.pack(fill="both", expand=True, pady=(0, 10))

        # 左：列表
        left = ctk.CTkFrame(main, fg_color=self._pal["card"],
                            corner_radius=14,
                            border_width=1,
                            border_color=self._pal["border"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        lst_head = ctk.CTkFrame(left, fg_color="transparent")
        lst_head.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(lst_head, text="视频列表", font=(FONT_FAMILY, 14, "bold"),
                     text_color=self._pal["text"]).pack(side="left")
        self._list_count_label = ctk.CTkLabel(
            lst_head, text="共 0 项", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"])
        self._list_count_label.pack(side="right")

        sel_bar = ctk.CTkFrame(left, fg_color="transparent")
        sel_bar.pack(fill="x", padx=14, pady=(0, 6))
        for text, cmd in (("全选", self._select_all),
                          ("反选", self._invert_selection),
                          ("清空选择", self._deselect_all)):
            ctk.CTkButton(sel_bar, text=text, width=76, height=26,
                          corner_radius=7, font=(FONT_FAMILY, 10),
                          fg_color=self._pal["card2"],
                          hover_color=self._pal["border"],
                          border_width=1, border_color=self._pal["border"],
                          text_color=self._pal["sub"],
                          command=cmd).pack(side="left", padx=(0, 6))

        self._list_scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent", corner_radius=0)
        self._list_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self._empty_label = ctk.CTkLabel(
            self._list_scroll,
            text="输入链接并点击「提取」\n支持粘贴抖音分享的整段文字",
            font=(FONT_FAMILY, 12), text_color=self._pal["sub"])
        self._empty_label.pack(pady=60)

        # 右：详情
        right = ctk.CTkFrame(main, width=380, fg_color=self._pal["card"],
                             corner_radius=14, border_width=1,
                             border_color=self._pal["border"])
        right.pack(side="right", fill="both")
        right.pack_propagate(False)
        self._build_detail_panel(right)

    def _build_detail_panel(self, parent):
        pad = 16
        ctk.CTkLabel(parent, text="作品详情", font=(FONT_FAMILY, 14, "bold"),
                     text_color=self._pal["text"]).pack(
            anchor="w", padx=pad, pady=(14, 8))

        # 封面
        self._cover_label = ctk.CTkLabel(parent, text="未选择作品预览",
                                         width=348, height=176,
                                         corner_radius=10,
                                         fg_color=self._pal["card2"],
                                         text_color=self._pal["sub"],
                                         font=(FONT_FAMILY, 12))
        self._cover_label.pack(padx=pad)

        self._detail_title = ctk.CTkLabel(
            parent, text="", font=(FONT_FAMILY, 13, "bold"),
            text_color=self._pal["text"], wraplength=348, justify="left",
            anchor="w")
        self._detail_title.pack(anchor="w", padx=pad, pady=(10, 2))

        self._detail_info = ctk.CTkLabel(
            parent, text="", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"], wraplength=348, justify="left",
            anchor="w")
        self._detail_info.pack(anchor="w", padx=pad, pady=(0, 10))

        # 画质
        ctk.CTkLabel(parent, text="画质", font=(FONT_FAMILY, 11),
                     text_color=self._pal["sub"]).pack(anchor="w", padx=pad)
        self._quality_menu = ctk.CTkOptionMenu(
            parent,
            values=[q[0] for q in VideoExtractor.get_quality_presets()],
            height=34, corner_radius=9, font=(FONT_FAMILY, 12),
            fg_color=self._pal["card2"], button_color=self._pal["accent"],
            button_hover_color=self._pal["accent2"],
            text_color=self._pal["text"],
            command=lambda _v: self._on_quality_change(),
            dropdown_fg_color=self._pal["card2"],
            dropdown_hover_color=self._pal["border"],
            dropdown_text_color=self._pal["text"])
        preset_labels = [q[0] for q in VideoExtractor.get_quality_presets()]
        cur_label = self._current_quality
        label_for_cur = next(
            (lab for lab, code in VideoExtractor.get_quality_presets()
             if code == cur_label), preset_labels[0])
        self._quality_menu.set(label_for_cur)
        self._quality_menu.pack(fill="x", padx=pad, pady=(2, 12))

        # 按钮
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=pad)
        self._download_btn = ctk.CTkButton(
            btn_row, text="下载选中", height=38, corner_radius=10,
            font=(FONT_FAMILY, 12, "bold"), fg_color=self._pal["accent"],
            hover_color=self._pal["accent2"], command=self._download_selected)
        self._download_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(btn_row, text="下载全部", height=38, corner_radius=10,
                      font=(FONT_FAMILY, 12, "bold"),
                      fg_color=self._pal["card2"],
                      hover_color=self._pal["border"],
                      border_width=1, border_color=self._pal["border"],
                      text_color=self._pal["text"],
                      command=self._download_all).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

        # 附加信息
        self._detail_extra = ctk.CTkTextbox(
            parent, height=110, fg_color=self._pal["card2"],
            text_color=self._pal["sub"], font=(FONT_FAMILY, 10),
            corner_radius=10, wrap="word")
        self._detail_extra.pack(fill="both", expand=True, padx=pad,
                                pady=(12, 14))
        self._detail_extra.configure(state="disabled")

    def _build_queue_area(self, parent):
        card = ctk.CTkFrame(parent, fg_color=self._pal["card"],
                            corner_radius=14, border_width=1,
                            border_color=self._pal["border"])
        card.pack(fill="x")

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(head, text="下载队列", font=(FONT_FAMILY, 13, "bold"),
                     text_color=self._pal["text"]).pack(side="left")
        self._queue_count_label = ctk.CTkLabel(
            head, text="", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"])
        self._queue_count_label.pack(side="right")

        self._queue_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._queue_frame.pack(fill="x", padx=12, pady=(0, 10))
        self._empty_queue_label = ctk.CTkLabel(
            self._queue_frame, text="暂无下载任务", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"])
        self._empty_queue_label.pack(pady=6)

    def _build_statusbar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent", height=24)
        bar.pack(fill="x")
        self._status_label = ctk.CTkLabel(
            bar, text="就绪", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"])
        self._status_label.pack(side="left")
        self._status_stats = ctk.CTkLabel(
            bar, text="", font=(FONT_FAMILY, 11),
            text_color=self._pal["sub"])
        self._status_stats.pack(side="right")

    # ── 主题/模式 ──────────────────────────────────────────────
    def _toggle_theme(self):
        mode = "Light" if ctk.get_appearance_mode() == "Dark" else "Dark"
        ctk.set_appearance_mode(mode)
        self.settings["appearance"] = mode
        save_settings(self.settings_path, self.settings)
        # 自定义配色是硬编码的，设置外观模式不会自动更新控件颜色，
        # 因此重建整个界面以应用新调色板。
        self._rebuild_ui()

    def _rebuild_ui(self):
        """按新的 appearance 重建界面，并保留已提取的数据与输入内容"""
        keep_url = ""
        keep_dir = ""
        if hasattr(self, "_url_entry"):
            try:
                keep_url = self._url_entry.get()
                keep_dir = self._dir_entry.get()
            except Exception:
                pass
        try:
            # 先移走焦点并刷新空闲事件，避免销毁含 CTkButton 的树时
            # 挂起的 focus 回调指向已销毁的 canvas 而报 TclError
            self.root.focus_set()
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            if self._content.winfo_exists():
                self._content.destroy()
                self.root.update_idletasks()
        except Exception:
            pass
        self._queue_rows.clear()
        self._build_layout()
        if keep_url:
            try:
                self._url_entry.insert(0, keep_url)
            except Exception:
                pass
        if keep_dir:
            try:
                self._dir_entry.delete(0, "end")
                self._dir_entry.insert(0, keep_dir)
            except Exception:
                pass
        if self._video_items:
            self._list_count_label.configure(
                text=f"共 {len(self._video_items)} 项")
            self._refresh_video_list()
            self._show_detail(min(self._selected_indices) if
                              self._selected_indices else 0)
        self._refresh_queue_display()

    def _on_mode_change(self, value: str):
        self._search_mode = (value == "关键词搜索")
        if self._search_mode:
            self._platform_combo.pack(side="left", before=self._dir_entry,
                                      padx=(0, 8))
            self._url_entry.configure(
                placeholder_text="输入搜索关键词，例如：和平精英 超体对抗...")
        else:
            self._platform_combo.pack_forget()
            self._url_entry.configure(
                placeholder_text="粘贴抖音/视频链接（支持整段分享文字）...")

    def _bind_events(self):
        self._url_entry.bind("<Return>", lambda e: self._extract_videos())

    # ── 工具方法 ──────────────────────────────────────────────
    def _set_status(self, text: str):
        self._status_label.configure(text=text)

    def _update_stats(self):
        text = (f"已完成 {self.download_manager.completed_count} · "
                f"失败 {self.download_manager.failed_count} · "
                f"总任务 {self.download_manager.total_count}")
        self._status_stats.configure(text=text)

    # ── 提取主流程 ────────────────────────────────────────────
    @staticmethod
    def _extract_url_from_text(text: str) -> Optional[str]:
        urls = VideoExtractor.extract_urls_from_text(text)
        return urls[0] if urls else None

    def _extract_videos(self):
        text = self._url_entry.get().strip()
        if not text:
            messagebox.showwarning("提示", "请输入目标 URL 或搜索关键词")
            return
        if self._extracting:
            return

        if self._search_mode:
            platform = self._platform_combo.get()
            self._start_extraction("search", text, platform)
            return

        url = text if is_url(text) else self._extract_url_from_text(text)
        if not url:
            messagebox.showwarning(
                "提示", "未找到有效链接\n\n可以直接粘贴抖音分享的整段文字，"
                        "会自动识别其中的链接")
            return

        if any(domain in url for domain in
               ["lt0577.com", "tvplay", "vod", "drama"]):
            self._start_batch_extract(url)
            return

        if url != text:
            self._url_entry.delete(0, "end")
            self._url_entry.insert(0, url)

        valid, msg = VideoExtractor.validate_url(url)
        if not valid:
            messagebox.showerror("不支持的 URL", msg)
            return

        self._start_extraction("extract", url)

    def _start_extraction(self, mode: str, *args, **kw):
        self._extracting = True
        self._extract_btn.configure(text="处理中…", state="disabled")
        self._clear_video_list()
        self._set_status(f"{'搜索' if mode == 'search' else '提取'}中...")

        if mode == "search":
            keyword, platform = args
            bl_map = {"B站搜索": "Bilibili", "全网剧集": "Bilibili_media"}
            real_platform = bl_map.get(platform, platform)
            self._extract_future = concurrent.futures.ThreadPoolExecutor(
                max_workers=1)
            future = self._extract_future.submit(
                self.extractor.search, keyword, real_platform)
        else:
            url = args[0]
            self._extract_future = concurrent.futures.ThreadPoolExecutor(
                max_workers=1)
            future = self._extract_future.submit(
                self.extractor.get_video_list, url)
        threading.Thread(target=self._wait_extract, args=(future,),
                         daemon=True).start()
        self._extract_timer = threading.Timer(25.0, self._on_extract_timeout,
                                              args=(future,))
        self._extract_timer.daemon = True
        self._extract_timer.start()

    def _start_batch_extract(self, url: str):
        self._extracting = True
        self._extract_btn.configure(text="批量提取中…", state="disabled")
        self._clear_video_list()
        self._set_status("正在批量提取剧集...")
        threading.Thread(target=self._do_batch_extract, args=(url,),
                         daemon=True).start()

    def _do_batch_extract(self, url: str):
        try:
            from core.batch_extractor import batch_extract
            results = batch_extract(url, timeout=60, max_episodes=0)
            self.root.after(0, self._on_batch_done, results)
        except Exception as e:
            self.root.after(0, self._on_extract_error, f"批量提取中断: {e}")

    def _on_batch_done(self, results: list):
        self._finish_extract_ui()
        if not results:
            messagebox.showinfo("提示", "未提取到任何剧集")
            return
        self._video_items, self._selected_indices = [], set()
        for r in results:
            item = {
                "title": f"{r.get('drama_title') or '剧集'} - {r['title']}",
                "url": r["video_url"], "episode": r["episode"],
                "duration": None, "filesize": None, "max_height": None,
                "quality_str": "m3u8",
                "uploader": r.get("drama_title", "剧集"),
                "description": "", "thumbnail": "", "view_count": 0,
                "formats": [], "_batch": True, "_batch_info": r,
            }
            self._video_items.append(item)
            self._selected_indices.add(len(self._video_items) - 1)
        self._refresh_video_list()
        self._list_count_label.configure(text=f"共 {len(results)} 集")
        self._set_status(f"批量提取完成: {len(results)} 集")

    def _wait_extract(self, future):
        try:
            items = future.result(timeout=90)
            if getattr(self, "_extract_timer", None):
                self._extract_timer.cancel()
            self.root.after(0, self._on_extract_done, items)
        except concurrent.futures.TimeoutError:
            self.root.after(0, self._on_extract_error,
                            "提取超时（超过 90 秒）\n\n"
                            "该 URL 可能不被支持或网站响应过慢。")
        except ValueError as e:
            self.root.after(0, self._on_extract_error, str(e))
        except Exception as e:
            self.root.after(0, self._on_extract_error, f"处理失败：{e}")

    def _on_extract_timeout(self, future):
        if future.done():
            return
        self.root.after(0, self._show_timeout_warning)

    def _show_timeout_warning(self):
        if not self._extracting:
            return
        if messagebox.askyesno("提取时间较长",
                               "视频提取已超过 25 秒，可能该链接不被支持。\n\n"
                               "是否取消当前操作？", icon="warning"):
            self._cancel_extraction()

    def _cancel_extraction(self):
        self._extracting = False
        self._extract_btn.configure(text="提 取", state="normal")
        if getattr(self, "_extract_timer", None):
            self._extract_timer.cancel()
        if getattr(self, "_extract_future", None):
            self._extract_future.shutdown(wait=False)
        self._set_status("已取消")

    def _finish_extract_ui(self):
        self._extracting = False
        self._extract_btn.configure(text="提 取", state="normal")

    def _on_extract_done(self, items: list):
        self._finish_extract_ui()
        if not items:
            messagebox.showinfo("提示", "未找到可提取的视频\n"
                                        "可能原因：URL 无效、网站不支持、"
                                        "或需要登录")
            self._set_status("未找到视频")
            return

        self._video_items, self._selected_indices = [], set()
        self._video_meta = {}
        for item in items:
            vi = VideoExtractor.build_video_item(item)
            for key in ("_cookies", "_cookie_file", "_douyin_fallback",
                        "all_urls", "page_url", "_page_url",
                        "_douyin_uri", "_douyin_images", "_raw"):
                if key in item:
                    vi[key] = item[key]
            self._video_items.append(vi)

        self._refresh_video_list()
        self._list_count_label.configure(
            text=f"共 {len(self._video_items)} 项")
        self._select_all()
        self._set_status(f"找到 {len(self._video_items)} 项")
        self._update_stats()

    def _on_extract_error(self, error: str):
        self._finish_extract_ui()
        messagebox.showerror("提取失败", error)
        self._set_status("提取失败")

    # ── 列表渲染（卡片行） ────────────────────────────────────
    def _clear_video_list(self):
        self._video_items = []
        self._selected_indices = set()
        self._video_meta = {}
        self._thumb_cache.clear()
        for w in self._list_scroll.winfo_children():
            w.destroy()
        self._empty_label = ctk.CTkLabel(
            self._list_scroll,
            text="输入链接并点击「提取」\n支持粘贴抖音分享的整段文字",
            font=(FONT_FAMILY, 12), text_color=self._pal["sub"])
        self._empty_label.pack(pady=60)
        self._list_count_label.configure(text="共 0 项")
        self._clear_detail()

    def _refresh_video_list(self):
        for w in self._list_scroll.winfo_children():
            w.destroy()
        for idx, vi in enumerate(self._video_items):
            self._create_list_row(idx, vi)

    def _create_list_row(self, idx: int, vi: dict):
        row = ctk.CTkFrame(self._list_scroll, fg_color=self._pal["card2"],
                           corner_radius=10, border_width=1,
                           border_color=self._pal["border"])
        row.pack(fill="x", pady=(0, 6))
        row.configure(cursor="hand2")

        sel = idx in self._selected_indices
        mark = ctk.CTkFrame(row, width=4, corner_radius=2,
                            fg_color=self._pal["accent"] if sel else
                            self._pal["border"])
        mark.pack(side="left", fill="y", padx=(8, 6), pady=6)

        thumb = ctk.CTkLabel(row, text="▶", width=52, height=52,
                             corner_radius=8,
                             fg_color=self._pal["border"],
                             text_color=self._pal["sub"],
                             font=(FONT_FAMILY, 20))
        thumb.pack(side="left", padx=(0, 10), pady=8)
        self._load_thumb_async(vi.get("thumbnail", ""), thumb, (52, 52))

        body = ctk.CTkFrame(row, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, pady=8)

        title = vi.get("title", "未知")
        if len(title) > 46:
            title = title[:44] + "…"
        ctk.CTkLabel(body, text=title, font=(FONT_FAMILY, 12, "bold"),
                     text_color=self._pal["text"],
                     anchor="w", justify="left").pack(fill="x")

        meta = " · ".join(filter(None, [
            vi.get("uploader", ""),
            vi.get("duration_str", ""),
            vi.get("quality_str", ""),
        ]))
        ctk.CTkLabel(body, text=meta or "—", font=(FONT_FAMILY, 10),
                     text_color=self._pal["sub"],
                     anchor="w").pack(fill="x")

        def toggle(e=None, i=idx):
            self._toggle_select(i)
        check = ctk.CTkButton(
            row, text="已选" if sel else "选", width=64, height=26,
            corner_radius=7, font=(FONT_FAMILY, 10),
            fg_color=self._pal["accent"] if sel else self._pal["border"],
            hover_color=self._pal["accent2"] if sel else self._pal["card2"],
            text_color="#ffffff" if sel else self._pal["text"],
            command=toggle)
        check.pack(side="right", padx=10, pady=8)

        row.bind("<Button-1>", lambda e, i=idx: self._toggle_select(i))
        mark.bind("<Button-1>", lambda e, i=idx: self._toggle_select(i))
        thumb.bind("<Button-1>", lambda e, i=idx: self._toggle_select(i))
        body.bind("<Button-1>", lambda e, i=idx: self._toggle_select(i))
        row.bind("<Double-1>", lambda e, i=idx: self._on_row_double_click(i))

    def _load_thumb_async(self, url: str, label: ctk.CTkLabel, size: tuple):
        if not url or url in self._thumb_cache:
            if url in self._thumb_cache:
                try:
                    if label.winfo_exists():
                        label.configure(image=self._thumb_cache[url], text="")
                except Exception:
                    pass
            return

        def fetch():
            try:
                import requests
                import io
                resp = requests.get(url, timeout=8)
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img.thumbnail((size[0], size[1]))
                photo = ctk.CTkImage(light_image=img, dark_image=img,
                                     size=size)
                self._thumb_cache[url] = photo

                def apply():
                    try:
                        if label.winfo_exists():
                            label.configure(image=photo, text="")
                    except Exception:
                        pass
                self.root.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=fetch, daemon=True).start()

    def _on_row_double_click(self, idx: int):
        vi = self._video_items[idx]
        if vi.get("_is_search_result"):
            self._set_status(f"正在批量提取: {vi['title']}")
            self._start_batch_extract(vi["url"])

    def _toggle_select(self, idx: int):
        if idx in self._selected_indices:
            self._selected_indices.discard(idx)
        else:
            self._selected_indices.add(idx)
        self._refresh_video_list()
        self._show_detail(idx)

    def _select_all(self):
        self._selected_indices = set(range(len(self._video_items)))
        self._refresh_video_list()
        if self._video_items:
            self._show_detail(0)

    def _deselect_all(self):
        self._selected_indices.clear()
        self._refresh_video_list()

    def _invert_selection(self):
        all_idx = set(range(len(self._video_items)))
        self._selected_indices = all_idx - self._selected_indices
        self._refresh_video_list()

    # ── 详情 ──────────────────────────────────────────────────
    def _clear_detail(self):
        self._detail_title.configure(text="未选择作品")
        self._detail_info.configure(text="")
        self._detail_extra.configure(state="normal")
        self._detail_extra.delete("1.0", "end")
        self._detail_extra.configure(state="disabled")
        self._cover_label.configure(text="未选择作品预览", image=None)

    def _show_detail(self, idx: int):
        if idx < 0 or idx >= len(self._video_items):
            return
        vi = self._video_items[idx]
        self._detail_title.configure(text=vi.get("title", "无标题"))

        info_lines = [
            f"作者  {vi.get('uploader', '未知')}",
            f"时长  {vi.get('duration_str', '未知')}",
            f"画质  {vi.get('quality_str', '未知')}",
        ]
        if vi.get("view_count"):
            info_lines.append(f"点赞  {vi.get('view_count'):,}")
        self._detail_info.configure(text="    ".join(info_lines))

        extra = f"URL: {vi.get('url', '')}"
        desc = vi.get("description", "").strip()
        if desc:
            extra += f"\n\n简介：\n{desc}"
        images = vi.get("_douyin_images") or []
        if images:
            extra += (f"\n\n[图集] 共 {len(images)} 张图片，"
                      f"将下载为 标题_01.jpg / 标题_02.jpg ...")
        self._detail_extra.configure(state="normal")
        self._detail_extra.delete("1.0", "end")
        self._detail_extra.insert("1.0", extra)
        self._detail_extra.configure(state="disabled")

        cover = vi.get("thumbnail") or (images[0] if images else "")
        self._cover_label.configure(text="封面加载中…")
        self._load_cover(cover)

    def _load_cover(self, url: str):
        def fetch():
            def safe_apply(**kw):
                try:
                    if self._cover_label.winfo_exists():
                        self._cover_label.configure(**kw)
                except Exception:
                    pass

            if not url:
                self.root.after(0, lambda: safe_apply(
                    text="暂无封面预览", image=None))
                return
            try:
                import requests
                import io
                resp = requests.get(url, timeout=10)
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img.thumbnail((640, 340))
                photo = ctk.CTkImage(light_image=img, dark_image=img,
                                     size=(320, int(320 * img.height /
                                                    img.width)))
                self.root.after(0, lambda: safe_apply(
                    image=photo, text="", width=348, height=196))
            except Exception:
                self.root.after(0, lambda: safe_apply(
                    text="封面加载失败", image=None))

        threading.Thread(target=fetch, daemon=True).start()

    def _on_quality_change(self):
        selected_label = self._quality_menu.get()
        for label, code in VideoExtractor.get_quality_presets():
            if label == selected_label:
                self._current_quality = code
                break

    # ── 下载 ──────────────────────────────────────────────────
    def _get_selected_videos(self) -> list:
        return [self._video_items[i] for i in sorted(self._selected_indices)
                if i < len(self._video_items)]

    def _download_selected(self):
        items = self._get_selected_videos()
        if not items:
            messagebox.showwarning("提示", "请先勾选要下载的作品")
            return
        self._start_downloads(items)

    def _download_all(self):
        if not self._video_items:
            messagebox.showwarning("提示", "没有可下载的作品")
            return
        self._start_downloads(self._video_items)

    def _start_downloads(self, items: list):
        output_dir = self._dir_entry.get().strip()
        if not output_dir:
            messagebox.showwarning("提示", "请设置下载目录")
            return
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.settings["download_dir"] = output_dir

        quality = self._current_quality
        for vi in items:
            self.download_manager.add_task(vi, quality, output_dir)
        self._set_status(f"已添加 {len(items)} 个下载任务")
        self._update_stats()

    def _refresh_queue_display(self):
        """增量刷新下载队列：只新建/更新需要的行，避免全部销毁造成闪烁与 TclError"""
        count = self.download_manager.total_count
        self._queue_count_label.configure(text=f"共 {count} 个任务")

        tasks = (self.download_manager._active +
                 self.download_manager._queue +
                 self.download_manager._completed[-4:])[-6:]
        visible_ids = [t.task_id for t in tasks]

        # 移除已不在展示范围的旧行
        for tid in list(self._queue_rows.keys()):
            if tid not in visible_ids:
                w = self._queue_rows.pop(tid)[0]
                try:
                    if w.winfo_exists():
                        w.destroy()
                except Exception:
                    pass

        if count == 0:
            if self._queue_rows:
                for frame, *_ in self._queue_rows.values():
                    try:
                        if frame.winfo_exists():
                            frame.destroy()
                    except Exception:
                        pass
                self._queue_rows.clear()
            self._empty_queue_label.pack(pady=6)
            return
        if self._empty_queue_label.winfo_manager():
            self._empty_queue_label.pack_forget()

        for task in tasks:
            self._create_task_row(task)

    def _create_task_row(self, task: DownloadTask):
        row = self._queue_rows.get(task.task_id)
        if row is not None:
            frame, pb, pct, info = row
            # 行可能已被销毁（防御）
            try:
                if not frame.winfo_exists():
                    row = None
            except Exception:
                row = None
        if row is not None:
            frame, pb, pct, info = row
            pb.set(task.progress / 100)
            if task.status == "downloading":
                pct.configure(text=f"{task.progress:.0f}%")
                info.configure(text=(f"{format_speed(task.speed)} · "
                                     f"剩余 {format_eta(task.eta)}"))
            elif task.status == "completed":
                pct.configure(text="100%")
                info.configure(text="完成", text_color=self._pal["success"])
            elif task.status == "failed":
                info.configure(text=f"失败: {(task.error or '')[:36]}",
                               text_color=self._pal["danger"])
            elif task.status == "cancelled":
                info.configure(text="已取消", text_color=self._pal["warning"])
            else:
                info.configure(text="等待中…", text_color=self._pal["sub"])
            return

        frame = ctk.CTkFrame(self._queue_frame, fg_color="transparent")
        frame.pack(fill="x", pady=1)
        ctk.CTkLabel(frame, text=task.title_display, width=280,
                     font=(FONT_FAMILY, 10), text_color=self._pal["text"],
                     anchor="w").pack(side="left", padx=(2, 10))
        pb = ctk.CTkProgressBar(frame, width=220, height=8,
                                corner_radius=4,
                                progress_color=self._pal["accent"],
                                fg_color=self._pal["border"])
        pb.pack(side="left", padx=(0, 8))
        pb.set(0)
        pct = ctk.CTkLabel(frame, text=f"{task.progress:.0f}%",
                           width=46, font=(FONT_FAMILY, 10),
                           text_color=self._pal["sub"])
        pct.pack(side="left", padx=(0, 8))
        info = ctk.CTkLabel(frame, text="等待中…", width=220,
                            font=(FONT_FAMILY, 10),
                            text_color=self._pal["sub"], anchor="w")
        info.pack(side="left")
        if task.status in ("pending", "downloading"):
            ctk.CTkButton(frame, text="✕", width=26, height=22,
                          corner_radius=6, font=(FONT_FAMILY, 9),
                          fg_color=self._pal["danger"],
                          hover_color=self._pal["border"],
                          command=lambda t=task: self._cancel_task(t)
                          ).pack(side="right")
        self._queue_rows[task.task_id] = (frame, pb, pct, info)

    def _cancel_task(self, task: DownloadTask):
        self.download_manager.cancel_task(task)

    # ── 轮询 ──────────────────────────────────────────────────
    def _poll_downloads(self):
        if (self.download_manager.is_busy or
                self.download_manager.total_count > 0):
            self._refresh_queue_display()
            self._update_stats()
        self.root.after(500, self._poll_downloads)

    def _poll_progress_queue(self):
        try:
            while True:
                msg_type, task = self.download_manager.progress_queue.get_nowait()
                if msg_type == "completed":
                    self._set_status(f"下载完成: {task.title_display}")
                elif msg_type == "failed":
                    self._set_status(
                        f"下载失败: {task.title_display} - {task.error}")
                elif msg_type == "cancelled":
                    self._set_status(f"已取消: {task.title_display}")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_progress_queue)

    # ── 菜单命令 ──────────────────────────────────────────────
    def _change_download_dir(self):
        path = filedialog.askdirectory(title="选择下载目录")
        if path:
            self._dir_entry.delete(0, "end")
            self._dir_entry.insert(0, path)
            self.settings["download_dir"] = path
            save_settings(self.settings_path, self.settings)

    def _open_download_dir(self):
        import subprocess
        path = self._dir_entry.get().strip()
        if path:
            try:
                subprocess.Popen(["explorer", os.path.normpath(path)])
            except Exception:
                pass

    def _import_cookie(self):
        path = filedialog.askopenfilename(
            title="选择 Cookie 文件（Netscape 格式）",
            filetypes=[("Cookie 文件", "*.txt"), ("所有文件", "*.*")])
        if path:
            self._cookies_file = path
            self.extractor._cookies_file = path
            self._cookie_btn.configure(text="Cookie 已导入")
            self.settings["cookies_file"] = path
            save_settings(self.settings_path, self.settings)
            self._set_status("Cookie 已导入")

    def _auto_get_cookie(self):
        self._set_status("正在从浏览器获取 Cookie...")
        for browser in ("edge", "chrome", "firefox"):
            cookie_file = VideoExtractor.try_export_browser_cookies(browser)
            if cookie_file:
                self._cookies_file = cookie_file
                self.extractor._cookies_file = cookie_file
                self._cookie_btn.configure(text="Cookie 已导入")
                self.settings["cookies_file"] = cookie_file
                save_settings(self.settings_path, self.settings)
                self._set_status(f"已从 {browser} 获取 Cookie")
                messagebox.showinfo(
                    "Cookie 获取成功",
                    f"已成功从 {browser} 导出 Cookie\n现在可以提取抖音视频了。")
                return
        messagebox.showerror(
            "Cookie 获取失败",
            "未能从浏览器获取到有效 Cookie。\n\n"
            "可能原因：浏览器正在运行（请先完全关闭）；未安装 Edge/Chrome。\n\n"
            "请手动导出：浏览器访问 douyin.com → Get cookies.txt 扩展 → 导出")

    def _set_proxy(self):
        current = self._proxy or ""
        proxy = simpledialog.askstring(
            "代理设置",
            "输入代理地址（如 http://127.0.0.1:7890）\n留空清除代理：",
            initialvalue=current)
        if proxy is None:
            return
        proxy = proxy.strip()
        self._proxy = proxy if proxy else None
        self.extractor._proxy = self._proxy
        self._proxy_btn.configure(text=self._proxy_label_text())
        self.settings["proxy"] = self._proxy or ""
        save_settings(self.settings_path, self.settings)

    def _import_urls_from_file(self):
        path = filedialog.askopenfilename(
            title="导入 URL 文件", filetypes=[("文本文件", "*.txt"),
                                            ("所有文件", "*.*")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                urls = VideoExtractor.extract_urls_from_text(content)
                if urls:
                    self._url_entry.delete(0, "end")
                    self._url_entry.insert(0, urls[0])
                    self._set_status(f"已导入 {len(urls)} 个 URL")
                else:
                    messagebox.showinfo("提示", "文件中未找到有效 URL")
            except Exception as e:
                messagebox.showerror("错误", f"读取文件失败：{e}")

    def _clear_completed(self):
        self.download_manager._completed.clear()
        self._queue_rows.clear()
        self._refresh_queue_display()
        self._set_status("已清除完成记录")

    def _cancel_all(self):
        self.download_manager.cancel_all()
        self._refresh_queue_display()
        self._set_status("已取消所有任务")

    def _show_help(self):
        messagebox.showinfo(
            "使用说明",
            "【链接提取】\n"
            "粘贴抖音分享链接(支持整段文字) → 提取 → 勾选 → 下载\n\n"
            "【关键词搜索】\n"
            "切换到「关键词搜索」，选择平台后输入关键词\n\n"
            "【画质】\n"
            "右侧选择画质; 抖音默认自动选最高可用档位\n\n"
            "【图集】\n"
            "抖音图文帖自动识别，下载为 标题_01.jpg 序列\n\n"
            "【Cookie】\n"
            "部分视频需要登录态: 浏览器导出 douyin.com cookie.txt 后导入\n\n"
            "【批量剧集】\n"
            "粘贴剧集网站链接自动展开全部集数")

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "视频爬虫 · Modern v2.0\n\n"
            "核心引擎: yt-dlp + 抖音签名环境提取(2026)\n"
            "界面: CustomTkinter\n\n"
            "仅供学习交流使用，请尊重版权与平台规则。")

    def _show_settings_dialog(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("首选项")
        dialog.geometry("440x260")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="最大并发下载数",
                     font=(FONT_FAMILY, 12),
                     text_color=self._pal["text"]).pack(anchor="w",
                                                        padx=20, pady=(18, 2))
        concurrent_var = tk.StringVar(
            value=str(self.settings.get("max_concurrent", 3)))
        ctk.CTkEntry(dialog, textvariable=concurrent_var, width=90,
                     height=32, corner_radius=8).pack(
            anchor="w", padx=20, pady=(0, 12))

        ctk.CTkLabel(dialog, text="默认画质", font=(FONT_FAMILY, 12),
                     text_color=self._pal["text"]).pack(anchor="w",
                                                        padx=20, pady=(0, 2))
        presets = VideoExtractor.get_quality_presets()
        quality_var = tk.StringVar()
        for label, code in presets:
            if code == self.settings.get("default_quality"):
                quality_var.set(label)
                break
        if not quality_var.get():
            quality_var.set(presets[0][0])
        quality_menu = ctk.CTkOptionMenu(
            dialog, values=[p[0] for p in presets], variable=quality_var,
            width=240, height=32, corner_radius=8,
            fg_color=self._pal["card2"], button_color=self._pal["accent"],
            text_color=self._pal["text"])
        quality_menu.pack(anchor="w", padx=20)

        def save():
            try:
                n = int(concurrent_var.get())
                self.settings["max_concurrent"] = n
                self.download_manager.set_max_concurrent(n)
            except ValueError:
                pass
            for label, code in presets:
                if label == quality_var.get():
                    self.settings["default_quality"] = code
                    self._current_quality = code
                    break
            save_settings(self.settings_path, self.settings)
            dialog.destroy()

        ctk.CTkButton(dialog, text="保存", width=110, height=34,
                      corner_radius=9, font=(FONT_FAMILY, 12, "bold"),
                      fg_color=self._pal["accent"],
                      hover_color=self._pal["accent2"],
                      command=save).pack(pady=(16, 0))

    # ── 关闭 ──────────────────────────────────────────────────
    def _on_close(self):
        save_settings(self.settings_path, self.settings)
        self.download_manager.cleanup()
        self.root.destroy()
