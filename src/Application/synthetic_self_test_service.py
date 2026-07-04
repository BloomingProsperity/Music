from __future__ import annotations

import base64
import io
import json
import lzma
import pathlib
import shutil
import struct
import wave
from typing import Any, Iterable

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from src.Application.sample_verification_service import DEFAULT_SAMPLE_PLATFORMS, SampleVerificationSummary, run_sample_verification
from src.Infrastructure import kugou_decoder
from src.Infrastructure.platforms.qq import musicex_offline
from src.Infrastructure.runtime_paths import RuntimePaths


NCM_CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
NCM_META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
KUWO_YEELION_MAGIC = b"yeelion-kuwo-tme"
KUWO_YEELION_PREDEFINED_KEY = b"MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"


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
    cursor = 0
    for index in range(256):
        cursor = (cursor + box[index] + key[index % len(key)]) & 0xFF
        box[index], box[cursor] = box[cursor], box[index]
    for index in range(256):
        first_index = (index + 1) & 0xFF
        first = box[first_index]
        second = box[(first + first_index) & 0xFF]
        key_box[index] = box[(second + first) & 0xFF]
    return bytes(key_box)


def _xor_ncm_music(data: bytes, key: bytes) -> bytes:
    box = _ncm_key_box(key)
    return bytes(value ^ box[index & 0xFF] for index, value in enumerate(data))


def _encrypt_ncm_core_key(key: bytes) -> bytes:
    encrypted = AES.new(NCM_CORE_KEY, AES.MODE_ECB).encrypt(pad(b"neteasecloudmusic" + key, 16))
    return bytes(value ^ 0x64 for value in encrypted)


def _encrypt_ncm_metadata(metadata: dict[str, Any]) -> bytes:
    raw = b"music:" + json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    encrypted = AES.new(NCM_META_KEY, AES.MODE_ECB).encrypt(pad(raw, 16))
    wrapped = b"163 key(Don't modify):" + base64.b64encode(encrypted)
    return bytes(value ^ 0x63 for value in wrapped)


def _write_ncm_fixture(path: pathlib.Path, payload: bytes) -> None:
    rc4_key = b"self-test-ncm-key"
    metadata = {
        "format": "wav",
        "musicId": 30001,
        "musicName": "Self Test",
        "artist": [["QKK", 1]],
        "album": "Self Test",
        "albumPic": "",
    }
    key_block = _encrypt_ncm_core_key(rc4_key)
    metadata_block = _encrypt_ncm_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return bytes(value ^ key[index & 31] for index, value in enumerate(data))


def _write_kwm_fixture(path: pathlib.Path, payload: bytes) -> None:
    key = bytes(range(1, 33))
    prepared = bytearray(payload)
    key_probe_offset = 32 * 467
    swapped_key = key[16:32] + key[:16]
    prepared[key_probe_offset:key_probe_offset + 32] = bytes(value ^ mask for value, mask in zip(swapped_key, key))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 1024 + _xor_kwm(bytes(prepared), key))


def _kuwo_yeelion_mask(raw_key: int) -> bytes:
    key_text = str(raw_key)
    if len(key_text) >= 32:
        key_text = key_text[:32]
    else:
        key_text = (key_text * ((32 // len(key_text)) + 1))[:32]
    return bytes(left ^ ord(right) for left, right in zip(KUWO_YEELION_PREDEFINED_KEY, key_text))


def _write_yeelion_kwm_fixture(path: pathlib.Path, payload: bytes) -> None:
    raw_key = 1234567890123456789
    header = bytearray(1024)
    header[:len(KUWO_YEELION_MAGIC)] = KUWO_YEELION_MAGIC
    struct.pack_into("<Q", header, 0x18, raw_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + _xor_kwm(payload, _kuwo_yeelion_mask(raw_key)))


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
    encrypted_blocks: list[bytes] = []
    previous_cipher = bytes(8)
    previous_decoded = bytes(8)
    for index in range(0, len(plain), 8):
        block = plain[index:index + 8]
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


def _write_qq_fixture(path: pathlib.Path, payload: bytes) -> None:
    final_key = b"qkk-local-key-16"
    ekey = base64.b64encode(_qq_raw_key_for_final_key(final_key)).decode("ascii")
    encrypted = bytearray(payload)
    musicex_offline._make_cipher(final_key).decrypt(encrypted, 0)
    ekey_data = f"001localqq,{ekey}".encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _write_kugou_fixture(path: pathlib.Path, key_path: pathlib.Path, payload: bytes) -> None:
    own_key = bytes((index * 11 + 7) & 0xFF for index in range(kugou_decoder.OWN_KEY_LEN - 1)) + b"\0"
    pub_key = bytes((index * 17 + 5) & 0xFF for index in range((len(payload) + 15) // 16 + 8))
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(lzma.compress(pub_key))
    header = bytearray(kugou_decoder.HEADER_LEN)
    header[:16] = kugou_decoder.KGM_MAGIC
    struct.pack_into("<III", header, 0x10, kugou_decoder.HEADER_LEN, 3, 0)
    header[0x1C:0x2C] = own_key[:16]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + _encrypt_kugou_v3_payload(payload, own_key, pub_key))


def _copy_config(config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in config.items():
        result[key] = dict(value) if isinstance(value, dict) else value
    return result


def _write_synthetic_inputs(input_root: pathlib.Path, platforms: Iterable[str]) -> pathlib.Path:
    payload = _wav_payload()
    normalized = {str(platform).strip().lower() for platform in platforms}
    if "qq" in normalized:
        _write_qq_fixture(input_root / "qq" / "local_e2e.mflac", payload)
    if "kugou" in normalized:
        _write_kugou_fixture(input_root / "kugou" / "local_e2e.kgm", input_root / "kugou" / "kugou_key.xz", payload)
    if "netease" in normalized:
        _write_ncm_fixture(input_root / "netease" / "local_e2e.ncm", payload)
    if "kuwo" in normalized:
        _write_kwm_fixture(input_root / "kuwo" / "local_e2e.kwm", payload)
        _write_yeelion_kwm_fixture(input_root / "kuwo" / "local_yeelion.kwma", payload)
    return input_root / "kugou" / "kugou_key.xz"


def run_synthetic_self_test(
    *,
    output_dir: pathlib.Path,
    config: dict[str, Any],
    paths: RuntimePaths,
    platforms: Iterable[str] = DEFAULT_SAMPLE_PLATFORMS,
    max_workers: int = 1,
    bitrate_kbps: int = 128,
    fresh: bool = True,
) -> SampleVerificationSummary:
    output_dir = output_dir.expanduser().resolve()
    input_root = output_dir / "_self_test_inputs"
    normalized_platforms = tuple(str(platform).strip().lower() for platform in platforms)
    if fresh and input_root.exists():
        shutil.rmtree(input_root)
    input_root.mkdir(parents=True, exist_ok=True)

    kugou_key = _write_synthetic_inputs(input_root, normalized_platforms)
    self_config = _copy_config(config)
    self_config.setdefault("qq", {})
    self_config.setdefault("kugou", {})
    self_config.setdefault("netease", {})
    self_config.setdefault("kuwo", {})
    self_config["qq"]["qq_fetch_missing_ekey"] = False
    self_config["qq"]["qq_cache_ekeys"] = False
    self_config["kugou"]["key_file"] = str(kugou_key)

    return run_sample_verification(
        input_paths=[input_root],
        output_dir=output_dir,
        config=self_config,
        paths=paths,
        platforms=normalized_platforms,
        recursive=True,
        max_workers=max_workers,
        bitrate_kbps=bitrate_kbps,
        fresh=fresh,
    )
