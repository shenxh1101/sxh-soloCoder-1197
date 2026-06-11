import os
import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime


TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_SKIPPED = "skipped"


@dataclass
class TaskRecord:
    url: str
    status: str = TASK_STATUS_PENDING
    output_file: Optional[str] = None
    output_format: Optional[str] = None
    total_segments: int = 0
    downloaded_bytes: int = 0
    error_message: Optional[str] = None
    failed_segments: List[int] = field(default_factory=list)
    decrypt_failed: List[int] = field(default_factory=list)
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0
    quality_index: Optional[int] = None
    quality_label: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskRecord":
        return cls(
            url=data.get("url", ""),
            status=data.get("status", TASK_STATUS_PENDING),
            output_file=data.get("output_file"),
            output_format=data.get("output_format"),
            total_segments=data.get("total_segments", 0),
            downloaded_bytes=data.get("downloaded_bytes", 0),
            error_message=data.get("error_message"),
            failed_segments=list(data.get("failed_segments", [])),
            decrypt_failed=list(data.get("decrypt_failed", [])),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            duration_seconds=data.get("duration_seconds", 0.0),
            quality_index=data.get("quality_index"),
            quality_label=data.get("quality_label"),
        )


class TaskRecorder:
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.tasks: Dict[str, TaskRecord] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for url, task_data in data.items():
                    self.tasks[url] = TaskRecord.from_dict(task_data)
            except Exception:
                self.tasks = {}

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.log_file)) or ".", exist_ok=True)
        data = {}
        for url, record in self.tasks.items():
            data[url] = record.to_dict()
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存任务记录失败: {e}")

    @staticmethod
    def _url_key(url: str) -> str:
        return url

    def get_or_create(self, url: str) -> TaskRecord:
        key = self._url_key(url)
        if key not in self.tasks:
            self.tasks[key] = TaskRecord(
                url=url,
                created_at=datetime.now().isoformat(timespec='seconds')
            )
        return self.tasks[key]

    def mark_running(self, url: str, output_format: Optional[str] = None,
                     quality_index: Optional[int] = None,
                     quality_label: Optional[str] = None):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_RUNNING
        task.started_at = datetime.now().isoformat(timespec='seconds')
        if output_format:
            task.output_format = output_format
        if quality_index is not None:
            task.quality_index = quality_index
        if quality_label:
            task.quality_label = quality_label
        self.save()

    def mark_success(self, url: str, output_file: str, total_segments: int,
                     downloaded_bytes: int):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_SUCCESS
        task.output_file = output_file
        task.total_segments = total_segments
        task.downloaded_bytes = downloaded_bytes
        task.error_message = None
        task.failed_segments = []
        task.decrypt_failed = []
        task.finished_at = datetime.now().isoformat(timespec='seconds')
        if task.started_at:
            try:
                start = datetime.fromisoformat(task.started_at)
                end = datetime.fromisoformat(task.finished_at)
                task.duration_seconds = (end - start).total_seconds()
            except:
                pass
        self.save()

    def mark_failed(self, url: str, error_message: str,
                    failed_segments: Optional[List[int]] = None,
                    decrypt_failed: Optional[List[int]] = None,
                    total_segments: int = 0,
                    downloaded_bytes: int = 0):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_FAILED
        task.error_message = error_message
        task.total_segments = total_segments
        task.downloaded_bytes = downloaded_bytes
        if failed_segments:
            task.failed_segments = sorted(failed_segments)
        if decrypt_failed:
            task.decrypt_failed = sorted(decrypt_failed)
        task.finished_at = datetime.now().isoformat(timespec='seconds')
        if task.started_at:
            try:
                start = datetime.fromisoformat(task.started_at)
                end = datetime.fromisoformat(task.finished_at)
                task.duration_seconds = (end - start).total_seconds()
            except:
                pass
        self.save()

    def is_success(self, url: str) -> bool:
        key = self._url_key(url)
        if key not in self.tasks:
            return False
        task = self.tasks[key]
        if task.status != TASK_STATUS_SUCCESS:
            return False
        if task.output_file and os.path.exists(task.output_file):
            return True
        return False

    def get_status(self, url: str) -> str:
        key = self._url_key(url)
        if key not in self.tasks:
            return TASK_STATUS_PENDING
        return self.tasks[key].status

    def get_task(self, url: str) -> Optional[TaskRecord]:
        return self.tasks.get(self._url_key(url))

    def get_statistics(self) -> dict:
        stats = {
            "total": len(self.tasks),
            TASK_STATUS_SUCCESS: 0,
            TASK_STATUS_FAILED: 0,
            TASK_STATUS_RUNNING: 0,
            TASK_STATUS_PENDING: 0,
            TASK_STATUS_SKIPPED: 0,
        }
        for task in self.tasks.values():
            s = task.status
            if s in stats:
                stats[s] += 1
        return stats

    def format_summary(self) -> str:
        stats = self.get_statistics()
        lines = [
            f"任务记录汇总 (共 {stats['total']} 个):",
            f"  成功: {stats[TASK_STATUS_SUCCESS]}",
            f"  失败: {stats[TASK_STATUS_FAILED]}",
            f"  运行中: {stats[TASK_STATUS_RUNNING]}",
            f"  待处理: {stats[TASK_STATUS_PENDING]}",
        ]
        if stats[TASK_STATUS_SKIPPED] > 0:
            lines.append(f"  跳过: {stats[TASK_STATUS_SKIPPED]}")
        return "\n".join(lines)

    def list_failed(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_FAILED]

    def list_pending(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_PENDING]
