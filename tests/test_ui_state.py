from __future__ import annotations

import pathlib

import pytest

from src.Presentation.ui_state import (
    PlatformRunOptions,
    build_qq_batch_config,
    platform_specs,
    validate_writable_output_dir,
)


def test_platform_specs_keep_core_pages_and_formats() -> None:
    specs = platform_specs()

    assert [item.platform_id for item in specs] == ["qq", "kugou", "netease", "kuwo"]
    assert specs[0].source_extensions == (".mflac", ".mgg", ".mmp4")
    assert [control.key for control in specs[0].format_controls] == ["mflac", "mgg", "mmp4"]
    assert all(control.options == ("mp3", "flac", "m4a", "wav") for control in specs[0].format_controls)
    assert specs[1].source_extensions == (".kgm", ".kgma", ".kgg", ".vpr", ".kgm.flac", ".vpr.flac")
    assert specs[2].source_extensions == (".ncm",)
    assert specs[3].enabled is False


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
        sample_rate_hz=48000,
        bitrate_kbps=320,
        qq_fetch_missing_ekey=False,
        qq_cache_ekeys=True,
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
    assert batch_config.settings["qq_fetch_missing_ekey"] is False
    assert batch_config.settings["qq_cache_ekeys"] is True


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
