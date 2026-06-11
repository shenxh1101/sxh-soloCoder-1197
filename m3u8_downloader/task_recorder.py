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

VERIFY_FAIL_SEGMENT_COUNT = "segment_count"
VERIFY_FAIL_FILE_SIZE = "file_size"
VERIFY_FAIL_DURATION = "duration"
VERIFY_FAIL_FFPROBE = "ffprobe"
VERIFY_FAIL_NO_OUTPUT = "no_output"
DOWNLOAD_FAIL_HTTP_AUTH = "http_auth"
DOWNLOAD_FAIL_HTTP_NOT_FOUND = "http_not_found"
DOWNLOAD_FAIL_HTTP_RATE_LIMIT = "http_rate_limit"
DOWNLOAD_FAIL_HTTP_SERVER = "http_server"
DOWNLOAD_FAIL_TIMEOUT = "timeout"
DOWNLOAD_FAIL_DNS = "dns"
DOWNLOAD_FAIL_CONNECTION = "connection"
DOWNLOAD_FAIL_SEGMENTS = "missing_segments"
DOWNLOAD_FAIL_DECRYPT = "decrypt"
MERGE_FAIL_FFMPEG = "ffmpeg"
MERGE_FAIL_NO_SEGMENTS = "no_segments"
UNKNOWN_FAIL = "unknown"


