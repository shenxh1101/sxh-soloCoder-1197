import argparse
import os
import sys
from typing import List, Optional

from .parser import M3U8Parser
from .downloader import M3U8Downloader
from .merger import FFmpegMerger, cleanup_temp_dir
from .utils import (
    get_temp_dir,
    sanitize_filename,
    format_size,
    format_time
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


def parse_proxy(proxy_str: str) -> dict:
    if not proxy_str:
        return None
    proxies = {
        "http": proxy_str,
        "https": proxy_str
    }
    return proxies


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
    verbose: bool = False
) -> bool:
    proxies = parse_proxy(proxy)

    print(f"正在解析 m3u8: {url}")
    try:
        parser = M3U8Parser(url, proxies=proxies)
        playlist = parser.parse()
    except Exception as e:
        print(f"解析 m3u8 失败: {e}")
        return False

    print(f"找到 {len(playlist.segments)} 个分片")
    if playlist.duration > 0:
        print(f"总时长: {format_time(playlist.duration)}")
    if playlist.is_encrypted:
        print(f"检测到加密: {playlist.segments[0].encryption.method}")

    if not output_filename:
        base_name = os.path.basename(url).split('?')[0]
        if base_name.endswith('.m3u8'):
            base_name = base_name[:-5]
        if not base_name:
            base_name = "video"
        base_name = sanitize_filename(base_name)
        output_filename = f"{base_name}.{output_format.lower()}"
    else:
        if not output_filename.lower().endswith(f'.{output_format.lower()}'):
            output_filename = f"{output_filename}.{output_format.lower()}"

    output_file = os.path.join(output_dir, output_filename)

    if os.path.exists(output_file) and resume:
        print(f"输出文件已存在: {output_file}")
        return True

    temp_dir = get_temp_dir(output_dir)

    if verbose:
        print(f"临时目录: {temp_dir}")

    print(f"开始下载 (并发数: {concurrency})...")
    downloader = M3U8Downloader(
        playlist=playlist,
        output_dir=temp_dir,
        concurrency=concurrency,
        proxies=proxies,
        resume=resume
    )

    if not downloader.download(show_progress=True):
        print("下载失败")
        if not keep_temp:
            cleanup_temp_dir(temp_dir)
        return False

    print("下载完成，开始合并...")

    merger = FFmpegMerger(ffmpeg_path=ffmpeg_path)
    if not merger.check_ffmpeg():
        print("错误: 未找到 ffmpeg，请确保 ffmpeg 已安装并在 PATH 中")
        print("临时文件保留在:", temp_dir)
        return False

    segment_files = downloader.get_segment_files()

    if not merger.merge(
        segment_files=segment_files,
        output_file=output_file,
        output_format=output_format,
        verbose=verbose
    ):
        print("合并失败")
        if not keep_temp:
            cleanup_temp_dir(temp_dir)
        return False

    print("合并完成!")

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"输出文件: {output_file}")
        print(f"文件大小: {format_size(file_size)}")

    if not keep_temp:
        if verbose:
            print("清理临时文件...")
        cleanup_temp_dir(temp_dir)

    return True


def download_batch(
    url_list_file: str,
    output_dir: str = ".",
    output_format: str = "mp4",
    concurrency: int = 5,
    proxy: Optional[str] = None,
    resume: bool = True,
    keep_temp: bool = False,
    ffmpeg_path: str = "ffmpeg",
    verbose: bool = False
) -> None:
    urls = read_url_list(url_list_file)
    if not urls:
        print("URL列表为空")
        return

    print(f"共 {len(urls)} 个URL待下载")
    print("=" * 50)

    success_count = 0
    fail_count = 0

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] 处理: {url}")
        print("-" * 50)

        try:
            result = download_single(
                url=url,
                output_dir=output_dir,
                output_format=output_format,
                concurrency=concurrency,
                proxy=proxy,
                resume=resume,
                keep_temp=keep_temp,
                ffmpeg_path=ffmpeg_path,
                verbose=verbose
            )
            if result:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"处理出错: {e}")
            fail_count += 1

        print("-" * 50)

    print("\n" + "=" * 50)
    print(f"批量下载完成: 成功 {success_count}, 失败 {fail_count}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M3U8 视频下载与合并工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s https://example.com/video.m3u8
  %(prog)s -u https://example.com/video.m3u8 -o output.mp4
  %(prog)s -f urls.txt -d downloads/ -c 10
  %(prog)s -u https://example.com/video.m3u8 --format mkv --proxy http://127.0.0.1:8080
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
        help="从文件中读取URL列表进行批量下载"
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
        help="下载并发数 (默认: 5)"
    )

    parser.add_argument(
        "--proxy",
        help="代理服务器地址，如 http://127.0.0.1:8080"
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
        "-v", "--verbose",
        action="store_true",
        help="显示详细输出"
    )

    parser.add_argument(
        "--version",
        action="version",
        version="m3u8-downloader 1.0.0"
    )

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    url = args.url or args.url_arg

    if not url and not args.file:
        parser.print_help()
        print("\n错误: 请指定 m3u8 URL 或 URL 列表文件")
        sys.exit(1)

    output_format = args.format.lower()
    resume = not args.no_resume
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if args.file:
        download_batch(
            url_list_file=args.file,
            output_dir=output_dir,
            output_format=output_format,
            concurrency=args.concurrency,
            proxy=args.proxy,
            resume=resume,
            keep_temp=args.keep_temp,
            ffmpeg_path=args.ffmpeg,
            verbose=args.verbose
        )
    else:
        success = download_single(
            url=url,
            output_dir=output_dir,
            output_filename=args.output,
            output_format=output_format,
            concurrency=args.concurrency,
            proxy=args.proxy,
            resume=resume,
            keep_temp=args.keep_temp,
            ffmpeg_path=args.ffmpeg,
            verbose=args.verbose
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
