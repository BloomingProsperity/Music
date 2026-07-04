from __future__ import annotations

import pathlib
import time

from src.Infrastructure.transcoder import detect_audio_container, detect_container_from_header
from src.Infrastructure.xor_stream import xor_repeating_key_inplace


HEADER_SIZE = 1024
KEY_SIZE = 32
MAX_FIND_KEY_TIME = 468
STREAM_CHUNK_SIZE = 1024 * 1024


class KwmDecodeError(RuntimeError):
    pass


def _output_basename(input_path: pathlib.Path) -> str:
    name = input_path.name
    return name[:-4] if name.lower().endswith(".kwm") else input_path.stem


def _swap_key_halves(key: bytes) -> bytes:
    if len(key) != KEY_SIZE:
        raise KwmDecodeError("invalid kwm key size")
    return key[16:32] + key[:16]


def find_kwm_key(input_path: pathlib.Path) -> tuple[bytes, str]:
    if input_path.stat().st_size <= HEADER_SIZE + KEY_SIZE:
        raise KwmDecodeError("kwm file is too small")

    previous = bytes(KEY_SIZE)
    last = b""
    with input_path.open("rb") as source:
        source.seek(HEADER_SIZE)
        for _ in range(MAX_FIND_KEY_TIME):
            chunk = source.read(KEY_SIZE)
            if len(chunk) != KEY_SIZE:
                break
            if chunk == previous:
                return chunk, "repeated_chunk"
            previous = chunk
            last = chunk

    if len(last) != KEY_SIZE:
        raise KwmDecodeError("kwm key material is missing")
    return _swap_key_halves(last), "fallback_swap"


def peek_kwm_payload_container(input_path: pathlib.Path) -> str | None:
    input_path = pathlib.Path(input_path).expanduser().resolve()
    key, _key_source = find_kwm_key(input_path)
    with input_path.open("rb") as source:
        source.seek(HEADER_SIZE)
        header = bytearray(source.read(64))
    if not header:
        return None
    xor_repeating_key_inplace(header, key, 0)
    container = detect_container_from_header(header)
    return None if container == "bin" else container


def decode_kwm_file(input_path: pathlib.Path, output_dir: pathlib.Path) -> dict:
    started = time.perf_counter()
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    key, key_source = find_kwm_key(input_path)
    temp_output = output_dir / f".{_output_basename(input_path)}.{time.time_ns()}.tmp"
    decoded_bytes = 0

    try:
        with input_path.open("rb", buffering=STREAM_CHUNK_SIZE) as source, temp_output.open("wb", buffering=STREAM_CHUNK_SIZE) as target:
            source.seek(HEADER_SIZE)
            while True:
                block = bytearray(source.read(STREAM_CHUNK_SIZE))
                if not block:
                    break
                xor_repeating_key_inplace(block, key, decoded_bytes)
                target.write(block)
                decoded_bytes += len(block)

        detected_container, recognition_stage = detect_audio_container(temp_output)
        if detected_container == "bin":
            raise KwmDecodeError("unrecognized_audio_container")
        final_ext = detected_container
        final_output = output_dir / f"{_output_basename(input_path)}.{final_ext}"
        if final_output.exists():
            final_output.unlink()
        temp_output.replace(final_output)
        elapsed = round(time.perf_counter() - started, 6)
        return {
            "input_path": str(input_path),
            "output_path": str(final_output),
            "detected_container": detected_container,
            "final_extension": final_ext,
            "recognition_stage": recognition_stage,
            "backend": "python:kuwo-kwm-xor",
            "decoded_bytes": decoded_bytes,
            "key_source": key_source,
            "timing": {
                "header_parse_sec": 0.0,
                "key_material_sec": 0.0,
                "stream_decode_sec": elapsed,
                "publish_sec": 0.0,
                "total_sec": elapsed,
            },
        }
    finally:
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass
