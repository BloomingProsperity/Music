from __future__ import annotations

import pathlib

from src.Application.sample_verification_service import SampleVerificationSummary
from src.Application.synthetic_self_test_service import run_synthetic_self_test
from src.Infrastructure.runtime_paths import RuntimePaths


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


def test_synthetic_self_test_generates_all_platform_inputs_and_runs_verification(tmp_path: pathlib.Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_sample_verification(**kwargs):
        captured.update(kwargs)
        input_root = pathlib.Path(kwargs["input_paths"][0])
        assert (input_root / "qq" / "local_e2e.mflac").exists()
        assert (input_root / "kugou" / "local_e2e.kgm").exists()
        assert (input_root / "kugou" / "kugou_key.xz").exists()
        assert (input_root / "netease" / "local_e2e.ncm").exists()
        assert (input_root / "kuwo" / "local_e2e.kwm").exists()
        assert (input_root / "kuwo" / "local_yeelion.kwma").exists()
        return SampleVerificationSummary([])

    monkeypatch.setattr("src.Application.synthetic_self_test_service.run_sample_verification", fake_run_sample_verification)

    summary = run_synthetic_self_test(
        output_dir=tmp_path / "self-test",
        config={"shared": {}, "qq": {}, "kugou": {}, "netease": {}, "kuwo": {}},
        paths=_paths(tmp_path),
        max_workers=1,
        bitrate_kbps=128,
    )

    assert isinstance(summary, SampleVerificationSummary)
    assert captured["platforms"] == ("qq", "kugou", "netease", "kuwo")
    assert captured["fresh"] is True
    kugou_settings = captured["config"]["kugou"]  # type: ignore[index]
    assert pathlib.Path(kugou_settings["key_file"]).name == "kugou_key.xz"
    assert captured["config"]["qq"]["qq_cache_ekeys"] is False  # type: ignore[index]
