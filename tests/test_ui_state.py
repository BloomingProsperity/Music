from __future__ import annotations

import pathlib

import pytest

from src.Presentation.ui_state import (
    PlatformRunOptions,
    build_platform_batch_config,
    build_qq_batch_config,
    platform_specs,
    validate_platform_runtime_for_ui,
    validate_writable_output_dir,
)


class _FakeAdapter:
    def __init__(self, *, ok: bool = True, reason: str | None = None, files: list[pathlib.Path] | None = None) -> None:
        self.ok = ok
        self.reason = reason
        self.files = files or []

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return self.ok, self.reason

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        return self.files


def test_platform_specs_keep_core_pages_and_formats() -> None:
    specs = platform_specs()

    assert [item.platform_id for item in specs] == ["qq", "kugou", "netease", "kuwo"]
    assert specs[0].source_extensions == (".mflac", ".mgg", ".mmp4")
    assert [control.key for control in specs[0].format_controls] == ["qq_output_format"]
    assert specs[0].format_controls[0].label == "输出格式"
    assert specs[0].format_controls[0].options == ("mp3", "flac", "m4a", "wav")
    assert specs[1].source_extensions == (".kgm", ".kgma", ".kgg", ".vpr", ".kgm.flac", ".vpr.flac")
    assert specs[2].source_extensions == (".ncm",)
    assert specs[3].source_extensions == (".kwm", ".kwma", ".kwm.flac")
    assert [control.key for control in specs[3].format_controls] == ["target_format_kwm"]
    assert specs[1].enabled is True
    assert specs[2].enabled is True
    assert specs[3].enabled is True
    assert specs[3].status_text == "可用"


def test_platform_specs_do_not_put_development_explanations_in_ui_text() -> None:
    forbidden = ("待升级", "开发", "后续", "旧运行期", "Frida", "原理", "解释")

    for spec in platform_specs():
        visible_text = " ".join([spec.title, spec.subtitle, spec.status_text])
        assert not any(word in visible_text for word in forbidden)


def test_build_qq_batch_config_preserves_paths_formats_and_transcode_options() -> None:
    options = PlatformRunOptions(
        input_path=pathlib.Path(r"C:\music\qq"),
        output_dir=pathlib.Path(r"C:\music\mp3"),
        recursive=True,
        transcode_enabled=True,
        transcode_max_workers=3,
        embed_cover_art=False,
        supplement_album_metadata=False,
        delete_source_after_success=True,
        sample_rate_hz=48000,
        bitrate_kbps=320,
        qq_fetch_missing_ekey=False,
        qq_cache_ekeys=True,
        platform_settings={"qq_auto_launch_client": True},
        format_rules={"mflac": "mp3", "mgg": "mp3", "mmp4": "m4a"},
    )

    batch_config = build_qq_batch_config(options)

    assert batch_config.platform_id == "qq"
    assert batch_config.input_path == pathlib.Path(r"C:\music\qq")
    assert batch_config.output_dir == pathlib.Path(r"C:\music\mp3")
    assert batch_config.recursive is True
    assert batch_config.collision_policy == "suffix"
    assert batch_config.settings["format_rules"] == {"mflac": "mp3", "mgg": "mp3", "mmp4": "m4a"}
    assert batch_config.settings["transcode_enabled"] is True
    assert batch_config.settings["transcode_max_workers"] == 3
    assert batch_config.settings["transcode_sample_rate_hz"] == 48000
    assert batch_config.settings["transcode_bitrate_kbps"] == 320
    assert batch_config.settings["embed_cover_art"] is False
    assert batch_config.settings["supplement_album_metadata"] is False
    assert batch_config.settings["delete_source_after_success"] is True
    assert batch_config.settings["qq_fetch_missing_ekey"] is False
    assert batch_config.settings["qq_cache_ekeys"] is True
    assert batch_config.settings["qq_auto_launch_client"] is True


def test_build_qq_batch_config_does_not_cap_user_transcode_parallelism() -> None:
    options = PlatformRunOptions(
        input_path=pathlib.Path(r"C:\music\qq"),
        output_dir=pathlib.Path(r"C:\music\mp3"),
        transcode_max_workers=12,
    )

    batch_config = build_qq_batch_config(options)

    assert batch_config.settings["transcode_max_workers"] == 12


def test_build_platform_batch_config_preserves_netease_settings_and_callbacks() -> None:
    seen: list[tuple[str, dict]] = []
    options = PlatformRunOptions(
        input_path=pathlib.Path(r"C:\music\netease"),
        output_dir=pathlib.Path(r"C:\music\out"),
        recursive=False,
        transcode_enabled=True,
        transcode_max_workers=4,
        embed_cover_art=True,
        supplement_album_metadata=True,
        delete_source_after_success=True,
        sample_rate_hz=44100,
        bitrate_kbps=256,
        platform_settings={"target_format_ncm": "mp3"},
        event_sink=lambda event, payload: seen.append((event, payload)),
        stop_requested=lambda: False,
    )

    batch_config = build_platform_batch_config("netease", options)

    assert batch_config.platform_id == "netease"
    assert batch_config.input_path == pathlib.Path(r"C:\music\netease")
    assert batch_config.output_dir == pathlib.Path(r"C:\music\out")
    assert batch_config.recursive is False
    assert batch_config.settings["target_format_ncm"] == "mp3"
    assert batch_config.settings["transcode_enabled"] is True
    assert batch_config.settings["transcode_max_workers"] == 4
    assert batch_config.settings["embed_cover_art"] is True
    assert batch_config.settings["supplement_album_metadata"] is True
    assert batch_config.settings["delete_source_after_success"] is True
    assert batch_config.settings["transcode_sample_rate_hz"] == 44100
    assert batch_config.settings["transcode_bitrate_kbps"] == 256
    assert batch_config.event_sink is options.event_sink
    assert batch_config.stop_requested is options.stop_requested


