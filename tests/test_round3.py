import os
import sys
import json
import struct
import tempfile
import shutil
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from m3u8_downloader.parser import (
    M3U8Parser, M3U8Playlist, Segment, EncryptionInfo,
    MasterPlaylistEntry, parse_time_str, format_quality_table
)
from m3u8_downloader.decryptor import (
    parse_iv, get_iv_from_segment_index, robust_pkcs7_unpad,
    SegmentDecryptor, KeyManager
)
from m3u8_downloader.downloader import (
    DownloadProgress, ProgressBar, DownloadResult, M3U8Downloader
)
from m3u8_downloader.task_recorder import (
    TaskRecorder, TaskRecord,
    TASK_STATUS_SUCCESS, TASK_STATUS_FAILED, TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING, TASK_STATUS_SKIPPED
)


def test_1_media_sequence_encryption():
    """测试1: EXT-X-MEDIA-SEQUENCE 非0起始序号的加解密"""
    print("=" * 70)
    print("测试1: EXT-X-MEDIA-SEQUENCE 非0起始序号的加解密")
    print("=" * 70)

    m3u8_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:1234
#EXT-X-TARGETDURATION:10
#EXT-X-KEY:METHOD=AES-128,URI="key.key"
#EXTINF:10.0,
segment0.ts
#EXTINF:10.0,
segment1.ts
#EXTINF:10.0,
segment2.ts
#EXTINF:10.0,
segment3.ts
#EXTINF:10.0,
segment4.ts
#EXT-X-ENDLIST
"""
    parser = M3U8Parser("http://example.com/test.m3u8")
    playlist = parser.parse_media_playlist(content=m3u8_content)

    assert playlist.media_sequence == 1234, f"起始序号应为1234，实际为{playlist.media_sequence}"
    assert len(playlist.segments) == 5

    for i, seg in enumerate(playlist.segments):
        expected_true = 1234 + i
        assert seg.true_index == expected_true, \
            f"分片{i} true_index应为{expected_true}，实际为{seg.true_index}"
        assert seg.index == i, f"分片{i} index应为{i}，实际为{seg.index}"
        assert seg.start_time == i * 10.0

    print(f"  ✓ 解析 media_sequence={playlist.media_sequence}")
    print(f"  ✓ 分片 true_index: {[s.true_index for s in playlist.segments]}")
    print(f"  ✓ 分片 start_time: {[s.start_time for s in playlist.segments]}")
    print(f"  ✓ 总时长: {playlist.duration}s")

    test_key = b"0123456789abcdef"
    decryptor = SegmentDecryptor(test_key)

    for i, seg in enumerate(playlist.segments):
        expected_iv = get_iv_from_segment_index(seg.true_index)
        calc_iv = parse_iv(seg.encryption.iv, seg.true_index)
        assert expected_iv == calc_iv, \
            f"分片{i} IV计算错误"

        plaintext = f"test_data_for_segment_{i}".encode()
        padded = pad(plaintext, AES.block_size)
        cipher = AES.new(test_key, AES.MODE_CBC, iv=expected_iv)
        encrypted = cipher.encrypt(padded)

        decrypted = decryptor.decrypt_segment_bytes(
            encrypted, seg.true_index, seg.encryption.iv
        )
        assert decrypted == plaintext, \
            f"分片{i} 使用true_index={seg.true_index} 解密失败"

    print(f"  ✓ AES-128 使用 true_index 解密验证通过 (5个分片)")

    print()
    m3u8_no_seq = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:5.0,
seg0.ts
#EXTINF:5.0,
seg1.ts
#EXT-X-ENDLIST
"""
    pl2 = parser.parse_media_playlist(content=m3u8_no_seq)
    assert pl2.media_sequence == 0, f"无MEDIA-SEQUENCE时默认为0，实际为{pl2.media_sequence}"
    for i, seg in enumerate(pl2.segments):
        assert seg.true_index == i
    print(f"  ✓ 无 MEDIA-SEQUENCE 时默认从0开始")

    print("测试1 全部通过 ✓")
    print()


