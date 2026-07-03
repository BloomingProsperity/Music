from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.Infrastructure.platforms.qq.adapter import QQPlatformAdapter
from src.Infrastructure.platforms.qq.decrypt_artifact import (
    MUSICEX_TRAILER_LEN,
    QQArtifactState,
    inspect_decrypt_artifact,
)


def _musicex_source_bytes(payload_size: int = 2048) -> bytes:
    payload = bytes((index % 251 for index in range(payload_size)))
    trailer = b"\0" * (MUSICEX_TRAILER_LEN - len(b"musicex\0")) + b"musicex\0"
    return payload + trailer


class QQDecryptArtifactTests(unittest.TestCase):
    def test_inspect_marks_legacy_passthrough_when_output_is_source_without_musicex_trailer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            output = root / "song.flac"
            encrypted = _musicex_source_bytes()
            source.write_bytes(encrypted)
            output.write_bytes(encrypted[:-MUSICEX_TRAILER_LEN])

            state = inspect_decrypt_artifact(source, output)

            self.assertEqual(state, QQArtifactState.LEGACY_PASSTHROUGH)

    def test_inspect_accepts_output_that_differs_from_encrypted_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            output = root / "song.flac"
            source.write_bytes(_musicex_source_bytes())
            output.write_bytes(b"fLaC" + b"\0" * 2048)

            state = inspect_decrypt_artifact(source, output)

            self.assertEqual(state, QQArtifactState.DECODED)

    def test_adapter_rejects_legacy_passthrough_and_cleans_staged_output(self) -> None:
        class PassthroughGateway:
            def decrypt_file(self, src_file: str, dst_file: str) -> bool:
                source_bytes = pathlib.Path(src_file).read_bytes()
                pathlib.Path(dst_file).write_bytes(source_bytes[:-MUSICEX_TRAILER_LEN])
                return True

        class TestAdapter(QQPlatformAdapter):
            def __init__(self, safe_dir: pathlib.Path) -> None:
                super().__init__()
                self.safe_dir = safe_dir

            def _load_runtime(self):
                return lambda: PassthroughGateway(), lambda _work_dir: str(self.safe_dir)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "song.mflac"
            work_dir = root / "work"
            safe_dir = root / "safe"
            source.write_bytes(_musicex_source_bytes())

            adapter = TestAdapter(safe_dir)
            adapter._gateway = PassthroughGateway()  # type: ignore[assignment]

            with self.assertRaisesRegex(RuntimeError, "unsupported_qq_musicex_variant"):
                adapter.decrypt_one(source, work_dir, {}, log_dir=root)

            self.assertFalse((work_dir / "song.flac").exists())
            self.assertFalse(any(safe_dir.glob("qqsrc_*")))


if __name__ == "__main__":
    unittest.main()
