import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .utils import resolve_url, get_base_url


@dataclass
class EncryptionInfo:
    method: str = "NONE"
    key_url: Optional[str] = None
    iv: Optional[str] = None
    key: Optional[bytes] = None


@dataclass
class Segment:
    index: int
    true_index: int
    url: str
    duration: float = 0.0
    start_time: float = 0.0
    encryption: EncryptionInfo = field(default_factory=EncryptionInfo)
    byte_size: Optional[int] = None


@dataclass
class M3U8Playlist:
    url: str
    segments: List[Segment] = field(default_factory=list)
    is_encrypted: bool = False
    duration: float = 0.0
    media_sequence: int = 0

    def slice_by_time(self, start_sec: Optional[float] = None,
                      end_sec: Optional[float] = None) -> "M3U8Playlist":
        if start_sec is None:
            start_sec = 0.0
        if end_sec is None:
            end_sec = self.duration

        start_sec = max(0.0, float(start_sec))
        end_sec = min(self.duration, float(end_sec))

        if start_sec >= end_sec:
            raise ValueError(
                f"无效时间范围: start={start_sec}s, end={end_sec}s "
                f"(总时长={self.duration}s)"
            )

        new_segments: List[Segment] = []
        new_duration = 0.0
        new_local_idx = 0
        new_is_encrypted = False

        for seg in self.segments:
            seg_end = seg.start_time + seg.duration
            overlaps = seg_end > start_sec and seg.start_time < end_sec
            if overlaps:
                new_seg = Segment(
                    index=new_local_idx,
                    true_index=seg.true_index,
                    url=seg.url,
                    duration=seg.duration,
                    start_time=new_duration,
                    encryption=EncryptionInfo(
                        method=seg.encryption.method,
                        key_url=seg.encryption.key_url,
                        iv=seg.encryption.iv
                    ),
                    byte_size=seg.byte_size
                )
                if new_seg.encryption.method != "NONE":
                    new_is_encrypted = True
                new_segments.append(new_seg)
                new_duration += seg.duration
                new_local_idx += 1

        if not new_segments:
            raise ValueError(
                f"时间范围 [{start_sec}s, {end_sec}s] 内没有分片"
            )

        new_playlist = M3U8Playlist(
            url=self.url,
            segments=new_segments,
            is_encrypted=new_is_encrypted,
            duration=new_duration,
            media_sequence=self.media_sequence
        )
        return new_playlist

    def describe_time_slice(self, start_sec: Optional[float],
                            end_sec: Optional[float]) -> str:
        if start_sec is None:
            start_sec = 0.0
        if end_sec is None:
            end_sec = self.duration
        from .utils import format_time
        return (
            f"截取范围: {format_time(start_sec)} - {format_time(end_sec)} "
            f"(总时长 {format_time(self.duration)})"
        )


@dataclass
class MasterPlaylistEntry:
    index: int
    url: str
    bandwidth: int = 0
    resolution: Optional[str] = None
    codecs: Optional[str] = None

    @property
    def quality_label(self) -> str:
        parts = []
        if self.resolution:
            parts.append(self.resolution)
        if self.bandwidth > 0:
            mbps = self.bandwidth / 1_000_000
            parts.append(f"{mbps:.2f}Mbps")
        if not parts:
            parts.append(f"Stream {self.index}")
        return " | ".join(parts)


def format_quality_table(entries: List[MasterPlaylistEntry]) -> str:
    lines = ["可用清晰度列表："]
    lines.append("-" * 70)
    lines.append(f"{'序号':<6}{'分辨率':<14}{'码率':<14}{'编码':<14}")
    lines.append("-" * 70)
    for e in entries:
        bw_str = f"{e.bandwidth / 1_000_000:.2f}Mbps" if e.bandwidth else "-"
        res_str = e.resolution or "-"
        codec_str = e.codecs or "-"
        lines.append(f"{e.index:<6}{res_str:<14}{bw_str:<14}{codec_str:<14}")
    lines.append("-" * 70)
    return "\n".join(lines)


def parse_time_str(s: str) -> float:
    s = s.strip()
    if not s:
        raise ValueError("空的时间字符串")

    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            m, sec = parts
            return int(m) * 60 + float(sec)
        elif len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + float(sec)
        else:
            raise ValueError(f"无法解析时间格式: {s}")
    else:
        return float(s)


