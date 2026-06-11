import argparse
import os
import sys
from typing import List, Optional, Tuple, Dict

from .parser import (
    M3U8Parser,
    format_quality_table,
    MasterPlaylistEntry,
    parse_time_str
)
from .downloader import M3U8Downloader, DownloadResult
from .merger import FFmpegMerger, cleanup_temp_dir, verify_output
from .task_recorder import (
    TaskRecorder,
    TaskRecord,
    TASK_STATUS_SUCCESS,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_FAILED,
    TASK_STATUS_VERIFY_FAILED,
    MERGE_FAIL_FFMPEG,
    MERGE_FAIL_NO_SEGMENTS,
    UNKNOWN_FAIL,
    DOWNLOAD_FAIL_SEGMENTS,
    DOWNLOAD_FAIL_DECRYPT,
)
from .utils import (
    get_temp_dir,
    sanitize_filename,
    format_size,
    format_time,
    load_cookies,
    parse_header_string,
    build_headers,
    classify_http_error,
    classify_request_exception,
)


def read_url_list(file_path: str) -> List[str]:
    urls = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
    except Exception as e:
        print(f"读取URL列表失败: {e}")
        sys.exit(1)
    return urls


def parse_proxy(proxy_str: str) -> Optional[dict]:
    if not proxy_str:
        return None
    return {
        "http": proxy_str,
        "https": proxy_str
    }


def _generate_output_filename(url: str, output_format: str,
                               output_filename: Optional[str]) -> str:
    if output_filename:
        if not output_filename.lower().endswith(f'.{output_format.lower()}'):
            return f"{output_filename}.{output_format.lower()}"
        return output_filename

    base_name = os.path.basename(url).split('?')[0]
    if base_name.endswith('.m3u8'):
        base_name = base_name[:-5]
    if not base_name:
        base_name = "video"
    base_name = sanitize_filename(base_name)
    return f"{base_name}.{output_format.lower()}"


def _check_existing_output(output_file: str, resume: bool) -> bool:
    if resume and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"输出文件已存在且有效: {output_file}")
        return True
    return False


def _build_request_headers(custom_header_str: Optional[str],
                            cookie_file: Optional[str] = None,
                            cookie_string: Optional[str] = None,
                            use_cookie_env: bool = True) -> dict:
    custom_headers = parse_header_string(custom_header_str) if custom_header_str else {}
    cookies = load_cookies(
        cookie_file=cookie_file,
        cookie_string=cookie_string,
        use_env=use_cookie_env,
    )
    headers = build_headers(custom_headers=custom_headers, cookies=cookies)
    if cookies:
        print(f"已加载 {len(cookies)} 个 Cookie (文件={bool(cookie_file)}, "
              f"字符串={bool(cookie_string)}, 环境变量={use_cookie_env})")
    if custom_headers:
        print(f"已加载 {len(custom_headers)} 个自定义 Header")
    return headers


def _parse_playlist(url: str, proxies: Optional[dict],
                     quality_index: Optional[int],
                     target_resolution: Optional[str],
                     target_bandwidth_mbps: Optional[float],
                     list_qualities: bool,
                     headers: Optional[dict] = None) -> Tuple[Optional[M3U8Parser],
                                                      Optional[List[MasterPlaylistEntry]],
                                                      Optional[object],
                                                      Optional[str]]:
    try:
        parser = M3U8Parser(url, proxies=proxies, headers=headers)
        entries = parser.get_master_entries()

        if entries and list_qualities:
            print(format_quality_table(entries))
            return parser, entries, None, "LISTED"

        selected_entry = None
        target_bw = None
        if target_bandwidth_mbps:
            target_bw = int(target_bandwidth_mbps * 1_000_000)

        if entries:
            try:
                selected_entry = parser.select_quality(
                    entries,
                    select_index=quality_index,
                    target_bandwidth=target_bw,
                    target_resolution=target_resolution,
                    select_highest=(quality_index is None and
                                    target_bw is None and
                                    target_resolution is None)
                )
                print(f"已选择清晰度: #{selected_entry.index} {selected_entry.quality_label}")
            except ValueError as e:
                print(f"清晰度选择失败: {e}")
                print(format_quality_table(entries))
                return None, entries, None, str(e)

        playlist = parser.parse(
            quality_index=(selected_entry.index if selected_entry else None),
            target_resolution=target_resolution,
            target_bandwidth=target_bw
        )

        return parser, entries, playlist, None

    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "403" in err_str:
            return None, None, None, f"鉴权失败: {err_str} (请检查 Cookie 或自定义 Header)"
        return None, None, None, f"解析 m3u8 失败: {e}"


