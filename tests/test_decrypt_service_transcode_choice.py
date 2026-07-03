from __future__ import annotations

import logging
import pathlib
import unittest

from src.Application.decrypt_service import _PreparedArtifact, _resolve_batch_transcode_choice
from src.Application.models import BatchRunConfig


class TranscodeChoiceTests(unittest.TestCase):
    def test_auto_transcode_still_runs_for_successful_artifacts_when_other_files_failed(self) -> None:
        artifact = _PreparedArtifact(
            index=1,
            total_count=2,
            input_path=pathlib.Path("ok.mflac"),
            basename="ok",
            desired_target="mp3",
            file_started=0.0,
            working_path=pathlib.Path("ok.flac"),
            detected_container="flac",
            detail={},
            decrypt_detail_timing={},
            file_timing={},
        )
        config = BatchRunConfig(
            platform_id="qq",
            input_path=pathlib.Path("."),
            output_dir=pathlib.Path("out"),
            recursive=False,
            collision_policy="suffix",
            settings={
                "transcode_enabled": True,
                "auto_transcode_after_decode": True,
            },
        )

        should_transcode, pending = _resolve_batch_transcode_choice(
            logging.getLogger("test"),
            config,
            [artifact],
            failed_count=1,
            stopped_early=False,
        )

        self.assertTrue(should_transcode)
        self.assertEqual(pending, [artifact])


if __name__ == "__main__":
    unittest.main()
