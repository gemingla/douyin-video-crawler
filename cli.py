#!/usr/bin/env python3
"""视频爬虫命令行版（抖音增强专用）

用法：
    python cli.py <视频链接>                     # 提取并下载（默认最高画质）
    python cli.py <链接> --extract-only          # 只提取元数据，不下载
    python cli.py <链接> --headful               # 弹出真实浏览器（反检测最稳）
    python cli.py <链接> --channel msedge        # 使用系统 Edge 内核
    python cli.py <链接> -o D:/download          # 指定输出目录
    python cli.py <链接> --timeout 60            # 页面加载超时（秒）

抖音链接支持：v.douyin.com 短链 / douyin.com/video/xxx /
             iesdouyin.com/share/video/xxx/ / douyin.com/note/xxx（图集）
"""

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from utils.helpers import fix_console_encoding
fix_console_encoding()


def _progress_bar(downloaded: int, total: int, msg: str = ""):
    if total > 0:
        pct = min(downloaded / total * 100, 100)
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        line = (f"\r  [{bar}] {pct:5.1f}%  "
                f"({downloaded / 1048576:.1f}MB / {total / 1048576:.1f}MB)")
    else:
        line = f"\r  已下载 {downloaded / 1048576:.1f}MB"
    sys.stdout.write(line + "   " + msg)
    sys.stdout.flush()


def print_meta(result: dict):
    print("=" * 56)
    print(f"  视频ID:   {result.get('video_id', '')}")
    print(f"  标题:     {result.get('title', '')}")
    print(f"  作者:     {result.get('uploader', '')}")
    if result.get("duration"):
        print(f"  时长:     {int(result['duration'])} 秒")
    if result.get("width") or result.get("height"):
        print(f"  分辨率:   {result.get('width', '?')}x{result.get('height', '?')}")
    if result.get("digg_count"):
        print(f"  点赞:     {result.get('digg_count')}")
    if result.get("images"):
        print(f"  图集:     {len(result['images'])} 张")
    print(f"  提取方式: {result.get('method', '')}")
    print(f"  候选地址: {len(result.get('urls') or [])} 条")
    print("=" * 56)


def main():
    ap = argparse.ArgumentParser(description="视频爬虫（抖音增强）")
    ap.add_argument("url", help="视频链接")
    ap.add_argument("-o", "--output", default=str(ROOT / "downloads"),
                    help="输出目录（默认 downloads/）")
    ap.add_argument("--extract-only", action="store_true",
                    help="只提取元数据，不下载")
    ap.add_argument("--headless", dest="headless", action="store_true",
                    default=True, help="无头模式（默认）")
    ap.add_argument("--headful", dest="headless", action="store_false",
                    help="有头模式：弹出真实浏览器窗口")
    ap.add_argument("--channel", default="chromium",
                    choices=["chromium", "chrome", "msedge"],
                    help="浏览器内核（默认 chromium 完整版 new-headless）")
    ap.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    ap.add_argument("--timeout", type=int, default=45,
                    help="页面超时（秒，默认 45）")
    ap.add_argument("--quality", default="best",
                    choices=["best", "1080p", "720p", "540p", "360p"],
                    help="画质档位（默认 best=取最大可用）")
    ap.add_argument("--image-format", default="jpg",
                    choices=["jpg", "png", "webp", "keep"],
                    help="图集图片保存格式（默认 jpg；keep=保持源格式）")
    ap.add_argument("--login-cookie", default=None,
                    help="Netscape 格式 Cookie 文件（导出 douyin.com 的"
                         "登录 Cookie，成功率最高）")
    ap.add_argument("--cdp", default=None,
                    help="连接已打开的浏览器远程调试端口，"
                         "如 http://127.0.0.1:9222（用真实登录态）")
    ap.add_argument("--ytdlp-only", action="store_true",
                    help="只用 yt-dlp（非抖音站点或调试用）")
    args = ap.parse_args()

    from core.douyin_extractor import (
        extract_douyin_video, download_douyin_result,
        download_douyin_images, DouyinError,
    )
    from core.extractor import VideoExtractor

    is_douyin = ("douyin" in args.url.lower())
    has_playwright = True
    try:
        import playwright  # noqa: F401
    except ImportError:
        has_playwright = False

    if is_douyin and not args.ytdlp_only and not has_playwright:
        print("缺少 playwright，请先: pip install playwright && "
              "python -m playwright install chromium")
        return 1

    if is_douyin and not args.ytdlp_only:
        print(f"[1/3] 初始化浏览器提取（channel={args.channel}, "
              f"headless={args.headless}）...")

        def cb(msg):
            print(f"       {msg}")

        result = extract_douyin_video(
            args.url,
            timeout=args.timeout * 1000,
            headless=args.headless,
            channel=args.channel,
            proxy=args.proxy,
            login_cookie_file=args.login_cookie,
            cdp_url=args.cdp,
            progress_cb=cb,
        )
        if not result.get("success"):
            print(f"\n提取失败：{result.get('error', '未知错误')}")
            return 2

        print_meta(result)

        if args.extract_only:
            return 0

        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = result.get("title") or "抖音视频"
        print(f"\n[2/3] 下载中 -> {out_dir.resolve()}")
        cancel = threading.Event()
        try:
            if result.get("images") and not result.get("urls"):
                files = download_douyin_images(
                    result, str(out_dir), name,
                    progress_cb=lambda d, t, s: None,
                    cancel_event=cancel,
                    image_format=args.image_format,
                )
                print(f"\n[3/3] 完成：{len(files)} 张图片（{args.image_format}）")
                for f in files:
                    print(f"   {f}")
            else:
                path = download_douyin_result(
                    result, str(out_dir), name,
                    quality=args.quality,
                    progress_cb=lambda d, t, s: (
                        _progress_bar(d, t) if s == "downloading"
                        else print("\n[3/3] 完成")),
                    cancel_event=cancel,
                )
                print(f"\n[3/3] 完成：{path}")
        except DouyinError as e:
            print(f"\n下载失败：{e}")
            return 3
        return 0

    # ── 通用（yt-dlp）路径 ──
    print("[1/3] 使用 yt-dlp 提取...")
    ex = VideoExtractor()
    info = ex.extract(args.url)
    title = info.get("title", "视频")
    print(f"  标题: {title}")
    print(f"  时长: {info.get('duration', '未知')} 秒")
    if args.extract_only:
        return 0

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    import yt_dlp

    def hook(d):
        if d["status"] == "downloading":
            _progress_bar(d.get("downloaded_bytes", 0),
                          d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
        elif d["status"] == "finished":
            print("\n[3/3] 完成")

    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([args.url])
    return 0


if __name__ == "__main__":
    sys.exit(main())
