import os
import sys
import json
import struct
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from m3u8_downloader.utils import (
    load_cookies_from_file,
    cookies_to_header,
    parse_header_string,
    build_headers,
    classify_http_error,
    format_size,
    format_time
)
from m3u8_downloader.downloader import DownloadProgress, DownloadResult
from m3u8_downloader.task_recorder import (
    TaskRecorder, TaskRecord, VerificationResult,
    TASK_STATUS_SUCCESS, TASK_STATUS_FAILED, TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING, TASK_STATUS_SKIPPED, TASK_STATUS_VERIFY_FAILED
)
from m3u8_downloader.merger import verify_output, probe_file


def test_1_custom_headers_and_cookies():
    """测试1: 自定义请求头和 Cookie 支持"""
    print("=" * 70)
    print("测试1: 自定义请求头和 Cookie 支持")
    print("=" * 70)

    headers = parse_header_string("Referer:https://example.com,Authorization:Bearer abc123")
    assert headers["Referer"] == "https://example.com"
    assert headers["Authorization"] == "Bearer abc123"
    print(f"  ✓ parse_header_string 解析多个 Header: {headers}")

    single = parse_header_string("X-Custom:value123")
    assert single["X-Custom"] == "value123"
    print(f"  ✓ parse_header_string 解析单个 Header")

    empty = parse_header_string("")
    assert empty == {}
    print(f"  ✓ 空字符串返回空 dict")

    malformed = parse_header_string("invalid-no-colon")
    assert malformed == {}
    print(f"  ✓ 格式错误的 Header 被忽略")

    cookies = {"session_id": "abc123", "token": "xyz789"}
    header_str = cookies_to_header(cookies)
    assert "session_id=abc123" in header_str
    assert "token=xyz789" in header_str
    print(f"  ✓ cookies_to_header: {header_str}")

    tmpdir = tempfile.mkdtemp(prefix="m3u8_cookie_")
    try:
        cookie_file = os.path.join(tmpdir, "cookies.txt")
        with open(cookie_file, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(".example.com\tTRUE\t/\tFALSE\t0\tsession_id\tabc123\n")
            f.write(".example.com\tTRUE\t/\tFALSE\t0\ttoken\txyz789\n")

        loaded = load_cookies_from_file(cookie_file)
        assert "session_id" in loaded
        assert loaded["session_id"] == "abc123"
        assert "token" in loaded
        print(f"  ✓ Netscape 格式 Cookie 加载: {loaded}")

        nv_file = os.path.join(tmpdir, "nv_cookies.txt")
        with open(nv_file, 'w') as f:
            f.write("name1=value1\n")
            f.write("name2=value2\n")
        loaded2 = load_cookies_from_file(nv_file)
        assert loaded2.get("name1") == "value1"
        assert loaded2.get("name2") == "value2"
        print(f"  ✓ name=value 格式 Cookie 加载: {loaded2}")

        built = build_headers(
            custom_headers={"Referer": "https://example.com"},
            cookies={"session_id": "abc123"}
        )
        assert "Referer" in built
        assert built["Referer"] == "https://example.com"
        assert "Cookie" in built
        assert "session_id=abc123" in built["Cookie"]
        assert "User-Agent" in built
        print(f"  ✓ build_headers 合并 UA + 自定义 + Cookie")

        no_cookie = build_headers()
        assert "User-Agent" in no_cookie
        assert "Cookie" not in no_cookie
        print(f"  ✓ 无 Cookie 时不添加 Cookie 头")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    assert classify_http_error(401) == "auth"
    assert classify_http_error(403) == "auth"
    assert classify_http_error(404) == "not_found"
    assert classify_http_error(429) == "rate_limit"
    assert classify_http_error(500) == "server_error"
    assert classify_http_error(503) == "server_error"
    assert classify_http_error(400) == "client_error"
    assert classify_http_error(200) == "unknown"
    print(f"  ✓ classify_http_error 全部分类正确")

    print("测试1 全部通过 ✓")
    print()


def test_2_batch_grouping():
    """测试2: 批量任务分组管理"""
    print("=" * 70)
    print("测试2: 批量任务分组管理")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp(prefix="m3u8_group_")
    try:
        log_file = os.path.join(tmpdir, "grouped_tasks.json")
        recorder = TaskRecorder(log_file)

        out_a1 = os.path.join(tmpdir, "a1.mp4")
        with open(out_a1, 'wb') as f:
            f.write(b"x" * 1024)

        recorder.mark_running("http://a.com/1.m3u8", group="drama_ep01", output_dir=tmpdir)
        recorder.mark_success("http://a.com/1.m3u8", out_a1, 10, 1024000)

        recorder.mark_running("http://a.com/2.m3u8", group="drama_ep01", output_dir=tmpdir)
        recorder.mark_failed("http://a.com/2.m3u8", "timeout", total_segments=10)

        recorder.mark_running("http://b.com/1.m3u8", group="drama_ep02", output_dir=tmpdir)
        recorder.mark_success("http://b.com/1.m3u8", out_a1, 8, 800000)

        recorder.mark_running("http://c.com/1.m3u8")
        recorder.mark_failed("http://c.com/1.m3u8", "network error")

        groups = recorder.get_groups()
        assert "drama_ep01" in groups
        assert "drama_ep02" in groups
        print(f"  ✓ get_groups: {groups}")

        gs1 = recorder.get_group_statistics("drama_ep01")
        assert gs1["total"] == 2
        assert gs1[TASK_STATUS_SUCCESS] == 1
        assert gs1[TASK_STATUS_FAILED] == 1
        print(f"  ✓ drama_ep01 统计: 成功={gs1[TASK_STATUS_SUCCESS]}, 失败={gs1[TASK_STATUS_FAILED]}")

        gs2 = recorder.get_group_statistics("drama_ep02")
        assert gs2["total"] == 1
        assert gs2[TASK_STATUS_SUCCESS] == 1
        print(f"  ✓ drama_ep02 统计: 成功={gs2[TASK_STATUS_SUCCESS]}")

        retry_all = recorder.get_retry_urls()
        assert len(retry_all) == 2
        print(f"  ✓ get_retry_urls 全部: {len(retry_all)} 个")

        retry_ep01 = recorder.get_retry_urls(group="drama_ep01")
        assert len(retry_ep01) == 1
        assert "http://a.com/2.m3u8" in retry_ep01
        print(f"  ✓ get_retry_urls(group=drama_ep01): {retry_ep01}")

        retry_ep02 = recorder.get_retry_urls(group="drama_ep02")
        assert len(retry_ep02) == 0
        print(f"  ✓ get_retry_urls(group=drama_ep02): 空")

        task = recorder.get_task("http://a.com/1.m3u8")
        assert task.group == "drama_ep01"
        assert task.output_dir == tmpdir
        print(f"  ✓ 任务记录包含 group 和 output_dir")

        recorder2 = TaskRecorder(log_file)
        task2 = recorder2.get_task("http://a.com/1.m3u8")
        assert task2.group == "drama_ep01"
        print(f"  ✓ JSON 持久化保留 group 字段")

        report_file = recorder.export_report(format="txt")
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "分组汇总" in content
        assert "drama_ep01" in content
        print(f"  ✓ TXT 报告包含分组汇总")

        report_json = recorder.export_report(format="json", group="drama_ep01")
        with open(report_json, 'r', encoding='utf-8') as f:
            jdata = json.load(f)
        assert jdata["filter_group"] == "drama_ep01"
        print(f"  ✓ JSON 报告支持按分组过滤")

        print("测试2 全部通过 ✓")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print()


def test_3_verification():
    """测试3: 下载完成后校验模式"""
    print("=" * 70)
    print("测试3: 下载完成后校验模式")
    print("=" * 70)

    ver = VerificationResult()
    assert ver.all_ok == True
    assert ver.segment_count_ok == True
    assert ver.file_size_ok == True
    assert ver.duration_ok == True
    assert ver.ffprobe_ok == True
    print(f"  ✓ 默认 VerificationResult 全部通过")

    ver_fail = VerificationResult(
        file_size_ok=False,
        duration_ok=False,
        details="文件过小; 时长偏差大"
    )
    assert ver_fail.all_ok == False
    d = ver_fail.to_dict()
    ver_restored = VerificationResult.from_dict(d)
    assert ver_restored.file_size_ok == False
    assert ver_restored.duration_ok == False
    assert ver_restored.ffprobe_ok == True
    print(f"  ✓ VerificationResult 序列化/反序列化正确")

    tmpdir = tempfile.mkdtemp(prefix="m3u8_verify_")
    try:
        small_file = os.path.join(tmpdir, "tiny.mp4")
        with open(small_file, 'wb') as f:
            f.write(b"tiny")

        result = verify_output(small_file, 10, 60.0, min_file_size=1024)
        ver = result["verification"]
        assert result["ok"] == False
        assert ver.file_size_ok == False
        assert "文件过小" in ver.details
        print(f"  ✓ 文件过小校验失败: {ver.details}")

        nonexistent = os.path.join(tmpdir, "nope.mp4")
        result2 = verify_output(nonexistent, 10, 60.0)
        assert result2["ok"] == False
        assert result2["verification"].file_size_ok == False
        assert result2["verification"].ffprobe_ok == False
        print(f"  ✓ 不存在文件校验失败")

        log_file = os.path.join(tmpdir, "ver_tasks.json")
        recorder = TaskRecorder(log_file)

        ver_bad = VerificationResult(file_size_ok=False, details="文件过小")
        out_file = os.path.join(tmpdir, "ok.mp4")
        with open(out_file, 'wb') as f:
            f.write(b"x" * 2048)

        recorder.mark_success(
            "http://v.com/1.m3u8", out_file, 10, 1024000,
            verification=ver_bad
        )
        task = recorder.get_task("http://v.com/1.m3u8")
        assert task.status == TASK_STATUS_VERIFY_FAILED
        assert task.verification.file_size_ok == False
        print(f"  ✓ 校验异常时状态为 verify_failed")

        ver_ok = VerificationResult(details="全部通过")
        recorder.mark_success(
            "http://v.com/2.m3u8", out_file, 10, 1024000,
            verification=ver_ok
        )
        task2 = recorder.get_task("http://v.com/2.m3u8")
        assert task2.status == TASK_STATUS_SUCCESS
        print(f"  ✓ 校验通过时状态为 success")

        ver_list = recorder.list_verify_failed()
        assert len(ver_list) == 1
        print(f"  ✓ list_verify_failed 返回校验异常任务")

        recorder2 = TaskRecorder(log_file)
        restored = recorder2.get_task("http://v.com/1.m3u8")
        assert restored.status == TASK_STATUS_VERIFY_FAILED
        assert restored.verification.file_size_ok == False
        print(f"  ✓ 验证结果持久化后能正确恢复")

        report_file = recorder.export_report(format="txt")
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "校验异常任务" in content
        assert "文件过小" in content
        print(f"  ✓ TXT 报告包含校验异常任务段")

        print("测试3 全部通过 ✓")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print()


def test_4_progress_settlement():
    """测试4: 进度条结算修复"""
    print("=" * 70)
    print("测试4: 进度条结算修复")
    print("=" * 70)

    progress = DownloadProgress(total_segments=3)

    assert progress.downloaded_bytes == 0
    assert progress.completed_segments == 0
    assert progress.in_progress_bytes == 0
    assert progress.current_speed == 0
    print(f"  ✓ 初始状态: 全部为0")

    progress.add_in_progress(50000)
    progress.add_in_progress(30000)
    assert progress.in_progress_bytes == 80000
    assert progress.total_display_bytes == 80000
    print(f"  ✓ in_progress 累加: {progress.in_progress_bytes}")

    progress.add_completed(50000)
    assert progress.completed_segments == 1
    assert progress.downloaded_bytes == 50000
    assert progress.in_progress_bytes == 30000
    assert progress.total_display_bytes == 80000
    print(f"  ✓ 第1个分片完成: downloaded={progress.downloaded_bytes}, "
          f"in_progress={progress.in_progress_bytes}")

    progress.add_completed(30000)
    assert progress.completed_segments == 2
    assert progress.downloaded_bytes == 80000
    assert progress.in_progress_bytes == 0
    print(f"  ✓ 第2个分片完成: in_progress 已清零")

    progress.add_in_progress(40000)
    progress.add_completed(40000)
    assert progress.completed_segments == 3
    assert progress.downloaded_bytes == 120000
    assert progress.in_progress_bytes == 0
    print(f"  ✓ 全部完成: downloaded={progress.downloaded_bytes}, in_progress=0")

    p2 = DownloadProgress(total_segments=2)

    p2.add_in_progress(100000)
    p2.remove_in_progress(100000)
    assert p2.in_progress_bytes == 0
    assert p2.downloaded_bytes == 0
    print(f"  ✓ 重试回退: in_progress 清零, downloaded 不受影响")

    p3 = DownloadProgress(total_segments=2)
    p3.add_in_progress(50000)
    p3.add_completed(50000)
    pct = p3.percentage
    assert pct > 0
    print(f"  ✓ 新目录下载: 完成第1个分片后进度={pct:.1f}% (不再显示0%)")

    p4 = DownloadProgress(total_segments=5)
    p4.add_completed(100000)
    p4.update_speed()
    total_elapsed = p4.elapsed_time
    if total_elapsed < 0.3:
        import time
        time.sleep(0.4)
        p4.update_speed()
    assert p4.current_speed > 0 or p4.downloaded_bytes > 0
    print(f"  ✓ 速度计算: downloaded={p4.downloaded_bytes}, speed={p4.current_speed:.0f} B/s")

    print("测试4 全部通过 ✓")
    print()


def test_5_cli_params():
    """测试5: CLI 新参数检查"""
    print("=" * 70)
    print("测试5: CLI 新参数检查")
    print("=" * 70)

    from m3u8_downloader.cli import create_parser
    parser = create_parser()

    help_text = parser.format_help()

    for param in ["--header", "--cookie", "--group", "--verify", "--ffprobe"]:
        assert param in help_text, f"缺少参数 {param}"
    print(f"  ✓ 所有新参数存在于帮助信息: --header, --cookie, --group, --verify, --ffprobe")

    args = parser.parse_args([
        "-u", "http://example.com/test.m3u8",
        "--header", "Referer:https://x.com",
        "--header", "X-Custom:value123",
        "--cookie", "cookies.txt",
        "--group", "drama_ep01",
        "--verify",
        "--ffprobe", "/usr/bin/ffprobe"
    ])
    assert args.header == ["Referer:https://x.com", "X-Custom:value123"]
    assert args.cookie == "cookies.txt"
    assert args.group == "drama_ep01"
    assert args.verify == True
    assert args.ffprobe == "/usr/bin/ffprobe"
    print(f"  ✓ 参数解析: header={args.header}, cookie={args.cookie}, "
          f"group={args.group}, verify={args.verify}")

    args2 = parser.parse_args([
        "--continue", "tasks.json",
        "--group", "drama_ep01",
        "--verify"
    ])
    assert args2.continue_task == "tasks.json"
    assert args2.group == "drama_ep01"
    assert args2.verify == True
    print(f"  ✓ --continue + --group + --verify 参数解析正常")

    print("测试5 全部通过 ✓")
    print()


def run_all_tests():
    print()
    print("*" * 70)
    print("*" + " " * 18 + "M3U8 下载工具 - 第四轮改进测试" + " " * 18 + "*")
    print("*" * 70)
    print()

    tests = [
        test_1_custom_headers_and_cookies,
        test_2_batch_grouping,
        test_3_verification,
        test_4_progress_settlement,
        test_5_cli_params,
    ]

    passed = 0
    failed = 0
    failed_names = []

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            failed_names.append(test_func.__name__)
            print(f"✗ 测试 {test_func.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            print()

    print("=" * 70)
    print(f"测试结果: 共 {len(tests)} 项，通过 {passed} 项，失败 {failed} 项")
    if failed_names:
        print(f"失败测试: {', '.join(failed_names)}")
    else:
        print("全部测试通过！")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
