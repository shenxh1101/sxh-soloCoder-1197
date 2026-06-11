import os
import subprocess
import shutil
from typing import List, Optional
import tempfile


class FFmpegMerger:
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path

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


def cleanup_temp_dir(temp_dir: str) -> bool:
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return True
    except Exception as e:
        print(f"清理临时文件失败: {e}")
        return False
