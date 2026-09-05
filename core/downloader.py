"""下载管理器 - 多线程队列下载"""

import os
import re
import threading
import queue
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field

import yt_dlp

from core.extractor import VideoExtractor


def _looks_like_video(url: str) -> bool:
    """粗略判断 URL 是否为视频（CDN/play 接口），区别于图片"""
    if re.search(r'aweme/v1/play', url, re.I):
        return True
    if re.search(r'(\.jpg|\.jpeg|\.png|\.webp)(\?|$)', url, re.I):
        return False
    return bool(re.search(
        r'(douyinvod|zjcdn|bytovod|v3-dy|v5-dy|iesdouyin|video|\.mp4)', url,
        re.I))


@dataclass
class DownloadTask:
    """单个下载任务"""
    video_item: Dict[str, Any]
    format_code: str
    output_dir: str
    filename: str = ""
    progress: float = 0.0
    speed: Optional[float] = None
    eta: Optional[float] = None
    total_bytes: Optional[float] = None
    downloaded_bytes: float = 0.0
    status: str = "pending"  # pending | downloading | completed | failed | cancelled
    error: Optional[str] = None
    task_id: int = 0

    @property
    def title_display(self) -> str:
        title = self.video_item.get("title", "未知")
        if len(title) > 50:
            return title[:47] + "..."
        return title