@dataclass
class VerificationResult:
    segment_count_ok: bool = True
    file_size_ok: bool = True
    duration_ok: bool = True
    ffprobe_ok: bool = True
    output_exists_ok: bool = True
    details: str = ""
    fail_types: List[str] = field(default_factory=list)
    expected_segments: int = 0
    actual_segments: Optional[int] = None
    expected_duration: float = 0.0
    actual_duration: Optional[float] = None
    expected_min_size: int = 0
    actual_file_size: Optional[int] = None
    ffprobe_streams: int = 0

    @property
    def all_ok(self) -> bool:
        return (self.segment_count_ok and self.file_size_ok
                and self.duration_ok and self.ffprobe_ok
                and self.output_exists_ok)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResult":
        return cls(
            segment_count_ok=data.get("segment_count_ok", True),
            file_size_ok=data.get("file_size_ok", True),
            duration_ok=data.get("duration_ok", True),
            ffprobe_ok=data.get("ffprobe_ok", True),
            output_exists_ok=data.get("output_exists_ok", True),
            details=data.get("details", ""),
            fail_types=list(data.get("fail_types", [])),
            expected_segments=data.get("expected_segments", 0),
            actual_segments=data.get("actual_segments"),
            expected_duration=float(data.get("expected_duration", 0.0)),
            actual_duration=(float(data["actual_duration"])
                             if data.get("actual_duration") is not None else None),
            expected_min_size=data.get("expected_min_size", 0),
            actual_file_size=data.get("actual_file_size"),
            ffprobe_streams=data.get("ffprobe_streams", 0),
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
    fail_type: Optional[str] = None
    suggestion: Optional[str] = None

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
            fail_type=data.get("fail_type"),
            suggestion=data.get("suggestion"),
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
        task.error_message = None if not verification or verification.all_ok else (
            "验片不通过: " + verification.details
        )
        task.failed_segments = []
        task.decrypt_failed = []
        task.verification = verification
        if verification and not verification.all_ok:
            task.fail_type = VERIFY_FAIL_SEGMENT_COUNT
            if not verification.output_exists_ok:
                task.fail_type = VERIFY_FAIL_NO_OUTPUT
            elif verification.fail_types:
                task.fail_type = verification.fail_types[0]
        else:
            task.fail_type = None
        task.suggestion = None
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
                    downloaded_bytes: int = 0,
                    fail_type: Optional[str] = None,
                    suggestion: Optional[str] = None):
        task = self.get_or_create(url)
        task.status = TASK_STATUS_FAILED
        task.error_message = error_message
        task.total_segments = total_segments
        task.downloaded_bytes = downloaded_bytes
        if failed_segments:
            task.failed_segments = sorted(failed_segments)
        if decrypt_failed:
            task.decrypt_failed = sorted(decrypt_failed)
        task.fail_type = fail_type
        task.suggestion = suggestion
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
                if t.status == TASK_STATUS_SUCCESS]

    def list_skipped(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_SKIPPED]

    def list_verify_failed(self) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status == TASK_STATUS_VERIFY_FAILED]

    def list_by_fail_type(self, fail_type: str) -> List[TaskRecord]:
        return [t for t in self.tasks.values()
                if t.status in (TASK_STATUS_FAILED, TASK_STATUS_VERIFY_FAILED)
                and t.fail_type == fail_type]

    def get_fail_type_groups(self,
                              group: Optional[str] = None) -> Dict[str, List[TaskRecord]]:
        buckets: Dict[str, List[TaskRecord]] = {}
        for t in self.tasks.values():
            if t.status not in (TASK_STATUS_FAILED, TASK_STATUS_VERIFY_FAILED):
                continue
            if group and t.group != group:
                continue
            ft = t.fail_type or UNKNOWN_FAIL
            buckets.setdefault(ft, []).append(t)
        return buckets

    def get_queue_view(self, group: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        view: Dict[str, Dict[str, int]] = {}
        groups_to_check: List[str]
        if group:
            groups_to_check = [group]
        else:
            groups_set: List[str] = []
            for t in self.tasks.values():
                if t.group and t.group not in groups_set:
                    groups_set.append(t.group)
            groups_to_check = sorted(groups_set) or ["(无分组)"]
        for g in groups_to_check:
            if g == "(无分组)":
                records = [t for t in self.tasks.values() if not t.group]
            else:
                records = [t for t in self.tasks.values() if t.group == g]
            bucket = {
                "total": len(records),
                TASK_STATUS_PENDING: 0,
                TASK_STATUS_RUNNING: 0,
                TASK_STATUS_FAILED: 0,
                TASK_STATUS_VERIFY_FAILED: 0,
                TASK_STATUS_SUCCESS: 0,
                TASK_STATUS_SKIPPED: 0,
            }
            for t in records:
                if t.status in bucket:
                    bucket[t.status] += 1
            view[g] = bucket
        return view

    def format_queue_view(self, group: Optional[str] = None) -> str:
        view = self.get_queue_view(group=group)
        lines = []
        lines.append("=" * 80)
        lines.append("任务队列视图")
        lines.append(f"生成时间: {datetime.now().isoformat(timespec='seconds')}")
        lines.append("=" * 80)
        header = (f"{'分组':<24} {'总数':>5} {'待跑':>5} {'运行':>5} {'失败':>5} "
                  f"{'异常':>5} {'成功':>5} {'跳过':>5}")
        lines.append(header)
        lines.append("-" * 80)
        for g, stat in view.items():
            name = g if len(g) <= 22 else (g[:19] + "...")
            lines.append(
                f"{name:<24} {stat['total']:>5} "
                f"{stat[TASK_STATUS_PENDING]:>5} {stat[TASK_STATUS_RUNNING]:>5} "
                f"{stat[TASK_STATUS_FAILED]:>5} {stat[TASK_STATUS_VERIFY_FAILED]:>5} "
                f"{stat[TASK_STATUS_SUCCESS]:>5} {stat[TASK_STATUS_SKIPPED]:>5}"
            )
        lines.append("=" * 80)
        return "\n".join(lines)

    def export_failed_urls(self, output_file: str,
                           group: Optional[str] = None,
                           include_verify_failed: bool = True) -> int:
        urls: List[str] = []
        for t in self.tasks.values():
            if t.status == TASK_STATUS_FAILED:
                pass
            elif t.status == TASK_STATUS_VERIFY_FAILED and include_verify_failed:
                pass
            else:
                continue
            if group and t.group != group:
                continue
            urls.append(t.url)
        if not urls:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write("")
            except Exception:
                pass
            return 0
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                for u in urls:
                    f.write(u + "\n")
            return len(urls)
        except Exception as e:
            print(f"导出失败清单错误: {e}")
            return 0

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
                    "fail_type_groups": {
                        k: [t.to_dict() for t in v]
                        for k, v in self.get_fail_type_groups(group=group).items()
                    }
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
                lines.append(f"  有效成功: {group_stats[TASK_STATUS_SUCCESS]}")
                lines.append(f"  异常合计: {group_stats[TASK_STATUS_FAILED]+group_stats[TASK_STATUS_VERIFY_FAILED]}")
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

                fail_type_groups = self.get_fail_type_groups(group=group)
                if fail_type_groups:
                    lines.append("=" * 70)
                    lines.append("【异常类型分组】")
                    lines.append("-" * 70)
                    type_names = {
                        VERIFY_FAIL_NO_OUTPUT: "输出文件不存在",
                        VERIFY_FAIL_SEGMENT_COUNT: "分片数量不匹配",
                        VERIFY_FAIL_FILE_SIZE: "文件大小异常",
                        VERIFY_FAIL_DURATION: "输出时长偏差大",
                        VERIFY_FAIL_FFPROBE: "ffprobe 无法探测",
                        DOWNLOAD_FAIL_HTTP_AUTH: "HTTP 鉴权失败 401/403",
                        DOWNLOAD_FAIL_HTTP_NOT_FOUND: "HTTP 资源不存在 404",
                        DOWNLOAD_FAIL_HTTP_RATE_LIMIT: "HTTP 频率限制 429",
                        DOWNLOAD_FAIL_HTTP_SERVER: "HTTP 服务器错误 5xx",
                        DOWNLOAD_FAIL_TIMEOUT: "网络请求超时",
                        DOWNLOAD_FAIL_DNS: "DNS 解析失败",
                        DOWNLOAD_FAIL_CONNECTION: "网络连接失败",
                        DOWNLOAD_FAIL_SEGMENTS: "下载分片缺失",
                        DOWNLOAD_FAIL_DECRYPT: "解密失败",
                        MERGE_FAIL_FFMPEG: "FFmpeg 合并失败",
                        MERGE_FAIL_NO_SEGMENTS: "无可合并分片",
                        UNKNOWN_FAIL: "其他未归类错误",
                    }
                    for ft, items in sorted(fail_type_groups.items(),
                                             key=lambda kv: -len(kv[1])):
                        name = type_names.get(ft, ft)
                        lines.append(f"  [{name}]  {len(items)} 条")
                        sample = items[:3]
                        for s in sample:
                            if s.error_message:
                                snippet = s.error_message[:80]
                                lines.append(f"    - {snippet}{'...' if len(s.error_message) > 80 else ''}")
                            if s.suggestion:
                                lines.append(f"      建议: {s.suggestion[:100]}")
                        if len(items) > 3:
                            lines.append(f"    ... 还有 {len(items)-3} 条")
                    lines.append("")

                if pending_list:
                    lines.append("=" * 70)
                    lines.append(f"【待处理任务】 ({len(pending_list)} 个)")
                    lines.append("-" * 70)
                    for t in pending_list:
                        lines.append(f"  URL: {t.url}")
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
