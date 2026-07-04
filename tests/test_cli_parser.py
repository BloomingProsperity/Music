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
                ]
            )

        self.assertTrue(args.qq_no_fetch_ekey)
        self.assertEqual(args.qq_ekey_cache_dir, str(root / "cache"))

    def test_qq_decrypt_cli_uses_local_mode_settings(self) -> None:
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
                },
                "kugou": {},
                "netease": {},
                "kuwo": {},
            }

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "_run_platform", return_value=0) as run_platform,
            ):
                result = cli.main(["qq", "decrypt", "--input", str(root), "--output", str(root / "out")])

        self.assertEqual(result, 0)
        run_platform.assert_called_once()

    def test_explicit_cli_transcode_enables_noninteractive_batch_transcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            config = {
                "shared": {
                    "output_dir": str(paths.output_dir),
                    "recursive": True,
                    "transcode_enabled": False,
                    "embed_cover_art": False,
                    "supplement_album_metadata": False,
                },
                "qq": {},
                "kugou": {},
                "netease": {"auto_transcode_after_decode": False},
                "kuwo": {},
            }

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "_run_platform", return_value=0) as run_platform,
            ):
                result = cli.main(
                    [
                        "netease",
                        "decrypt",
                        "--input",
                        str(root),
                        "--output",
                        str(root / "out"),
                        "--format-ncm",
                        "mp3",
                        "--transcode",
                    ]
                )

        self.assertEqual(result, 0)
        passed_config = run_platform.call_args.args[1]
        self.assertTrue(passed_config["shared"]["transcode_enabled"])
        self.assertEqual(passed_config["netease"]["target_format_ncm"], "mp3")
        self.assertTrue(passed_config["netease"]["auto_transcode_after_decode"])

    def test_explicit_cli_target_format_reenables_shared_transcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            config = {
                "shared": {
                    "output_dir": str(paths.output_dir),
                    "recursive": True,
                    "transcode_enabled": False,
                    "embed_cover_art": False,
                    "supplement_album_metadata": False,
                },
                "qq": {},
                "kugou": {},
                "netease": {"auto_transcode_after_decode": False},
                "kuwo": {},
            }

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "_run_platform", return_value=0) as run_platform,
            ):
                result = cli.main(
                    [
                        "netease",
                        "decrypt",
                        "--input",
                        str(root),
                        "--output",
                        str(root / "out"),
                        "--format-ncm",
                        "mp3",
                    ]
                )

        self.assertEqual(result, 0)
        passed_config = run_platform.call_args.args[1]
        self.assertTrue(passed_config["shared"]["transcode_enabled"])
        self.assertEqual(passed_config["netease"]["target_format_ncm"], "mp3")
        self.assertTrue(passed_config["netease"]["auto_transcode_after_decode"])

    def test_kuwo_decrypt_parser_accepts_kwm_format_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            parser = build_parser(_runtime_paths(root))

            for target_format in ("auto", "mp3", "flac", "m4a", "wav"):
                args = parser.parse_args(["kuwo", "decrypt", "--format-kwm", target_format])

                self.assertEqual(args.platform, "kuwo")
                self.assertEqual(args.command, "decrypt")
                self.assertEqual(args.format_kwm, target_format)


if __name__ == "__main__":
    unittest.main()
