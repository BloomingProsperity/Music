from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.Infrastructure.runtime_paths import RuntimePaths
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
                ]
            )

            self.assertEqual(args.sample_rate, 48000)
            self.assertEqual(args.bitrate, 320)


if __name__ == "__main__":
    unittest.main()