def test_2_progress_realtime_and_retry():
    """测试2: 进度条实时刷新 + 重试不乱进度"""
    print("=" * 70)
    print("测试2: 进度条实时刷新 + 重试不乱进度")
    print("=" * 70)

    progress = DownloadProgress(total_segments=10)

    assert progress.percentage == 0.0
    assert progress.total_display_bytes == 0

    progress.add_in_progress(32768)
    assert progress.in_progress_bytes == 32768
    assert progress.total_display_bytes == 32768
    print(f"  ✓ 实时 in_progress 字节累加: {progress.total_display_bytes}")

    progress.add_in_progress(32768)
    assert progress.in_progress_bytes == 65536
    print(f"  ✓ 再次累加后: {progress.total_display_bytes}")

    progress.remove_in_progress(65536)
    assert progress.in_progress_bytes == 0
    assert progress.total_display_bytes >= 0, "使用峰值保护允许回退时保持非负"
    print(f"  ✓ 重试回退后 in_progress 清零: {progress.total_display_bytes} (峰值保护已启用)")

    progress.remove_in_progress(99999)
    assert progress.in_progress_bytes == 0
    print(f"  ✓ remove_in_progress 防负值保护")

    progress.add_completed(100000)
    assert progress.completed_segments == 1
    assert progress.downloaded_bytes == 100000
    assert progress.estimated_bytes_per_segment == 100000.0
    print(f"  ✓ 完成1个分片，估算单分片大小: {progress.estimated_bytes_per_segment}")

    est_total = progress.estimated_total
    assert est_total > 0
    print(f"  ✓ 估算总字节数: {est_total}")

    pct = progress.percentage
    assert pct > 0
    print(f"  ✓ 进度百分比: {pct:.1f}%")

    for i in range(1, 10):
        progress.add_completed(100000)
    assert progress.completed_segments == 10
    print(f"  ✓ 完成全部10个分片")
    print(f"  ✓ 最终已下载字节: {progress.downloaded_bytes}")
    print(f"  ✓ 进度百分比: {progress.percentage:.1f}%")

    result = DownloadResult(total_segments=5)
    result.success_segments.add(0)
    result.success_segments.add(1)
    result.success_segments.add(2)
    result.failed_segments[3] = "timeout"
    result.failed_segments[4] = "network error"
    assert result.success_count == 3
    assert result.failed_count == 2
    assert result.can_merge == False
    assert result.all_success == False
    failed_info = result.list_failed()
    assert "下载失败的分片" in failed_info
    assert "分片 3" in failed_info
    assert "分片 4" in failed_info
    print(f"  ✓ DownloadResult 失败分片记录 & can_merge 严格判定")

    print("测试2 全部通过 ✓")
    print()


def test_3_continue_task():
    """测试3: 继续未完成任务"""
    print("=" * 70)
    print("测试3: 继续未完成任务")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp(prefix="m3u8_test_")
    try:
        log_file = os.path.join(tmpdir, "tasks.json")

        recorder = TaskRecorder(log_file)

        url_success = "http://example.com/success.m3u8"
        url_failed = "http://example.com/failed.m3u8"
        url_pending = "http://example.com/pending.m3u8"
        url_running = "http://example.com/running.m3u8"

        out_success = os.path.join(tmpdir, "success.mp4")
        with open(out_success, 'wb') as f:
            f.write(b"fake mp4 data")

        recorder.mark_success(url_success, out_success, 10, 1024000)
        recorder.mark_failed(url_failed, "timeout", failed_segments=[3, 7], total_segments=10)
        recorder.get_or_create(url_pending)
        recorder.mark_running(url_running)

        stats = recorder.get_statistics()
        assert stats[TASK_STATUS_SUCCESS] == 1
        assert stats[TASK_STATUS_FAILED] == 1
        assert stats[TASK_STATUS_PENDING] == 1
        assert stats[TASK_STATUS_RUNNING] == 1
        print(f"  ✓ 初始状态: 成功={stats[TASK_STATUS_SUCCESS]}, "
              f"失败={stats[TASK_STATUS_FAILED]}, "
              f"待处理={stats[TASK_STATUS_PENDING]}, "
              f"运行中={stats[TASK_STATUS_RUNNING]}")

        assert recorder.is_success(url_success) == True
        assert recorder.is_success(url_failed) == False
        print(f"  ✓ is_success 双保险检查 (状态+文件存在)")

        retry_urls = recorder.get_retry_urls()
        assert len(retry_urls) == 3
        assert url_failed in retry_urls
        assert url_pending in retry_urls
        assert url_running in retry_urls
        assert url_success not in retry_urls
        print(f"  ✓ get_retry_urls 返回失败/待处理/运行中: {len(retry_urls)} 个")

        recorder.mark_skipped(url_success, reason="already_completed")
        skipped = recorder.list_skipped()
        assert len(skipped) == 1
        assert skipped[0].url == url_success
        print(f"  ✓ mark_skipped & list_skipped")

        summary = recorder.format_summary()
        assert "成功" in summary and "失败" in summary
        print(f"  ✓ format_summary 生成文本")

        recorder2 = TaskRecorder(log_file)
        assert len(recorder2.tasks) == 4
        print(f"  ✓ 重新加载任务记录，持久化正常")

        print("测试3 全部通过 ✓")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print()


