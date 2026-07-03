from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from src.Infrastructure import transcoder


class ProbeAudioContainerTests(unittest.TestCase):
    def test_probe_ignores_ffmpeg_container_guess_when_decode_fails(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stderr="Input #0, flac, from 'bad.flac':\ninvalid sync code\n",
        )

        with (
            mock.patch.object(transcoder, "resolve_ffmpeg_path", return_value=pathlib.Path("ffmpeg.exe")),
            mock.patch.object(subprocess, "run", return_value=completed),
        ):
            self.assertIsNone(transcoder.probe_audio_container(pathlib.Path("bad.flac")))

    def test_probe_accepts_container_when_ffmpeg_decode_succeeds(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stderr="Input #0, flac, from 'ok.flac':\n",
        )

        with (
            mock.patch.object(transcoder, "resolve_ffmpeg_path", return_value=pathlib.Path("ffmpeg.exe")),
            mock.patch.object(subprocess, "run", return_value=completed),
        ):
            self.assertEqual(transcoder.probe_audio_container(pathlib.Path("ok.flac")), "flac")

    def test_mp3_transcode_with_explicit_bitrate_uses_cbr_without_vbr_quality_arg(self) -> None:
        completed = SimpleNamespace(returncode=0, stderr="", stdout="")
        commands: list[list[str]] = []

        def capture_run(command: list[str], **_kwargs):
            commands.append(command)
            output_path = pathlib.Path(command[-1])
            output_path.write_bytes(b"mp3")
            return completed

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source.flac"
            output = root / "output.mp3"
            source.write_bytes(b"fLaC")
            with (
                mock.patch.object(transcoder, "resolve_ffmpeg_path", return_value=pathlib.Path("ffmpeg.exe")),
                mock.patch.object(subprocess, "run", side_effect=capture_run),
            ):
                transcoder.transcode_file(source, output, "mp3", bitrate_kbps=320)

        command = commands[0]
        self.assertIn("-b:a", command)
        self.assertIn("320k", command)
        self.assertNotIn("-q:a", command)


if __name__ == "__main__":
    unittest.main()
