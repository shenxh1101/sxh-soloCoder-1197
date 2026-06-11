import os
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from typing import Optional
import struct


def parse_iv(iv_str: Optional[str], segment_index: int) -> bytes:
    if not iv_str:
        return get_iv_from_segment_index(segment_index)

    iv_str = iv_str.strip()

    try:
        if iv_str.startswith("0x") or iv_str.startswith("0X"):
            hex_str = iv_str[2:]
            if len(hex_str) < 32:
                hex_str = hex_str.zfill(32)
            iv_bytes = bytes.fromhex(hex_str)
        else:
            try:
                int_val = int(iv_str)
                return get_iv_from_segment_index(int_val)
            except ValueError:
                if len(iv_str) % 2 != 0:
                    iv_str = "0" + iv_str
                iv_bytes = bytes.fromhex(iv_str)

        if len(iv_bytes) < 16:
            iv_bytes = b"\x00" * (16 - len(iv_bytes)) + iv_bytes
        elif len(iv_bytes) > 16:
            iv_bytes = iv_bytes[-16:]

        return iv_bytes
    except Exception:
        return get_iv_from_segment_index(segment_index)


def get_iv_from_segment_index(index: int) -> bytes:
    packed = struct.pack(">Q", index)
    return b"\x00" * 8 + packed


def robust_pkcs7_unpad(data: bytes) -> bytes:
    if len(data) == 0:
        return data
    if len(data) % AES.block_size != 0:
        return data

    try:
        return unpad(data, AES.block_size, style='pkcs7')
    except ValueError:
        last_byte = data[-1]
        if 1 <= last_byte <= AES.block_size:
            if data[-last_byte:] == bytes([last_byte]) * last_byte:
                return data[:-last_byte]
        return data


class SegmentDecryptor:
    def __init__(self, key: bytes):
        if len(key) != 16:
            raise ValueError(f"AES-128 key must be 16 bytes, got {len(key)} bytes")
        self.key = key

    def decrypt_segment_bytes(self, encrypted_data: bytes, segment_index: int,
                               explicit_iv_str: Optional[str] = None) -> bytes:
        iv = parse_iv(explicit_iv_str, segment_index)
        cipher = AES.new(self.key, AES.MODE_CBC, iv=iv)
        decrypted = cipher.decrypt(encrypted_data)
        return robust_pkcs7_unpad(decrypted)

    def decrypt_segment_file(self, input_path: str, output_path: str,
                              segment_index: int,
                              explicit_iv_str: Optional[str] = None) -> bool:
        try:
            if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
                return False

            with open(input_path, 'rb') as f:
                encrypted_data = f.read()

            decrypted_data = self.decrypt_segment_bytes(
                encrypted_data, segment_index, explicit_iv_str
            )

            if len(decrypted_data) == 0:
                return False

            with open(output_path, 'wb') as f:
                f.write(decrypted_data)

            return True
        except Exception:
            return False


class KeyManager:
    def __init__(self):
        self._keys = {}

    def get_key(self, key_url: str, fetcher_func) -> Optional[bytes]:
        if key_url in self._keys:
            return self._keys[key_url]
        try:
            key = fetcher_func(key_url)
            if key and len(key) >= 16:
                if len(key) > 16:
                    key = key[:16]
                self._keys[key_url] = key
                return key
            return None
        except Exception:
            return None

    def get_decryptor(self, key_url: str, fetcher_func) -> Optional[SegmentDecryptor]:
        key = self.get_key(key_url, fetcher_func)
        if key:
            return SegmentDecryptor(key)
        return None

    def clear(self):
        self._keys.clear()