def download_single(
    url: str,
    output_dir: str = ".",
    output_filename: Optional[str] = None,
    output_format: str = "mp4",
    concurrency: int = 5,
    proxy: Optional[str] = None,
    resume: bool = True,
    keep_temp: bool = False,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    verbose: bool = False,
    quality_index: Optional[int] = None,
    target_resolution: Optional[str] = None,
    target_bandwidth_mbps: Optional[float] = None,
    list_qualities_only: bool = False,
    task_recorder: Optional[TaskRecorder] = None,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    custom_headers: Optional[dict] = None,
    group: Optional[str] = None,
    verify: bool = False
) -> Tuple[bool, str]:
    proxies = parse_proxy(proxy)

    output_filename = _generate_output_filename(url, output_format, output_filename)
    output_file = os.path.join(output_dir, output_filename)

    quality_label_for_log = None
    selected_quality_idx = None

    print(f"\n正在解析 m3u8: {url}")
    parser, entries, playlist, err = _parse_playlist(
        url, proxies, quality_index, target_resolution,
        target_bandwidth_mbps, list_qualities_only,
        headers=custom_headers
    )

    if err == "LISTED":
        return True, "已列出清晰度"
    if err:
        print(err)
        if task_recorder:
            task_recorder.mark_failed(url, err)
        return False, err

    if playlist is None:
        msg = "无法获取播放列表"
        print(msg)
        if task_recorder:
            task_recorder.mark_failed(url, msg)
        return False, msg

    selected_entry = None
    if entries:
        selected_entry = parser.select_quality(
            entries,
            select_index=quality_index,
            target_bandwidth=(int(target_bandwidth_mbps * 1_000_000)
                              if target_bandwidth_mbps else None),
            target_resolution=target_resolution,
            select_highest=(quality_index is None and
                            target_bandwidth_mbps is None and
                            target_resolution is None)
        )
        quality_label_for_log = selected_entry.quality_label
        selected_quality_idx = selected_entry.index

    if start_sec is not None or end_sec is not None:
        try:
            print(playlist.describe_time_slice(start_sec, end_sec))
            playlist = playlist.slice_by_time(start_sec, end_sec)
            print(f"截取后共 {len(playlist.segments)} 个分片，时长 {format_time(playlist.duration)}")
        except ValueError as e:
            msg = f"时间范围截取失败: {e}"
            print(msg)
            if task_recorder:
                task_recorder.mark_failed(url, msg)
            return False, msg

    total_segments = len(playlist.segments)
    expected_duration = playlist.duration
    print(f"找到 {total_segments} 个分片")
    if playlist.duration > 0:
        print(f"总时长: {format_time(playlist.duration)}")
    if playlist.is_encrypted:
        print(f"检测到加密: AES-128 (起始序号: {playlist.media_sequence})")

    if _check_existing_output(output_file, resume):
        if task_recorder:
            task_recorder.mark_success(url, output_file, total_segments, 0)
        return True, "文件已存在"

    if task_recorder:
        task_recorder.mark_running(
            url, output_format, selected_quality_idx, quality_label_for_log,
            group=group, output_dir=output_dir
        )

    temp_dir = get_temp_dir(output_dir)
    if verbose:
        print(f"临时目录: {temp_dir}")

    print(f"开始下载 (并发数: {concurrency})...")
    downloader = M3U8Downloader(
        playlist=playlist,
        output_dir=temp_dir,
        concurrency=concurrency,
        proxies=proxies,
        headers=custom_headers,
        resume=resume,
        retries=5,
        timeout=60
    )

    result: DownloadResult = downloader.download(show_progress=True)

    if not result.can_merge:
        msg_bits = []
        if result.failed_count > 0:
            msg_bits.append(f"{result.failed_count}个分片下载失败")
        if result.decrypt_failed:
            msg_bits.append(f"{len(result.decrypt_failed)}个分片解密失败")
        if result.cancelled:
            msg_bits.append("任务被中止")
        if result.error_message:
            msg_bits.append(result.error_message)

        msg = "；".join(msg_bits) if msg_bits else "分片不完整"
        print(f"\n✗ 下载未完成: {msg}")
        print(result.list_failed())
        if result.suggestion:
            print(result.suggestion)

        if not keep_temp:
            if verbose:
                print("保留临时目录以便断点续传:")
                print(f"  {temp_dir}")
        else:
            print(f"临时目录: {temp_dir}")

        if task_recorder:
            task_recorder.mark_failed(
                url, msg,
                failed_segments=list(result.failed_segments.keys()),
                decrypt_failed=list(result.decrypt_failed.keys()),
                total_segments=total_segments,
                downloaded_bytes=result.total_bytes,
                fail_type=result.fail_type or (
                    DOWNLOAD_FAIL_DECRYPT if result.decrypt_failed
                    else DOWNLOAD_FAIL_SEGMENTS
                ),
                suggestion=result.suggestion,
            )
        return False, msg

    print("下载和解密全部完成，开始合并...")

    merger = FFmpegMerger(ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
    if not merger.check_ffmpeg():
        msg = "未找到 ffmpeg，请确保 ffmpeg 已安装并在 PATH 中"
        print(f"错误: {msg}")
        print(f"临时文件保留在: {temp_dir}")
        if task_recorder:
            task_recorder.mark_failed(
                url, msg,
                total_segments=total_segments,
                downloaded_bytes=result.total_bytes,
                fail_type=MERGE_FAIL_NO_SEGMENTS,
                suggestion="建议: 安装 ffmpeg 并加入 PATH，或用 --ffmpeg 指定可执行文件路径",
            )
        return False, msg

    segment_files = downloader.get_segment_files()

    merge_ok = merger.merge(
        segment_files=segment_files,
        output_file=output_file,
        output_format=output_format,
        verbose=verbose
    )

    if not merge_ok:
        msg = "FFmpeg 合并失败"
        print(f"✗ {msg}")
        print(f"分片文件仍在: {temp_dir}")
        if task_recorder:
            task_recorder.mark_failed(
                url, msg,
                total_segments=total_segments,
                downloaded_bytes=result.total_bytes,
                fail_type=MERGE_FAIL_FFMPEG,
                suggestion="建议: 检查 ffmpeg 是否正常可用，或使用 -v 查看详细错误",
            )
        return False, msg

    print("✓ 合并完成!")

    verification = None
    if verify:
        print("正在校验输出文件...")
        ver_result = verify_output(
            output_file, total_segments, expected_duration,
            ffprobe_path=ffprobe_path,
            actual_segments=len(segment_files),
        )
        verification = ver_result["verification"]
        if ver_result["ok"]:
            print(f"  ✓ 校验通过: {verification.details}")
        else:
            print(f"  ✗ 校验未通过: {verification.details}")
            if verification.fail_types:
                vnames = {
                    "no_output": "输出不存在",
                    "segment_count": "分片数不匹配",
                    "file_size": "文件大小异常",
                    "duration": "时长偏差大",
                    "ffprobe": "ffprobe 无法探测",
                }
                short = [vnames.get(f, f) for f in verification.fail_types]
                print(f"  ✗ 异常项: {', '.join(short)}")

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"  输出文件: {output_file}")
        print(f"  文件大小: {format_size(file_size)}")

    if not keep_temp:
        if verbose:
            print("清理临时文件...")
        cleanup_temp_dir(temp_dir)

    if task_recorder:
        task_recorder.mark_success(
            url, output_file, total_segments, result.total_bytes,
            verification=verification,
        )

    if verification and not verification.all_ok:
        return False, f"下载完成但校验异常: {verification.details}"

    return True, "成功"


