import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional

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
    url: str
    duration: float = 0.0
    encryption: EncryptionInfo = field(default_factory=EncryptionInfo)


@dataclass
class M3U8Playlist:
    url: str
    segments: List[Segment] = field(default_factory=list)
    is_encrypted: bool = False
    duration: float = 0.0


@dataclass
class MasterPlaylistEntry:
    url: str
    bandwidth: int = 0
    resolution: Optional[str] = None
    codecs: Optional[str] = None


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
                            url=full_url,
                            bandwidth=bandwidth,
                            resolution=resolution,
                            codecs=codecs
                        ))
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
        segment_index = 0

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if line.startswith("#EXT-X-KEY:"):
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
                playlist.duration += current_duration

            elif line and not line.startswith("#"):
                segment_url = resolve_url(self.base_url, line)
                segment = Segment(
                    index=segment_index,
                    url=segment_url,
                    duration=current_duration,
                    encryption=EncryptionInfo(
                        method=current_encryption.method,
                        key_url=current_encryption.key_url,
                        iv=current_encryption.iv
                    )
                )
                playlist.segments.append(segment)
                segment_index += 1

            elif line.startswith("#EXT-X-ENDLIST"):
                break

            i += 1

        return playlist

    def parse(self, select_highest_quality: bool = True) -> M3U8Playlist:
        content = self.fetch()

        if self.is_master_playlist(content):
            entries = self.parse_master_playlist(content)
            if not entries:
                raise ValueError("No streams found in master playlist")

            if select_highest_quality:
                entries.sort(key=lambda x: x.bandwidth, reverse=True)
                selected = entries[0]
            else:
                selected = entries[0]

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
