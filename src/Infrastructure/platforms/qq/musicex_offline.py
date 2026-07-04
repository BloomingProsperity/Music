from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import hashlib
import json
import logging
import math
import os
import pathlib
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.Infrastructure.transcoder import fast_detect_container


MUSICEX_MAGIC = b"musicex\0"
QTAG_MAGIC = b"QTag"
STAG_MAGIC = b"STag"
ENCV2_PREFIX = b"QQMusic EncV2,Key:"
ENCV2_KEY1 = bytes((0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40, 0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28))
ENCV2_KEY2 = bytes((0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28, 0x23, 0x40, 0x21, 0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59))

logger = logging.getLogger("qkkdecrypt.infrastructure.platforms.qq.musicex_offline")
_QMC2_NATIVE_LIB = None
_QMC2_NATIVE_ATTEMPTED = False


@dataclass(frozen=True, slots=True)
class QQEncryptedTail:
    format: str
    song_mid: str
    filename: str
    audio_size: int
    ekey: str | None = None


def _decode_utf16_field(data: bytes) -> str:
    return data.decode("utf-16le", errors="ignore").rstrip("\0")


def _parse_musicex_tail(file_obj, file_size: int) -> QQEncryptedTail | None:
    if file_size < 192:
        return None
    file_obj.seek(-16, os.SEEK_END)
    tail_size = int.from_bytes(file_obj.read(4), "little")
    if tail_size <= 16 or tail_size > 4096 or tail_size > file_size:
        return None

    file_obj.seek(-tail_size, os.SEEK_END)
    tail = file_obj.read(tail_size)
    if not tail.endswith(MUSICEX_MAGIC):
        return None

    candidates = [
        (12, 72, 72, 168, file_size - tail_size),
        (28, 88, 88, 184, file_size - tail_size - 16),
    ]
    for song_start, song_end, file_start, file_end, audio_size in candidates:
        song_mid = _decode_utf16_field(tail[song_start:song_end]).strip()
        filename = _decode_utf16_field(tail[file_start:file_end]).strip()
        if song_mid.startswith("00") and "." in filename and audio_size > 0:
            return QQEncryptedTail(
                format="musicex",
                song_mid=song_mid,
                filename=filename,
                audio_size=audio_size,
            )
    return None


def _parse_legacy_tag(file_obj, file_size: int, tag_type: bytes) -> QQEncryptedTail | None:
    file_obj.seek(-8, os.SEEK_END)
    ekey_len = int.from_bytes(file_obj.read(4), "little")
    if ekey_len <= 0 or ekey_len > 4096:
        return None
    audio_size = file_size - 8 - ekey_len
    if audio_size <= 0:
        return None

    file_obj.seek(audio_size)
    ekey_data = file_obj.read(ekey_len)
    if tag_type == QTAG_MAGIC:
        parts = ekey_data.split(b",", 1)
        song_mid = parts[0].decode("utf-8", errors="ignore") if parts else ""
        ekey = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else ""
    else:
        song_mid = ""
        ekey = ekey_data.decode("utf-8", errors="ignore")
    if not ekey:
        return None
    return QQEncryptedTail(
        format="legacy",
        song_mid=song_mid,
        filename="",
        audio_size=audio_size,
        ekey=ekey,
    )


def parse_encrypted_tail(input_path: pathlib.Path) -> QQEncryptedTail | None:
    file_size = input_path.stat().st_size
    if file_size < 8:
        return None
    with input_path.open("rb") as file_obj:
        file_obj.seek(-8, os.SEEK_END)
        tail8 = file_obj.read(8)
        if tail8 == MUSICEX_MAGIC:
            return _parse_musicex_tail(file_obj, file_size)

        file_obj.seek(-4, os.SEEK_END)
        tail4 = file_obj.read(4)
        if tail4 in {QTAG_MAGIC, STAG_MAGIC}:
            return _parse_legacy_tag(file_obj, file_size, tail4)
    return None


def _simple_make_key(salt: int, length: int) -> bytes:
    return bytes(int(abs(math.tan(float(salt) + float(index) * 0.1)) * 100.0) & 0xFF for index in range(length))


def _tea_decrypt_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">II", block)
    k0, k1, k2, k3 = struct.unpack(">4I", key)
    delta = 0x9E3779B9
    total = (delta * 16) & 0xFFFFFFFF
    for _ in range(16):
        v1 = (v1 - (((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3))) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1))) & 0xFFFFFFFF
        total = (total - delta) & 0xFFFFFFFF
    return struct.pack(">II", v0, v1)


