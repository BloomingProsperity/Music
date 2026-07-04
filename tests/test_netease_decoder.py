from __future__ import annotations

import base64
import io
import json
import pathlib
import wave

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from src.Infrastructure.netease_decoder import NcmDecodeError, decode_ncm_file, read_ncm_metadata


CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")


def _key_box(key: bytes) -> bytes:
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


def _xor_music(data: bytes, key: bytes) -> bytes:
    box = _key_box(key)
    return bytes(byte ^ box[index & 0xFF] for index, byte in enumerate(data))


def _encrypt_core_key(key: bytes) -> bytes:
    encrypted = AES.new(CORE_KEY, AES.MODE_ECB).encrypt(pad(b"neteasecloudmusic" + key, 16))
    return bytes(byte ^ 0x64 for byte in encrypted)


def _encrypt_metadata(metadata: dict) -> bytes:
    raw = b"music:" + json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    encrypted = AES.new(META_KEY, AES.MODE_ECB).encrypt(pad(raw, 16))
    wrapped = b"163 key(Don't modify):" + base64.b64encode(encrypted)
    return bytes(byte ^ 0x63 for byte in wrapped)


def _wav_payload(length: int = 128) -> bytes:
    frames = bytes((index * 11 + 7) & 0xFF for index in range(max(32, length)))
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(8000)
        wav.writeframes(frames)
    return target.getvalue()


def _ncm_fixture(tmp_path: pathlib.Path, payload: bytes) -> pathlib.Path:
    rc4_key = b"streaming-key"
    metadata = {
        "format": "wav",
        "musicId": 10001,
        "musicName": "本地流式测试",
        "artist": [["Tester", 1]],
        "album": "Decoder Tests",
        "albumPic": "",
    }
    key_block = _encrypt_core_key(rc4_key)
    metadata_block = _encrypt_metadata(metadata)
    path = tmp_path / "sample.ncm"
    with path.open("wb") as target:
        target.write(b"CTENFDAM")
        target.write(b"\0\0")
        target.write(len(key_block).to_bytes(4, "little"))
        target.write(key_block)
        target.write(len(metadata_block).to_bytes(4, "little"))
        target.write(metadata_block)
        target.write((0).to_bytes(4, "little"))
        target.write(b"\0" * 5)
        target.write((0).to_bytes(4, "little"))
        target.write(_xor_music(payload, rc4_key))
    return path


def test_read_ncm_metadata_parses_format_without_decoding_music(tmp_path: pathlib.Path) -> None:
    ncm_path = _ncm_fixture(tmp_path, _wav_payload())

    parsed = read_ncm_metadata(ncm_path)

    assert parsed.audio_offset < ncm_path.stat().st_size
    assert parsed.raw_format == "wav"
    assert parsed.metadata["musicName"] == "本地流式测试"


def test_decode_ncm_file_restores_audio_payload_in_chunks(tmp_path: pathlib.Path, monkeypatch) -> None:
    payload = _wav_payload(257)
    ncm_path = _ncm_fixture(tmp_path, payload)
    monkeypatch.setattr("src.Infrastructure.netease_decoder.STREAM_CHUNK_SIZE", 17)

    summary = decode_ncm_file(ncm_path, tmp_path / "out")

    output_path = pathlib.Path(summary["output_path"])
    assert output_path.read_bytes() == payload
    assert summary["backend"] == "python:netease-ncm-stream"
    assert summary["detected_container"] == "wav"
    assert summary["final_extension"] == "wav"
    assert summary["metadata"]["musicName"] == "本地流式测试"
    assert summary["decoded_bytes"] == len(payload)


def test_decode_ncm_file_rejects_invalid_header(tmp_path: pathlib.Path) -> None:
    bad_path = tmp_path / "bad.ncm"
    bad_path.write_bytes(b"not-ncm")

    with pytest.raises(NcmDecodeError):
        decode_ncm_file(bad_path, tmp_path / "out")
