import os
import re
import tempfile
from http.cookiejar import MozillaCookieJar
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


def load_cookies_from_file(cookie_file: str) -> dict:
    cookies = {}
    if not cookie_file or not os.path.exists(cookie_file):
        return cookies
    try:
        jar = MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            cookies[cookie.name] = cookie.value
    except Exception:
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        cookies[parts[5]] = parts[6]
                    elif '=' in line:
                        name, _, value = line.partition('=')
                        cookies[name.strip()] = value.strip()
        except Exception:
            pass
    return cookies


def cookies_to_header(cookies: dict) -> str:
    if not cookies:
        return ""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def parse_header_string(header_str: str) -> dict:
    headers = {}
    if not header_str:
        return headers
    for part in header_str.split(','):
        part = part.strip()
        if ':' not in part:
            continue
        name, _, value = part.partition(':')
        name = name.strip()
        value = value.strip()
        if name and value:
            headers[name] = value
    return headers


def build_headers(custom_headers: dict = None, cookies: dict = None,
                   user_agent: str = None) -> dict:
    headers = {
        "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if custom_headers:
        headers.update(custom_headers)
    if cookies:
        cookie_header = cookies_to_header(cookies)
        if cookie_header:
            headers["Cookie"] = cookie_header
    return headers


def classify_http_error(status_code: int, url: str = "") -> str:
    if status_code in (401, 403):
        return "auth"
    if status_code in (404, 410):
        return "not_found"
    if status_code == 429:
        return "rate_limit"
    if 400 <= status_code < 500:
        return "client_error"
    if 500 <= status_code < 600:
        return "server_error"
    return "unknown"