def _decrypt_tencent_tea(data: bytes, key: bytes) -> bytes | None:
    if len(data) % 8 != 0 or len(data) < 16:
        return None
    dest_buf = bytearray(_tea_decrypt_block(data[:8], key))
    pad_len = dest_buf[0] & 0x07
    out_len = len(data) - 1 - pad_len - 2 - 7
    if out_len <= 0:
        return None

    out = bytearray(out_len)
    iv_prev = bytes(8)
    iv_cur = data[:8]
    in_pos = 8
    dest_index = 1 + pad_len

    def crypt_block() -> tuple[bytearray, bytes, bytes, int, int]:
        nonlocal iv_prev, iv_cur, in_pos
        previous = iv_cur
        current = data[in_pos:in_pos + 8]
        mixed = bytearray(dest_buf[index] ^ data[in_pos + index] for index in range(8))
        decoded = bytearray(_tea_decrypt_block(bytes(mixed), key))
        in_pos += 8
        return decoded, previous, current, in_pos, 0

    skipped_zero_bytes = 0
    while skipped_zero_bytes < 2:
        if dest_index < 8:
            dest_index += 1
            skipped_zero_bytes += 1
        else:
            dest_buf, iv_prev, iv_cur, in_pos, dest_index = crypt_block()

    out_pos = 0
    while out_pos < out_len:
        if dest_index < 8:
            out[out_pos] = dest_buf[dest_index] ^ iv_prev[dest_index]
            dest_index += 1
            out_pos += 1
        else:
            dest_buf, iv_prev, iv_cur, in_pos, dest_index = crypt_block()
    return bytes(out)


def _decrypt_encv2(raw_key: bytes) -> bytes | None:
    payload = raw_key[len(ENCV2_PREFIX):]
    first = _decrypt_tencent_tea(payload, ENCV2_KEY1)
    if first is None:
        return None
    second = _decrypt_tencent_tea(first, ENCV2_KEY2)
    if second is None:
        return None
    try:
        return base64.b64decode(second)
    except Exception:
        return None


def derive_qmc2_key(raw_key: bytes) -> bytes | None:
    if raw_key.startswith(ENCV2_PREFIX):
        raw_key = _decrypt_encv2(raw_key) or b""
        if len(raw_key) < 16:
            return None
    if len(raw_key) < 8:
        return None
    simple_key = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for index in range(8):
        tea_key[index * 2] = simple_key[index]
        tea_key[index * 2 + 1] = raw_key[index]
    decrypted = _decrypt_tencent_tea(raw_key[8:], bytes(tea_key))
    if decrypted is None:
        return None
    return raw_key[:8] + decrypted


