"""工具函数"""

import os
import re
import sys
import json
from pathlib import Path
from typing import Optional


def default_download_dir() -> str:
    """跨平台默认下载目录：~/Downloads/VideoCrawler（保持旧 Windows 路径兼容）"""
    d = Path.home() / "Downloads" / "VideoCrawler"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(d)


def fix_console_encoding() -> None:
    """让控制台打印不因 GBK 编码崩溃（Windows cmd 默认 GBK + 含 emoji/• 的文本）。

    保留原编码（中文照常显示），仅将无法编码的字符替换为 '?'。
    无控制台窗口（PyInstaller --windowed/gui）时 stdout 为 None，重定向到
    内存流避免 print 抛异常。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            try:
                setattr(sys, stream_name, __import__("io").StringIO())
            except Exception:
                pass
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:
            pass


def format_size(bytes_val: Optional[float]) -> str:
    """格式化文件大小"""
    if bytes_val is None:
        return "未知"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def format_speed(bytes_per_sec: Optional[float]) -> str:
    """格式化下载速度"""
    if bytes_per_sec is None:
        return "0 B/s"
    return format_size(bytes_per_sec) + "/s"


def format_duration(seconds: Optional[float]) -> str:
    """格式化时长"""
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_eta(seconds: Optional[float]) -> str:
    """格式化预计剩余时间"""
    if seconds is None:
        return "--:--"
    return format_duration(seconds)


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def load_settings(path: str) -> dict:
    """加载设置"""
    default = {
        "download_dir": default_download_dir(),
        "max_concurrent": 3,
        "proxy": "",
        "cookies_file": "",
        "default_quality": "bestvideo+bestaudio/best",
        "window_geometry": "1200x800",
        "appearance": "Dark",
        "image_format": "jpg",
    }
    try:
        if Path(path).exists():
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            default.update(saved)
    except Exception:
        pass
    return default


def save_settings(path: str, settings: dict):
    """保存设置"""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存设置失败: {e}")


def is_url(text: str) -> bool:
    """检查是否为 URL"""
    return bool(re.match(r'^https?://', text.strip()))
