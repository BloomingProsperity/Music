from __future__ import annotations

import io
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

    def test_decrypt_parser_allows_user_defined_transcode_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            parser = build_parser(_runtime_paths(root))

            args = parser.parse_args(["qq", "decrypt", "--transcode-workers", "8"])

            self.assertEqual(args.transcode_workers, 8)

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

    def test_sample_verify_parser_accepts_inputs_platforms_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            parser = build_parser(_runtime_paths(root))

            args = parser.parse_args(
                [
                    "sample-verify",
                    "--input",
                    r"C:\music",
                    "--input",
                    r"D:\music",
                    "--output",
                    r"C:\qkk_verify",
                    "--platform",
                    "netease",
                    "--platform",
                    "kuwo",
                    "--bitrate",
                    "320",
                    "--max-workers",
                    "2",
                    "--no-recursive",
                    "--fresh",
                ]
            )

        self.assertEqual(args.platform, "sample-verify")
        self.assertEqual(args.input, [r"C:\music", r"D:\music"])
        self.assertEqual(args.output, r"C:\qkk_verify")
        self.assertEqual(args.verify_platform, ["netease", "kuwo"])
        self.assertEqual(args.bitrate, 320)
        self.assertEqual(args.max_workers, 2)
        self.assertTrue(args.no_recursive)
        self.assertTrue(args.fresh)

    def test_sample_verify_cli_returns_service_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            config = {"shared": {}, "qq": {}, "kugou": {}, "netease": {}, "kuwo": {}}
            summary = mock.Mock(exit_code=3, results=[], total_candidates=0, verified_count=0, failed_count=0)

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "run_sample_verification", return_value=summary) as verify,
                mock.patch.object(
                    cli,
                    "write_sample_verification_reports",
                    return_value=(root / "out" / "sample_verify_report.json", root / "out" / "sample_verify_report.txt"),
                ) as write_reports,
            ):
                result = cli.main(
                    [
                        "sample-verify",
                        "--input",
                        str(root),
                        "--output",
                        str(root / "out"),
                        "--platform",
                        "netease",
                        "--fresh",
                    ]
                )

        self.assertEqual(result, 3)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs["input_paths"], [pathlib.Path(root)])
        self.assertEqual(verify.call_args.kwargs["output_dir"], pathlib.Path(root / "out"))
        self.assertEqual(verify.call_args.kwargs["platforms"], ("netease",))
        self.assertTrue(verify.call_args.kwargs["fresh"])
        write_reports.assert_called_once_with(summary, pathlib.Path(root / "out"))

    def test_sample_verify_cli_reports_partial_missing_samples_distinctly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            paths = _runtime_paths(root)
            config = {"shared": {}, "qq": {}, "kugou": {}, "netease": {}, "kuwo": {}}
            verified = mock.Mock(
                platform_id="netease",
                status="verified",
                input_path=root,
                verified_outputs=[root / "out" / "song.mp3"],
            )
            missing = mock.Mock(
                platform_id="kugou",
                status="not_found",
                input_path=root,
                verified_outputs=[],
            )
            summary = mock.Mock(exit_code=3, results=[verified, missing], total_candidates=1, verified_count=1, failed_count=0)

            with (
                mock.patch.object(cli.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(cli, "load_config", return_value=({}, config)),
                mock.patch.object(cli, "run_sample_verification", return_value=summary),
                mock.patch.object(
                    cli,
                    "write_sample_verification_reports",
                    return_value=(root / "out" / "sample_verify_report.json", root / "out" / "sample_verify_report.txt"),
                ),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                result = cli.main(
                    [
                        "sample-verify",
                        "--input",
                        str(root),
                        "--output",
                        str(root / "out"),
                        "--platform",
                        "all",
                    ]
                )

        self.assertEqual(result, 3)
        output = stdout.getvalue()
        self.assertIn("网易云音乐: 验证通过 1 个输出", output)
        self.assertIn("酷狗音乐: 未发现样本", output)
        self.assertIn("部分平台未发现样本，不能视为全部平台真实样本验证完成。", output)
        self.assertNotIn("未找到可验证样本，不能视为平台真实样本验证完成。", output)


if __name__ == "__main__":
    unittest.main()
