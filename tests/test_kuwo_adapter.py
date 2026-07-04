from __future__ import annotations

import pathlib

from src.Infrastructure.platforms.registry import build_platform_adapter


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
    ignored = tmp_path / "ignored.ncm"
    nested.parent.mkdir()
    source.write_bytes(b"kwm")
    nested.write_bytes(b"kwm")
    ignored.write_bytes(b"ncm")

    assert adapter.collect_files(source, recursive=False) == [source]
    assert adapter.collect_files(tmp_path, recursive=False) == [source]
    assert adapter.collect_files(tmp_path, recursive=True) == [nested, source]
    assert adapter.output_basename(source) == "song"
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
