from __future__ import annotations

import base64
import io
import json
import lzma
import os
import pathlib
import subprocess
import struct
import wave
from types import SimpleNamespace

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from src.Application.decrypt_service import run_batch
from src.Application.models import BatchRunConfig
from src.Infrastructure.platforms.qq import musicex_offline
from src.Infrastructure.platforms.qq.adapter import QQPlatformAdapter
from src.Infrastructure.platforms.kuwo.adapter import KuwoPlatformAdapter
from src.Infrastructure.platforms.kugou.adapter import KugouPlatformAdapter
from src.Infrastructure.platforms.netease.adapter import NeteasePlatformAdapter
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Infrastructure.transcoder import resolve_ffmpeg_path
from src.Infrastructure import kugou_decoder


CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")


def _wav_payload(frame_count: int = 16000) -> bytes:
    frames = bytes((index * 13 + 9) & 0xFF for index in range(frame_count))
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(8000)
        wav.writeframes(frames)
    return target.getvalue()


def _ncm_key_box(key: bytes) -> bytes:
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


def _xor_ncm_music(data: bytes, key: bytes) -> bytes:
    box = _ncm_key_box(key)
    return bytes(byte ^ box[index & 0xFF] for index, byte in enumerate(data))


def _encrypt_ncm_core_key(key: bytes) -> bytes:
    encrypted = AES.new(CORE_KEY, AES.MODE_ECB).encrypt(pad(b"neteasecloudmusic" + key, 16))
    return bytes(byte ^ 0x64 for byte in encrypted)


def _encrypt_ncm_metadata(metadata: dict) -> bytes:
    raw = b"music:" + json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    encrypted = AES.new(META_KEY, AES.MODE_ECB).encrypt(pad(raw, 16))
    wrapped = b"163 key(Don't modify):" + base64.b64encode(encrypted)
    return bytes(byte ^ 0x63 for byte in wrapped)


def _write_ncm_fixture(path: pathlib.Path, payload: bytes) -> None:
    rc4_key = b"e2e-stream-key"
    metadata = {
        "format": "wav",
        "musicId": 20001,
        "musicName": "Local E2E",
        "artist": [["Tester", 1]],
        "album": "Platform Tests",
        "albumPic": "",
    }
    key_block = _encrypt_ncm_core_key(rc4_key)
    metadata_block = _encrypt_ncm_metadata(metadata)
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
        target.write(_xor_ncm_music(payload, rc4_key))


def _xor_kwm(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index & 31] for index, byte in enumerate(data))


