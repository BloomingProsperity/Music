from __future__ import annotations

import base64
import json
import pathlib
import time
from dataclasses import dataclass, field
from typing import BinaryIO

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from src.Infrastructure.transcoder import detect_audio_container
from src.Infrastructure.xor_stream import xor_repeating_key_inplace


MAGIC_HEADER = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
METADATA_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
RC4_KEY_PREFIX = b"neteasecloudmusic"
METADATA_PREFIX = b"163 key(Don't modify):"
STREAM_CHUNK_SIZE = 1024 * 1024
SUPPORTED_RAW_FORMATS = {"flac", "m4a", "mp3", "wav"}


class NcmDecodeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NcmMetadata:
    raw_format: str
    metadata: dict
    audio_offset: int
    metadata_type: str = "music"


@dataclass(frozen=True, slots=True)
class _ParsedHeader:
    public: NcmMetadata
    key_box: bytes
    cover_size: int = 0
    timing: dict[str, float] = field(default_factory=dict)


def _read_exact(source: BinaryIO, size: int, label: str) -> bytes:
    data = source.read(size)
    if len(data) != size:
        raise NcmDecodeError(f"incomplete ncm {label}")
    return data


def _read_u32(source: BinaryIO, label: str) -> int:
    return int.from_bytes(_read_exact(source, 4, label), "little")


def _normalize_format(value: object) -> str:
    normalized = str(value or "mp3").strip().lower()
    if normalized == "ogg":
        normalized = "m4a"
    return normalized if normalized in SUPPORTED_RAW_FORMATS else "mp3"


def _aes_unpad_decrypt(key: bytes, data: bytes) -> bytes:
    return unpad(AES.new(key, AES.MODE_ECB).decrypt(data), 16, "pkcs7")


def _decrypt_rc4_key(encrypted: bytes) -> bytes:
    encrypted = bytes(byte ^ 0x64 for byte in encrypted)
    decrypted = _aes_unpad_decrypt(CORE_KEY, encrypted)
    if not decrypted.startswith(RC4_KEY_PREFIX):
        raise NcmDecodeError("invalid ncm key prefix")
    key = decrypted[len(RC4_KEY_PREFIX):]
    if not key:
        raise NcmDecodeError("empty ncm music key")
    return key


def _decrypt_metadata(encrypted: bytes) -> tuple[str, dict]:
    if not encrypted:
        return "music", {}
    data = bytes(byte ^ 0x63 for byte in encrypted)
    if not data.startswith(METADATA_PREFIX):
        raise NcmDecodeError("invalid ncm metadata prefix")
    decrypted = _aes_unpad_decrypt(METADATA_KEY, base64.b64decode(data[len(METADATA_PREFIX):]))
    separator = decrypted.find(b":")
    if separator < 0:
        raise NcmDecodeError("invalid ncm metadata payload")
    metadata_type = decrypted[:separator].decode("utf-8", errors="replace")
    payload = json.loads(decrypted[separator + 1:].decode("utf-8", errors="replace") or "{}")
    if metadata_type == "dj":
        payload = dict((payload or {}).get("mainMusic") or {})
    elif not isinstance(payload, dict):
        payload = {}
    return metadata_type, payload


def _build_key_box(key: bytes) -> bytes:
    box = bytearray(range(256))
    key_box = bytearray(256)
    j = 0
    for index in range(256):
        j = (j + box[index] + key[index % len(key)]) & 0xFF
        box[index], box[j] = box[j], box[index]
    for index in range(256):
        cursor = (index + 1) & 0xFF
        first = box[cursor]
        second = box[(first + cursor) & 0xFF]
        key_box[index] = box[(second + first) & 0xFF]
    return bytes(key_box)


def _output_basename(input_path: pathlib.Path) -> str:
    name = input_path.name
    return name[:-4] if name.lower().endswith(".ncm") else input_path.stem


def _parse_header(source: BinaryIO) -> _ParsedHeader:
    started = time.perf_counter()
    if _read_exact(source, 8, "magic") != MAGIC_HEADER:
        raise NcmDecodeError("invalid ncm magic header")
    _read_exact(source, 2, "gap")

    key_material_started = time.perf_counter()
    encrypted_key_size = _read_u32(source, "key size")
    music_key = _decrypt_rc4_key(_read_exact(source, encrypted_key_size, "key data"))

    encrypted_metadata_size = _read_u32(source, "metadata size")
    metadata_type, metadata = _decrypt_metadata(_read_exact(source, encrypted_metadata_size, "metadata data"))
    key_box = _build_key_box(music_key)
    key_material_sec = round(time.perf_counter() - key_material_started, 6)

    _read_exact(source, 4, "crc32")
    _read_exact(source, 5, "gap2")
    cover_size = _read_u32(source, "cover size")
    if cover_size:
        _read_exact(source, cover_size, "cover data")
    audio_offset = source.tell()
    raw_format = _normalize_format(metadata.get("format"))
    return _ParsedHeader(
        public=NcmMetadata(
            raw_format=raw_format,
            metadata=metadata,
            audio_offset=audio_offset,
            metadata_type=metadata_type,
        ),
        key_box=key_box,
        cover_size=cover_size,
        timing={
            "header_parse_sec": round(time.perf_counter() - started, 6),
            "key_material_sec": key_material_sec,
        },
    )


def read_ncm_metadata(input_path: pathlib.Path) -> NcmMetadata:
    with pathlib.Path(input_path).open("rb") as source:
        return _parse_header(source).public


def decode_ncm_file(input_path: pathlib.Path, output_dir: pathlib.Path) -> dict:
    started = time.perf_counter()
    input_path = pathlib.Path(input_path).expanduser().resolve()
    output_dir = pathlib.Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    decoded_bytes = 0

    with input_path.open("rb", buffering=STREAM_CHUNK_SIZE) as source:
        parsed = _parse_header(source)
        temp_output = output_dir / f".{_output_basename(input_path)}.{time.time_ns()}.tmp"
        try:
            stream_started = time.perf_counter()
            with temp_output.open("wb", buffering=STREAM_CHUNK_SIZE) as target:
                while True:
                    block = bytearray(source.read(STREAM_CHUNK_SIZE))
                    if not block:
                        break
                    xor_repeating_key_inplace(block, parsed.key_box, decoded_bytes)
                    target.write(block)
                    decoded_bytes += len(block)
            stream_decode_sec = round(time.perf_counter() - stream_started, 6)

            detected_container, recognition_stage = detect_audio_container(temp_output)
            final_extension = detected_container if detected_container != "bin" else parsed.public.raw_format
            final_output = output_dir / f"{_output_basename(input_path)}.{final_extension}"
            if final_output.exists():
                final_output.unlink()
            temp_output.replace(final_output)
        finally:
            if temp_output.exists():
                try:
                    temp_output.unlink()
                except OSError:
                    pass

    elapsed = round(time.perf_counter() - started, 6)
    return {
        "input_path": str(input_path),
        "output_path": str(final_output),
        "detected_container": detected_container,
        "final_extension": final_extension,
        "recognition_stage": recognition_stage,
        "backend": "python:netease-ncm-stream",
        "decoded_bytes": decoded_bytes,
        "metadata": dict(parsed.public.metadata),
        "metadata_type": parsed.public.metadata_type,
        "cover_size": parsed.cover_size,
        "timing": {
            "header_parse_sec": float(parsed.timing.get("header_parse_sec", 0.0)),
            "key_material_sec": float(parsed.timing.get("key_material_sec", 0.0)),
            "stream_decode_sec": stream_decode_sec,
            "publish_sec": 0.0,
            "total_sec": elapsed,
        },
    }