def test_build_platform_batch_config_preserves_kugou_key_and_db_settings() -> None:
    options = PlatformRunOptions(
        input_path=pathlib.Path(r"C:\music\kugou"),
        output_dir=pathlib.Path(r"C:\music\out"),
        recursive=True,
        transcode_enabled=False,
        transcode_max_workers=1,
        sample_rate_hz=None,
        bitrate_kbps=None,
        platform_settings={
            "target_format_kgma": "flac",
            "target_format_kgg": "mp3",
            "key_file": r"C:\keys\kugou_key.xz",
            "kgg_db_path": r"C:\keys\KGMusicV3.db",
        },
    )

    batch_config = build_platform_batch_config("kugou", options)

    assert batch_config.platform_id == "kugou"
    assert batch_config.input_path == pathlib.Path(r"C:\music\kugou")
    assert batch_config.output_dir == pathlib.Path(r"C:\music\out")
    assert batch_config.recursive is True
    assert batch_config.settings["target_format_kgma"] == "flac"
    assert batch_config.settings["target_format_kgg"] == "mp3"
    assert batch_config.settings["key_file"] == r"C:\keys\kugou_key.xz"
    assert batch_config.settings["kgg_db_path"] == r"C:\keys\KGMusicV3.db"
    assert batch_config.settings["transcode_enabled"] is False
    assert batch_config.settings["transcode_max_workers"] == 1


def test_build_platform_batch_config_preserves_kuwo_settings_and_callbacks() -> None:
    seen: list[tuple[str, dict]] = []
    options = PlatformRunOptions(
        input_path=pathlib.Path(r"C:\music\kuwo"),
        output_dir=pathlib.Path(r"C:\music\out"),
        recursive=False,
        transcode_enabled=True,
        transcode_max_workers=5,
        sample_rate_hz=48000,
        bitrate_kbps=192,
        platform_settings={"target_format_kwm": "mp3"},
        event_sink=lambda event, payload: seen.append((event, payload)),
        stop_requested=lambda: False,
    )

    batch_config = build_platform_batch_config("kuwo", options)

    assert batch_config.platform_id == "kuwo"
    assert batch_config.input_path == pathlib.Path(r"C:\music\kuwo")
    assert batch_config.output_dir == pathlib.Path(r"C:\music\out")
    assert batch_config.recursive is False
    assert batch_config.settings["target_format_kwm"] == "mp3"
    assert batch_config.settings["transcode_enabled"] is True
    assert batch_config.settings["transcode_max_workers"] == 5
    assert batch_config.settings["transcode_sample_rate_hz"] == 48000
    assert batch_config.settings["transcode_bitrate_kbps"] == 192
    assert batch_config.event_sink is options.event_sink
    assert batch_config.stop_requested is options.stop_requested


def test_validate_writable_output_dir_creates_and_cleans_probe_file(tmp_path: pathlib.Path) -> None:
    output_dir = tmp_path / "music-output"

    validate_writable_output_dir(output_dir)

    assert output_dir.is_dir()
    assert not list(output_dir.glob(".qkk-write-test-*"))


def test_validate_writable_output_dir_rejects_file_path(tmp_path: pathlib.Path) -> None:
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        validate_writable_output_dir(output_file)


def test_validate_platform_runtime_for_ui_accepts_netease_with_writable_output(tmp_path: pathlib.Path) -> None:
    input_dir = tmp_path / "ncm"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    result = validate_platform_runtime_for_ui(
        "netease",
        _FakeAdapter(),
        {"target_format_ncm": "mp3"},
        input_dir,
        output_dir,
        recursive=True,
    )

    assert result.ok is True
    assert result.reason is None
    assert output_dir.is_dir()


def test_validate_platform_runtime_for_ui_rejects_adapter_runtime_error(tmp_path: pathlib.Path) -> None:
    input_dir = tmp_path / "kgm"
    output_dir = tmp_path / "out"
    input_dir.mkdir()

    result = validate_platform_runtime_for_ui(
        "kugou",
        _FakeAdapter(ok=False, reason="missing key"),
        {},
        input_dir,
        output_dir,
        recursive=True,
    )

    assert result.ok is False
    assert result.reason == "missing key"


def test_validate_platform_runtime_for_ui_rejects_kgg_without_database(tmp_path: pathlib.Path, monkeypatch) -> None:
    input_dir = tmp_path / "kgm"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    kgg_file = input_dir / "song.kgg"
    kgg_file.write_bytes(b"kgg")
    monkeypatch.setattr("src.Presentation.ui_state.auto_find_kgg_db_path", lambda: None)

    result = validate_platform_runtime_for_ui(
        "kugou",
        _FakeAdapter(files=[kgg_file]),
        {"kgg_db_path": str(tmp_path / "missing.db")},
        input_dir,
        output_dir,
        recursive=True,
    )

    assert result.ok is False
    assert "KGMusicV3.db" in str(result.reason)
