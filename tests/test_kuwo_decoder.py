from __future__ import annotations

import pathlib
import struct

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


def test_decode_kwm_file_supports_yeelion_header_key(tmp_path: pathlib.Path) -> None:
    raw_key = 1234567890123456789
    key_text = str(raw_key)[:32]
    if len(key_text) < 32:
        key_text = (key_text * ((32 // len(key_text)) + 1))[:32]
    predefined = b"MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"
    mask = bytes(left ^ ord(right) for left, right in zip(predefined, key_text))
    payload = bytes(_wav_like_payload(4096))
    encrypted = _xor_with_key(payload, mask)
    header = bytearray(1024)
    header[:16] = b"yeelion-kuwo-tme"
    struct.pack_into("<Q", header, 0x18, raw_key)
    kwm_path = tmp_path / "yeelion.kwm"
    kwm_path.write_bytes(bytes(header) + encrypted)

    summary = decode_kwm_file(kwm_path, tmp_path / "out")

    output_path = pathlib.Path(summary["output_path"])
    assert output_path.read_bytes() == payload
    assert summary["detected_container"] == "wav"
    assert summary["key_source"] == "yeelion_header"


def test_decode_kwm_file_strips_kwma_suffix(tmp_path: pathlib.Path) -> None:
    key = bytes(range(1, 33))
    payload = _wav_like_payload(32 * 468)
    last_chunk_start = 32 * 467
    swapped_key = _swap_halves(key)
    payload[last_chunk_start:last_chunk_start + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    encrypted = _xor_with_key(bytes(payload), key)
    kwm_path = tmp_path / "song.kwma"
    kwm_path.write_bytes(b"\0" * 1024 + encrypted)

    summary = decode_kwm_file(kwm_path, tmp_path / "out")

    assert pathlib.Path(summary["output_path"]).name == "song.wav"


def test_decode_kwm_file_uses_shared_xor_helper(tmp_path: pathlib.Path, monkeypatch) -> None:
    key = bytes(range(1, 33))
    payload = _wav_like_payload(32 * 468)
    last_chunk_start = 32 * 467
    swapped_key = _swap_halves(key)
    payload[last_chunk_start:last_chunk_start + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    encrypted = _xor_with_key(bytes(payload), key)
    kwm_path = tmp_path / "song.kwm"
    kwm_path.write_bytes(b"\0" * 1024 + encrypted)
    calls: list[tuple[int, int, int]] = []

    def fake_xor(block: bytearray, key_bytes: bytes, start_offset: int) -> str:
        calls.append((len(block), len(key_bytes), start_offset))
        for index in range(len(block)):
            block[index] ^= key_bytes[(start_offset + index) % len(key_bytes)]
        return "fake"

    monkeypatch.setattr("src.Infrastructure.kuwo_decoder.STREAM_CHUNK_SIZE", 37)
    monkeypatch.setattr("src.Infrastructure.kuwo_decoder.xor_repeating_key_inplace", fake_xor)

    summary = decode_kwm_file(kwm_path, tmp_path / "out")

    assert pathlib.Path(summary["output_path"]).read_bytes() == bytes(payload)
    assert len(calls) > 1
    assert all(key_size == 32 for _, key_size, _ in calls)
    assert [start for _, _, start in calls] == [index * 37 for index in range(len(calls))]


def test_decode_kwm_file_rejects_too_small_files(tmp_path: pathlib.Path) -> None:
    kwm_path = tmp_path / "bad.kwm"
    kwm_path.write_bytes(b"short")

    with pytest.raises(KwmDecodeError):
        decode_kwm_file(kwm_path, tmp_path / "out")


def test_decode_kwm_file_rejects_unrecognized_payload_without_publishing_output(tmp_path: pathlib.Path) -> None:
    key = bytes(range(1, 33))
    payload = bytearray(b"not an audio container")
    payload.extend(bytes((index * 11 + 7) & 0xFF for index in range(32 * 468 - len(payload))))
    last_chunk_start = 32 * 467
    swapped_key = _swap_halves(key)
    payload[last_chunk_start:last_chunk_start + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    encrypted = _xor_with_key(bytes(payload), key)
    kwm_path = tmp_path / "bad-audio.kwm"
    output_dir = tmp_path / "out"
    kwm_path.write_bytes(b"\0" * 1024 + encrypted)

    with pytest.raises(KwmDecodeError, match="unrecognized_audio_container"):
        decode_kwm_file(kwm_path, output_dir)

    assert not list(output_dir.glob("*"))
