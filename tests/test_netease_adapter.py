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


def test_netease_adapter_keeps_auto_when_metadata_format_is_unavailable(tmp_path: pathlib.Path, monkeypatch) -> None:
    adapter = NeteasePlatformAdapter()
    source = tmp_path / "song.ncm"
    source.write_bytes(b"ncm")

    class BrokenNeteaseFile:
        def __init__(self, _path: pathlib.Path) -> None:
            pass

        def decrypt(self):
            raise RuntimeError("metadata unavailable")

    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.read_ncm_metadata", lambda _path: (_ for _ in ()).throw(RuntimeError("bad metadata")))
    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.NeteaseCloudMusicFile", BrokenNeteaseFile)

    assert adapter.predicted_extension(source, {"target_format_ncm": "auto"}) is None
    assert adapter.desired_target_format(source, {"target_format_ncm": "auto"}) == "auto"


def test_netease_adapter_fallback_preserves_source_and_metadata(tmp_path: pathlib.Path, monkeypatch) -> None:
    adapter = NeteasePlatformAdapter()
    source = tmp_path / "song.ncm"
    work_dir = tmp_path / "work"
    log_dir = tmp_path / "log"
    source.write_bytes(b"ncm")

    class MusicMetadata:
        format = "mp3"

    class Metadata:
        type = "music"
        json = {
            "format": "mp3",
            "musicName": "Fallback Song",
            "artist": [["Tester", 1]],
            "album": "Fallback Album",
        }

    class FallbackNeteaseFile:
        music_metadata = MusicMetadata()
        metadata = Metadata()

        def __init__(self, path: pathlib.Path) -> None:
            self.path = path

        def decrypt(self):
            return self

        def dump_music(self, output_hint: pathlib.Path) -> pathlib.Path:
            output_path = pathlib.Path(output_hint).with_suffix(".mp3")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"ID3")
            return output_path

    monkeypatch.setattr(
        "src.Infrastructure.platforms.netease.adapter.decode_ncm_file",
        lambda _input_path, _work_dir: (_ for _ in ()).throw(RuntimeError("stream failed")),
    )
    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.NeteaseCloudMusicFile", FallbackNeteaseFile)
    monkeypatch.setattr("src.Infrastructure.platforms.netease.adapter.detect_audio_container", lambda _path: ("mp3", "fast_header"))

    detail = adapter.decrypt_one(source, work_dir, {}, log_dir=log_dir)

    assert detail["backend"] == "python:ncmdump-py"
    assert detail["input_path"] == str(source)
    assert detail["metadata_type"] == "music"
    assert detail["metadata"]["musicName"] == "Fallback Song"
    assert detail["metadata"]["artist"] == [["Tester", 1]]
