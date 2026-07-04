from __future__ import annotations

import logging
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

from src.Application import decrypt_service
from src.Application.decrypt_service import (
    _PreparedArtifact,
    _artifact_needs_transcode,
    _artist_from_summary_or_filename,
    _maybe_transcode,
    _resolve_publish_target,
    _resolve_batch_transcode_choice,
    _transcode_worker_count,
)
from src.Application.models import BatchRunConfig
from src.Infrastructure.runtime_paths import RuntimePaths


class TranscodeChoiceTests(unittest.TestCase):
    def test_flac_target_is_reencoded_even_when_detected_as_flac(self) -> None:
        self.assertTrue(_artifact_needs_transcode("flac", "flac"))
        self.assertFalse(_artifact_needs_transcode("mp3", "mp3"))

    def test_same_suffix_flac_reencode_uses_distinct_working_path(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.flac"
            source.write_bytes(b"fLaC")

            seen: dict[str, pathlib.Path] = {}

            def fake_transcode(input_path: pathlib.Path, output_path: pathlib.Path, target_format: str, **_kwargs):
                seen["input"] = input_path
                seen["output"] = output_path
                self.assertNotEqual(input_path, output_path)
                output_path.write_bytes(b"fLaC-clean")
                return {"output_path": str(output_path), "return_code": 0}

            with mock.patch.object(decrypt_service, "transcode_file", side_effect=fake_transcode):
                working_path, final_extension, meta = _maybe_transcode(
                    logging.getLogger("test"),
                    pathlib.Path("song.mflac"),
                    "flac",
                    source,
                    "flac",
                    {},
                )

            self.assertEqual(final_extension, "flac")
            self.assertIsNotNone(meta)
            self.assertNotEqual(working_path, source)
            self.assertFalse(source.exists())
            self.assertEqual(working_path.read_bytes(), b"fLaC-clean")
            self.assertEqual(seen["input"], source)
            self.assertEqual(seen["output"], working_path)

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

    def test_transcode_worker_count_keeps_user_defined_parallelism(self) -> None:
        self.assertEqual(_transcode_worker_count({"transcode_max_workers": 12}), 12)

    def test_artist_grouping_uses_netease_artist_names_instead_of_raw_arrays(self) -> None:
        artist = _artist_from_summary_or_filename(
            {
                "metadata": {
                    "artist": [["Tester", 1001], ["Guest", 1002]],
                }
            },
            pathlib.Path("Tester - Local E2E.ncm"),
        )

        self.assertEqual(artist, "Tester、Guest")

    def test_publish_target_can_group_by_artist(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = pathlib.Path(temp_dir)
            config = BatchRunConfig(
                platform_id="qq",
                input_path=root,
                output_dir=root / "out",
                recursive=False,
                collision_policy="suffix",
                settings={"group_by_artist": True},
            )
            manifest = mock.Mock()
            manifest.get_platform.return_value = None

            target, mode, _existing = _resolve_publish_target(
                base_name="晴天",
                input_path=pathlib.Path("周杰伦 - 晴天.mflac"),
                extension="mp3",
                platform_id="qq",
                output_dir=root / "out",
                manifest_repo=manifest,
                config=config,
                artist_name="周杰伦",
            )

        self.assertEqual(target, root / "out" / "周杰伦" / "晴天.mp3")
        self.assertEqual(mode, "direct")

    def test_run_batch_transcodes_prepared_artifacts_with_configured_parallelism(self) -> None:
        class FakeAdapter:
            platform_id = "qq"
            display_name = "QQ音乐"

            def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
                return sorted(input_path.glob("*.mflac"))

            def output_basename(self, input_path: pathlib.Path) -> str:
                return input_path.stem

            def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
                return "mp3"

            def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
                return "mp3"

            def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
                output_path = work_dir / f"{input_path.stem}.flac"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fLaC" + b"\0" * 2048)
                return {
                    "output_path": str(output_path),
                    "detected_container": "flac",
                    "final_extension": "flac",
                    "recognition_stage": "test",
                    "backend": "test",
                    "decoded_bytes": output_path.stat().st_size,
                    "timing": {"stream_decode_sec": 0.001, "total_sec": 0.001},
                }

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = pathlib.Path(temp_dir)
            input_dir = root / "in"
            output_dir = root / "out"
            input_dir.mkdir()
            (input_dir / "one.mflac").write_bytes(b"encrypted")
            (input_dir / "two.mflac").write_bytes(b"encrypted")
            paths = RuntimePaths(
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
            active = 0
            max_active = 0
            lock = threading.Lock()

            def fake_transcode(input_path: pathlib.Path, output_path: pathlib.Path, target_format: str, **_kwargs):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.1)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"ID3" + input_path.name.encode("utf-8"))
                with lock:
                    active -= 1
                return {"output_path": str(output_path), "return_code": 0}

            config = BatchRunConfig(
                platform_id="qq",
                input_path=input_dir,
                output_dir=output_dir,
                recursive=False,
                collision_policy="suffix",
                settings={
                    "transcode_enabled": True,
                    "auto_transcode_after_decode": True,
                    "transcode_max_workers": 2,
                    "embed_cover_art": False,
                },
            )

            with (
                mock.patch.object(decrypt_service.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(decrypt_service, "transcode_file", side_effect=fake_transcode),
            ):
                result_code = decrypt_service.run_batch(config, FakeAdapter())

        self.assertEqual(result_code, 0)
        self.assertGreaterEqual(max_active, 2)

    def test_run_batch_starts_transcoding_after_pipeline_threshold(self) -> None:
        class FakeAdapter:
            platform_id = "qq"
            display_name = "QQ音乐"

            def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
                return sorted(input_path.glob("*.mflac"))

            def output_basename(self, input_path: pathlib.Path) -> str:
                return input_path.stem

            def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
                return "mp3"

            def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
                return "mp3"

            def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
                output_path = work_dir / f"{input_path.stem}.flac"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fLaC" + b"\0" * 2048)
                return {
                    "output_path": str(output_path),
                    "detected_container": "flac",
                    "final_extension": "flac",
                    "recognition_stage": "test",
                    "backend": "test",
                    "decoded_bytes": output_path.stat().st_size,
                    "timing": {"stream_decode_sec": 0.001, "total_sec": 0.001},
                }

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = pathlib.Path(temp_dir)
            input_dir = root / "in"
            output_dir = root / "out"
            input_dir.mkdir()
            for index in range(12):
                (input_dir / f"{index:02d}.mflac").write_bytes(b"encrypted")
            paths = RuntimePaths(
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
            decrypted_count = 0
            first_transcode_after_decrypts: int | None = None
            lock = threading.Lock()

            def sink(event_name: str, _payload: dict) -> None:
                nonlocal decrypted_count
                if event_name == "file_decrypted":
                    with lock:
                        decrypted_count += 1

            def fake_transcode(input_path: pathlib.Path, output_path: pathlib.Path, target_format: str, **_kwargs):
                nonlocal first_transcode_after_decrypts
                with lock:
                    if first_transcode_after_decrypts is None:
                        first_transcode_after_decrypts = decrypted_count
                time.sleep(0.05)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"ID3" + input_path.name.encode("utf-8"))
                return {"output_path": str(output_path), "return_code": 0}

            config = BatchRunConfig(
                platform_id="qq",
                input_path=input_dir,
                output_dir=output_dir,
                recursive=False,
                collision_policy="suffix",
                settings={
                    "transcode_enabled": True,
                    "auto_transcode_after_decode": True,
                    "transcode_max_workers": 2,
                    "embed_cover_art": False,
                },
                event_sink=sink,
            )

            with (
                mock.patch.object(decrypt_service.RuntimePaths, "discover", return_value=paths),
                mock.patch.object(decrypt_service, "transcode_file", side_effect=fake_transcode),
            ):
                result_code = decrypt_service.run_batch(config, FakeAdapter())

        self.assertEqual(result_code, 0)
        self.assertEqual(first_transcode_after_decrypts, 11)


if __name__ == "__main__":
    unittest.main()
