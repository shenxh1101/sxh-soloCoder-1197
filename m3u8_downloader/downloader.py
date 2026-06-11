import os
import sys
import time
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable
import requests

from .parser import M3U8Playlist, Segment
from .decryptor import AES128Decryptor, get_iv_from_segment_index
from .utils import (
    get_ts_filename,
    get_decrypted_ts_filename,
    format_size,
    format_speed,
    format_time
)


class DownloadProgress:
    def __init__(self, total_segments: int):
        self.total_segments = total_segments
        self.completed_segments = 0
        self.total_bytes = 0
        self.downloaded_bytes = 0
        self.start_time = time.time()
        self.last_update_time = time.time()
        self.last_bytes = 0
        self.current_speed = 0
        self._lock = threading.Lock()
        self._running = True

    def add_completed(self, size_bytes: int):
        with self._lock:
            self.completed_segments += 1
            self.downloaded_bytes += size_bytes

    def update_speed(self):
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update_time
            if elapsed > 0.5:
                bytes_diff = self.downloaded_bytes - self.last_bytes
                self.current_speed = bytes_diff / elapsed if elapsed > 0 else 0
                self.last_bytes = self.downloaded_bytes
                self.last_update_time = now

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
        if self.current_speed <= 0 or self.total_bytes <= 0:
            if self.completed_segments > 0 and self.total_segments > 0:
                remaining_segments = self.total_segments - self.completed_segments
                time_per_segment = self.elapsed_time / self.completed_segments
                return remaining_segments * time_per_segment
            return None
        remaining_bytes = self.total_bytes - self.downloaded_bytes
        return remaining_bytes / self.current_speed if self.current_speed > 0 else None

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

        if progress.downloaded_bytes > 0:
            status += f"  {format_size(progress.downloaded_bytes)}"

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
        retries: int = 3,
        timeout: int = 30,
        resume: bool = True
    ):
        self.playlist = playlist
        self.output_dir = output_dir
        self.concurrency = concurrency
        self.proxies = proxies
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.retries = retries
        self.timeout = timeout
        self.resume = resume
        self.decryptor_map = {}
        self._progress = None
        self._progress_bar = ProgressBar()

    def _get_decryptor(self, segment: Segment) -> Optional[AES128Decryptor]:
        if segment.encryption.method == "NONE":
            return None

        key_url = segment.encryption.key_url
        if key_url in self.decryptor_map:
            return self.decryptor_map[key_url]

        try:
            response = requests.get(
                key_url,
                proxies=self.proxies,
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            key = response.content

            iv = None
            if segment.encryption.iv:
                iv_str = segment.encryption.iv
                if iv_str.startswith("0x") or iv_str.startswith("0X"):
                    iv = bytes.fromhex(iv_str[2:])
                else:
                    iv = bytes.fromhex(iv_str)
            else:
                iv = get_iv_from_segment_index(segment.index)

            decryptor = AES128Decryptor(key, iv)
            self.decryptor_map[key_url] = decryptor
            return decryptor
        except Exception as e:
            print(f"\n获取密钥失败: {e}")
            return None

    def _download_segment(self, segment: Segment) -> bool:
        filename = get_ts_filename(segment.index)
        filepath = os.path.join(self.output_dir, filename)

        if self.resume and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            size = os.path.getsize(filepath)
            self._progress.add_completed(size)
            return True

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

                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                size = os.path.getsize(filepath)
                if size == 0:
                    raise ValueError("下载的文件为空")

                self._progress.add_completed(size)
                return True

            except Exception:
                if attempt < self.retries - 1:
                    time.sleep(1 * (attempt + 1))
                else:
                    return False

        return False

    def _decrypt_if_needed(self, segment: Segment) -> bool:
        if segment.encryption.method == "NONE":
            return True

        decryptor = self._get_decryptor(segment)
        if not decryptor:
            return False

        input_file = os.path.join(self.output_dir, get_ts_filename(segment.index))
        output_file = os.path.join(self.output_dir, get_decrypted_ts_filename(segment.index))

        if self.resume and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return True

        return decryptor.decrypt_segment(input_file, output_file)

    def get_segment_files(self) -> List[str]:
        files = []
        for segment in self.playlist.segments:
            if segment.encryption.method == "NONE":
                filename = get_ts_filename(segment.index)
            else:
                filename = get_decrypted_ts_filename(segment.index)
            files.append(os.path.join(self.output_dir, filename))
        return files

    def download(self, show_progress: bool = True) -> bool:
        os.makedirs(self.output_dir, exist_ok=True)

        segments = self.playlist.segments
        total = len(segments)
        if total == 0:
            print("没有可下载的分片")
            return False

        self._progress = DownloadProgress(total)

        if show_progress:
            progress_thread = threading.Thread(
                target=self._progress_monitor,
                daemon=True
            )
            progress_thread.start()

        success_count = 0
        failed_segments = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_segment = {
                executor.submit(self._download_segment, seg): seg
                for seg in segments
            }

            for future in as_completed(future_to_segment):
                segment = future_to_segment[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        failed_segments.append(segment)
                except Exception:
                    failed_segments.append(segment)

        self._progress.stop()

        if show_progress:
            self._progress_bar.finish(self._progress)

        if failed_segments:
            print(f"警告: {len(failed_segments)} 个分片下载失败")
            if len(failed_segments) > total * 0.1:
                print("失败分片过多，停止处理")
                return False

        if self.playlist.is_encrypted:
            print("正在解密分片...")
            decrypt_success = 0
            for segment in self.playlist.segments:
                if segment.encryption.method != "NONE":
                    if self._decrypt_if_needed(segment):
                        decrypt_success += 1
                    else:
                        print(f"分片 {segment.index} 解密失败")

            print(f"解密完成: {decrypt_success}/{total}")

        return success_count > total * 0.9

    def _progress_monitor(self):
        while self._progress._running:
            self._progress_bar.render(self._progress)
            time.sleep(0.3)
