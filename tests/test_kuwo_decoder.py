from __future__ import annotations

import pathlib

import pytest

from src.Infrastructure.kuwo_decoder import KwmDecodeError, decode_kwm_file


def _swap_halves(value: bytes) -> bytes:
    return value[16:32] + value[:16]


def _xor_with_key(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index & 31] for index, byte in enumerate(data))


def _wav_like_payload(length: int) -> bytearray:
    payload = bytearray(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    payload.extend(bytes((index * 17 + 3) & 0xFF for index in range(length - len(payload))))
    return payload


def test_decode_kwm_file_restores_wav_payload_with_fallback_key(tmp_path: pathlib.Path) -> None:
    key = bytes(range(1, 33))
    payload = _wav_like_payload(32 * 468)
    last_chunk_start = 32 * 467
    swapped_key = _swap_halves(key)
    payload[last_chunk_start:last_chunk_start + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    encrypted = _xor_with_key(bytes(payload), key)
    kwm_path = tmp_path / "song.kwm"
    kwm_path.write_bytes(b"\0" * 1024 + encrypted)

    summary = decode_kwm_file(kwm_path, tmp_path / "out")

    output_path = pathlib.Path(summary["output_path"])
    assert output_path.read_bytes() == bytes(payload)
    assert summary["detected_container"] == "wav"
    assert summary["final_extension"] == "wav"
    assert summary["key_source"] == "fallback_swap"


def test_decode_kwm_file_rejects_too_small_files(tmp_path: pathlib.Path) -> None:
    kwm_path = tmp_path / "bad.kwm"
    kwm_path.write_bytes(b"short")

    with pytest.raises(KwmDecodeError):
        decode_kwm_file(kwm_path, tmp_path / "out")
