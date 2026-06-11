import os
import sys
import time
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable, Dict, Set
import requests

from .parser import M3U8Playlist, Segment
from .decryptor import KeyManager, SegmentDecryptor
from .utils import (
    get_ts_filename,
    get_decrypted_ts_filename,
    format_size,
    format_speed,
    format_time
)


class DownloadResult:
    def __init__(self, total_segments: int):
        self.total_segments = total_segments
        self.success_segments: Set[int] = set()
        self.failed_segments: Dict[int, str] = {}
        self.decrypted_segments: Set[int] = set()
        self.decrypt_failed: Dict[int, str] = {}
        self.total_bytes: int = 0
        self.cancelled = False
        self.error_message: Optional[str] = None

    @property
    def success_count(self) -> int:
        return len(self.success_segments)

    @property
    def failed_count(self) -> int:
        return len(self.failed_segments)

    @property
    def all_success(self) -> bool:
        return (self.success_count == self.total_segments
                and not self.cancelled
                and len(self.decrypt_failed) == 0)

    @property
    def can_merge(self) -> bool:
        return (self.success_count == self.total_segments
                and not self.cancelled
                and len(self.decrypt_failed) == 0)

    def list_failed(self) -> str:
        if not self.failed_segments and not self.decrypt_failed:
            return ""
        lines = []
        if self.failed_segments:
            lines.append("下载失败的分片:")
            for idx, err in sorted(self.failed_segments.items()):
                lines.append(f"  分片 {idx}: {err}")
        if self.decrypt_failed:
            lines.append("解密失败的分片:")
            for idx, err in sorted(self.decrypt_failed.items()):
                lines.append(f"  分片 {idx}: {err}")
        return "\n".join(lines)


class DownloadProgress:
    def __init__(self, total_segments: int, estimated_total_bytes: int = 0):
        self.total_segments = total_segments
        self.completed_segments = 0
        self.total_bytes = estimated_total_bytes
        self.downloaded_bytes = 0
        self.in_progress_bytes = 0
        self.start_time = time.time()
        self.last_update_time = time.time()
        self.last_bytes = 0
        self.current_speed = 0
        self._lock = threading.Lock()
        self._running = True

    def add_in_progress(self, size_bytes: int):
        with self._lock:
            self.in_progress_bytes += size_bytes

    def remove_in_progress(self, size_bytes: int):
        with self._lock:
            self.in_progress_bytes = max(0, self.in_progress_bytes - size_bytes)

    def add_completed(self, size_bytes: int):
        with self._lock:
            self.completed_segments += 1
            self.downloaded_bytes += size_bytes

    def set_total_bytes(self, total: int):
        with self._lock:
            self.total_bytes = total

    def update_speed(self):
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update_time
            if elapsed > 0.2:
                bytes_diff = self.downloaded_bytes - self.last_bytes
                self.current_speed = bytes_diff / elapsed if elapsed > 0 else 0
                self.last_bytes = self.downloaded_bytes
                self.last_update_time = now

    @property
    def total_display_bytes(self) -> int:
        return self.downloaded_bytes + self.in_progress_bytes

    @property
    def percentage(self) -> float:
        if self.total_segments == 0:
            return 100.0
        return (self.completed_segments / self.total_segments) * 100

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    @property
    def eta(self) -> Optional[float]:
        if self.total_segments > 0 and self.completed_segments > 0:
            remaining_segments = self.total_segments - self.completed_segments
            time_per_segment = self.elapsed_time / self.completed_segments
            return remaining_segments * time_per_segment
        if self.current_speed > 0 and self.total_bytes > 0:
            remaining = max(0, self.total_bytes - self.downloaded_bytes)
            return remaining / self.current_speed if self.current_speed > 0 else None
        return None

    def stop(self):
        self._running = False