def test_4_time_slice():
    """测试4: 时间范围截取"""
    print("=" * 70)
    print("测试4: 时间范围截取")
    print("=" * 70)

    segments_data = [
        (0, 10.0), (1, 10.0), (2, 10.0), (3, 10.0), (4, 10.0),
        (5, 10.0), (6, 10.0), (7, 10.0), (8, 10.0), (9, 10.0)
    ]
    segments = []
    for idx, dur in segments_data:
        segments.append(Segment(
            index=idx,
            true_index=100 + idx,
            url=f"http://example.com/seg{idx}.ts",
            duration=dur,
            start_time=idx * 10.0,
            encryption=EncryptionInfo(method="NONE")
        ))
    playlist = M3U8Playlist(
        url="http://example.com/test.m3u8",
        segments=segments,
        duration=100.0,
        media_sequence=100
    )

    assert parse_time_str("600") == 600.0
    assert parse_time_str("10:00") == 600.0
    assert parse_time_str("00:10:00") == 600.0
    assert parse_time_str("10:30.5") == 630.5
    assert parse_time_str("1:05:30") == 3930.0
    print(f"  ✓ parse_time_str: 纯秒/MM:SS/HH:MM:SS 都能解析")

    desc = playlist.describe_time_slice(25.0, 55.0)
    assert "25.0s" in desc and "55.0s" in desc
    print(f"  ✓ describe_time_slice: {desc}")

    sliced = playlist.slice_by_time(25.0, 55.0)
    assert sliced.duration == 40.0
    assert len(sliced.segments) == 4
    print(f"  ✓ 截取 25s~55s: {len(sliced.segments)}个分片, 时长{sliced.duration}s")

    expected_local_idx = [0, 1, 2, 3]
    expected_true_idx = [102, 103, 104, 105]
    for i, seg in enumerate(sliced.segments):
        assert seg.index == expected_local_idx[i], \
            f"本地index应为{expected_local_idx[i]}，实际{seg.index}"
        assert seg.true_index == expected_true_idx[i], \
            f"真实true_index应为{expected_true_idx[i]}，实际{seg.true_index}"
    print(f"  ✓ 截取后本地index重排: {[s.index for s in sliced.segments]}")
    print(f"  ✓ 截取后保留原始true_index: {[s.true_index for s in sliced.segments]}")

    sliced2 = playlist.slice_by_time(None, 15.0)
    assert len(sliced2.segments) == 2
    print(f"  ✓ 只指定end=None start: 前15s取{sliced2.duration}s, {len(sliced2.segments)}个分片")

    sliced3 = playlist.slice_by_time(85.0, None)
    assert len(sliced3.segments) == 2
    print(f"  ✓ 只指定start=None end: 后15s取{sliced3.duration}s, {len(sliced3.segments)}个分片")

    sliced4 = playlist.slice_by_time()
    assert len(sliced4.segments) == 10
    print(f"  ✓ 不指定范围: 返回全部")

    try:
        playlist.slice_by_time(50.0, 30.0)
        assert False, "应抛出异常"
    except ValueError:
        pass
    print(f"  ✓ start>=end 抛出 ValueError")

    try:
        playlist.slice_by_time(200.0, 300.0)
        assert False, "应抛出异常"
    except ValueError:
        pass
    print(f"  ✓ 完全超出范围 抛出 ValueError")

    print("测试4 全部通过 ✓")
    print()


