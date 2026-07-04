from __future__ import annotations

import json
import pathlib
import subprocess
from types import SimpleNamespace

from src.Application.models import BatchRunConfig
from src.Application.sample_verification_service import (
    SamplePlatformResult,
    SampleVerificationSummary,
    _strict_decode,
    run_sample_verification,
    write_sample_verification_reports,
)
from src.Infrastructure.runtime_paths import RuntimePaths


class _FakeAdapter:
    display_name = "Fake"

    def __init__(self, platform_id: str, files: list[pathlib.Path] | None = None, runtime_ok: bool = True) -> None:
        self.platform_id = platform_id
        self.files = files or []
        self.runtime_ok = runtime_ok

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return self.runtime_ok, None if self.runtime_ok else "runtime missing"

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        return [item for item in self.files if item.is_relative_to(input_path)]


def _paths(root: pathlib.Path) -> RuntimePaths:
    return RuntimePaths(
        root_dir=root,
        bundle_dir=root,
        assets_dir=root / "assets",
        plugins_dir=root / "plugins",
        log_dir=root / "_log",
        output_dir=root / "output",
        docs_dir=root / "_docs",
        plugins_config=root / "plugins" / "plugins.json",
        output_manifest=root / "plugins" / "output_manifest.json",
    )


def test_sample_verification_returns_unverified_when_no_samples_are_found(tmp_path: pathlib.Path, monkeypatch) -> None:
    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        return _FakeAdapter(platform_id)

    monkeypatch.setattr("src.Application.sample_verification_service.build_platform_adapter", fake_build_platform_adapter)

    summary = run_sample_verification(
        input_paths=[tmp_path],
        output_dir=tmp_path / "out",
        config={"shared": {}, "netease": {}, "kuwo": {}},
        paths=_paths(tmp_path),
        platforms=("netease", "kuwo"),
    )

    assert summary.exit_code == 3
    assert summary.total_candidates == 0
    assert summary.verified_count == 0
    assert [item.status for item in summary.results] == ["not_found", "not_found"]


def test_sample_verification_returns_unverified_when_any_requested_platform_has_no_samples() -> None:
    summary = SampleVerificationSummary(
        [
            SamplePlatformResult("netease", pathlib.Path("C:/music"), 1, "verified", verified_outputs=[pathlib.Path("C:/out/song.mp3")]),
            SamplePlatformResult("kuwo", pathlib.Path("C:/music"), 0, "not_found"),
        ]
    )

    assert summary.exit_code == 3


def test_sample_verification_runs_batch_and_strict_decodes_outputs(tmp_path: pathlib.Path, monkeypatch) -> None:
    source = tmp_path / "song.ncm"
    source.write_bytes(b"encrypted")
    output = tmp_path / "out" / "netease" / "song.mp3"

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        return _FakeAdapter(platform_id, [source])

    def fake_run_batch(batch_config: BatchRunConfig, _adapter: _FakeAdapter) -> int:
        assert batch_config.platform_id == "netease"
        assert batch_config.settings["target_format_ncm"] == "mp3"
        assert batch_config.settings["transcode_enabled"] is True
        assert batch_config.settings["auto_transcode_after_decode"] is True
        output.parent.mkdir(parents=True)
        output.write_bytes(b"ID3")
        assert batch_config.event_sink is not None
        batch_config.event_sink("file_finished", {"result": "success", "output_path": str(output)})
        return 0

    monkeypatch.setattr("src.Application.sample_verification_service.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Application.sample_verification_service.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Application.sample_verification_service.resolve_ffmpeg_path", lambda _paths: pathlib.Path("ffmpeg.exe"))
    monkeypatch.setattr("src.Application.sample_verification_service._strict_decode", lambda _ffmpeg, _path: SimpleNamespace(ok=True, reason=""))

    summary = run_sample_verification(
        input_paths=[tmp_path],
        output_dir=tmp_path / "out",
        config={"shared": {}, "netease": {}},
        paths=_paths(tmp_path),
        platforms=("netease",),
    )

    assert summary.exit_code == 0
    assert summary.total_candidates == 1
    assert summary.verified_count == 1
    assert summary.results[0].status == "verified"
    assert summary.results[0].verified_outputs == [output]


def test_sample_verification_strict_decodes_reused_outputs(tmp_path: pathlib.Path, monkeypatch) -> None:
    source = tmp_path / "song.ncm"
    source.write_bytes(b"encrypted")
    output = tmp_path / "out" / "netease" / "song.mp3"

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        return _FakeAdapter(platform_id, [source])

    def fake_run_batch(batch_config: BatchRunConfig, _adapter: _FakeAdapter) -> int:
        output.parent.mkdir(parents=True)
        output.write_bytes(b"old")
        assert batch_config.event_sink is not None
        batch_config.event_sink("file_finished", {"result": "already_decrypted", "output_path": str(output)})
        return 0

    monkeypatch.setattr("src.Application.sample_verification_service.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Application.sample_verification_service.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Application.sample_verification_service.resolve_ffmpeg_path", lambda _paths: pathlib.Path("ffmpeg.exe"))
    monkeypatch.setattr("src.Application.sample_verification_service._strict_decode", lambda _ffmpeg, _path: SimpleNamespace(ok=True, reason=""))

    summary = run_sample_verification(
        input_paths=[tmp_path],
        output_dir=tmp_path / "out",
        config={"shared": {}, "netease": {}},
        paths=_paths(tmp_path),
        platforms=("netease",),
    )

    assert summary.exit_code == 0
    assert summary.verified_count == 1
    assert summary.results[0].status == "verified"
    assert summary.results[0].verified_outputs == [output]