class ProgressBar:
    def __init__(self, width: int = 40):
        self.width = width
        self._last_line_length = 0

    def render(self, progress: DownloadProgress):
        progress.update_speed()
        pct = progress.percentage
        filled = int(self.width * pct / 100)
        bar = "█" * filled + "░" * (self.width - filled)

        status = f"\r[{bar}] {pct:5.1f}% "
        status += f"({progress.completed_segments}/{progress.total_segments}) "
        status += f"{format_speed(progress.current_speed)} "

        if progress.eta is not None:
            status += f"ETA: {format_time(progress.eta)}"
        else:
            status += "ETA: --"

        display_bytes = progress.total_display_bytes
        if display_bytes > 0:
            status += f"  已下载: {format_size(display_bytes)}"

        sys.stdout.write(status)
        sys.stdout.flush()
        self._last_line_length = len(status)

    def clear(self):
        sys.stdout.write("\r" + " " * self._last_line_length + "\r")
        sys.stdout.flush()

    def finish(self, progress: DownloadProgress):
        self.clear()
        pct = progress.percentage
        filled = int(self.width * pct / 100)
        bar = "█" * filled + "░" * (self.width - filled)
        status = f"[{bar}] {pct:5.1f}% "
        status += f"({progress.completed_segments}/{progress.total_segments}) "
        status += f"总大小: {format_size(progress.downloaded_bytes)} "
        status += f"用时: {format_time(progress.elapsed_time)}"
        print(status)


