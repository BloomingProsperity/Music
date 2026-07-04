from __future__ import annotations

import logging
import pathlib
import time
from dataclasses import dataclass, field

from src.Infrastructure.transcoder import detect_audio_container


SUPPORTED_SUFFIXES = {'.mflac', '.mgg', '.mmp4'}
DEFAULT_RULES = {'mflac': 'mp3', 'mgg': 'mp3', 'mmp4': 'mp3'}
RAW_CONTAINER_RULES = {'mflac': 'flac', 'mgg': 'ogg', 'mmp4': 'm4a'}
WHITELIST = {'flac', 'm4a', 'mp3', 'wav'}
logger = logging.getLogger('qkkdecrypt.infrastructure.platforms.qq')


@dataclass(slots=True)
class QQPlatformAdapter:
    platform_id: str = 'qq'
    display_name: str = 'QQ音乐'
    _offline_decryptor: QQOfflineMusicExDecryptor | None = field(default=None, init=False, repr=False)

    def _ensure_offline_decryptor(self) -> QQOfflineMusicExDecryptor:
        if self._offline_decryptor is None:
            from src.Infrastructure.platforms.qq.musicex_offline import QQOfflineMusicExDecryptor
            self._offline_decryptor = QQOfflineMusicExDecryptor()
        return self._offline_decryptor

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        pattern = '**/*' if recursive else '*'
        return sorted(candidate for candidate in input_path.glob(pattern) if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES)

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def _normalized_rules(self, settings: dict) -> dict[str, str]:
        merged = dict(DEFAULT_RULES)
        raw = settings.get('format_rules') or {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                source = str(key or '').strip().lower().lstrip('.')
                target = str(value or '').strip().lower().lstrip('.')
                if source in merged and target in WHITELIST:
                    merged[source] = target
        return merged

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        source = input_path.suffix.lower().lstrip('.')
        return self._normalized_rules(settings).get(source)

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        return self.predicted_extension(input_path, settings) or 'auto'

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        started = time.perf_counter()
        source_suffix = input_path.suffix.lower().lstrip('.')
        default_ext = RAW_CONTAINER_RULES.get(source_suffix, 'flac')
        final_work_path = work_dir / f"{input_path.stem}.{default_ext}"

        offline_detail = self._ensure_offline_decryptor().decrypt_to_file(
            input_path,
            final_work_path,
            settings,
            log_dir=log_dir,
        )
        if offline_detail is None:
            raise RuntimeError(
                'qq_local_ekey_missing: QQ 本地解码缺少可用 ekey；'
                '请先缓存该文件 ekey，或临时打开 QQ 音乐补取 key 后重试'
            )

        elapsed = round(time.perf_counter() - started, 6)
        offline_detail.setdefault('output_path', str(final_work_path))
        offline_detail.setdefault('detected_container', detect_audio_container(final_work_path)[0])
        offline_detail.setdefault('final_extension', offline_detail.get('detected_container', default_ext))
        offline_detail.setdefault('recognition_stage', 'offline_qmc2')
        offline_detail.setdefault('decoded_bytes', final_work_path.stat().st_size)
        offline_detail['timing'] = {
            'header_parse_sec': 0.0,
            'key_material_sec': 0.0,
            'stream_decode_sec': elapsed,
            'publish_sec': 0.0,
            'total_sec': elapsed,
        }
        return offline_detail
