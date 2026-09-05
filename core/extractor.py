"""视频信息提取器 - 基于 yt-dlp"""

import re
import sys
import io
import os
import time
import subprocess
import tempfile
import contextlib
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import urllib.parse

import yt_dlp
from yt_dlp.utils import DownloadError, UnsupportedError

from utils.helpers import format_size, format_duration

# 预设画质选项
QUALITY_PRESETS = [
    ("最佳画质", "bestvideo+bestaudio/best"),
    ("4K (2160p)", "bestvideo[height<=2160]+bestaudio/best[height<=2160]"),
    ("1440p (2K)", "bestvideo[height<=1440]+bestaudio/best[height<=1440]"),
    ("1080p (全高清)", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
    ("720p (高清)", "bestvideo[height<=720]+bestaudio/best[height<=720]"),
    ("480p (标清)", "bestvideo[height<=480]+bestaudio/best[height<=480]"),
    ("360p (流畅)", "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    ("仅音频 (MP3)", "bestaudio/best"),
]


def _is_video_cdn(url: str) -> bool:
    """粗略判断是否为抖音视频直链（CDN/播放接口），排除封面图"""
    if re.search(r'aweme/v1/play', url):
        return True
    if re.search(r'(\.jpg|\.jpeg|\.png|\.webp)(\?|$)', url, re.I):
        return False
    return bool(re.search(
        r'(douyinvod|zjcdn|bytovod|v3-dy|v5-dy|iesdouyin|douyinpic)', url,
        re.I))

# 平台搜索配置
SEARCH_PLATFORMS = {
    "YouTube": "ytsearch",
    "Bilibili": "bilibili_search",
    "通用": "ytsearch",
}


class _NullLogger:
    """静默日志器，抑制 yt-dlp 控制台输出"""
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _suppress_ytdlp_output():
    """抑制 yt-dlp 写入 stderr（通过重定向）"""
    return contextlib.redirect_stderr(io.StringIO())


class VideoExtractor:
    """视频信息提取器"""

    def __init__(self, cookies_file: Optional[str] = None, proxy: Optional[str] = None):
        self._cookies_file = cookies_file
        self._proxy = proxy

    def _get_base_opts(self, for_download: bool = False) -> Dict[str, Any]:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "logger": _NullLogger(),
            "extract_flat": False,
            "ignoreerrors": not for_download,
            "no_color": True,
        }
        if self._cookies_file and Path(self._cookies_file).exists():
            opts["cookiefile"] = self._cookies_file
        if self._proxy:
            opts["proxy"] = self._proxy
        return opts

    def extract(self, url: str) -> Dict[str, Any]:
        """提取视频/播放列表信息，失败抛出异常"""
        # 自动解析短链接
        resolved = self.resolve_url(url)
        if resolved != url:
            url = resolved

        opts = self._get_base_opts()
        with _suppress_ytdlp_output():
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                except (UnsupportedError, DownloadError) as e:
                    err_msg = str(e)
                    # 检测抖音 Cookie 错误
                    if 'Fresh cookies' in err_msg or 'douyin' in err_msg.lower():
                        raise ValueError(
                            f"抖音视频需要 Cookie 才能提取\n\n"
                            f"{self.get_cookie_help_text()}")
                    if 'Unsupported' in err_msg or 'unsupported' in err_msg:
                        raise ValueError(f"不支持的 URL 或网站：{url}\n\n"
                                         f"提示：请使用单个视频页面的完整链接。\n"
                                         f"例如：\n"
                                         f"  • 抖音视频：https://www.douyin.com/video/xxxx\n"
                                         f"  • B站视频：https://www.bilibili.com/video/BVxxxx\n"
                                         f"  • YouTube：https://www.youtube.com/watch?v=xxxx\n\n"
                                         f"原始错误：{e}")
                    raise ValueError(f"提取失败：{e}")
        if info is None:
            raise ValueError("未能提取到任何视频信息，请检查 URL 是否正确")
        return info

    def get_video_list(self, url: str) -> List[Dict[str, Any]]:
        """获取视频列表（自动展开播放列表）"""
        # 检测是否为抖音 URL（需要特殊处理）
        is_douyin = bool(re.search(
            r'(douyin\.com/(video/|note/|slide/)|v\.douyin\.com|iesdouyin\.com)',
            url))

        # 抖音优先走 Playwright 兜底（yt-dlp 的抖音提取器已损坏）
        try:
            info = self.extract(url)
            entries = info.get("entries", [info])
            result = [e for e in entries if e is not None]
            if result:
                return result
        except ValueError as e:
            if is_douyin:
                return self._fallback_douyin(url)
            raise
        except Exception:
            if is_douyin:
                return self._fallback_douyin(url)
            raise

        # yt-dlp 返回空结果
        if is_douyin:
            return self._fallback_douyin(url)
        raise ValueError("未找到可提取的视频内容，请检查 URL 是否包含有效视频")

    def _fallback_douyin(self, url: str) -> List[Dict[str, Any]]:
        """抖音兜底：浏览器签名环境提取详情 API（2026 版多路径）"""
        resolved = self.resolve_url(url)
        try:
            from core.douyin_extractor import extract_douyin_video
            result = extract_douyin_video(
                resolved, timeout=30000, proxy=self._proxy,
                login_cookie_file=self._cookies_file)
            # 视频(有 urls) 或 图集(只有 images) 都算成功
            has_content = bool(
                result.get("urls") or result.get("images"))
            if not (result and result.get("success") and has_content):
                err = (result or {}).get("error", "")
                raise ValueError(
                    "抖音视频提取失败\n\n"
                    f"{err}\n\n"
                    "帮助：\n"
                    "1. 确认已安装浏览器: python -m playwright install chromium\n"
                    "2. 若提示被反爬拦截，可用 python cli.py <链接> --headful "
                    "弹出真浏览器重试\n"
                    "3. 检查网络能否访问 douyin.com\n"
                    "4. 部分视频需要登录后才可提取，请先在浏览器中登录抖音"
                )

            all_urls = result.get("urls") or []
            # 优先直接 CDN 视频直链
            video_urls = [u for u in all_urls if _is_video_cdn(u)]
            if not video_urls:
                video_urls = all_urls
            best_url = video_urls[0] if video_urls else ""
            is_note = bool(result.get("images")) and not all_urls

            # 用 desc 标题（B站/抖音简介常为真实标题），无则时间戳
            raw_title = (result.get("title") or "").strip()
            clean_title = raw_title[:60] or f"抖音视频_{int(time.time())}"

            duration = result.get("duration") or None
            height = result.get("height") or 0
            quality_str = ("图集" if is_note
                           else f"{height}p" if height else "未知")

            item = {
                "title": clean_title,
                "url": best_url,
                "all_urls": all_urls,
                "duration": duration,
                "duration_str": format_duration(duration) if duration else "未知",
                "filesize": None,
                "max_height": height if not is_note else 0,
                "quality_str": quality_str,
                "uploader": result.get("uploader") or "抖音用户",
                "description": (result.get("title") or "")[:200],
                "thumbnail": result.get("cover", ""),
                "view_count": result.get("digg_count", 0),
                "formats": [],
                "_cookies": result.get("cookies", []),  # 保存 Cookie，下载直接用
                "_cookie_file": result.get("cookie_file"),
                "_douyin_uri": result.get("uri", ""),
                "_douyin_images": result.get("images", []),
                "_page_url": resolved,
                "_raw": result,
                "_douyin_fallback": True,
            }
            return [item]
        except ImportError:
            pass
        raise ValueError(
            "抖音视频提取失败\n\n"
            "抖音目前（2025-2026）加强了反爬措施，需要完整浏览器渲染。\n\n"
            "请尝试：\n"
            "1. 确保已安装 playwright: pip install playwright\n"
            "2. 确保已安装 Chromium: python -m playwright install chromium\n"
            "3. 用命令行模式重试: python cli.py <链接> --headful\n"
            "4. 检查网络连接能否访问 douyin.com\n\n"
            "或使用其它抖音专用下载工具"
        )

    def search(self, keyword: str, platform: str = "YouTube", limit: int = 20) -> List[Dict[str, Any]]:
        """搜索视频"""
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("搜索关键词不能为空")

        if platform == "Bilibili":
            return self._search_bilibili(keyword, limit, search_type="video")
        elif platform == "Bilibili_media":
            return self._search_bilibili(keyword, limit, search_type="media_ft")
        else:
            # YouTube 搜索
            search_url = f"ytsearch{limit}:{keyword}"
            return self.get_video_list(search_url)

    def _search_bilibili(self, keyword: str, limit: int = 20,
                         search_type: str = "video") -> List[Dict[str, Any]]:
        """用 Bilibili 官方 API 搜索

        Args:
            keyword: 搜索关键词
            limit: 返回数量
            search_type: video=全站视频, media_ft=影视剧
        """
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://search.bilibili.com/",
            "Cookie": "buvid3=test; b_nut=1700000000;",
        }
        url = (f"https://api.bilibili.com/x/web-interface/search/type"
               f"?search_type={search_type}&keyword={urllib.parse.quote(keyword)}&page=1")

        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()

        if data.get("code") != 0:
            raise ValueError(f"B站搜索失败: {data.get('message', '未知错误')}")

        items = []

        if search_type == "media_ft":
            # 影视剧搜索返回结构不同
            results = data.get("data", {}).get("items", [])
            for v in results[:limit]:
                title = re.sub(r'<[^>]+>', '', v.get("title", ""))
                url = v.get("url", "")
                if not url:
                    continue
                items.append({
                    "title": title,
                    "url": f"https:{url}" if url.startswith("//") else url,
                    "webpage_url": f"https:{url}" if url.startswith("//") else url,
                    "duration": 0,
                    "uploader": v.get("author", "") or v.get("org_title", ""),
                    "channel": v.get("author", ""),
                    "view_count": 0,
                    "thumbnail": v.get("cover", "") or v.get("pic", ""),
                    "description": "",
                    "formats": [],
                })
        else:
            # 全站视频搜索
            results = data.get("data", {}).get("result", [])
            for v in results[:limit]:
                bvid = v.get("bvid", "")
                if not bvid:
                    continue
                title = re.sub(r'<[^>]+>', '', v.get("title", ""))
                dur_raw = v.get("duration", "0")
                if isinstance(dur_raw, str) and ":" in dur_raw:
                    parts = dur_raw.split(":")
                    duration = int(parts[0]) * 60 + int(parts[1])
                else:
                    duration = int(float(str(dur_raw)))
                items.append({
                    "title": title,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "webpage_url": f"https://www.bilibili.com/video/{bvid}",
                    "duration": duration,
                    "uploader": v.get("author", ""),
                    "channel": v.get("author", ""),
                    "view_count": v.get("play", 0),
                    "thumbnail": v.get("pic", ""),
                    "description": v.get("description", ""),
                    "formats": [],
                })

        if not items:
            raise ValueError("未搜索到相关结果")

        return items

    def get_download_opts(
        self,
        format_code: str,
        output_dir: str,
        progress_hook,
    ) -> Dict[str, Any]:
        """获取下载选项"""
        opts = {
            "format": format_code,
            "outtmpl": str(Path(output_dir) / "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "logger": _NullLogger(),
            "ignoreerrors": True,
            "no_color": True,
        }
        if self._cookies_file and Path(self._cookies_file).exists():
            opts["cookiefile"] = self._cookies_file
        else:
            # 自动尝试浏览器 Cookie
            browser_cookie_file = self.try_export_browser_cookies('edge')
            if browser_cookie_file:
                opts['cookiefile'] = browser_cookie_file
        if self._proxy:
            opts["proxy"] = self._proxy
        return opts

    @staticmethod
    def build_video_item(info: Dict[str, Any]) -> Dict[str, Any]:
        """从 yt-dlp 原始信息提取结构化视频条目"""
        duration = info.get("duration")
        filesize = info.get("filesize") or info.get("filesize_approx")
        formats = info.get("formats", [])

        max_height = 0
        for f in formats:
            h = f.get("height") or 0
            if h > max_height:
                max_height = h

        return {
            "title": info.get("title", "未知标题"),
            "url": info.get("webpage_url") or info.get("url", ""),
            "duration": duration,
            "duration_str": format_duration(duration),
            "filesize": filesize,
            "filesize_str": format_size(filesize),
            "max_height": max_height,
            "quality_str": f"{max_height}p" if max_height else "未知",
            "uploader": info.get("uploader") or info.get("channel") or "未知",
            "description": (info.get("description") or "")[:200],
            "thumbnail": info.get("thumbnail", ""),
            "view_count": info.get("view_count", 0),
            "formats": formats,
            "_raw": info,
        }

    @staticmethod
    def extract_urls_from_text(text: str) -> List[str]:
        """从文本中提取所有 URL"""
        pattern = r'https?://[^\s<>"\'()，。、]+'
        return re.findall(pattern, text)

    @staticmethod
    def get_quality_presets() -> List[Tuple[str, str]]:
        """获取画质预设列表"""
        return QUALITY_PRESETS.copy()

    @staticmethod
    def is_video_url(url: str) -> bool:
        """判断是否是视频类 URL（粗略判断）"""
        video_patterns = [
            r'(youtube\.com|youtu\.be)',
            r'bilibili\.com/video/',
            r'douyin\.com/video/',
            r'douyin\.com/note/',
            r'douyin\.com/slide/',
            r'(v\.|www\.|m\.)?douyin\.com',
            r'iesdouyin\.com',
            r'twitter\.com/\w+/status/',
            r'instagram\.com/(p|reel)/',
            r'tiktok\.com/@',
            r'vimeo\.com/\d+',
            r'facebook\.com/.*/videos/',
        ]
        return any(re.search(p, url) for p in video_patterns)

    @staticmethod
    def detect_url_type(url: str) -> str:
        """检测 URL 类型，返回建议"""
        if re.search(r'(search|keyword|q=)', url):
            return "search_page"
        if re.search(r'(douyin\.com|iesdouyin\.com|tiktok\.com)', url):
            return "douyin"
        if re.search(r'bilibili\.com', url):
            return "bilibili"
        if re.search(r'(youtube\.com|youtu\.be)', url):
            return "youtube"
        return "unknown"

    @staticmethod
    def resolve_url(url: str) -> str:
        """解析短链接，返回真实 URL（如 v.douyin.com/xxx → 视频/分享页）"""
        if not re.match(r'https?://v\.douyin\.com/', url):
            return url
        try:
            import requests
            resp = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/148.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.douyin.com/",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                allow_redirects=True,
                timeout=15,
            )
            resolved = resp.url
            if resolved and resolved != url:
                return resolved
        except Exception:
            # 老办法兜底
            try:
                req = urllib.request.Request(url, method='HEAD')
                req.add_header(
                    'User-Agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36')
                resp = urllib.request.urlopen(req, timeout=10)
                resolved = resp.url
                if resolved != url:
                    return resolved
            except Exception:
                pass
        return url

    @staticmethod
    def try_export_browser_cookies(browser: str = 'edge') -> Optional[str]:
        """尝试从浏览器导出 Cookie 到临时文件。返回 cookie 文件路径或 None"""
        cookie_file = os.path.join(tempfile.gettempdir(), f'ytdlp_cookies_{browser}.txt')
        try:
            # 用 yt-dlp CLI 导出 cookie（比 Python API 兼容性更好）
            result = subprocess.run(
                ['yt-dlp', '--cookies-from-browser', browser,
                 '--cookies', cookie_file, '--skip-download',
                 'https://example.com'],
                capture_output=True, text=True, timeout=30,
            )
            if os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 100:
                return cookie_file
        except Exception:
            pass
        # 清理失败时生成的残留文件
        if os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
            except Exception:
                pass
        return None

    @staticmethod
    def get_cookie_help_text() -> str:
        """获取 Cookie 导出指引"""
        return (
            "抖音现在需要浏览器 Cookie 才能提取视频。\n\n"
            "方法一：使用浏览器 Cookie 功能（推荐）\n"
            "  在菜单 设置 → 一键获取 Cookie\n\n"
            "方法二：手动导出 Cookie\n"
            "  1. 打开 Edge/Chrome，访问 douyin.com\n"
            "  2. 安装 Get cookies.txt 扩展\n"
            "  3. 在抖音页面点击扩展图标 → Export\n"
            "  4. 保存生成的 cookies.txt 文件\n"
            "  5. 在菜单 设置 → Cookie 文件 导入\n\n"
            "方法三：关闭浏览器后重试\n"
            "  完全关闭 Edge/Chrome，再次尝试提取"
        )

    @staticmethod
    def validate_url(url: str) -> Tuple[bool, str]:
        """预检 URL 是否可提取。返回 (是否有效, 提示信息)"""
        # 抖音短链接 v.douyin.com 放行
        if re.match(r'https?://v\.douyin\.com/', url):
            return (True, "")

        # 抖音分享/笔记/图文链接放行
        if re.search(r'iesdouyin\.com/(share/video|share/note)/', url):
            return (True, "")
        if re.search(r'douyin\.com/(note|slide)/', url):
            return (True, "")

        # 抖音搜索/精选/话题等聚合页
        if re.search(r'douyin\.com/(search|jingxuan|discover|trending)/', url):
            return (False,
                     "抖音搜索/精选页面不被支持\n\n"
                     "请使用单个视频链接：\n"
                     "https://www.douyin.com/video/xxxxxxxxxx\n\n"
                     "在抖音 App 中打开视频 -> 分享 -> 复制链接")
        # 已知不支持的其他搜索页
        if re.search(r'tiktok\.com/(search|explore)/', url):
            return (False,
                     "TikTok 搜索/探索页面不被支持\n\n"
                     "请使用单个视频链接：\n"
                     "https://www.tiktok.com/@username/video/xxxxxxxxxx")

        # 已知支持的视频 URL 模式
        supported_patterns = [
            r'(youtube\.com|youtu\.be)',
            r'bilibili\.com/video/',
            r'bilibili\.com/medialist/',
            r'bilibili\.com/playlist/',
            r'douyin\.com/video/',
            r'douyin\.com/note/',
            r'douyin\.com/slide/',
            r'iesdouyin\.com',
            r'twitter\.com/\w+/status/',
            r'instagram\.com/(p|reel)/',
            r'tiktok\.com/@.*/video/',
            r'vimeo\.com/\d+',
            r'facebook\.com/.*/videos/',
            r'ixigua\.com',
            r'huya\.com',
            r'douyu\.com',
        ]
        is_known = any(re.search(p, url) for p in supported_patterns)
        return (True, "") if is_known else (True, "")  # 未知网站也放行，让 yt-dlp 自行判断
