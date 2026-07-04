from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from src.Application.models import BatchRunConfig
from src.Infrastructure.config_repository import auto_find_kgg_db_path
from src.Infrastructure.file_catalog import file_requires_kgg_db


TARGET_FORMATS = ("mp3", "flac", "m4a", "wav")


@dataclass(frozen=True, slots=True)
class FormatControlSpec:
    key: str
    label: str
    options: tuple[str, ...] = TARGET_FORMATS
    default: str = "mp3"


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    platform_id: str
    title: str
    subtitle: str
    source_extensions: tuple[str, ...]
    format_controls: tuple[FormatControlSpec, ...]
    enabled: bool
    status_text: str


@dataclass(slots=True)
class PlatformRunOptions:
    input_path: pathlib.Path
    output_dir: pathlib.Path
    recursive: bool = True
    transcode_enabled: bool = True
    transcode_max_workers: int = 2
    embed_cover_art: bool = False
    supplement_album_metadata: bool = False
    group_by_artist: bool = False
    delete_source_after_success: bool = False
    sample_rate_hz: int | None = None
    bitrate_kbps: int | None = 320
    qq_fetch_missing_ekey: bool = True
    qq_cache_ekeys: bool = True
    format_rules: dict[str, str] = field(default_factory=lambda: {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"})
    platform_settings: dict[str, Any] = field(default_factory=dict)
    event_sink: Any | None = None
    stop_requested: Any | None = None


@dataclass(frozen=True, slots=True)
class PlatformRuntimeValidation:
    ok: bool
    reason: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


def platform_specs() -> list[PlatformSpec]:
    return [
        PlatformSpec(
            platform_id="qq",
            title="QQ音乐",
            subtitle=".mflac / .mgg / .mmp4",
            source_extensions=(".mflac", ".mgg", ".mmp4"),
            format_controls=(
                FormatControlSpec("mflac", "mflac 输出格式"),
                FormatControlSpec("mgg", "mgg 输出格式"),
                FormatControlSpec("mmp4", "mmp4 输出格式"),
            ),
            enabled=True,
            status_text="可用",
        ),
        PlatformSpec(
            platform_id="kugou",
            title="酷狗音乐",
            subtitle=".kgm / .kgma / .kgg / .vpr",
            source_extensions=(".kgm", ".kgma", ".kgg", ".vpr", ".kgm.flac", ".vpr.flac"),
            format_controls=(
                FormatControlSpec("target_format_kgma", "kgm/kgma/vpr 输出格式", ("auto", *TARGET_FORMATS), "auto"),
                FormatControlSpec("target_format_kgg", "kgg 输出格式", ("auto", *TARGET_FORMATS), "auto"),
            ),
            enabled=True,
            status_text="可用",
        ),
        PlatformSpec(
            platform_id="netease",
            title="网易云音乐",
            subtitle=".ncm",
            source_extensions=(".ncm",),
            format_controls=(
                FormatControlSpec("target_format_ncm", "ncm 输出格式", ("auto", *TARGET_FORMATS), "auto"),
            ),
            enabled=True,
            status_text="可用",
        ),
        PlatformSpec(
            platform_id="kuwo",
            title="酷我音乐",
            subtitle=".kwm / .kwma / .kwm.flac",
            source_extensions=(".kwm", ".kwma", ".kwm.flac"),
            format_controls=(
                FormatControlSpec("target_format_kwm", "kwm 输出格式", ("auto", *TARGET_FORMATS), "auto"),
            ),
            enabled=True,
            status_text="可用",
        ),
    ]


def _clamp_workers(value: int) -> int:
    return max(1, int(value or 2))


def _normalize_format_rules(raw: dict[str, str]) -> dict[str, str]:
    defaults = {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"}
    normalized = dict(defaults)
    for key in defaults:
        value = str(raw.get(key, defaults[key]) or defaults[key]).strip().lower()
        normalized[key] = value if value in TARGET_FORMATS else defaults[key]
    return normalized


def validate_writable_output_dir(output_dir: pathlib.Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    probe = output_dir / f".qkk-write-test-{time.time_ns()}.tmp"
    try:
        probe.write_bytes(b"ok")
    finally:
        probe.unlink(missing_ok=True)


def validate_platform_runtime_for_ui(
    platform_id: str,
    adapter: Any,
    settings: dict[str, Any],
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    recursive: bool,
) -> PlatformRuntimeValidation:
    normalized_platform = (platform_id or "").strip().lower()
    normalized_settings = dict(settings)
    if not input_path.exists():
        return PlatformRuntimeValidation(False, "输入路径不存在", normalized_settings)
    try:
        validate_writable_output_dir(output_dir)
    except OSError as exc:
        return PlatformRuntimeValidation(False, f"输出目录不可写：{exc}", normalized_settings)

    ok, reason = adapter.validate_runtime(normalized_settings)
    if not ok:
        return PlatformRuntimeValidation(False, reason or "运行环境不可用", normalized_settings)

    if normalized_platform == "kugou":
        files = adapter.collect_files(input_path, recursive)
        has_kgg = any(file_requires_kgg_db(file_path) for file_path in files)
        if has_kgg:
            db_value = str(normalized_settings.get("kgg_db_path", "") or "").strip()
            db_path = pathlib.Path(db_value) if db_value else pathlib.Path()
            if not db_path.exists():
                found = auto_find_kgg_db_path()
                if found is None:
                    return PlatformRuntimeValidation(False, "未找到可用的 KGMusicV3.db，无法解密 kgg。", normalized_settings)
                normalized_settings["kgg_db_path"] = str(found)

    return PlatformRuntimeValidation(True, None, normalized_settings)


def build_qq_batch_config(options: PlatformRunOptions) -> BatchRunConfig:
    settings: dict[str, Any] = dict(options.platform_settings)
    settings.update({
        "format_rules": _normalize_format_rules(options.format_rules),
        "transcode_enabled": bool(options.transcode_enabled),
        "transcode_max_workers": _clamp_workers(options.transcode_max_workers),
        "embed_cover_art": bool(options.embed_cover_art),
        "supplement_album_metadata": bool(options.supplement_album_metadata),
        "group_by_artist": bool(options.group_by_artist),
        "delete_source_after_success": bool(options.delete_source_after_success),
        "transcode_sample_rate_hz": options.sample_rate_hz,
        "transcode_bitrate_kbps": options.bitrate_kbps,
        "auto_transcode_after_decode": True,
        "qq_fetch_missing_ekey": bool(options.qq_fetch_missing_ekey),
        "qq_cache_ekeys": bool(options.qq_cache_ekeys),
        "qq_auto_launch_client": bool(settings.get("qq_auto_launch_client", True)),
    })
    return BatchRunConfig(
        platform_id="qq",
        input_path=options.input_path,
        output_dir=options.output_dir,
        recursive=bool(options.recursive),
        collision_policy="suffix",
        settings=settings,
        interactive=False,
        event_sink=options.event_sink,
        stop_requested=options.stop_requested,
        transcode_confirmation_resolver=None,
    )


def _common_batch_settings(options: PlatformRunOptions) -> dict[str, Any]:
    return {
        "transcode_enabled": bool(options.transcode_enabled),
        "transcode_max_workers": _clamp_workers(options.transcode_max_workers),
        "embed_cover_art": bool(options.embed_cover_art),
        "supplement_album_metadata": bool(options.supplement_album_metadata),
        "group_by_artist": bool(options.group_by_artist),
        "delete_source_after_success": bool(options.delete_source_after_success),
        "transcode_sample_rate_hz": options.sample_rate_hz,
        "transcode_bitrate_kbps": options.bitrate_kbps,
        "auto_transcode_after_decode": True,
    }


def _normalize_target(value: Any, default: str = "auto") -> str:
    normalized = str(value or default).strip().lower().lstrip(".")
    if normalized == "ogg":
        normalized = "m4a"
    return normalized if normalized in ("auto", *TARGET_FORMATS) else default


def build_platform_batch_config(platform_id: str, options: PlatformRunOptions) -> BatchRunConfig:
    normalized_platform = (platform_id or "").strip().lower()
    if normalized_platform == "qq":
        return build_qq_batch_config(options)

    raw_settings = dict(options.platform_settings)
    settings = _common_batch_settings(options)
    if normalized_platform == "netease":
        settings["target_format_ncm"] = _normalize_target(raw_settings.get("target_format_ncm", "auto"))
    elif normalized_platform == "kugou":
        settings["target_format_kgma"] = _normalize_target(raw_settings.get("target_format_kgma", "auto"))
        settings["target_format_kgg"] = _normalize_target(raw_settings.get("target_format_kgg", "auto"))
        for key in ("key_file", "kgg_db_path"):
            value = str(raw_settings.get(key, "") or "").strip()
            if value:
                settings[key] = value
    elif normalized_platform == "kuwo":
        settings["target_format_kwm"] = _normalize_target(raw_settings.get("target_format_kwm", "auto"))
    else:
        raise ValueError(f"unsupported platform: {platform_id}")

    return BatchRunConfig(
        platform_id=normalized_platform,
        input_path=options.input_path,
        output_dir=options.output_dir,
        recursive=bool(options.recursive),
        collision_policy="suffix",
        settings=settings,
        interactive=False,
        event_sink=options.event_sink,
        stop_requested=options.stop_requested,
        transcode_confirmation_resolver=None,
    )