class M3U8Parser:
    def __init__(self, url: str, proxies: Optional[dict] = None, headers: Optional[dict] = None):
        self.url = url
        self.proxies = proxies
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.base_url = get_base_url(url)
        self._content = None

    def fetch(self) -> str:
        if self._content is not None:
            return self._content
        response = requests.get(
            self.url,
            proxies=self.proxies,
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()
        self._content = response.text
        return self._content

    def is_master_playlist(self, content: Optional[str] = None) -> bool:
        if content is None:
            content = self.fetch()
        return "#EXT-X-STREAM-INF" in content

    def parse_master_playlist(self, content: Optional[str] = None) -> List[MasterPlaylistEntry]:
        if content is None:
            content = self.fetch()
        entries = []
        idx = 0
        lines = content.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = self._parse_attributes(line.split(":", 1)[1])
                bandwidth = int(attrs.get("BANDWIDTH", "0"))
                resolution = attrs.get("RESOLUTION")
                codecs = attrs.get("CODECS")
                if i + 1 < len(lines):
                    url = lines[i + 1].strip()
                    if url and not url.startswith("#"):
                        full_url = resolve_url(self.base_url, url)
                        entries.append(MasterPlaylistEntry(
                            index=idx,
                            url=full_url,
                            bandwidth=bandwidth,
                            resolution=resolution,
                            codecs=codecs
                        ))
                        idx += 1
                        i += 1
            i += 1
        return entries

    def parse_media_playlist(self, url: Optional[str] = None,
                             content: Optional[str] = None) -> M3U8Playlist:
        if url:
            parser = M3U8Parser(url, proxies=self.proxies, headers=self.headers)
            return parser.parse_media_playlist(content=content)

        if content is None:
            content = self.fetch()

        playlist = M3U8Playlist(url=self.url)
        lines = content.strip().split("\n")

        if not lines or lines[0] != "#EXTM3U":
            raise ValueError("Invalid M3U8 file: missing #EXTM3U header")

        current_encryption = EncryptionInfo()
        current_duration = 0.0
        current_time = 0.0
        segment_index = 0

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    playlist.media_sequence = int(line.split(":", 1)[1].strip())
                except ValueError:
                    playlist.media_sequence = 0

            elif line.startswith("#EXT-X-KEY:"):
                attrs = self._parse_attributes(line.split(":", 1)[1])
                method = attrs.get("METHOD", "NONE")
                key_uri = attrs.get("URI")
                iv = attrs.get("IV")

                if method == "NONE":
                    current_encryption = EncryptionInfo(method="NONE")
                else:
                    key_url = resolve_url(self.base_url, key_uri.strip('"')) if key_uri else None
                    current_encryption = EncryptionInfo(
                        method=method,
                        key_url=key_url,
                        iv=iv
                    )
                    playlist.is_encrypted = True

            elif line.startswith("#EXTINF:"):
                duration_str = line.split(":", 1)[1].split(",")[0]
                try:
                    current_duration = float(duration_str)
                except ValueError:
                    current_duration = 0.0

            elif line and not line.startswith("#"):
                segment_url = resolve_url(self.base_url, line)
                true_index = playlist.media_sequence + segment_index
                segment = Segment(
                    index=segment_index,
                    true_index=true_index,
                    url=segment_url,
                    duration=current_duration,
                    start_time=current_time,
                    encryption=EncryptionInfo(
                        method=current_encryption.method,
                        key_url=current_encryption.key_url,
                        iv=current_encryption.iv
                    )
                )
                playlist.segments.append(segment)
                playlist.duration += current_duration
                current_time += current_duration
                segment_index += 1

            elif line.startswith("#EXT-X-ENDLIST"):
                break

            i += 1

        return playlist

    def get_master_entries(self) -> List[MasterPlaylistEntry]:
        content = self.fetch()
        if self.is_master_playlist(content):
            return self.parse_master_playlist(content)
        return []

    def select_quality(self,
                       entries: List[MasterPlaylistEntry],
                       select_index: Optional[int] = None,
                       target_bandwidth: Optional[int] = None,
                       target_resolution: Optional[str] = None,
                       select_highest: bool = True) -> MasterPlaylistEntry:
        if not entries:
            raise ValueError("No streams available")

        if select_index is not None:
            if 0 <= select_index < len(entries):
                return entries[select_index]
            raise ValueError(f"清晰度序号 {select_index} 超出范围 (0-{len(entries)-1})")

        if target_resolution:
            for e in entries:
                if e.resolution and e.resolution.lower() == target_resolution.lower():
                    return e
            raise ValueError(f"未找到分辨率为 {target_resolution} 的流")

        if target_bandwidth is not None:
            best = None
            best_diff = float('inf')
            for e in entries:
                diff = abs(e.bandwidth - target_bandwidth)
                if diff < best_diff:
                    best_diff = diff
                    best = e
            if best:
                return best

        sorted_entries = sorted(entries, key=lambda x: x.bandwidth, reverse=True)
        if select_highest:
            return sorted_entries[0]
        else:
            return sorted_entries[-1] if len(sorted_entries) > 1 else sorted_entries[0]

    def parse(self,
              select_highest_quality: bool = True,
              quality_index: Optional[int] = None,
              target_bandwidth: Optional[int] = None,
              target_resolution: Optional[str] = None) -> M3U8Playlist:
        content = self.fetch()

        if self.is_master_playlist(content):
            entries = self.parse_master_playlist(content)
            if not entries:
                raise ValueError("No streams found in master playlist")

            selected = self.select_quality(
                entries,
                select_index=quality_index,
                target_bandwidth=target_bandwidth,
                target_resolution=target_resolution,
                select_highest=select_highest_quality
            )

            parser = M3U8Parser(selected.url, proxies=self.proxies, headers=self.headers)
            return parser.parse_media_playlist()
        else:
            return self.parse_media_playlist(content=content)

    def fetch_key(self, key_url: str) -> bytes:
        response = requests.get(
            key_url,
            proxies=self.proxies,
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _parse_attributes(attr_str: str) -> dict:
        attrs = {}
        pattern = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)')
        for match in pattern.finditer(attr_str):
            key = match.group(1)
            value = match.group(2)
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            attrs[key] = value
        return attrs