class _MapCipher:
    def __init__(self, key: bytes) -> None:
        self.key = key
        self.key_len = len(key)
        self._direct_masks: bytes | None = None
        self._mod_masks: bytes | None = None

    @staticmethod
    def _rotate(value: int, bits: int) -> int:
        shift = (bits + 4) % 8
        return ((value << shift) | (value >> shift)) & 0xFF

    def _mask(self, offset: int) -> int:
        if self.key_len == 0:
            return 0
        if offset > 0x7FFF:
            offset %= 0x7FFF
        index = (offset * offset + 71214) % self.key_len
        return self._rotate(self.key[index], index & 0x07)

    def _mask_bytes(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        if offset < 0:
            return bytes(self._mask(offset + index) for index in range(length))

        output = bytearray()
        remaining = length
        if offset <= 0x7FFF:
            if self._direct_masks is None:
                self._direct_masks = bytes(self._mask(index) for index in range(0x8000))
            direct_len = min(remaining, 0x8000 - offset)
            output.extend(self._direct_masks[offset:offset + direct_len])
            offset += direct_len
            remaining -= direct_len

        if remaining > 0:
            if self._mod_masks is None:
                self._mod_masks = bytes(self._mask(index) for index in range(0x7FFF))
            cycle = self._mod_masks
            start = offset % 0x7FFF
            while remaining > 0:
                block_len = min(remaining, len(cycle) - start)
                output.extend(cycle[start:start + block_len])
                remaining -= block_len
                start = 0
        return bytes(output)

    def decrypt(self, buffer: bytearray, offset: int) -> None:
        mask = self._mask_bytes(offset, len(buffer))
        _xor_bytes_inplace(buffer, mask)


def _xor_bytes_inplace(buffer: bytearray, mask: bytes) -> None:
    if not buffer:
        return
    value = int.from_bytes(buffer, "little") ^ int.from_bytes(mask, "little")
    buffer[:] = value.to_bytes(len(buffer), "little")


class _RC4Cipher:
    SEGMENT_SIZE = 5120
    FIRST_SEGMENT_SIZE = 128

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.key_len = len(key)
        self.box = [index & 0xFF for index in range(self.key_len)]
        cursor = 0
        for index in range(self.key_len):
            cursor = (cursor + self.box[index] + key[index]) % self.key_len
            self.box[index], self.box[cursor] = self.box[cursor], self.box[index]
        self.hash = self._compute_hash()

    def _compute_hash(self) -> int:
        result = 1
        for value in self.key:
            if value == 0:
                continue
            next_result = (result * value) & 0xFFFFFFFF
            if next_result == 0 or next_result <= result:
                break
            result = next_result
        return result

    def _segment_skip(self, segment_id: int) -> int:
        seed = int(self.key[segment_id % self.key_len])
        if seed == 0:
            return 0
        index = int(float(self.hash) / float((segment_id + 1) * seed) * 100.0)
        return index % self.key_len

    def decrypt(self, buffer: bytearray, offset: int) -> None:
        remaining = len(buffer)
        processed = 0

        if offset < self.FIRST_SEGMENT_SIZE:
            block_size = min(remaining, self.FIRST_SEGMENT_SIZE - offset)
            self._decrypt_first_segment(buffer, 0, block_size, offset)
            processed += block_size
            offset += block_size
            remaining -= block_size
            if remaining == 0:
                return

        if offset % self.SEGMENT_SIZE != 0:
            block_size = min(remaining, self.SEGMENT_SIZE - offset % self.SEGMENT_SIZE)
            self._decrypt_segment(buffer, processed, block_size, offset)
            processed += block_size
            offset += block_size
            remaining -= block_size
            if remaining == 0:
                return

        while remaining > self.SEGMENT_SIZE:
            self._decrypt_segment(buffer, processed, self.SEGMENT_SIZE, offset)
            processed += self.SEGMENT_SIZE
            offset += self.SEGMENT_SIZE
            remaining -= self.SEGMENT_SIZE

        if remaining > 0:
            self._decrypt_segment(buffer, processed, remaining, offset)

    def _decrypt_first_segment(self, buffer: bytearray, buffer_offset: int, length: int, stream_offset: int) -> None:
        for index in range(length):
            skip = self._segment_skip(stream_offset + index)
            buffer[buffer_offset + index] ^= self.key[skip]

    def _decrypt_segment(self, buffer: bytearray, buffer_offset: int, length: int, stream_offset: int) -> None:
        box = self.box.copy()
        cursor_a = 0
        cursor_b = 0
        skip_len = (stream_offset % self.SEGMENT_SIZE) + self._segment_skip(stream_offset // self.SEGMENT_SIZE)
        for index in range(-skip_len, length):
            cursor_a = (cursor_a + 1) % self.key_len
            cursor_b = (box[cursor_a] + cursor_b) % self.key_len
            box[cursor_a], box[cursor_b] = box[cursor_b], box[cursor_a]
            if index >= 0:
                mask_index = (box[cursor_a] + box[cursor_b]) % self.key_len
                buffer[buffer_offset + index] ^= box[mask_index]


def _make_cipher(key: bytes):
    if len(key) > 300:
        return _RC4Cipher(key)
    return _MapCipher(key)


def _qmc2_native_name() -> str:
    if sys.platform.startswith("win"):
        return "qmc2_fast.dll"
    if sys.platform == "darwin":
        return "libqmc2_fast.dylib"
    return "libqmc2_fast.so"


def _qmc2_native_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "native"


def _qmc2_native_enabled() -> bool:
    value = os.environ.get("QKK_QQ_QMC2_NATIVE", "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    return bool(getattr(sys, "frozen", False))


def _qmc2_native_signature_matches(native_path: pathlib.Path) -> bool:
    signature_path = native_path.with_name(native_path.name + ".sha256")
    try:
        expected = signature_path.read_text(encoding="utf-8").strip().split()[0].lower()
    except (IndexError, OSError):
        return False
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        return False
    digest = hashlib.sha256(native_path.read_bytes()).hexdigest()
    return digest == expected


def _load_qmc2_native():
    global _QMC2_NATIVE_ATTEMPTED, _QMC2_NATIVE_LIB
    if _QMC2_NATIVE_LIB is not None:
        return _QMC2_NATIVE_LIB
    if _QMC2_NATIVE_ATTEMPTED:
        return None
    _QMC2_NATIVE_ATTEMPTED = True
    if not _qmc2_native_enabled():
        return None

    native_path = _qmc2_native_dir() / _qmc2_native_name()
    if not native_path.exists():
        return None
    if not _qmc2_native_signature_matches(native_path):
        logger.info("QQ qmc2 native decryptor skipped: missing or mismatched sha256 signature")
        return None
    try:
        library = ctypes.CDLL(str(native_path))
        library.qmc2_decrypt.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_longlong,
        ]
        library.qmc2_decrypt.restype = ctypes.c_int
    except OSError as exc:
        logger.info("failed to load qmc2 native decryptor: %s", exc)
        return None
    _QMC2_NATIVE_LIB = library
    return library


def decrypt_qmc2_buffer_fast(key: bytes, buffer: bytearray, offset: int) -> bool:
    library = _load_qmc2_native()
    if library is None:
        return False
    if not key or not buffer:
        return False
    native_buffer = (ctypes.c_char * len(buffer)).from_buffer(buffer)
    try:
        return int(library.qmc2_decrypt(key, len(key), native_buffer, len(buffer), offset)) == 1
    except (AttributeError, OSError, ValueError):
        return False


def _default_ekey_cache_dir() -> pathlib.Path:
    base = os.environ.get("APPDATA")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
    return root / "QQKWKG-TriMusicDecrypt" / "qq_ekeys"


def _filename_basename(filename: str) -> str:
    return str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _settings_bool(settings: dict, key: str, default: bool) -> bool:
    value = settings.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def configure_windows_process_api(kernel32, psapi, memory_info_type=None) -> None:
    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    psapi.EnumProcesses.restype = ctypes.wintypes.BOOL
    psapi.GetModuleBaseNameA.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.wintypes.DWORD,
    ]
    psapi.GetModuleBaseNameA.restype = ctypes.wintypes.DWORD
    kernel32.ReadProcessMemory.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = ctypes.wintypes.BOOL
    if memory_info_type is not None:
        kernel32.VirtualQueryEx.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(memory_info_type),
            ctypes.c_size_t,
        ]
        kernel32.VirtualQueryEx.restype = ctypes.c_size_t


