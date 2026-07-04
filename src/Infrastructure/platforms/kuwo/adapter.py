from __future__ import annotations

import pathlib
from dataclasses import dataclass

from src.Infrastructure.kuwo_decoder import decode_kwm_file, peek_kwm_payload_container
from src.Infrastructure.transcoder import normalize_target_format


SUPPORTED_SUFFIXES = {".kwm"}


@dataclass(slots=True)
class KuwoPlatformAdapter:
    platform_id: str = "kuwo"
    display_name: str = "酷我音乐"

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        if not input_path.exists():
            return []
        pattern = "**/*" if recursive else "*"
        return sorted(
            candidate
            for candidate in input_path.glob(pattern)
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def output_basename(self, input_path: pathlib.Path) -> str:
        name = input_path.name
        return name[:-4] if name.lower().endswith(".kwm") else input_path.stem

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        target = normalize_target_format(settings.get("target_format_kwm", "auto"))
        if target != "auto":
            return target
        try:
            return peek_kwm_payload_container(input_path)
        except Exception:
            return None

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        return normalize_target_format(settings.get("target_format_kwm", "auto"))

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        return decode_kwm_file(input_path, work_dir)