def download_batch(
    url_list_file: Optional[str] = None,
    output_dir: str = ".",
    output_format: str = "mp4",
    concurrency: int = 5,
    proxy: Optional[str] = None,
    resume: bool = True,
    keep_temp: bool = False,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    verbose: bool = False,
    quality_index: Optional[int] = None,
    target_resolution: Optional[str] = None,
    target_bandwidth_mbps: Optional[float] = None,
    task_log_file: Optional[str] = None,
    skip_completed: bool = True,
    continue_task_log: Optional[str] = None,
    continue_group: Optional[str] = None,
    export_report: bool = False,
    report_format: str = "txt",
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    custom_headers: Optional[dict] = None,
    group: Optional[str] = None,
    verify: bool = False
) -> None:
    if continue_task_log:
        if not os.path.exists(continue_task_log):
            print(f"错误: 任务记录文件不存在: {continue_task_log}")
            sys.exit(1)
        task_log_file = continue_task_log
        recorder = TaskRecorder(task_log_file)
        urls = recorder.get_retry_urls(group=continue_group)
        if not urls:
            if continue_group:
                print(f"分组 '{continue_group}' 中没有需要继续处理的任务")
            else:
                print("没有需要继续处理的任务（所有任务均已成功）")
            print(recorder.format_summary())
            return
        if continue_group:
            print(f"继续未完成任务: 分组 '{continue_group}'，从 {task_log_file} 读取")
        else:
            print(f"继续未完成任务: 从 {task_log_file} 读取")
        print(f"待处理任务数: {len(urls)}")
    else:
        if not url_list_file:
            print("错误: 请指定 URL 列表文件 (-f) 或使用 --continue")
            sys.exit(1)
        urls = read_url_list(url_list_file)
        if not urls:
            print("URL列表为空")
            return
        if not task_log_file:
            base = os.path.splitext(os.path.basename(url_list_file))[0]
            task_log_file = os.path.join(output_dir, f"{base}_tasks.json")
        recorder = TaskRecorder(task_log_file)

    print(f"任务记录文件: {task_log_file}")
    print(recorder.format_summary())
    if group:
        print(f"当前分组: {group}")
    print()

    print(f"共 {len(urls)} 个URL待处理")
    print("=" * 60)

    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] 处理: {url[:80]}{'...' if len(url) > 80 else ''}")
        print("-" * 60)

        if skip_completed and recorder.is_success(url):
            task = recorder.get_task(url)
            out = task.output_file if task else "(未知)"
            print(f"跳过: 之前已成功完成 -> {out}")
            recorder.mark_skipped(url, reason="previously_completed")
            skip_count += 1
            print("-" * 60)
            continue

        try:
            ok, msg = download_single(
                url=url,
                output_dir=output_dir,
                output_format=output_format,
                concurrency=concurrency,
                proxy=proxy,
                resume=resume,
                keep_temp=keep_temp,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
                verbose=verbose,
                quality_index=quality_index,
                target_resolution=target_resolution,
                target_bandwidth_mbps=target_bandwidth_mbps,
                list_qualities_only=False,
                task_recorder=recorder,
                start_sec=start_sec,
                end_sec=end_sec,
                custom_headers=custom_headers,
                group=group,
                verify=verify
            )
            if ok:
                success_count += 1
                print(f"✓ 成功: {msg}")
            else:
                fail_count += 1
                print(f"✗ 失败: {msg}")
        except KeyboardInterrupt:
            print("\n用户中断")
            recorder.save()
            break
        except Exception as e:
            fail_count += 1
            err = f"未捕获异常: {e}"
            print(f"✗ {err}")
            recorder.mark_failed(url, err)

        print("-" * 60)
        recorder.save()

    print("\n" + "=" * 60)
    print("批量下载结束:")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    if skip_count > 0:
        print(f"  跳过: {skip_count}")
    print()
    print(recorder.format_summary())

    failed = recorder.list_failed()
    if failed:
        print(f"\n失败任务列表 ({len(failed)} 个):")
        for t in failed:
            print(f"  {t.url}")
            if t.error_message:
                print(f"    原因: {t.error_message[:100]}")
            if t.suggestion:
                print(f"    建议: {t.suggestion[:100]}")

    verify_failed = recorder.list_verify_failed()
    if verify_failed:
        print(f"\n校验异常任务列表 ({len(verify_failed)} 个):")
        for t in verify_failed:
            print(f"  {t.url}")
            if t.verification and t.verification.details:
                print(f"    详情: {t.verification.details[:100]}")
            if t.suggestion:
                print(f"    建议: {t.suggestion[:100]}")

    fail_type_groups = recorder.get_fail_type_groups(group=group or continue_group)
    if fail_type_groups:
        type_names_map = {
            "no_output": "输出文件不存在",
            "segment_count": "分片数不匹配",
            "file_size": "文件大小异常",
            "duration": "时长偏差大",
            "ffprobe": "ffprobe 探测失败",
            "http_auth": "HTTP 鉴权失败 401/403",
            "http_not_found": "HTTP 资源不存在 404",
            "http_rate_limit": "HTTP 频率限制 429",
            "http_server": "HTTP 服务器错误 5xx",
            "timeout": "网络请求超时",
            "dns": "DNS 解析失败",
            "connection": "网络连接失败",
            "missing_segments": "下载分片缺失",
            "decrypt": "解密失败",
            "ffmpeg": "FFmpeg 合并失败",
            "no_segments": "无可合并分片",
            "unknown": "其他未归类错误",
        }
        print(f"\n异常类型汇总 ({len(fail_type_groups)} 类):")
        for ft, items in sorted(fail_type_groups.items(),
                                  key=lambda kv: -len(kv[1])):
            name = type_names_map.get(ft, ft)
            print(f"  [{name}]  {len(items)} 条")

    groups = recorder.get_groups()
    if groups:
        print(f"\n分组汇总:")
        for g in groups:
            gs = recorder.get_group_statistics(g)
            true_fail = gs[TASK_STATUS_FAILED] + gs[TASK_STATUS_VERIFY_FAILED]
            print(f"  {g}: 成功={gs[TASK_STATUS_SUCCESS]} "
                  f"失败/异常={true_fail} "
                  f"(失败{gs[TASK_STATUS_FAILED]}+异常{gs[TASK_STATUS_VERIFY_FAILED]}) "
                  f"跳过={gs[TASK_STATUS_SKIPPED]}")

    if export_report or continue_task_log or fail_count > 0:
        report_file = recorder.export_report(format=report_format)
        if report_file:
            print(f"\n✓ 汇总报告已导出: {report_file}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M3U8 视频下载与合并工具 (增强版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
=== 常用示例 ===

1. 下载单个视频 (默认最高清晰度):
   %(prog)s https://example.com/video.m3u8

2. 使用 Cookie 文件下载需要登录的视频:
   %(prog)s -u https://example.com/video.m3u8 --cookie cookies.txt

3. 直接传入 Cookie 字符串和环境变量 Cookie:
   %(prog)s -u https://example.com/video.m3u8 --cookie-string "session=abc;token=xyz"

4. 传递自定义请求头 (防盗链 Referer):
   %(prog)s -u https://example.com/video.m3u8 --header "Referer:https://example.com"

5. 列出所有可选清晰度:
   %(prog)s -u https://example.com/video.m3u8 --list-qualities

6. 下载指定时间范围 (从第10分钟到第25分钟):
   %(prog)s -u https://example.com/video.m3u8 --start 10:00 --end 25:00

7. 批量下载并指定分组:
   %(prog)s -f urls.txt -d downloads/ --group drama_ep01

8. 继续未完成的批量任务:
   %(prog)s --continue downloads/urls_tasks.json

9. 继续某个分组的任务:
   %(prog)s --continue downloads/urls_tasks.json --group drama_ep01

10. 下载后校验:
    %(prog)s -u https://example.com/video.m3u8 --verify

11. 查看任务队列状态:
    %(prog)s --queue downloads/urls_tasks.json

12. 导出某个分组的失败清单 (可直接喂给 -f 再次下载):
    %(prog)s --export-failed failed_urls.txt --task-log downloads/urls_tasks.json --group drama_ep01

13. 使用代理:
    %(prog)s -u https://example.com/video.m3u8 --proxy http://127.0.0.1:8080
        """
    )

    parser.add_argument(
        "url",
        nargs="?",
        help="m3u8 视频URL"
    )
    parser.add_argument(
        "-u", "--url",
        dest="url_arg",
        help="m3u8 视频URL (与位置参数二选一)"
    )
    parser.add_argument(
        "-f", "--file",
        help="从文件中读取URL列表进行批量下载 (自动记录任务状态)"
    )
    parser.add_argument(
        "-o", "--output",
        help="输出文件名 (默认根据URL自动生成)"
    )
    parser.add_argument(
        "-d", "--output-dir",
        default=".",
        help="输出目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--format",
        choices=["mp4", "mkv", "MP4", "MKV"],
        default="mp4",
        help="输出格式: mp4 或 mkv (默认: mp4)"
    )
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        default=5,
        help="下载并发数 (默认: 5, 建议 3-16)"
    )
    parser.add_argument(
        "--proxy",
        help="代理服务器地址，如 http://127.0.0.1:8080 或 socks5://127.0.0.1:1080"
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="禁用断点续传 (重新下载所有分片)"
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留临时下载文件 (默认自动清理)"
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="ffmpeg 可执行文件路径 (默认: ffmpeg)"
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="ffprobe 可执行文件路径 (默认: ffprobe)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细输出"
    )

    auth_group = parser.add_argument_group("请求头与鉴权")
    auth_group.add_argument(
        "--header",
        action="append",
        default=None,
        metavar="HEADER",
        help="自定义请求头，格式 \"Name:Value\"，可多次使用，如 --header \"Referer:https://x.com\""
    )
    auth_group.add_argument(
        "--cookie",
        default=None,
        metavar="COOKIE_FILE",
        help="从浏览器导出的 Cookie 文件 (Netscape 格式或 name=value 逐行格式)"
    )
    auth_group.add_argument(
        "--cookie-string",
        default=None,
        metavar="COOKIE_STR",
        help="直接传入 Cookie 字符串，格式 \"name1=value1; name2=value2\""
    )
    auth_group.add_argument(
        "--no-cookie-env",
        action="store_true",
        help="禁用从环境变量 (M3U8_COOKIE/M3U8_COOKIES/HTTP_COOKIE) 读取 Cookie"
    )

    quality_group = parser.add_argument_group("清晰度选择")
    quality_group.add_argument(
        "--list-qualities",
        action="store_true",
        help="列出Master Playlist所有可选清晰度后退出 (不下载)"
    )
    quality_group.add_argument(
        "--quality",
        type=int,
        default=None,
        help="按序号选择清晰度 (从 0 开始，配合 --list-qualities 使用)"
    )
    quality_group.add_argument(
        "--resolution",
        type=str,
        default=None,
        help="按分辨率选择清晰度，如 1280x720 / 1920x1080"
    )
    quality_group.add_argument(
        "--bandwidth",
        type=float,
        default=None,
        help="按目标码率选择 (单位 Mbps，会选最接近的)"
    )

    time_group = parser.add_argument_group("时间范围截取")
    time_group.add_argument(
        "--start",
        default=None,
        help="起始时间 (支持 HH:MM:SS / MM:SS / 秒数)，例如 10:00 表示第10分钟"
    )
    time_group.add_argument(
        "--end",
        default=None,
        help="结束时间 (支持 HH:MM:SS / MM:SS / 秒数)，例如 25:00 表示第25分钟"
    )

    batch_group = parser.add_argument_group("批量下载与任务管理")
    batch_group.add_argument(
        "--task-log",
        default=None,
        help="批量下载任务记录文件 (默认放在输出目录下，基于列表文件名)"
    )
    batch_group.add_argument(
        "--no-skip-completed",
        action="store_true",
        help="批量下载时不跳过已成功记录的任务 (强制重新下载)"
    )
    batch_group.add_argument(
        "--continue",
        dest="continue_task",
        default=None,
        metavar="TASK_LOG",
        help="继续未完成的任务，从指定的任务记录 JSON 读取失败/待处理的 URL，跳过成功项"
    )
    batch_group.add_argument(
        "--group",
        default=None,
        help="批量下载分组名 (保存到任务记录中，--continue 时可按分组过滤)"
    )
    batch_group.add_argument(
        "--export-report",
        action="store_true",
        help="批量任务结束后导出汇总报告 (有失败任务时默认导出)"
    )
    batch_group.add_argument(
        "--report-format",
        choices=["txt", "json"],
        default="txt",
        help="汇总报告格式: txt 或 json (默认: txt)"
    )
    batch_group.add_argument(
        "--verify",
        action="store_true",
        help="下载完成后校验输出文件 (分片数量、文件大小、时长、ffprobe 探测)"
    )
    batch_group.add_argument(
        "--queue",
        metavar="TASK_LOG",
        default=None,
        help="查看任务记录的队列视图 (按分组统计各状态数量) 后退出，不下载"
    )
    batch_group.add_argument(
        "--export-failed",
        metavar="OUTPUT_FILE",
        default=None,
        help="导出失败/校验异常任务的 URL 清单到指定文件，可直接用 -f 参数再次下载"
    )

    parser.add_argument(
        "--version",
        action="version",
        version="m3u8-downloader 5.0.0 (enhanced)"
    )

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    url = args.url or args.url_arg

    start_sec = None
    end_sec = None
    try:
        if args.start:
            start_sec = parse_time_str(args.start)
        if args.end:
            end_sec = parse_time_str(args.end)
    except ValueError as e:
        print(f"时间格式错误: {e}")
        print("支持格式: HH:MM:SS, MM:SS, 或纯秒数")
        sys.exit(1)

    if args.queue:
        if not os.path.exists(args.queue):
            print(f"错误: 任务记录文件不存在: {args.queue}")
            sys.exit(1)
        recorder = TaskRecorder(args.queue)
        print(recorder.format_queue_view(group=args.group))
        return

    if args.export_failed:
        task_log = args.task_log
        if not task_log and args.continue_task:
            task_log = args.continue_task
        if not task_log and args.file:
            base = os.path.splitext(os.path.basename(args.file))[0]
            task_log = os.path.join(os.path.abspath(args.output_dir), f"{base}_tasks.json")
        if not task_log or not os.path.exists(task_log):
            print(f"错误: 无法确定任务记录文件，请使用 --task-log 指定，"
                  f"或在执行批量下载后再导出")
            sys.exit(1)
        recorder = TaskRecorder(task_log)
        count = recorder.export_failed_urls(
            args.export_failed,
            group=args.group,
            include_verify_failed=True
        )
        if count > 0:
            print(f"✓ 已导出 {count} 个失败/异常任务 URL 到: {args.export_failed}")
            print(f"  可使用以下命令重试: python main.py -f \"{args.export_failed}\"")
        else:
            print("没有需要导出的失败任务")
        return

    if not url and not args.file and not args.continue_task:
        parser.print_help()
        print("\n错误: 请指定 m3u8 URL (位置参数/-u) 或 URL 列表文件 (-f) 或 --continue")
        sys.exit(1)

    custom_headers = _build_request_headers(
        ",".join(args.header) if args.header else None,
        cookie_file=args.cookie,
        cookie_string=args.cookie_string,
        use_cookie_env=not args.no_cookie_env,
    )

    output_format = args.format.lower()
    resume = not args.no_resume
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    skip_completed = not args.no_skip_completed

    if args.file or args.continue_task:
        download_batch(
            url_list_file=args.file,
            output_dir=output_dir,
            output_format=output_format,
            concurrency=args.concurrency,
            proxy=args.proxy,
            resume=resume,
            keep_temp=args.keep_temp,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            verbose=args.verbose,
            quality_index=args.quality,
            target_resolution=args.resolution,
            target_bandwidth_mbps=args.bandwidth,
            task_log_file=args.task_log,
            skip_completed=skip_completed,
            continue_task_log=args.continue_task,
            continue_group=args.group,
            export_report=args.export_report,
            report_format=args.report_format,
            start_sec=start_sec,
            end_sec=end_sec,
            custom_headers=custom_headers,
            group=args.group,
            verify=args.verify
        )
    else:
        if args.list_qualities:
            proxies = parse_proxy(args.proxy)
            tmp_parser = M3U8Parser(url, proxies=proxies, headers=custom_headers)
            entries = tmp_parser.get_master_entries()
            if entries:
                print(format_quality_table(entries))
            else:
                print("该链接不是 Master Playlist，没有可选清晰度 (单一码率)")
            return

        ok, msg = download_single(
            url=url,
            output_dir=output_dir,
            output_filename=args.output,
            output_format=output_format,
            concurrency=args.concurrency,
            proxy=args.proxy,
            resume=resume,
            keep_temp=args.keep_temp,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            verbose=args.verbose,
            quality_index=args.quality,
            target_resolution=args.resolution,
            target_bandwidth_mbps=args.bandwidth,
            list_qualities_only=False,
            task_recorder=None,
            start_sec=start_sec,
            end_sec=end_sec,
            custom_headers=custom_headers,
            verify=args.verify
        )
        if ok:
            print(f"\n✓ 任务完成: {msg}")
            sys.exit(0)
        else:
            print(f"\n✗ 任务失败: {msg}")
            sys.exit(1)


if __name__ == "__main__":
    main()