class M3U8Downloader:
    def __init__(
        self,
        playlist: M3U8Playlist,
        output_dir: str,
        concurrency: int = 5,
        proxies: Optional[dict] = None,
        headers: Optional[dict] = None,
        retries: int = 5,
        timeout: int = 60,
        resume: bool = True,
        progress_callback: Optional[Callable] = None
    ):
        self.playlist = playlist
        self.output_dir = output_dir
        self.concurrency = max(1, concurrency)
        self.proxies = proxies
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.retries = max(1, retries)
        self.timeout = timeout
        self.resume = resume
        self.progress_callback = progress_callback

        self.key_manager = KeyManager()
        self._progress: Optional[DownloadProgress] = None
        self._progress_bar = ProgressBar()
        self.result: Optional[DownloadResult] = None

    def _fetch_key(self, key_url: str) -> Optional[bytes]:
        try:
            response = requests.get(
                key_url,
                proxies=self.proxies,
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    def _download_single_segment(self, segment: Segment) -> tuple[bool, int, str]:
        filename = get_ts_filename(segment.index)
        filepath = os.path.join(self.output_dir, filename)
        temp_filepath = filepath + ".part"
        downloaded_this_call = 0

        if self.resume and os.path.exists(filepath):
            size = os.path.getsize(filepath)
            if size > 0:
                return True, size, "already_downloaded"

        last_error = ""
        for attempt in range(self.retries):
            try:
                response = requests.get(
                    segment.url,
                    proxies=self.proxies,
                    headers=self.headers,
                    timeout=self.timeout,
                    stream=True
                )
                response.raise_for_status()

                content_length = response.headers.get("Content-Length")
                expected_size = int(content_length) if content_length else 0

                bytes_downloaded = 0
                with open(temp_filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            chunk_len = len(chunk)
                            bytes_downloaded += chunk_len
                            downloaded_this_call += chunk_len
                            if self._progress:
                                self._progress.add_in_progress(chunk_len)

                if expected_size > 0 and bytes_downloaded < expected_size * 0.9:
                    raise ValueError(
                        f"不完整下载: {bytes_downloaded}/{expected_size} bytes"
                    )

                if bytes_downloaded == 0:
                    raise ValueError("下载的文件为空")

                if os.path.exists(filepath):
                    os.remove(filepath)
                os.rename(temp_filepath, filepath)

                return True, bytes_downloaded, ""

            except Exception as e:
                last_error = str(e)
                if os.path.exists(temp_filepath):
                    try:
                        os.remove(temp_filepath)
                    except:
                        pass
                if attempt < self.retries - 1:
                    backoff = min(2 ** attempt, 10)
                    time.sleep(backoff)

        return False, 0, last_error

    def _decrypt_segment(self, segment: Segment) -> tuple[bool, str]:
        if segment.encryption.method == "NONE":
            return True, ""

        key_url = segment.encryption.key_url
        if not key_url:
            return False, "缺失密钥URL"

        decryptor = self.key_manager.get_decryptor(key_url, self._fetch_key)
        if not decryptor:
            return False, f"获取密钥失败: {key_url}"

        input_file = os.path.join(self.output_dir, get_ts_filename(segment.index))
        output_file = os.path.join(self.output_dir, get_decrypted_ts_filename(segment.index))

        if self.resume and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return True, "already_decrypted"

        ok = decryptor.decrypt_segment_file(
            input_file, output_file, segment.index, segment.encryption.iv
        )
        if not ok:
            return False, "解密处理失败"

        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            return False, "解密后文件为空"

        return True, ""

    def get_segment_files(self) -> List[str]:
        files = []
        for segment in self.playlist.segments:
            if segment.encryption.method == "NONE":
                filename = get_ts_filename(segment.index)
            else:
                filename = get_decrypted_ts_filename(segment.index)
            files.append(os.path.join(self.output_dir, filename))
        return files

    def download(self, show_progress: bool = True) -> DownloadResult:
        os.makedirs(self.output_dir, exist_ok=True)

        segments = self.playlist.segments
        total = len(segments)

        self.result = DownloadResult(total)

        if total == 0:
            self.result.error_message = "没有可下载的分片"
            return self.result

        self._progress = DownloadProgress(total)

        progress_thread = None
        if show_progress:
            progress_thread = threading.Thread(
                target=self._progress_monitor,
                daemon=True
            )
            progress_thread.start()

        download_errors = 0

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_segment = {
                executor.submit(self._download_single_segment, seg): seg
                for seg in segments
            }

            for future in as_completed(future_to_segment):
                segment = future_to_segment[future]
                try:
                    ok, size, note = future.result()
                    if self._progress:
                        self._progress.remove_in_progress(size)
                    if ok:
                        self.result.success_segments.add(segment.index)
                        self.result.total_bytes += size
                        if self._progress:
                            self._progress.add_completed(size)
                    else:
                        self.result.failed_segments[segment.index] = note or "未知错误"
                        download_errors += 1
                        if download_errors >= 20:
                            self.result.cancelled = True
                            self.result.error_message = f"下载失败过多 ({download_errors})，中止任务"
                            break
                except Exception as e:
                    self.result.failed_segments[segment.index] = str(e)
                    download_errors += 1

        if self._progress:
            self._progress.stop()

        if show_progress and progress_thread:
            progress_thread.join(timeout=1.0)
            self._progress_bar.finish(self._progress)

        if not self.result.can_merge:
            print("下载阶段结束，但有分片缺失：")
            print(self.result.list_failed())
            return self.result

        if self.playlist.is_encrypted:
            print("正在解密分片...")
            decrypt_total = 0
            for segment in segments:
                if segment.encryption.method != "NONE":
                    decrypt_total += 1

            if decrypt_total > 0:
                decrypt_success = 0
                for i, segment in enumerate(segments, 1):
                    if segment.encryption.method != "NONE":
                        ok, err = self._decrypt_segment(segment)
                        sys.stdout.write(
                            f"\r解密进度: {i}/{decrypt_total} "
                        )
                        sys.stdout.flush()
                        if ok:
                            decrypt_success += 1
                            self.result.decrypted_segments.add(segment.index)
                        else:
                            self.result.decrypt_failed[segment.index] = err
                print()

                if self.result.decrypt_failed:
                    print("解密阶段结束，但有分片解密失败：")
                    print(self.result.list_failed())

        return self.result

    def _progress_monitor(self):
        while self._progress and self._progress._running:
            self._progress_bar.render(self._progress)
            time.sleep(0.15)
