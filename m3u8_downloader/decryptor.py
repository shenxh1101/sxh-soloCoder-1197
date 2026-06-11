import os
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from typing import Optional


def pkcs7_unpad(data: bytes) -> bytes:
    return unpad(data, AES.block_size, style='pkcs7')


class AES128Decryptor:
    def __init__(self, key: bytes, iv: Optional[bytes] = None):
        self.key = key
        self.iv = iv if iv else bytes(AES.block_size)
        if len(self.key) != 16:
            raise ValueError(f"AES-128 key must be 16 bytes, got {len(self.key)} bytes")

    def decrypt(self, encrypted_data: bytes) -> bytes:
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.iv)
        decrypted = cipher.decrypt(encrypted_data)
        try:
            return pkcs7_unpad(decrypted)
        except ValueError:
            return decrypted

    def decrypt_segment(self, input_path: str, output_path: str) -> bool:
        try:
            with open(input_path, 'rb') as f:
                encrypted_data = f.read()

            decrypted_data = self.decrypt(encrypted_data)

            with open(output_path, 'wb') as f:
                f.write(decrypted_data)

            return True
        except Exception:
            return False


def get_iv_from_segment_index(index: int) -> bytes:
    iv = bytearray(16)
    for i in range(15, -1, -1):
        iv[i] = index & 0xFF
        index >>= 8
    return bytes(iv)