class DownloadManager:
    """管理下载队列和并发"""

    def __init__(self, extractor: VideoExtractor, max_concurrent: int = 3):
        self._extractor = extractor
        self._max_concurrent = max_concurrent
        self._queue: List[DownloadTask] = []
        self._active: List[DownloadTask] = []
        self._completed: List[DownloadTask] = []
        self._task_counter = 0
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

        # 进度更新队列（线程 -> UI）
        self.progress_queue: queue.Queue = queue.Queue()

        # 回调
        self.on_task_added: Optional[Callable] = None
        self.on_task_started: Optional[Callable] = None
        self.on_task_progress: Optional[Callable] = None
        self.on_task_completed: Optional[Callable] = None
        self.on_task_failed: Optional[Callable] = None
        self.on_all_completed: Optional[Callable] = None

    @property
    def total_count(self) -> int:
        return len(self._queue) + len(self._active) + len(self._completed)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def completed_count(self) -> int:
        return len([t for t in self._completed if t.status == "completed"])

    @property
    def failed_count(self) -> int:
        return len([t for t in self._completed if t.status == "failed"])

    @property
    def is_busy(self) -> bool:
        return len(self._active) > 0 or len(self._queue) > 0

    def add_task(self, video_item: Dict[str, Any], format_code: str, output_dir: str) -> DownloadTask:
        """添加下载任务"""
        with self._lock:
            self._task_counter += 1
            task = DownloadTask(
                video_item=video_item,
                format_code=format_code,
                output_dir=output_dir,
                task_id=self._task_counter,
            )
            self._queue.append(task)

        if self.on_task_added:
            self.on_task_added(task)

        self._process_queue()
        return task

    def add_tasks(self, items: List[tuple], format_code: str, output_dir: str) -> List[DownloadTask]:
        """批量添加任务。items: [(video_item,), ...]"""
        tasks = []
        for (video_item,) in items:
            tasks.append(self.add_task(video_item, format_code, output_dir))
        return tasks

    def _process_queue(self):
        """处理队列（启动新任务）"""
        while True:
            with self._lock:
                if len(self._active) >= self._max_concurrent or not self._queue:
                    break
                task = self._queue.pop(0)
                self._active.append(task)

            self._start_download(task)

    def _start_download(self, task: DownloadTask):
        """在后台线程启动下载"""
        task.status = "downloading"

        if self.on_task_started:
            self.on_task_started(task)

        thread = threading.Thread(
            target=self._download_worker,
            args=(task,),
            daemon=True,
        )
        thread.start()

    def _download_worker(self, task: DownloadTask):
        """下载工作线程"""
        def progress_hook(d: Dict):
            if self._cancel_event.is_set():
                raise Exception("下载已取消")

            if d["status"] == "downloading":
                task.total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
                task.downloaded_bytes = d.get("downloaded_bytes", 0)
                task.speed = d.get("speed")
                task.eta = d.get("eta")
                if task.total_bytes and task.total_bytes > 0:
                    task.progress = min(task.downloaded_bytes / task.total_bytes * 100, 99.9)
                task.filename = os.path.basename(d.get("filename", ""))
                self.progress_queue.put(("progress", task))
            elif d["status"] == "finished":
                task.progress = 100
                task.status = "completed"
                task.filename = os.path.basename(d.get("filename", ""))
                self.progress_queue.put(("completed", task))

        try:
            is_douyin_fallback = task.video_item.get("_douyin_fallback", False)

            if is_douyin_fallback:
                self._download_douyin(task)
            else:
                is_batch = task.video_item.get("_batch", False)
                opts = self._extractor.get_download_opts(
                    format_code=task.format_code,
                    output_dir=task.output_dir,
                    progress_hook=progress_hook,
                )
                # m3u8/HLS 批量下载加速
                opts["concurrent_fragments"] = 16 if is_batch else 2
                if is_batch:
                    opts["downloader"] = "aria2c"
                    opts["downloader_args"] = {"aria2c": [
                        "-x", "8", "-s", "8", "-k", "1M",
                        "--min-split-size", "1M",
                        "--max-connection-per-server", "8",
                    ]}
                opts["postprocessor_args"] = {"ffmpeg": ["-threads", "4"]}
                opts["embedthumbnail"] = False
                opts["embedsubs"] = False

                url = task.video_item.get("url") or task.video_item.get("_raw", {}).get("webpage_url", "")
                if not url:
                    raise ValueError("视频 URL 为空")

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

            if task.status != "completed":
                task.status = "completed"
                task.progress = 100
                self.progress_queue.put(("completed", task))

        except Exception as e:
            # 检查是否取消
            if self._cancel_event.is_set():
                task.status = "cancelled"
                self.progress_queue.put(("cancelled", task))
            else:
                task.status = "failed"
                task.error = str(e)
                self.progress_queue.put(("failed", task))

        # 从活跃列表移除
        with self._lock:
            if task in self._active:
                self._active.remove(task)
            if task.status in ("completed", "failed", "cancelled"):
                self._completed.append(task)

        # 继续处理队列
        self._process_queue()

        # 检查是否全部完成
        if not self.is_busy and self.on_all_completed:
            self.on_all_completed()

    def _find_best_video_url(self, urls: list) -> Optional[str]:
        """从多个视频 URL 中找到真正可用的（最大、非 404、非页面）"""
        import requests as _req

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        }

        candidates = []
        for u in urls:
            # 跳过页面 URL
            if re.search(r'douyin\.com/(video|note)/', u):
                continue
            try:
                resp = _req.head(u, headers=headers, timeout=10, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("content-type", "")
                if "video" not in content_type:
                    continue
                size = int(resp.headers.get("content-length", 0))
                candidates.append((size, u))
            except Exception:
                continue

        if not candidates:
            return None

        # 按文件大小降序排列，取最大的（= 最清晰的）
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_size, best_url = candidates[0]
        print(f"  [选择最佳视频] {best_size/1024/1024:.1f}MB -> {best_url[:80]}...")
        return best_url

    def _download_douyin(self, task: DownloadTask):
        """抖音下载：交给新版下载引擎（候选探测 + Cookie + uri 重建）"""
        item = task.video_item
        from core.douyin_extractor import (
            download_douyin_result, download_douyin_images,
            DouyinError, _clean_url,
        )

        # 组装提取结果（兼容新旧格式）
        raw = item.get("_raw") or {}
        if isinstance(raw, dict) and raw.get("urls"):
            result = dict(raw)
        else:
            urls = []
            seen = set()
            for u in item.get("all_urls") or []:
                u = _clean_url(u)
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)
            result = {
                "urls": urls,
                "cookies": item.get("_cookies") or [],
                "uri": item.get("_douyin_uri") or "",
                "images": item.get("_douyin_images") or [],
                "title": item.get("title", "抖音视频"),
                "video_id": "",
            }
        result.setdefault("cookies", item.get("_cookies") or [])
        result.setdefault("images", item.get("_douyin_images") or [])
        result.setdefault("uri", item.get("_douyin_uri") or "")

        # 图集（无视频直链但有多图）
        is_images_only = (
            (not result.get("urls"))
            and bool(result.get("images"))
        ) or (
            not any(_looks_like_video(u) for u in result.get("urls") or [])
            and bool(result.get("images"))
        )

        def on_progress(downloaded, total, status):
            if self._cancel_event.is_set():
                return
            if status == "finished":
                task.progress = 100
                task.total_bytes = total or task.total_bytes
                return
            task.downloaded_bytes = downloaded
            if total and total > 0:
                task.total_bytes = total
                task.progress = min(downloaded / total * 100, 99.9)
            self.progress_queue.put(("progress", task))

        try:
            if is_images_only:
                saved = download_douyin_images(
                    result, task.output_dir,
                    filename=item.get("title", "抖音图集"),
                    progress_cb=on_progress,
                    cancel_event=self._cancel_event,
                    image_format=item.get("_image_format") or "jpg",
                )
                if not saved:
                    raise DouyinError("图集图片下载失败")
                task.filename = os.path.basename(saved[-1])
                task.progress = 100
                task.status = "completed"
                self.progress_queue.put(("completed", task))
                print(f"[抖音] 图集下载完成: {len(saved)} 张")
                return

            filepath = download_douyin_result(
                result, task.output_dir,
                filename=item.get("title", "抖音视频"),
                progress_cb=on_progress,
                cancel_event=self._cancel_event,
            )
            task.filename = os.path.basename(filepath)
            task.progress = 100
            task.status = "completed"
            print(f"[抖音] 下载完成: {filepath}")
            self.progress_queue.put(("completed", task))

        except DouyinError as e:
            if self._cancel_event.is_set():
                task.status = "cancelled"
                self.progress_queue.put(("cancelled", task))
            else:
                task.status = "failed"
                task.error = str(e)
                self.progress_queue.put(("failed", task))
            raise  # 外层 except 会再次标记，但状态已正确

    def _find_best_video_url(self, urls: list) -> Optional[str]:
        """从多个视频 URL 中找到最大可用的（HEAD 探测）"""
        import requests as _req

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        }

        candidates = []
        for u in urls:
            # 跳过页面 URL
            if re.search(r'douyin\.com/(video|note)/', u):
                continue
            try:
                resp = _req.head(u, headers=headers, timeout=10,
                                 allow_redirects=True)
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("content-type", "")
                if "video" not in content_type:
                    continue
                size = int(resp.headers.get("content-length", 0))
                candidates.append((size, u))
            except Exception:
                continue

        if not candidates:
            return None

        # 按文件大小降序排列，取最大的（= 最清晰的）
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_size, best_url = candidates[0]
        print(f"  [选择最佳视频] {best_size/1024/1024:.1f}MB -> {best_url[:80]}...")
        return best_url

    def cancel_task(self, task: DownloadTask):
        """取消单个任务"""
        task.status = "cancelled"
        with self._lock:
            if task in self._queue:
                self._queue.remove(task)
                self._completed.append(task)
            if task in self._active:
                # 正在下载的通过 cancel_event 停止
                pass

    def cancel_all(self):
        """取消所有任务"""
        self._cancel_event.set()
        with self._lock:
            remaining = self._queue.copy()
            for t in remaining:
                t.status = "cancelled"
                self._queue.remove(t)
                self._completed.append(t)
        # 重置取消事件（新任务仍可添加）
        self._cancel_event.clear()

    def cleanup(self):
        """彻底清理：取消任务 + 杀 aria2c + 删 .part 残留"""
        import subprocess, glob

        print("[清理] 正在停止所有下载...")
        self.cancel_all()

        # Windows 杀 aria2c 进程（仅 Windows）
        import sys as _sys
        if _sys.platform.startswith("win"):
            try:
                subprocess.run(["taskkill", "/f", "/im", "aria2c.exe"],
                               capture_output=True, timeout=5)
            except Exception:
                pass

        # 删除 .part 残留
        output_dir = os.path.dirname(self._queue[0].output_dir) if self._queue else None
        if not output_dir:
            output_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        for f in glob.glob(os.path.join(output_dir, "*.part")) + glob.glob(os.path.join(output_dir, "*.ytdl")):
            try:
                os.remove(f)
                print(f"[清理] 删除残留: {os.path.basename(f)}")
            except Exception:
                pass

        print("[清理] 完成")

    def set_max_concurrent(self, n: int):
        """设置最大并发数"""
        with self._lock:
            self._max_concurrent = max(1, n)
        self._process_queue()
