from __future__ import annotations

import pathlib

from src.Infrastructure.platforms.kugou.adapter import KugouPlatformAdapter


def test_kugou_adapter_predicts_declared_flac_suffix_for_auto_target() -> None:
    adapter = KugouPlatformAdapter()

    assert adapter.predicted_extension(pathlib.Path("song.kgm.flac"), {"target_format_kgma": "auto"}) == "flac"
    assert adapter.predicted_extension(pathlib.Path("song.vpr.flac"), {"target_format_kgma": "auto"}) == "flac"

