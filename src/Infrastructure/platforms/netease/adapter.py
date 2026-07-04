from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field

from ncmdump import NeteaseCloudMusicFile

from src.Infrastructure.netease_decoder import decode_ncm_file, read_ncm_metadata
from src.Infrastructure.transcoder import detect_audio_container


SUPPORTED_SUFFIXES = {".ncm"}
WHITELIST = {"flac", "m4a", "mp3", "wav"}


@dataclass(slots=True)
class NeteasePlatformAdapter:
    platform_id: str = "netease"
    display_name: str = "网易云音乐"
    _raw_format_cache: dict[str, str | None] = field(default_factory=dict, init=False, repr=False)

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        pattern = "**/*" if recursive else "*"
        return sorted(
            candidate
            for candidate in input_path.glob(pattern)
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def _raw_format(self, input_path: pathlib.Path) -> str | None:
        cache_key = str(input_path.resolve()).lower()
        if cache_key in self._raw_format_cache:
            return self._raw_format_cache[cache_key]
        try:
            raw_format = read_ncm_metadata(input_path).raw_format
        except Exception:
            try:
                ncm = NeteaseCloudMusicFile(input_path).decrypt()
                raw_format = str(getattr(ncm.music_metadata, "format", "") or "").strip().lower()
            except Exception:
                raw_format = None
        if raw_format is None:
            self._raw_format_cache[cache_key] = None
            return None
        if raw_format == "ogg":
            raw_format = "m4a"
        if raw_format not in WHITELIST:
            raw_format = None
        self._raw_format_cache[cache_key] = raw_format
        return raw_format

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        target = str(settings.get("target_format_ncm", "auto") or "auto").strip().lower()
        return self._raw_format(input_path) if target == "auto" else target

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        target = str(settings.get("target_format_ncm", "auto") or "auto").strip().lower()
        if target != "auto":
            return target
        return self._raw_format(input_path) or "auto"

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        try:
            return decode_ncm_file(input_path, work_dir)
        except Exception:
            pass

        started = time.perf_counter()
        ncm = NeteaseCloudMusicFile(input_path).decrypt()
        output_hint = work_dir / input_path.stem
        dumped = ncm.dump_music(output_hint)
        final_work_path = pathlib.Path(dumped)
        detected_container, recognition_stage = detect_audio_container(final_work_path)
        elapsed = round(time.perf_counter() - started, 6)
        metadata = getattr(ncm, "metadata", None)
        metadata_json = getattr(metadata, "json", {})
        if not isinstance(metadata_json, dict):
            metadata_json = {}
        return {
            "input_path": str(input_path),
            "output_path": str(final_work_path),
            "detected_container": detected_container,
            "final_extension": detected_container,
            "recognition_stage": recognition_stage,
            "backend": "python:ncmdump-py",
            "decoded_bytes": final_work_path.stat().st_size if final_work_path.exists() else 0,
            "metadata": dict(metadata_json),
            "metadata_type": str(getattr(metadata, "type", "music") or "music"),
            "timing": {
                "header_parse_sec": 0.0,
                "key_material_sec": 0.0,
                "stream_decode_sec": elapsed,
                "publish_sec": 0.0,
                "total_sec": elapsed,
            },
        }
