from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from src.Infrastructure.runtime_paths import RuntimePaths
from src.Presentation import cli
from src.Presentation.cli import build_parser


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


class CliParserTests(unittest.TestCase):
    def test_decrypt_parser_accepts_transcode_profile_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            parser = build_parser(_runtime_paths(root))

            args = parser.parse_args(
                [
                    "qq",
                    "decrypt",
                    "--format-mflac",
                    "mp3",
                    "--sample-rate",
                    "48000",
                    "--bitrate",
                    "320",
                    "--transcode-workers",
                    "2",
                ]
            )

            self.assertEqual(args.sample_rate, 48000)
            self.assertEqual(args.bitrate, 320)
            self.assertEqual(args.transcode_workers, 2)

    def test_qq_decrypt_parser_accepts_local_mode_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            parser = build_parser(_runtime_paths(root))

            args = parser.parse_args(
                [
                    "qq",
                    "decrypt",
                    "--qq-no-fetch-ekey",
                    "--qq-ekey-cache-dir",
                    str(root / "cache"),
                    "--qq-legacy-frida",
                ]
            )

        self.assertTrue(args.qq_no_fetch_ekey)
        self.assertEqual(args.qq_ekey_cache_dir, str(root / "cache"))
        self.assertTrue(args.qq_legacy_frida)

    def test_qq_decrypt_cli_does_not_require_admin_for_default_local_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            config = {
                "shared": {
                    "output_dir": str(paths.output_dir),
                    "recursive": True,
                    "transcode_enabled": True,
                    "embed_cover_art": False,
                    "supplement_album_metadata": False,
                },
                "qq": {
                    "format_rules": {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"},
                    "qq_legacy_frida_enabled": False,
                },
                "kuwo": {},
                "kugou": {},
                "netease": {},
            }

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "is_running_as_admin", return_value=False),
                mock.patch.object(cli, "_run_platform", return_value=0) as run_platform,
            ):
                result = cli.main(["qq", "decrypt", "--input", str(root), "--output", str(root / "out")])

        self.assertEqual(result, 0)
        run_platform.assert_called_once()


if __name__ == "__main__":
    unittest.main()
