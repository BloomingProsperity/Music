from __future__ import annotations

import pathlib
import tempfile
import unittest
import json

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
            self.assertEqual(config["shared"]["transcode_max_workers"], 2)
            self.assertTrue(config["qq"]["qq_fetch_missing_ekey"])
            self.assertTrue(config["qq"]["qq_cache_ekeys"])
            self.assertFalse(config["shared"]["delete_source_after_success"])
            self.assertIn("kuwo", config)
            self.assertEqual(config["kuwo"]["input_dir"], "")
            self.assertEqual(config["kuwo"]["output_dir"], str(pathlib.Path(temp_dir) / "output" / "kuwo"))
            self.assertEqual(config["kuwo"]["target_format_kwm"], "auto")
            self.assertFalse(config["kuwo"]["auto_transcode_after_decode"])
            self.assertIsNone(config["kuwo"]["transcode_sample_rate_hz"])
            self.assertIsNone(config["kuwo"]["transcode_bitrate_kbps"])

    def test_config_preserves_user_defined_transcode_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            paths.plugins_config.parent.mkdir(parents=True, exist_ok=True)
            paths.plugins_config.write_text(
                json.dumps(
                    {
                        "decrypt_cli": {
                            "shared": {"transcode_max_workers": 12},
                            "transcode_batch": {"max_workers": 9},
                        }
                    }
                ),
                encoding="utf-8",
            )

            _, config = load_config(paths)

            self.assertEqual(config["shared"]["transcode_max_workers"], 12)
            self.assertEqual(config["transcode_batch"]["max_workers"], 9)

    def test_config_normalizes_delete_source_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            paths.plugins_config.parent.mkdir(parents=True, exist_ok=True)
            paths.plugins_config.write_text(
                json.dumps(
                    {
                        "decrypt_cli": {
                            "shared": {"delete_source_after_success": "true"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            _, config = load_config(paths)

            self.assertIs(config["shared"]["delete_source_after_success"], True)


if __name__ == "__main__":
    unittest.main()
