from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.Infrastructure.config_repository import load_config
from src.Infrastructure.runtime_paths import RuntimePaths


def _runtime_paths(root: pathlib.Path) -> RuntimePaths:
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


class ConfigRepositoryTests(unittest.TestCase):
    def test_default_qq_output_rules_target_mp3_and_auto_transcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, config = load_config(_runtime_paths(pathlib.Path(temp_dir)))

            self.assertEqual(
                config["qq"]["format_rules"],
                {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"},
            )
            self.assertTrue(config["qq"]["auto_transcode_after_decode"])
            self.assertEqual(config["qq"]["transcode_bitrate_kbps"], 320)
            self.assertTrue(config["qq"]["qq_offline_musicex_enabled"])
            self.assertTrue(config["qq"]["qq_fetch_missing_ekey"])
            self.assertFalse(config["qq"]["qq_legacy_frida_enabled"])


if __name__ == "__main__":
    unittest.main()
