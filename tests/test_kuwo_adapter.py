from __future__ import annotations

import pathlib

from src.Infrastructure.platforms.registry import build_platform_adapter


def _xor_kwm(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index & 31] for index, byte in enumerate(data))


def _write_kwm_fixture(path: pathlib.Path, payload: bytes) -> None:
    key = bytes(range(1, 33))
    prepared = bytearray(payload)
    key_probe_offset = 32 * 467
    swapped_key = key[16:32] + key[:16]
    prepared[key_probe_offset:key_probe_offset + 32] = bytes(a ^ b for a, b in zip(swapped_key, key))
    path.write_bytes(b"\0" * 1024 + _xor_kwm(bytes(prepared), key))


def test_registry_builds_kuwo_adapter() -> None:
    adapter = build_platform_adapter("kuwo")

    assert adapter.platform_id == "kuwo"
    assert adapter.display_name == "酷我音乐"
    assert not adapter.requires_running_process()
    assert adapter.validate_runtime({}) == (True, None)


def test_kuwo_adapter_collects_kwm_files_and_maps_target_formats(tmp_path: pathlib.Path) -> None:
    adapter = build_platform_adapter("kuwo")
    source = tmp_path / "song.kwm"
    nested = tmp_path / "nested" / "other.kwm"
    alternate = tmp_path / "nested" / "alternate.kwma"
    ignored = tmp_path / "ignored.ncm"
    nested.parent.mkdir()
    source.write_bytes(b"kwm")
    nested.write_bytes(b"kwm")
    alternate.write_bytes(b"kwma")
    ignored.write_bytes(b"ncm")

    assert adapter.collect_files(source, recursive=False) == [source]
    assert adapter.collect_files(alternate, recursive=False) == [alternate]
    assert adapter.collect_files(tmp_path, recursive=False) == [source]
    assert adapter.collect_files(tmp_path, recursive=True) == [alternate, nested, source]
    assert adapter.output_basename(source) == "song"
    assert adapter.output_basename(alternate) == "alternate"
    assert adapter.predicted_extension(source, {"target_format_kwm": "auto"}) is None
    assert adapter.predicted_extension(source, {"target_format_kwm": "mp3"}) == "mp3"
    assert adapter.desired_target_format(source, {"target_format_kwm": "auto"}) == "auto"
    assert adapter.desired_target_format(source, {"target_format_kwm": "wav"}) == "wav"


def test_kuwo_adapter_decrypt_one_delegates_to_python_decoder(tmp_path: pathlib.Path, monkeypatch) -> None:
    adapter = build_platform_adapter("kuwo")
    source = tmp_path / "track.kwm"
    work_dir = tmp_path / "work"
    log_dir = tmp_path / "log"
    source.write_bytes(b"kwm")
    calls: dict[str, pathlib.Path] = {}

    def fake_decode_kwm_file(input_path: pathlib.Path, output_dir: pathlib.Path) -> dict:
        calls["input_path"] = input_path
        calls["output_dir"] = output_dir
        output_path = output_dir / "track.wav"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF....WAVE")
        return {
            "output_path": str(output_path),
            "detected_container": "wav",
            "final_extension": "wav",
            "backend": "fake-kuwo",
            "decoded_bytes": output_path.stat().st_size,
        }

    monkeypatch.setattr("src.Infrastructure.platforms.kuwo.adapter.decode_kwm_file", fake_decode_kwm_file)

    detail = adapter.decrypt_one(source, work_dir, {"target_format_kwm": "auto"}, log_dir=log_dir)

    assert calls == {"input_path": source, "output_dir": work_dir}
    assert detail["output_path"].endswith("track.wav")
    assert detail["detected_container"] == "wav"
    assert detail["backend"] == "fake-kuwo"


def test_kuwo_adapter_predicts_auto_extension_from_decrypted_header(tmp_path: pathlib.Path) -> None:
    adapter = build_platform_adapter("kuwo")
    source = tmp_path / "track.kwm"
    payload = bytearray(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    payload.extend(b"\0" * (32 * 468 - len(payload)))
    _write_kwm_fixture(source, bytes(payload))

    assert adapter.predicted_extension(source, {"target_format_kwm": "auto"}) == "wav"
