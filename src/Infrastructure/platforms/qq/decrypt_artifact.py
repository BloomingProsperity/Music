from __future__ import annotations

from enum import Enum
from pathlib import Path


MUSICEX_TRAILER_MAGIC = b"musicex\0"
MUSICEX_TRAILER_LEN = 192
MIN_DECRYPT_OUTPUT_BYTES = 1024


class QQArtifactState(str, Enum):
    DECODED = "decoded"
    MISSING_OUTPUT = "missing_output"
    TOO_SMALL = "too_small"
    LEGACY_PASSTHROUGH = "legacy_passthrough"


def inspect_decrypt_artifact(
    encrypted_source: Path,
    decrypted_output: Path,
    *,
    min_output_bytes: int = MIN_DECRYPT_OUTPUT_BYTES,
) -> QQArtifactState:
    if not decrypted_output.exists():
        return QQArtifactState.MISSING_OUTPUT

    output_size = decrypted_output.stat().st_size
    if output_size <= min_output_bytes:
        return QQArtifactState.TOO_SMALL

    if _is_legacy_musicex_passthrough(encrypted_source, decrypted_output):
        return QQArtifactState.LEGACY_PASSTHROUGH

    return QQArtifactState.DECODED


def qq_artifact_failure_reason(state: QQArtifactState) -> str:
    if state == QQArtifactState.MISSING_OUTPUT:
        return "qq_decrypt_failed: missing decrypted output"
    if state == QQArtifactState.TOO_SMALL:
        return "qq_decrypt_failed: decrypted output is too small"
    if state == QQArtifactState.LEGACY_PASSTHROUGH:
        return "unsupported_qq_musicex_variant: legacy QQ decrypt returned encrypted payload without decrypting"
    return ""


def _is_legacy_musicex_passthrough(encrypted_source: Path, decrypted_output: Path) -> bool:
    try:
        source_size = encrypted_source.stat().st_size
        output_size = decrypted_output.stat().st_size
    except OSError:
        return False

    if source_size <= MUSICEX_TRAILER_LEN:
        return False
    if output_size != source_size - MUSICEX_TRAILER_LEN:
        return False
    if not _has_musicex_trailer(encrypted_source):
        return False
    return _same_file_prefix(encrypted_source, decrypted_output, output_size)


def _has_musicex_trailer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.seek(-len(MUSICEX_TRAILER_MAGIC), 2)
            return handle.read(len(MUSICEX_TRAILER_MAGIC)) == MUSICEX_TRAILER_MAGIC
    except OSError:
        return False


def _same_file_prefix(left: Path, right: Path, byte_count: int) -> bool:
    remaining = byte_count
    chunk_size = 1024 * 1024
    try:
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                if left_handle.read(read_size) != right_handle.read(read_size):
                    return False
                remaining -= read_size
    except OSError:
        return False
    return True
