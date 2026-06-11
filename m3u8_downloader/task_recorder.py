import os
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime


TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_SKIPPED = "skipped"
TASK_STATUS_VERIFY_FAILED = "verify_failed"


@dataclass
class VerificationResult:
    segment_count_ok: bool = True
    file_size_ok: bool = True
    duration_ok: bool = True
    ffprobe_ok: bool = True
    details: str = ""

    @property
    def all_ok(self) -> bool:
        return (self.segment_count_ok and self.file_size_ok
                and self.duration_ok and self.ffprobe_ok)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResult":
        return cls(
            segment_count_ok=data.get("segment_count_ok", True),
            file_size_ok=data.get("file_size_ok", True),
            duration_ok=data.get("duration_ok", True),
            ffprobe_ok=data.get("ffprobe_ok", True),
            details=data.get("details", ""),
        )


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
    group: Optional[str] = None
    output_dir: Optional[str] = None
    verification: Optional[VerificationResult] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.verification:
            d["verification"] = self.verification.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TaskRecord":
        ver_data = data.get("verification")
        verification = VerificationResult.from_dict(ver_data) if ver_data else None
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
            group=data.get("group"),
            output_dir=data.get("output_dir"),
            verification=verification,
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
                     quality_label: Optional[str] = None,
                     group: Optional[str] = None,
                     output_dir: Optional[str] = None):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_RUNNING
        task.started_at = datetime.now().isoformat(timespec='seconds')
        if output_format:
            task.output_format = output_format
        if quality_index is not None:
            task.quality_index = quality_index
        if quality_label:
            task.quality_label = quality_label
        if group:
            task.group = group
        if output_dir:
            task.output_dir = output_dir
        self.save()

    def mark_success(self, url: str, output_file: str, total_segments: int,
                     downloaded_bytes: int,
                     verification: Optional[VerificationResult] = None):
        task = self.get_or_create(url)
        if verification and not verification.all_ok:
            task.status = TASK_STATUS_VERIFY_FAILED
        else:
            task.status = TASK_STATUS_SUCCESS
        task.output_file = output_file
        task.total_segments = total_segments
        task.downloaded_bytes = downloaded_bytes
        task.error_message = None
        task.failed_segments = []
        task.decrypt_failed = []
        task.verification = verification
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
        if task.status not in (TASK_STATUS_SUCCESS, TASK_STATUS_VERIFY_FAILED):
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
            TASK_STATUS_VERIFY_FAILED: 0,
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
            f"  校验异常: {stats[TASK_STATUS_VERIFY_FAILED]}",
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

    def list_success(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status in (TASK_STATUS_SUCCESS, TASK_STATUS_VERIFY_FAILED)]

    def list_skipped(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_SKIPPED]

    def list_verify_failed(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_VERIFY_FAILED]

    def get_groups(self) -> List[str]:
        groups = set()
        for task in self.tasks.values():
            if task.group:
                groups.add(task.group)
        return sorted(groups)

    def get_group_statistics(self, group: str) -> dict:
        stats = {
            "total": 0,
            TASK_STATUS_SUCCESS: 0,
            TASK_STATUS_FAILED: 0,
            TASK_STATUS_VERIFY_FAILED: 0,
            TASK_STATUS_SKIPPED: 0,
            TASK_STATUS_PENDING: 0,
            TASK_STATUS_RUNNING: 0,
        }
        for task in self.tasks.values():
            if task.group == group:
                stats["total"] += 1
                if task.status in stats:
                    stats[task.status] += 1
        return stats

    def get_retry_urls(self, group: Optional[str] = None) -> List[str]:
        urls = []
        for task in self.tasks.values():
            if task.status in (TASK_STATUS_FAILED,
                               TASK_STATUS_PENDING,
                               TASK_STATUS_RUNNING):
                if group and task.group != group:
                    continue
                urls.append(task.url)
        return urls

    def mark_skipped(self, url: str, reason: str = "already_completed"):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_SKIPPED
        if not task.error_message:
            task.error_message = reason
        self.save()

    def export_report(self, report_file: Optional[str] = None,
                       format: str = "txt",
                       group: Optional[str] = None) -> Optional[str]:
        from .utils import format_size, format_time

        if not report_file:
            base, _ = os.path.splitext(self.log_file)
            report_file = f"{base}_report.{format}"

        if group:
            tasks_in_scope = [t for t in self.tasks.values() if t.group == group]
        else:
            tasks_in_scope = list(self.tasks.values())

        success_list = [t for t in tasks_in_scope
                        if t.status == TASK_STATUS_SUCCESS]
        verify_failed_list = [t for t in tasks_in_scope
                              if t.status == TASK_STATUS_VERIFY_FAILED]
        failed_list = [t for t in tasks_in_scope
                       if t.status == TASK_STATUS_FAILED]
        skipped_list = [t for t in tasks_in_scope
                        if t.status == TASK_STATUS_SKIPPED]
        pending_list = [t for t in tasks_in_scope
                        if t.status == TASK_STATUS_PENDING]

        try:
            if format.lower() == "json":
                report_data = {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "filter_group": group,
                    "statistics": self.get_group_statistics(group) if group else self.get_statistics(),
                    "success": [t.to_dict() for t in success_list],
                    "verify_failed": [t.to_dict() for t in verify_failed_list],
                    "failed": [t.to_dict() for t in failed_list],
                    "skipped": [t.to_dict() for t in skipped_list],
                    "pending": [t.to_dict() for t in pending_list],
                }
                with open(report_file, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, ensure_ascii=False, indent=2)
            else:
                lines = []
                lines.append("=" * 70)
                lines.append("M3U8 批量下载汇总报告")
                if group:
                    lines.append(f"分组: {group}")
                lines.append(f"生成时间: {datetime.now().isoformat(timespec='seconds')}")
                lines.append(f"任务日志: {self.log_file}")
                lines.append("=" * 70)
                lines.append("")

                group_stats = self.get_group_statistics(group) if group else self.get_statistics()
                lines.append("【统计】")
                lines.append(f"  总数:     {group_stats['total']}")
                lines.append(f"  成功:     {group_stats[TASK_STATUS_SUCCESS]}")
                lines.append(f"  校验异常: {group_stats[TASK_STATUS_VERIFY_FAILED]}")
                lines.append(f"  失败:     {group_stats[TASK_STATUS_FAILED]}")
                lines.append(f"  跳过:     {group_stats[TASK_STATUS_SKIPPED]}")
                lines.append(f"  待处理:   {group_stats[TASK_STATUS_PENDING]}")
                lines.append(f"  运行中:   {group_stats[TASK_STATUS_RUNNING]}")
                lines.append("")

                total_bytes = sum(t.downloaded_bytes for t in success_list)
                total_duration = sum(t.duration_seconds for t in success_list)
                if total_bytes > 0 or total_duration > 0:
                    lines.append("【累计】")
                    lines.append(f"  成功任务总下载量: {format_size(total_bytes)}")
                    lines.append(f"  成功任务总耗时:   {format_time(total_duration)}")
                    lines.append("")

                groups = self.get_groups()
                if groups and not group:
                    lines.append("【分组汇总】")
                    for g in groups:
                        gs = self.get_group_statistics(g)
                        lines.append(f"  {g}: 总数={gs['total']} "
                                     f"成功={gs[TASK_STATUS_SUCCESS]} "
                                     f"失败={gs[TASK_STATUS_FAILED]} "
                                     f"异常={gs[TASK_STATUS_VERIFY_FAILED]}")
                    lines.append("")

                if success_list:
                    lines.append("=" * 70)
                    lines.append(f"【成功任务】 ({len(success_list)} 个)")
                    lines.append("-" * 70)
                    for t in success_list:
                        lines.append(f"  URL: {t.url}")
                        if t.group:
                            lines.append(f"  分组: {t.group}")
                        if t.output_file:
                            lines.append(f"  输出: {t.output_file}")
                        size_info = []
                        if t.downloaded_bytes > 0:
                            size_info.append(format_size(t.downloaded_bytes))
                        if t.duration_seconds > 0:
                            size_info.append(format_time(t.duration_seconds))
                        if t.quality_label:
                            size_info.append(t.quality_label)
                        if size_info:
                            lines.append(f"  信息: {' | '.join(size_info)}")
                        lines.append("")

                if verify_failed_list:
                    lines.append("=" * 70)
                    lines.append(f"【校验异常任务】 ({len(verify_failed_list)} 个)")
                    lines.append("-" * 70)
                    for t in verify_failed_list:
                        lines.append(f"  URL: {t.url}")
                        if t.output_file:
                            lines.append(f"  输出: {t.output_file}")
                        if t.verification:
                            v = t.verification
                            checks = []
                            if not v.segment_count_ok:
                                checks.append("分片数量不匹配")
                            if not v.file_size_ok:
                                checks.append("文件大小异常")
                            if not v.duration_ok:
                                checks.append("时长不匹配")
                            if not v.ffprobe_ok:
                                checks.append("ffprobe 探测失败")
                            if checks:
                                lines.append(f"  未通过: {'; '.join(checks)}")
                            if v.details:
                                lines.append(f"  详情: {v.details}")
                        lines.append("")

                if failed_list:
                    lines.append("=" * 70)
                    lines.append(f"【失败任务】 ({len(failed_list)} 个)")
                    lines.append("-" * 70)
                    for t in failed_list:
                        lines.append(f"  URL: {t.url}")
                        if t.group:
                            lines.append(f"  分组: {t.group}")
                        if t.error_message:
                            lines.append(f"  原因: {t.error_message}")
                        if t.failed_segments:
                            preview = t.failed_segments[:10]
                            extra = "" if len(t.failed_segments) <= 10 else (
                                f"... (共{len(t.failed_segments)}个)"
                            )
                            lines.append(f"  下载失败分片: {preview}{extra}")
                        if t.decrypt_failed:
                            preview = t.decrypt_failed[:10]
                            extra = "" if len(t.decrypt_failed) <= 10 else (
                                f"... (共{len(t.decrypt_failed)}个)"
                            )
                            lines.append(f"  解密失败分片: {preview}{extra}")
                        lines.append("")

                if skipped_list:
                    lines.append("=" * 70)
                    lines.append(f"【跳过任务】 ({len(skipped_list)} 个)")
                    lines.append("-" * 70)
                    for t in skipped_list:
                        lines.append(f"  URL: {t.url}")
                        if t.output_file:
                            lines.append(f"  已有输出: {t.output_file}")
                        lines.append("")

                if pending_list:
                    lines.append("=" * 70)
                    lines.append(f"【待处理任务】 ({len(pending_list)} 个)")
                    lines.append("-" * 70)
                    for t in pending_list:
                        lines.append(f"  URL: {t.url}")
                    lines.append("")

                lines.append("=" * 70)
                lines.append("报告结束")
                lines.append("=" * 70)

                with open(report_file, 'w', encoding='utf-8') as f:
                    f.write("\n".join(lines))

            return report_file
        except Exception as e:
            print(f"导出报告失败: {e}")
            return None
