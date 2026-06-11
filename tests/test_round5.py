import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from m3u8_downloader.task_recorder import (
    TaskRecorder, TaskRecord, VerificationResult,
    TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS,
    TASK_STATUS_FAILED, TASK_STATUS_SKIPPED, TASK_STATUS_VERIFY_FAILED,
    VERIFY_FAIL_NO_OUTPUT, VERIFY_FAIL_SEGMENT_COUNT, VERIFY_FAIL_FILE_SIZE,
    VERIFY_FAIL_DURATION, VERIFY_FAIL_FFPROBE,
    DOWNLOAD_FAIL_HTTP_AUTH, DOWNLOAD_FAIL_TIMEOUT, DOWNLOAD_FAIL_DNS,
    DOWNLOAD_FAIL_CONNECTION, DOWNLOAD_FAIL_SEGMENTS, DOWNLOAD_FAIL_DECRYPT,
    MERGE_FAIL_FFMPEG, UNKNOWN_FAIL,
)
from m3u8_downloader.utils import (
    load_cookies_from_string, load_cookies_from_env, load_cookies,
    classify_request_exception,
)
from m3u8_downloader.merger import verify_output
from m3u8_downloader.downloader import (
    DownloadProgress, DownloadResult,
)


def run_all_tests():
    ok = 0
    fail = 0

    def run_test(name, fn):
        nonlocal ok, fail
        print()
        print("=" * 70)
        print(f"测试{name}")
        print("=" * 70)
        try:
            fn()
            print(f"测试{name} 全部通过 ✓")
            ok += 1
        except AssertionError as e:
            print(f"✗ 断言失败: {e}")
            import traceback
            traceback.print_exc()
            fail += 1
        except Exception as e:
            print(f"✗ 异常: {e}")
            import traceback
            traceback.print_exc()
            fail += 1

    def test_1_verification_flow():
        tmp = tempfile.mkdtemp()
        try:
            vr = VerificationResult()
            vr.segment_count_ok = False
            vr.fail_types.append(VERIFY_FAIL_SEGMENT_COUNT)
            vr.expected_segments = 100
            vr.actual_segments = 50
            vr.details = "分片数缺失: 实际50/期望100"
            assert not vr.all_ok, "有失败项不应通过"
            assert VERIFY_FAIL_SEGMENT_COUNT in vr.fail_types
            print("  ✓ VerificationResult 多字段 + fail_types")

            data = vr.to_dict()
            vr2 = VerificationResult.from_dict(data)
            assert vr2.expected_segments == 100
            assert vr2.actual_segments == 50
            assert VERIFY_FAIL_SEGMENT_COUNT in vr2.fail_types
            assert not vr2.all_ok
            print("  ✓ VerificationResult 序列化/反序列化保留扩展字段")

            log_file = os.path.join(tmp, "t1.json")
            rec = TaskRecorder(log_file)
            rec.mark_running("http://a.com/1.m3u8", output_dir=tmp, group="g1")
            rec.mark_success(
                "http://a.com/1.m3u8",
                output_file=os.path.join(tmp, "a.mp4"),
                total_segments=100,
                downloaded_bytes=10_000_000,
                verification=vr,
            )
            task = rec.get_task("http://a.com/1.m3u8")
            assert task.status == TASK_STATUS_VERIFY_FAILED, (
                f"验证不通过时状态应为 verify_failed，实际 {task.status}"
            )
            assert task.fail_type == VERIFY_FAIL_SEGMENT_COUNT
            assert task.error_message and "验片不通过" in task.error_message
            print("  ✓ mark_success 验片不过 => verify_failed + fail_type + error_message")

            rec.mark_running("http://a.com/2.m3u8", output_dir=tmp, group="g1")
            rec.mark_failed(
                "http://a.com/2.m3u8",
                error_message="鉴权失败 HTTP 403",
                fail_type=DOWNLOAD_FAIL_HTTP_AUTH,
                suggestion="建议: 使用 --cookie",
            )
            task2 = rec.get_task("http://a.com/2.m3u8")
            assert task2.fail_type == DOWNLOAD_FAIL_HTTP_AUTH
            assert "建议" in (task2.suggestion or "")
            print("  ✓ mark_failed 保存 fail_type + suggestion")

            groups = rec.get_fail_type_groups()
            assert VERIFY_FAIL_SEGMENT_COUNT in groups
            assert DOWNLOAD_FAIL_HTTP_AUTH in groups
            assert len(groups[VERIFY_FAIL_SEGMENT_COUNT]) == 1
            assert len(groups[DOWNLOAD_FAIL_HTTP_AUTH]) == 1
            print("  ✓ get_fail_type_groups 按异常类型归类")

            vlist = rec.list_verify_failed()
            assert len(vlist) == 1
            assert not rec.is_success("http://a.com/1.m3u8"), (
                "verify_failed 不应被 is_success 当作成功"
            )
            print("  ✓ verify_failed 不被算作成功(is_success=False)")

            txt_report = rec.export_report(format="txt")
            assert txt_report and os.path.exists(txt_report)
            with open(txt_report, 'r', encoding='utf-8') as f:
                content = f.read()
            assert "【异常类型分组】" in content
            # 异常类型名称应该对应
            assert ("鉴权失败" in content
                    or "401" in content or "403" in content
                    or "HTTP 鉴权" in content)
            # 校验异常任务段里应该有分片不匹配或 fail_types 信息
            assert "校验异常任务" in content
            print("  ✓ TXT 报告包含【异常类型分组】段")

            success_list = rec.list_success()
            assert len(success_list) == 0, "verify_failed 不应出现在 list_success"
            print("  ✓ list_success 不包含 verify_failed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_2_queue_and_export_failed():
        tmp = tempfile.mkdtemp()
        try:
            log_file = os.path.join(tmp, "q.json")
            rec = TaskRecorder(log_file)

            urls_g1 = [f"http://g1.com/{i}.m3u8" for i in range(5)]
            urls_g2 = [f"http://g2.com/{i}.m3u8" for i in range(3)]

            for i, u in enumerate(urls_g1):
                rec.mark_running(u, group="g1", output_dir=tmp)
                if i == 0:
                    rec.mark_success(u, os.path.join(tmp, f"g1_{i}.mp4"), 10, 1_000_000)
                elif i == 1:
                    rec.mark_failed(u, "403", fail_type=DOWNLOAD_FAIL_HTTP_AUTH)
                elif i == 2:
                    vr = VerificationResult()
                    vr.file_size_ok = False
                    vr.fail_types.append(VERIFY_FAIL_FILE_SIZE)
                    vr.details = "文件太小"
                    rec.mark_success(u, os.path.join(tmp, f"g1_{i}.mp4"), 10, 500, verification=vr)
                elif i == 3:
                    pass  # running
                else:
                    pass  # pending

            for i, u in enumerate(urls_g2):
                rec.mark_running(u, group="g2", output_dir=tmp)
                if i == 0:
                    rec.mark_success(u, os.path.join(tmp, f"g2_{i}.mp4"), 10, 2_000_000)
                elif i == 1:
                    rec.mark_failed(u, "超时", fail_type=DOWNLOAD_FAIL_TIMEOUT)

            queue_view_str = rec.format_queue_view()
            assert "任务队列视图" in queue_view_str
            assert "g1" in queue_view_str and "g2" in queue_view_str
            assert "待跑" in queue_view_str and "运行" in queue_view_str
            assert "失败" in queue_view_str and "异常" in queue_view_str
            print("  ✓ format_queue_view 包含所有分组和状态列")

            qv = rec.get_queue_view(group="g1")
            assert "g1" in qv
            g1_stat = qv["g1"]
            assert g1_stat["total"] == 5
            assert g1_stat[TASK_STATUS_SUCCESS] == 1
            assert g1_stat[TASK_STATUS_FAILED] == 1
            assert g1_stat[TASK_STATUS_VERIFY_FAILED] == 1
            unprocessed = g1_stat[TASK_STATUS_PENDING] + g1_stat[TASK_STATUS_RUNNING]
            assert unprocessed == 2, f"应有2个未处理（pending+running），实际 {unprocessed}"
            print("  ✓ get_queue_view 分组统计正确")

            out_txt = os.path.join(tmp, "fail_all.txt")
            n = rec.export_failed_urls(out_txt)
            assert n == 3
            with open(out_txt, 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f if l.strip()]
            assert len(lines) == 3
            assert urls_g1[1] in lines and urls_g1[2] in lines
            assert urls_g2[1] in lines
            print("  ✓ export_failed_urls 导出全部失败/异常（含 verify_failed）")

            out_g1 = os.path.join(tmp, "fail_g1.txt")
            n_g1 = rec.export_failed_urls(out_g1, group="g1")
            assert n_g1 == 2
            with open(out_g1, 'r', encoding='utf-8') as f:
                lines_g1 = [l.strip() for l in f if l.strip()]
            assert len(lines_g1) == 2
            assert urls_g2[1] not in lines_g1
            print("  ✓ export_failed_urls(group=g1) 正确按分组过滤")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_3_cookie_and_error_suggestions():
        s = "session=abc; token=xyz;  lang=zh-CN"
        d = load_cookies_from_string(s)
        assert d["session"] == "abc" and d["token"] == "xyz" and d["lang"] == "zh-CN"
        assert len(d) == 3
        print("  ✓ load_cookies_from_string 解析 name=value; 分号格式")

        os.environ["M3U8_COOKIE"] = "env_key=env_value"
        try:
            env_c = load_cookies_from_env()
            assert env_c.get("env_key") == "env_value"
            print("  ✓ load_cookies_from_env 读取 M3U8_COOKIE")

            merged = load_cookies(
                cookie_file=None,
                cookie_string="a=1;b=2",
                use_env=True,
            )
            assert merged["env_key"] == "env_value"
            assert merged["a"] == "1" and merged["b"] == "2"
            print("  ✓ load_cookies 合并 env + 字符串")

            merged_no_env = load_cookies(
                cookie_file=None, cookie_string="a=1", use_env=False,
            )
            assert "env_key" not in merged_no_env
            print("  ✓ load_cookies(use_env=False) 不读环境变量")
        finally:
            del os.environ["M3U8_COOKIE"]

        ft1, sug1 = classify_request_exception(TimeoutError_pseudo())
        assert ft1 == "timeout" and "建议" in sug1
        print(f"  ✓ Timeout => {ft1}, suggestion 已给出")

        ft2, sug2 = classify_request_exception(DNS_pseudo())
        assert ft2 == "dns" and "建议" in sug2
        print(f"  ✓ DNS 解析 => {ft2}, suggestion 已给出")

        ft3, sug3 = classify_request_exception(ConnReset_pseudo())
        assert ft3 == "connection" and "建议" in sug3
        print(f"  ✓ 连接重置 => {ft3}, suggestion 已给出")

    def test_4_progress_monotonic_and_speed():
        prog = DownloadProgress(total_segments=10)

        prog.add_in_progress(100_000)
        prog.add_in_progress(200_000)
        pct_1 = prog.percentage

        prog.add_completed(150_000)
        pct_2 = prog.percentage
        assert pct_2 >= pct_1, f"完成后百分比不应下降 {pct_2}<{pct_1}"
        pct_peak = prog._peak_percentage
        assert pct_peak > 0, "peak_percentage 应大于 0"
        print(f"  ✓ 进度百分比单调不减: {pct_1:.2f} -> {pct_2:.2f}")

        prog.remove_in_progress(100_000)
        pct_3 = prog.percentage
        assert pct_3 >= pct_peak, (
            f"回退 remove 后百分比不应低于峰值: {pct_3}<{pct_peak}"
        )
        print(f"  ✓ in_progress 回退后百分比不下降 (使用峰值)")

        display_after_rollback = prog.total_display_bytes
        peak_display = prog._peak_display_bytes
        assert display_after_rollback >= peak_display - 1, (
            f"display bytes 保持峰值: {display_after_rollback} vs {peak_display}"
        )
        print(f"  ✓ total_display_bytes 使用峰值防倒退")

        prog.add_in_progress(1_000_000)
        import time as _t
        _t.sleep(0.35)
        prog.update_speed()
        assert prog.current_speed > 0, (
            f"in_progress>0 且 elapsed>0.3s 时 speed 应>0，实际 {prog.current_speed}"
        )
        print(f"  ✓ 只有 in_progress 数据时也能估算速度: {prog.current_speed:.0f} B/s")

        dr = DownloadResult(total_segments=10)
        dr.failed_segments[0] = "鉴权失败 (HTTP 403)"
        dr.failed_segments[1] = "鉴权失败 (HTTP 403)"
        dr.failed_segments[2] = "下载超时"
        dr.set_dominant_fail()
        assert dr.fail_type == DOWNLOAD_FAIL_HTTP_AUTH
        assert dr.suggestion and "建议" in dr.suggestion
        print(f"  ✓ DownloadResult 主导失败判定: {dr.fail_type}")

    def test_5_cli_new_params():
        from m3u8_downloader.cli import create_parser
        parser = create_parser()
        help_txt = parser.format_help()
        for p in ["--cookie-string", "--no-cookie-env",
                  "--queue", "--export-failed"]:
            assert p in help_txt, f"帮助中缺少 {p}"
        print("  ✓ 所有新参数存在于帮助: --cookie-string, --no-cookie-env, --queue, --export-failed")

        ns = parser.parse_args([
            "-u", "http://x.com/a.m3u8",
            "--cookie-string", "s=1;t=2",
            "--no-cookie-env",
            "--verify",
        ])
        assert ns.cookie_string == "s=1;t=2"
        assert ns.no_cookie_env is True
        assert ns.verify is True
        print("  ✓ 新参数解析正常")

    def test_6_verify_output_details():
        tmp = tempfile.mkdtemp()
        try:
            no_file = os.path.join(tmp, "nope.mp4")
            r = verify_output(no_file, 10, 60.0)
            assert not r["ok"]
            v = r["verification"]
            assert not v.output_exists_ok
            assert VERIFY_FAIL_NO_OUTPUT in v.fail_types
            assert v.expected_segments == 10
            assert v.expected_duration == 60.0
            print("  ✓ verify_output 不存在文件 => no_output")

            tiny_file = os.path.join(tmp, "tiny.bin")
            with open(tiny_file, 'wb') as f:
                f.write(b"x" * 512)
            r2 = verify_output(tiny_file, 10, 0.0, min_file_size=1024, actual_segments=5)
            v2 = r2["verification"]
            assert not r2["ok"]
            assert VERIFY_FAIL_FILE_SIZE in v2.fail_types
            assert VERIFY_FAIL_SEGMENT_COUNT in v2.fail_types
            assert v2.actual_file_size == 512
            assert v2.actual_segments == 5
            assert v2.expected_min_size == 1024
            print(f"  ✓ verify_output 小文件+分片不匹配 => 多 fail_types: {v2.fail_types}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "*" * 70)
    print("*               M3U8 下载工具 - 第五轮改进测试                  *")
    print("*" * 70)

    run_test("1: 完整验片流程(串联+异常归类)", test_1_verification_flow)
    run_test("2: 队列视图+失败清单导出", test_2_queue_and_export_failed)
    run_test("3: Cookie 灵活传递+错误建议", test_3_cookie_and_error_suggestions)
    run_test("4: 进度条单调+大文件速度", test_4_progress_monotonic_and_speed)
    run_test("5: CLI 新参数检查", test_5_cli_new_params)
    run_test("6: verify_output 详细字段", test_6_verify_output_details)

    print("\n" + "=" * 70)
    print(f"测试结果: 共 {ok+fail} 项，通过 {ok} 项，失败 {fail} 项")
    if fail == 0:
        print("全部测试通过！")
    else:
        print(f"有 {fail} 项失败！")
    print("=" * 70)
    sys.exit(0 if fail == 0 else 1)


class _PseudoTimeout(Exception):
    pass


class _PseudoDNS(Exception):
    pass


class _PseudoConnReset(Exception):
    pass


def TimeoutError_pseudo():
    import requests.exceptions
    return requests.exceptions.ReadTimeout("Read timed out.")


def DNS_pseudo():
    import requests.exceptions
    return requests.exceptions.ConnectionError(
        "Name or service not known: getaddrinfo failed"
    )


def ConnReset_pseudo():
    import requests.exceptions
    return requests.exceptions.ConnectionError(
        "Connection reset by peer"
    )


if __name__ == "__main__":
    run_all_tests()
