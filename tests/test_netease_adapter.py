from __future__ import annotations

import pathlib

from src.Infrastructure.platforms.netease.adapter import NeteasePlatformAdapter


def test_netease_adapter_decrypt_one_uses_local_stream_decoder(tmp_path: pathlib.Path, monkeypatch) -> None:
    adapter = NeteasePlatformAdapter()
    source = tmp_path / "song.ncm"
    work_dir = tmp_path / "work"
    log_dir = tmp_path / "log"
    source.write_bytes(b"ncm")
    calls: dict[str, pathlib.Path] = {}

    def fake_decode_ncm_file(input_path: pathlib.Path, output_dir: pathlib.Path) -> dict:
        calls["input_path"] = input_path
        calls["output_dir"] = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "song.wav"
        output_path.write_bytes(b"RIFF....WAVE")
        return {
            "output_path": str(output_path),
            "detected_container": "wav",
            "final_extension": "wav",
            "backend": "fake-ncm-stream",
            "decoded_bytes": output_path.stat().st_size,
            "metadata": {"format": "wav"},
            "timing": {"stream_decode_sec": 0.001, "total_sec": 0.001},
        }

    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.decode_ncm_file", fake_decode_ncm_file)

    detail = adapter.decrypt_one(source, work_dir, {}, log_dir=log_dir)

    assert calls == {"input_path": source, "output_dir": work_dir}
    assert detail["backend"] == "fake-ncm-stream"
    assert detail["output_path"].endswith("song.wav")


def test_netease_adapter_predicts_extension_from_local_metadata(tmp_path: pathlib.Path, monkeypatch) -> None:
    adapter = NeteasePlatformAdapter()
    source = tmp_path / "song.ncm"
    source.write_bytes(b"ncm")

    class Metadata:
        raw_format = "flac"

    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.read_ncm_metadata", lambda _path: Metadata())

    assert adapter.predicted_extension(source, {"target_format_ncm": "auto"}) == "flac"
    assert adapter.desired_target_format(source, {"target_format_ncm": "auto"}) == "flac"
    assert adapter.predicted_extension(source, {"target_format_ncm": "mp3"}) == "mp3"
