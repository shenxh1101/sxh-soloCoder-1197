import os
import subprocess
import json as json_module
import shutil
from typing import List, Optional, Dict
import tempfile


class FFmpegMerger:
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def check_ffmpeg(self) -> bool:
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def check_ffprobe(self) -> bool:
        try:
            result = subprocess.run(
                [self.ffprobe_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _generate_concat_file(self, segment_files: List[str], concat_file_path: str) -> str:
        with open(concat_file_path, 'w', encoding='utf-8') as f:
            for seg_file in segment_files:
                abs_path = os.path.abspath(seg_file)
                f.write(f"file '{abs_path}'\n")
        return concat_file_path

    def merge(
        self,
        segment_files: List[str],
        output_file: str,
        output_format: str = "mp4",
        keep_audio: bool = True,
        verbose: bool = False
    ) -> bool:
        if not segment_files:
            print("没有可合并的分片文件")
            return False

        valid_files = [f for f in segment_files if os.path.exists(f) and os.path.getsize(f) > 0]
        if not valid_files:
            print("所有分片文件都不存在或为空")
            return False

        if len(valid_files) < len(segment_files):
            print(f"警告: {len(segment_files) - len(valid_files)} 个分片文件缺失，将跳过")

        output_dir = os.path.dirname(os.path.abspath(output_file))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        concat_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        )
        concat_file_path = concat_file.name
        concat_file.close()

        try:
            self._generate_concat_file(valid_files, concat_file_path)

            cmd = [
                self.ffmpeg_path,
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file_path,
                "-c", "copy"
            ]

            if not keep_audio:
                cmd.extend(["-an"])

            if output_format.lower() == "mp4":
                cmd.extend(["-movflags", "+faststart"])

            cmd.extend(["-y", output_file])

            if verbose:
                print(f"执行命令: {' '.join(cmd)}")

            if verbose:
                result = subprocess.run(cmd)
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True
                )

            if result.returncode == 0:
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    return True

            if not verbose and result.stderr:
                print(f"FFmpeg 错误: {result.stderr[-500:]}")

            return False

        except Exception as e:
            print(f"合并失败: {e}")
            return False
        finally:
            try:
                os.unlink(concat_file_path)
            except:
                pass

    def convert_format(
        self,
        input_file: str,
        output_file: str,
        output_format: str = "mkv",
        verbose: bool = False
    ) -> bool:
        if not os.path.exists(input_file):
            print(f"输入文件不存在: {input_file}")
            return False

        cmd = [
            self.ffmpeg_path,
            "-i", input_file,
            "-c", "copy",
            "-y",
            output_file
        ]

        try:
            if verbose:
                result = subprocess.run(cmd)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)

            return result.returncode == 0 and os.path.exists(output_file)
        except Exception as e:
            print(f"格式转换失败: {e}")
            return False


def probe_file(ffprobe_path: str, file_path: str) -> Optional[Dict]:
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                file_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout:
            return json_module.loads(result.stdout)
    except Exception:
        pass
    return None


def verify_output(
    output_file: str,
    expected_segments: int,
    expected_duration: float,
    ffprobe_path: str = "ffprobe",
    min_file_size: int = 1024,
    actual_segments: Optional[int] = None,
) -> dict:
    from .task_recorder import (
        VerificationResult,
        VERIFY_FAIL_SEGMENT_COUNT,
        VERIFY_FAIL_FILE_SIZE,
        VERIFY_FAIL_DURATION,
        VERIFY_FAIL_FFPROBE,
        VERIFY_FAIL_NO_OUTPUT,
    )

    result = VerificationResult()
    result.expected_segments = expected_segments
    result.expected_duration = float(expected_duration)
    result.expected_min_size = min_file_size
    detail_parts = []

    if not os.path.exists(output_file):
        result.output_exists_ok = False
        result.file_size_ok = False
        result.ffprobe_ok = False
        result.segment_count_ok = False
        result.duration_ok = False
        result.fail_types.append(VERIFY_FAIL_NO_OUTPUT)
        result.details = "输出文件不存在"
        return {"ok": False, "verification": result}

    file_size = os.path.getsize(output_file)
    result.actual_file_size = file_size
    if file_size < min_file_size:
        result.file_size_ok = False
        result.fail_types.append(VERIFY_FAIL_FILE_SIZE)
        detail_parts.append(f"文件过小({file_size}B<{min_file_size}B)")

    if actual_segments is not None:
        result.actual_segments = actual_segments
        if actual_segments < expected_segments:
            result.segment_count_ok = False
            result.fail_types.append(VERIFY_FAIL_SEGMENT_COUNT)
            detail_parts.append(
                f"分片数缺失: 实际{actual_segments}/期望{expected_segments}"
            )
    elif expected_segments > 0:
        result.actual_segments = None

    probe_data = probe_file(ffprobe_path, output_file)

    if probe_data is None:
        result.ffprobe_ok = False
        result.fail_types.append(VERIFY_FAIL_FFPROBE)
        detail_parts.append("ffprobe 无法探测文件")
    else:
        fmt = probe_data.get("format", {})
        streams = probe_data.get("streams", [])
        result.ffprobe_streams = len(streams)

        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)

        if not has_video and not has_audio:
            result.ffprobe_ok = False
            result.fail_types.append(VERIFY_FAIL_FFPROBE)
            detail_parts.append("未检测到视频或音频流")

        if fmt:
            probed_duration_str = fmt.get("duration")
            probed_duration = float(probed_duration_str) if probed_duration_str else 0.0
            result.actual_duration = probed_duration
            if expected_duration > 0 and probed_duration > 0:
                ratio = probed_duration / expected_duration
                if ratio < 0.5 or ratio > 1.5:
                    result.duration_ok = False
                    result.fail_types.append(VERIFY_FAIL_DURATION)
                    detail_parts.append(
                        f"时长偏差大: 探测{probed_duration:.1f}s "
                        f"vs 期望{expected_duration:.1f}s "
                        f"(比例{ratio:.2f})"
                    )
            elif expected_duration > 0 and probed_duration == 0:
                result.duration_ok = False
                result.fail_types.append(VERIFY_FAIL_DURATION)
                detail_parts.append("ffprobe 探测时长为 0")
        else:
            result.ffprobe_ok = False
            result.fail_types.append(VERIFY_FAIL_FFPROBE)
            detail_parts.append("ffprobe 无 format 信息")

    if result.all_ok:
        result.fail_types = []
        result.details = "全部通过"
    else:
        if not detail_parts:
            detail_parts.append("验片未通过")
        result.details = "; ".join(detail_parts)
    return {"ok": result.all_ok, "verification": result}


def cleanup_temp_dir(temp_dir: str) -> bool:
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return True
    except Exception as e:
        print(f"清理临时文件失败: {e}")
        return False