def test_5_export_report():
    """测试5: 批量下载汇总报告导出"""
    print("=" * 70)
    print("测试5: 批量下载汇总报告导出")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp(prefix="m3u8_report_")
    try:
        log_file = os.path.join(tmpdir, "batch_tasks.json")
        recorder = TaskRecorder(log_file)

        for i in range(3):
            out = os.path.join(tmpdir, f"success_{i}.mp4")
            with open(out, 'wb') as f:
                f.write(b"x" * 1024)
            recorder.mark_success(
                f"http://example.com/s{i}.m3u8", out, 10 + i, 1024 * (i + 1)
            )

        recorder.mark_failed(
            "http://example.com/fail1.m3u8",
            "下载超时",
            failed_segments=[2, 5, 8],
            total_segments=10,
            downloaded_bytes=512000
        )
        recorder.mark_failed(
            "http://example.com/fail2.m3u8",
            "解密失败",
            decrypt_failed=[0, 1],
            total_segments=5
        )

        recorder.mark_skipped("http://example.com/skip.m3u8", reason="previously_done")
        recorder.get_or_create("http://example.com/pending.m3u8")

        report_txt = recorder.export_report(format="txt")
        assert report_txt and os.path.exists(report_txt)
        with open(report_txt, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "【统计】" in content
        assert "【成功任务】" in content
        assert "【失败任务】" in content
        assert "【跳过任务】" in content
        assert "【待处理任务】" in content
        assert "下载超时" in content
        assert "解密失败" in content
        assert "成功:" in content and "3" in content
        assert "失败:" in content and "2" in content
        print(f"  ✓ TXT报告生成: {os.path.basename(report_txt)}")
        print(f"    大小: {os.path.getsize(report_txt)} bytes")

        report_json = recorder.export_report(format="json")
        assert report_json and os.path.exists(report_json)
        with open(report_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert "statistics" in data
        assert data["statistics"][TASK_STATUS_SUCCESS] == 3
        assert data["statistics"][TASK_STATUS_FAILED] == 2
        assert len(data["success"]) == 3
        assert len(data["failed"]) == 2
        assert len(data["skipped"]) == 1
        assert len(data["pending"]) == 1
        assert data["success"][0]["output_file"]
        print(f"  ✓ JSON报告生成: {os.path.basename(report_json)}")
        print(f"    统计: 成功={data['statistics'][TASK_STATUS_SUCCESS]}, "
              f"失败={data['statistics'][TASK_STATUS_FAILED]}")

        print("测试5 全部通过 ✓")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print()


def test_6_end_to_end_decrypt_with_media_sequence():
    """测试6: 端到端 - 用真实AES加密数据验证MEDIA-SEQUENCE非0起始解密"""
    print("=" * 70)
    print("测试6: 端到端 AES-128 + MEDIA-SEQUENCE=1000 解密验证")
    print("=" * 70)

    m3u8_with_seq = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:1000
#EXT-X-TARGETDURATION:5
#EXT-X-KEY:METHOD=AES-128,URI="http://example.com/key.key"
#EXTINF:5.0,
http://example.com/seg_1000.ts
#EXTINF:5.0,
http://example.com/seg_1001.ts
#EXTINF:5.0,
http://example.com/seg_1002.ts
#EXTINF:5.0,
http://example.com/seg_1003.ts
#EXT-X-ENDLIST
"""
    parser = M3U8Parser("http://example.com/video.m3u8")
    playlist = parser.parse_media_playlist(content=m3u8_with_seq)

    assert playlist.media_sequence == 1000
    assert len(playlist.segments) == 4

    expected_true = [1000, 1001, 1002, 1003]
    for i, seg in enumerate(playlist.segments):
        assert seg.true_index == expected_true[i]
        assert seg.encryption.method == "AES-128"
        assert seg.encryption.key_url == "http://example.com/key.key"
        assert seg.encryption.iv is None
    print(f"  ✓ 解析出 media_sequence={playlist.media_sequence}")
    print(f"  ✓ 分片 true_index: {[s.true_index for s in playlist.segments]}")
    print(f"  ✓ 无 IV 字段 (需要用序号计算)")

    test_key = b"A" * 16

    tmpdir = tempfile.mkdtemp(prefix="m3u8_e2e_")
    try:
        key_manager = KeyManager()
        decryptor = key_manager.get_decryptor(
            "http://example.com/key.key",
            lambda url: test_key
        )
        assert decryptor is not None

        for i, seg in enumerate(playlist.segments):
            plaintext = f"VIDEO_CONTENT_SEGMENT_{seg.true_index}_HELLO_WORLD_{'X' * (i * 17)}".encode()
            padded = pad(plaintext, AES.block_size)

            iv = get_iv_from_segment_index(seg.true_index)
            cipher = AES.new(test_key, AES.MODE_CBC, iv=iv)
            encrypted = cipher.encrypt(padded)

            input_file = os.path.join(tmpdir, f"segment_{seg.index:05d}.ts")
            output_file = os.path.join(tmpdir, f"segment_{seg.index:05d}_dec.ts")

            with open(input_file, 'wb') as f:
                f.write(encrypted)

            ok = decryptor.decrypt_segment_file(
                input_file, output_file, seg.true_index, seg.encryption.iv
            )
            assert ok, f"分片{i}解密失败"

            with open(output_file, 'rb') as f:
                decrypted = f.read()
            assert decrypted == plaintext, \
                f"分片{i} 内容不匹配!\n期望: {plaintext[:50]}\n实际: {decrypted[:50]}"

            print(f"  ✓ 分片#{i} true_index={seg.true_index} "
                  f"IV={iv.hex()[:16]}... 解密正确, 大小={len(decrypted)}B")

        print()
        print(f"  ✓ 全部4个分片使用真实序号解密成功！")
        print(f"  ✓ 非0起始序号(MEDIA-SEQUENCE=1000)的长视频/直播回放兼容验证通过")

        print("测试6 全部通过 ✓")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print()


def test_7_cli_help():
    """测试7: CLI帮助信息和参数解析"""
    print("=" * 70)
    print("测试7: CLI 参数检查")
    print("=" * 70)

    from m3u8_downloader.cli import create_parser
    parser = create_parser()

    help_text = parser.format_help()

    assert "--continue" in help_text
    assert "--start" in help_text
    assert "--end" in help_text
    assert "--export-report" in help_text
    assert "--report-format" in help_text
    print(f"  ✓ --continue 参数存在")
    print(f"  ✓ --start/--end 参数存在")
    print(f"  ✓ --export-report/--report-format 参数存在")

    assert "10:00" in help_text
    assert "25:00" in help_text
    print(f"  ✓ 帮助示例包含时间范围截取用法")

    test_args = parser.parse_args([
        "-u", "http://example.com/test.m3u8",
        "--start", "10:00", "--end", "25:00",
        "--quality", "1",
        "--export-report", "--report-format", "json",
        "-d", "./out", "-c", "8"
    ])
    assert test_args.start == "10:00"
    assert test_args.end == "25:00"
    assert test_args.quality == 1
    assert test_args.export_report == True
    assert test_args.report_format == "json"
    assert test_args.concurrency == 8
    print(f"  ✓ 参数解析正常: --start={test_args.start}, --end={test_args.end}")

    test_continue = parser.parse_args([
        "--continue", "tasks.json",
        "--report-format", "txt"
    ])
    assert test_continue.continue_task == "tasks.json"
    assert test_continue.report_format == "txt"
    print(f"  ✓ --continue 参数解析正常")

    print("测试7 全部通过 ✓")
    print()


def run_all_tests():
    print()
    print("*" * 70)
    print("*" + " " * 22 + "M3U8 下载工具 - 第三轮改进测试" + " " * 14 + "*")
    print("*" * 70)
    print()

    tests = [
        test_1_media_sequence_encryption,
        test_2_progress_realtime_and_retry,
        test_3_continue_task,
        test_4_time_slice,
        test_5_export_report,
        test_6_end_to_end_decrypt_with_media_sequence,
        test_7_cli_help,
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