class QQMusicCookieProvider:
    COOKIE_MARKER = b"qqmusic_key="

    def __init__(self) -> None:
        self._cached: dict[str, str] | None = None

    def get_cookie(self) -> dict[str, str] | None:
        if self._cached is not None:
            return self._cached
        result = self._extract_windows_cookie()
        if result is not None:
            self._cached = result
        return result

    @classmethod
    def _parse_cookie(cls, raw: str) -> dict[str, str] | None:
        cookie = raw.strip()
        for separator in ("\n", "\r", "\0"):
            if separator in cookie:
                cookie = cookie.split(separator, 1)[0]
        marker = "qqmusic_uin="
        marker_index = cookie.find(marker)
        if marker_index < 0:
            return None
        start = marker_index + len(marker)
        end = start
        while end < len(cookie) and cookie[end].isdigit():
            end += 1
        uin = cookie[start:end]
        if not uin:
            return None
        return {"cookie": cookie, "uin": uin}

    @classmethod
    def _extract_windows_cookie(cls) -> dict[str, str] | None:
        try:
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
        except AttributeError:
            return None
        configure_windows_process_api(kernel32, psapi)

        process_vm_read = 0x0010
        process_query_information = 0x0400
        enum_buffer = (ctypes.wintypes.DWORD * 4096)()
        cb_needed = ctypes.wintypes.DWORD()
        if not psapi.EnumProcesses(enum_buffer, ctypes.sizeof(enum_buffer), ctypes.byref(cb_needed)):
            return None

        qqmusic_pid = None
        max_path = 260
        process_count = cb_needed.value // ctypes.sizeof(ctypes.wintypes.DWORD)
        for index in range(process_count):
            pid = enum_buffer[index]
            if pid == 0:
                continue
            handle = kernel32.OpenProcess(process_query_information | process_vm_read, False, pid)
            if not handle:
                continue
            try:
                name_buffer = (ctypes.c_char * max_path)()
                if psapi.GetModuleBaseNameA(handle, None, name_buffer, max_path) and name_buffer.value == b"QQMusic.exe":
                    qqmusic_pid = pid
                    break
            finally:
                kernel32.CloseHandle(handle)
        if qqmusic_pid is None:
            return None

        process_handle = kernel32.OpenProcess(process_vm_read | process_query_information, False, qqmusic_pid)
        if not process_handle:
            return None

        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", ctypes.wintypes.DWORD),
                ("Protect", ctypes.wintypes.DWORD),
                ("Type", ctypes.wintypes.DWORD),
            ]
        configure_windows_process_api(kernel32, psapi, MEMORY_BASIC_INFORMATION)

        mem_commit = 0x1000
        page_readable = {0x02, 0x04, 0x06, 0x20, 0x40, 0x60, 0x80}
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        bytes_read = ctypes.c_size_t()
        try:
            while address < 0x7FFFFFFFFFFFFFFF:
                if kernel32.VirtualQueryEx(process_handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                    break
                region_size = int(mbi.RegionSize or 0)
                base_address = int(mbi.BaseAddress or 0)
                if region_size == 0:
                    break
                if mbi.State == mem_commit and mbi.Protect in page_readable and 0x1000 <= region_size <= 50 * 1024 * 1024:
                    buffer = (ctypes.c_char * region_size)()
                    if kernel32.ReadProcessMemory(process_handle, ctypes.c_void_p(base_address), buffer, region_size, ctypes.byref(bytes_read)):
                        data = bytes(buffer[: bytes_read.value])
                        position = data.find(cls.COOKIE_MARKER)
                        if position >= 0:
                            raw = data[position: position + 512].split(b"\0", 1)[0].decode("utf-8", errors="ignore")
                            parsed = cls._parse_cookie(raw)
                            if parsed is not None:
                                return parsed
                next_address = base_address + region_size
                if next_address <= address:
                    break
                address = next_address
        finally:
            kernel32.CloseHandle(process_handle)
        return None


class QQEKeyClient:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, song_mid: str, filename: str, cookie: str, uin: str) -> str | None:
        request_filename = _filename_basename(filename)
        if "." not in request_filename:
            extension = ".mflac" if request_filename.startswith("F0") else ".mgg"
            request_filename = f"{request_filename}{extension}"
        request_data = {
            "comm": {
                "cv": 4747474,
                "ct": 24,
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "notice": 0,
                "platform": "yqq.json",
                "needNewCode": 1,
                "uin": int(uin),
                "g_tk_new_20200303": 5381,
                "g_tk": 5381,
            },
            "req_1": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "filename": [request_filename],
                    "guid": "10000",
                    "songmid": [song_mid],
                    "songtype": [0],
                    "uin": uin,
                    "loginflag": 1,
                    "platform": "20",
                },
            },
        }
        request = urllib.request.Request(
            "https://u.y.qq.com/cgi-bin/musicu.fcg",
            data=json.dumps(request_data).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Cookie", cookie)
        request.add_header("User-Agent", "QQMusic/21")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read())
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            logger.info("failed to fetch QQ music ekey for %s/%s: %s", song_mid, request_filename, exc)
            return None
        midurlinfo = result.get("req_1", {}).get("data", {}).get("midurlinfo", [])
        if not midurlinfo:
            return None
        ekey = str(midurlinfo[0].get("ekey") or "")
        return ekey or None