def test_sample_verification_fresh_mode_removes_platform_output_before_batch(tmp_path: pathlib.Path, monkeypatch) -> None:
    source = tmp_path / "song.ncm"
    source.write_bytes(b"encrypted")
    stale = tmp_path / "out" / "netease" / "old.mp3"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    output = tmp_path / "out" / "netease" / "song.mp3"

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        return _FakeAdapter(platform_id, [source])

    def fake_run_batch(batch_config: BatchRunConfig, _adapter: _FakeAdapter) -> int:
        assert not stale.exists()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"new")
        assert batch_config.event_sink is not None
        batch_config.event_sink("file_finished", {"result": "success", "output_path": str(output)})
        return 0

    monkeypatch.setattr("src.Application.sample_verification_service.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Application.sample_verification_service.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Application.sample_verification_service.resolve_ffmpeg_path", lambda _paths: pathlib.Path("ffmpeg.exe"))
    monkeypatch.setattr("src.Application.sample_verification_service._strict_decode", lambda _ffmpeg, _path: SimpleNamespace(ok=True, reason=""))

    summary = run_sample_verification(
        input_paths=[tmp_path],
        output_dir=tmp_path / "out",
        config={"shared": {}, "netease": {}},
        paths=_paths(tmp_path),
        platforms=("netease",),
        fresh=True,
    )

    assert summary.exit_code == 0
    assert summary.results[0].verified_outputs == [output]


def test_sample_verification_fails_when_strict_decode_rejects_output(tmp_path: pathlib.Path, monkeypatch) -> None:
    source = tmp_path / "song.kwm"
    source.write_bytes(b"encrypted")
    output = tmp_path / "out" / "kuwo" / "song.mp3"

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        return _FakeAdapter(platform_id, [source])

    def fake_run_batch(batch_config: BatchRunConfig, _adapter: _FakeAdapter) -> int:
        output.parent.mkdir(parents=True)
        output.write_bytes(b"bad")
        assert batch_config.event_sink is not None
        batch_config.event_sink("file_finished", {"result": "success", "output_path": str(output)})
        return 0

    monkeypatch.setattr("src.Application.sample_verification_service.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Application.sample_verification_service.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Application.sample_verification_service.resolve_ffmpeg_path", lambda _paths: pathlib.Path("ffmpeg.exe"))
    monkeypatch.setattr("src.Application.sample_verification_service._strict_decode", lambda _ffmpeg, _path: SimpleNamespace(ok=False, reason="invalid mp3"))

    summary = run_sample_verification(
        input_paths=[tmp_path],
        output_dir=tmp_path / "out",
        config={"shared": {}, "kuwo": {}},
        paths=_paths(tmp_path),
        platforms=("kuwo",),
    )

    assert summary.exit_code == 2
    assert summary.verified_count == 0
    assert summary.results[0].status == "failed"
    assert "invalid mp3" in summary.results[0].reason


def test_strict_decode_uses_xerror_so_ffmpeg_warnings_cannot_be_ignored(tmp_path: pathlib.Path, monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _strict_decode(pathlib.Path("ffmpeg.exe"), tmp_path / "song.mp3")

    assert result.ok is True
    assert "-xerror" in captured["command"]


def test_write_sample_verification_reports_persists_json_and_text(tmp_path: pathlib.Path) -> None:
    verified = tmp_path / "out" / "song.mp3"
    summary = SampleVerificationSummary(
        [
            SamplePlatformResult(
                "netease",
                tmp_path / "music",
                1,
                "verified",
                result_code=0,
                verified_outputs=[verified],
            ),
            SamplePlatformResult("kuwo", tmp_path / "music", 0, "not_found"),
        ]
    )

    json_path, text_path = write_sample_verification_reports(summary, tmp_path / "reports")

    assert json_path.name == "sample_verify_report.json"
    assert text_path.name == "sample_verify_report.txt"
    assert json_path.exists()
    assert text_path.exists()
    json_text = json_path.read_text(encoding="utf-8")
    text = text_path.read_text(encoding="utf-8")
    report = json.loads(json_text)
    assert '"exit_code": 3' in json_text
    assert '"platform_id": "netease"' in json_text
    assert report["results"][0]["verified_outputs"] == [str(verified)]
    assert "netease verified candidates=1 verified_outputs=1" in text
    assert "kuwo not_found candidates=0 verified_outputs=0" in text
