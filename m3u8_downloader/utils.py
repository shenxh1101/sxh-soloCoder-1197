import os
import re
import tempfile
from urllib.parse import urljoin, urlparse


def get_temp_dir(base_dir=None):
    if base_dir:
        temp_dir = os.path.join(base_dir, "m3u8_temp")
    else:
        temp_dir = tempfile.mkdtemp(prefix="m3u8_download_")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(speed_bytes_per_sec):
    return format_size(speed_bytes_per_sec) + "/s"


def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m{secs:.0f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h{minutes}m"


def resolve_url(base_url, relative_url):
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    return urljoin(base_url, relative_url)


def get_base_url(m3u8_url):
    parsed = urlparse(m3u8_url)
    path = parsed.path
    if "/" in path:
        base_path = path.rsplit("/", 1)[0] + "/"
    else:
        base_path = "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"


def sanitize_filename(filename):
    invalid_chars = r'[<>:"/\\|?*]'
    filename = re.sub(invalid_chars, "_", filename)
    filename = filename.strip()
    if not filename:
        filename = "output"
    return filename[:200]


def get_ts_filename(index):
    return f"segment_{index:05d}.ts"


def get_decrypted_ts_filename(index):
    return f"segment_{index:05d}_dec.ts"