def _write_kwm_fixture(path: pathlib.Path, payload: bytes) -> None:
    key = bytes(range(1, 33))
    prepared = bytearray(payload)
    key_probe_offset = 32 * 467
    swapped_key = key[16:32] + key[:16]
    prepared[key_probe_offset:key_probe_offset + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    path.write_bytes(b"\0" * 1024 + _xor_kwm(bytes(prepared), key))


def _tea_encrypt_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">II", block)
    k0, k1, k2, k3 = struct.unpack(">4I", key)
    delta = 0x9E3779B9
    total = 0
    for _ in range(16):
        total = (total + delta) & 0xFFFFFFFF
        v0 = (v0 + (((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1))) & 0xFFFFFFFF
        v1 = (v1 + (((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3))) & 0xFFFFFFFF
    return struct.pack(">II", v0, v1)


def _encrypt_tencent_tea(data: bytes, key: bytes) -> bytes:
    pad_len = (8 - ((1 + 2 + len(data) + 7) % 8)) % 8
    plain = bytes([pad_len]) + bytes((0xA5 + index) & 0xFF for index in range(pad_len)) + b"\0\0" + data + b"\0" * 7
    blocks = [plain[index:index + 8] for index in range(0, len(plain), 8)]
    encrypted_blocks: list[bytes] = []
    previous_cipher = bytes(8)
    previous_decoded = bytes(8)
    for block in blocks:
        decoded = bytes(value ^ mask for value, mask in zip(block, previous_cipher))
        mixed = _tea_encrypt_block(decoded, key)
        encrypted = bytes(value ^ mask for value, mask in zip(mixed, previous_decoded))
        encrypted_blocks.append(encrypted)
        previous_cipher = encrypted
        previous_decoded = decoded
    return b"".join(encrypted_blocks)


def _qq_raw_key_for_final_key(final_key: bytes) -> bytes:
    simple_key = musicex_offline._simple_make_key(106, 8)
    tea_key = bytearray(16)
    for index in range(8):
        tea_key[index * 2] = simple_key[index]
        tea_key[index * 2 + 1] = final_key[index]
    return final_key[:8] + _encrypt_tencent_tea(final_key[8:], bytes(tea_key))


def _write_qq_mflac_fixture(path: pathlib.Path, payload: bytes) -> None:
    final_key = b"qkk-local-key-16"
    ekey = base64.b64encode(_qq_raw_key_for_final_key(final_key)).decode("ascii")
    encrypted = bytearray(payload)
    musicex_offline._make_cipher(final_key).decrypt(encrypted, 0)
    ekey_data = f"001localqq,{ekey}".encode("ascii")
    path.write_bytes(bytes(encrypted) + ekey_data + len(ekey_data).to_bytes(4, "little") + b"QTag")


def _encrypt_kugou_v3_payload(payload: bytes, own_key: bytes, pub_key: bytes) -> bytes:
    own_tables = kugou_decoder._build_own_transform_tables(own_key)
    pub_tables = kugou_decoder._build_pub_transform_tables()
    inverse_own_tables = []
    for table in own_tables:
        inverse = bytearray(256)
        for source, transformed in enumerate(table):
            inverse[transformed] = source
        inverse_own_tables.append(bytes(inverse))

    encrypted = bytearray(len(payload))
    for position, value in enumerate(payload):
        pub_index = position // kugou_decoder.PUB_KEY_LEN_MAGNIFICATION
        mask = pub_tables[position % len(pub_tables)][pub_key[pub_index]]
        encrypted[position] = inverse_own_tables[position % len(inverse_own_tables)][value ^ mask]
    return bytes(encrypted)


def _write_kugou_v3_fixture(path: pathlib.Path, key_path: pathlib.Path, payload: bytes) -> None:
    own_key = bytes((index * 11 + 7) & 0xFF for index in range(kugou_decoder.OWN_KEY_LEN - 1)) + b"\0"
    pub_key = bytes((index * 17 + 5) & 0xFF for index in range((len(payload) + 15) // 16 + 8))
    key_path.write_bytes(lzma.compress(pub_key))
    header = bytearray(kugou_decoder.HEADER_LEN)
    header[:16] = kugou_decoder.KGM_MAGIC
    struct.pack_into("<III", header, 0x10, kugou_decoder.HEADER_LEN, 3, 0)
    header[0x1C:0x2C] = own_key[:16]
    path.write_bytes(bytes(header) + _encrypt_kugou_v3_payload(payload, own_key, pub_key))


def _runtime_paths_for_test(root: pathlib.Path) -> RuntimePaths:
    real_paths = RuntimePaths.discover()
    return RuntimePaths(
        root_dir=root,
        bundle_dir=real_paths.bundle_dir,
        assets_dir=real_paths.assets_dir,
        plugins_dir=root / "plugins",
        log_dir=root / "_log",
        output_dir=root / "output",
        docs_dir=root / "_docs",
        plugins_config=root / "plugins" / "plugins.json",
        output_manifest=root / "plugins" / "output_manifest.json",
    )


def _assert_decodable_mp3(path: pathlib.Path, ffmpeg_path: pathlib.Path) -> None:
    completed = subprocess.run(
        [str(ffmpeg_path), "-v", "error", "-i", str(path), "-f", "null", os.devnull],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _run_batch_to_mp3(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_id: str,
    adapter,
    input_file: pathlib.Path,
    settings: dict,
) -> pathlib.Path:
    runtime_paths = _runtime_paths_for_test(tmp_path)
    monkeypatch.setattr(RuntimePaths, "discover", classmethod(lambda cls: runtime_paths))
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "mp3"
    input_dir.mkdir()
    input_file.replace(input_dir / input_file.name)
    config = BatchRunConfig(
        platform_id=platform_id,
        input_path=input_dir,
        output_dir=output_dir,
        recursive=False,
        collision_policy="suffix",
        settings={
            **settings,
            "transcode_enabled": True,
            "auto_transcode_after_decode": True,
            "transcode_max_workers": 1,
            "transcode_bitrate_kbps": 128,
            "embed_cover_art": False,
            "supplement_album_metadata": False,
        },
    )

    result_code = run_batch(config, adapter)

    assert result_code == 0
    mp3_files = sorted(output_dir.glob("*.mp3"))
    assert len(mp3_files) == 1
    return mp3_files[0]


def test_netease_batch_decrypts_and_transcodes_synthetic_ncm_to_decodable_mp3(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg_path = resolve_ffmpeg_path(RuntimePaths.discover())
    if ffmpeg_path is None:
        pytest.skip("ffmpeg executable is not available")
    source = tmp_path / "local_e2e.ncm"
    _write_ncm_fixture(source, _wav_payload())

    mp3_path = _run_batch_to_mp3(
        tmp_path,
        monkeypatch,
        "netease",
        NeteasePlatformAdapter(),
        source,
        {"target_format_ncm": "mp3"},
    )

    _assert_decodable_mp3(mp3_path, ffmpeg_path)


def test_qq_batch_decrypts_and_transcodes_synthetic_mflac_to_decodable_mp3(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg_path = resolve_ffmpeg_path(RuntimePaths.discover())
    if ffmpeg_path is None:
        pytest.skip("ffmpeg executable is not available")
    source = tmp_path / "local_e2e.mflac"
    _write_qq_mflac_fixture(source, _wav_payload())
    monkeypatch.setattr(musicex_offline, "decrypt_qmc2_buffer_fast", lambda _key, _buffer, _offset: False)

    mp3_path = _run_batch_to_mp3(
        tmp_path,
        monkeypatch,
        "qq",
        QQPlatformAdapter(),
        source,
        {"format_rules": {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"}},
    )

    _assert_decodable_mp3(mp3_path, ffmpeg_path)


def test_kuwo_batch_decrypts_and_transcodes_synthetic_kwm_to_decodable_mp3(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg_path = resolve_ffmpeg_path(RuntimePaths.discover())
    if ffmpeg_path is None:
        pytest.skip("ffmpeg executable is not available")
    source = tmp_path / "local_e2e.kwm"
    _write_kwm_fixture(source, _wav_payload())

    mp3_path = _run_batch_to_mp3(
        tmp_path,
        monkeypatch,
        "kuwo",
        KuwoPlatformAdapter(),
        source,
        {"target_format_kwm": "mp3"},
    )

    _assert_decodable_mp3(mp3_path, ffmpeg_path)


def test_kugou_batch_decrypts_and_transcodes_synthetic_kgm_to_decodable_mp3(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg_path = resolve_ffmpeg_path(RuntimePaths.discover())
    if ffmpeg_path is None:
        pytest.skip("ffmpeg executable is not available")
    source = tmp_path / "local_e2e.kgm"
    key_path = tmp_path / "kugou_key.xz"
    _write_kugou_v3_fixture(source, key_path, _wav_payload())
    monkeypatch.setattr(kugou_decoder, "get_native_backend", lambda: SimpleNamespace(available=False, dll_path=None))

    mp3_path = _run_batch_to_mp3(
        tmp_path,
        monkeypatch,
        "kugou",
        KugouPlatformAdapter(),
        source,
        {"target_format_kgma": "mp3", "key_file": str(key_path)},
    )

    _assert_decodable_mp3(mp3_path, ffmpeg_path)