class QQOfflineMusicExDecryptor:
    def __init__(
        self,
        *,
        cookie_provider: QQMusicCookieProvider | None = None,
        ekey_client: QQEKeyClient | None = None,
        cache_dir: pathlib.Path | None = None,
    ) -> None:
        self.cookie_provider = cookie_provider or QQMusicCookieProvider()
        self.ekey_client = ekey_client or QQEKeyClient()
        self.cache_dir = cache_dir or _default_ekey_cache_dir()

    def decrypt_to_file(
        self,
        input_path: pathlib.Path,
        output_path: pathlib.Path,
        settings: dict,
        *,
        log_dir: pathlib.Path,
    ) -> dict | None:
        meta = parse_encrypted_tail(input_path)
        if meta is None:
            return None

        ekey = self._resolve_ekey(meta, settings)
        if not ekey:
            logger.info("QQ offline musicex decrypt skipped: no ekey for %s", input_path)
            return None

        final_key = self._derive_key_from_ekey(ekey)
        if final_key is None:
            logger.info("QQ offline musicex decrypt skipped: invalid ekey for %s", input_path)
            return None

        tmp_path = output_path.with_name(f"{output_path.name}.qmc2_tmp")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._decrypt_payload(input_path, tmp_path, meta.audio_size, final_key)
            detected_container = fast_detect_container(tmp_path)
            if detected_container == "bin":
                tmp_path.unlink(missing_ok=True)
                return None
            if output_path.exists():
                output_path.unlink()
            tmp_path.replace(output_path)
        except OSError as exc:
            logger.warning("QQ offline musicex decrypt failed for %s: %s", input_path, exc)
            tmp_path.unlink(missing_ok=True)
            return None

        return {
            "output_path": str(output_path),
            "detected_container": detected_container,
            "final_extension": detected_container,
            "recognition_stage": "offline_qmc2",
            "backend": f"qmc2:{meta.format}",
            "decoded_bytes": output_path.stat().st_size,
            "artifact_state": "decoded",
            "musicex_song_mid": meta.song_mid,
            "musicex_filename": meta.filename,
            "musicex_audio_size": meta.audio_size,
            "musicex_ekey_source": "embedded" if meta.ekey else "api_or_cache",
        }

    def _resolve_ekey(self, meta: QQEncryptedTail, settings: dict) -> str | None:
        cache_key = self._cache_key(meta)
        if meta.ekey:
            if cache_key:
                self._cache_ekey(cache_key, meta.ekey, settings)
            return meta.ekey
        if not cache_key:
            return None

        cached = self._load_cached_ekey(cache_key, settings)
        if cached:
            return cached

        if not _settings_bool(settings, "qq_fetch_missing_ekey", True):
            return None

        cookie_info = self.cookie_provider.get_cookie()
        if not cookie_info:
            return None
        ekey = self.ekey_client.fetch(meta.song_mid, meta.filename, cookie_info["cookie"], cookie_info["uin"])
        if ekey:
            self._cache_ekey(cache_key, ekey, settings)
        return ekey

    @staticmethod
    def _cache_key(meta: QQEncryptedTail) -> str:
        filename = _filename_basename(meta.filename)
        parts = [item for item in (meta.song_mid, filename) if item]
        return "__".join(parts)

    def _cache_dir(self, settings: dict) -> pathlib.Path:
        override = str(settings.get("qq_ekey_cache_dir") or "").strip()
        if override:
            candidate = pathlib.Path(override).expanduser()
            if candidate.is_absolute():
                try:
                    cwd = pathlib.Path.cwd().resolve()
                    if candidate.resolve().is_relative_to(cwd):
                        logger.warning("QQ ekey cache dir inside current workspace ignored: %s", candidate)
                        return self.cache_dir
                except OSError:
                    pass
                return candidate
            logger.warning("relative QQ ekey cache dir ignored: %s", override)
        return self.cache_dir

    def _cache_path(self, key: str, settings: dict) -> pathlib.Path:
        cache_dir = self._cache_dir(settings)
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in key)
        return cache_dir / f"{safe_name}.txt"

    def _load_cached_ekey(self, key: str, settings: dict) -> str | None:
        path = self._cache_path(key, settings)
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError:
            return None
        return None

    def _cache_ekey(self, key: str, ekey: str, settings: dict) -> None:
        if settings.get("qq_cache_ekeys", True) is False:
            return
        path = self._cache_path(key, settings)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            tmp_path.write_text(ekey, encoding="utf-8")
            try:
                tmp_path.chmod(0o600)
            except OSError:
                pass
            tmp_path.replace(path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError as exc:
            logger.info("failed to cache QQ ekey for %s: %s", key, exc)

    @staticmethod
    def _derive_key_from_ekey(ekey: str) -> bytes | None:
        try:
            raw_key = base64.b64decode(ekey)
        except Exception:
            return None
        return derive_qmc2_key(raw_key)

    @staticmethod
    def _decrypt_payload(input_path: pathlib.Path, output_path: pathlib.Path, audio_size: int, key: bytes) -> None:
        cipher = None
        offset = 0
        chunk_size = 1024 * 1024
        with input_path.open("rb") as source, output_path.open("wb") as target:
            while offset < audio_size:
                read_size = min(chunk_size, audio_size - offset)
                chunk = bytearray(source.read(read_size))
                if not chunk:
                    break
                if not decrypt_qmc2_buffer_fast(key, chunk, offset):
                    if cipher is None:
                        cipher = _make_cipher(key)
                    cipher.decrypt(chunk, offset)
                target.write(chunk)
                offset += len(chunk)
        if offset != audio_size:
            raise OSError(f"incomplete QQ encrypted payload read: {offset}/{audio_size}")
