from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from src.Application.models import BatchRunConfig


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
    sample_rate_hz: int | None = None
    bitrate_kbps: int | None = 320
    qq_fetch_missing_ekey: bool = True
    qq_cache_ekeys: bool = True
    format_rules: dict[str, str] = field(default_factory=lambda: {"mflac": "mp3", "mgg": "mp3", "mmp4": "mp3"})
    event_sink: Any | None = None
    stop_requested: Any | None = None


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
            enabled=False,
            status_text="暂不可用",
        ),
        PlatformSpec(
            platform_id="netease",
            title="网易云音乐",
            subtitle=".ncm",
            source_extensions=(".ncm",),
            format_controls=(
                FormatControlSpec("target_format_ncm", "ncm 输出格式", ("auto", *TARGET_FORMATS), "auto"),
            ),
            enabled=False,
            status_text="暂不可用",
        ),
        PlatformSpec(
            platform_id="kuwo",
            title="酷我音乐",
            subtitle=".kwm",
            source_extensions=(".kwm",),
            format_controls=(
                FormatControlSpec("format_kwm", "kwm 输出格式", ("auto", *TARGET_FORMATS), "auto"),
            ),
            enabled=False,
            status_text="暂不可用",
        ),
    ]


def _clamp_workers(value: int) -> int:
    return max(1, min(int(value or 2), 4))


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


def build_qq_batch_config(options: PlatformRunOptions) -> BatchRunConfig:
    settings: dict[str, Any] = {
        "format_rules": _normalize_format_rules(options.format_rules),
        "transcode_enabled": bool(options.transcode_enabled),
        "transcode_max_workers": _clamp_workers(options.transcode_max_workers),
        "embed_cover_art": bool(options.embed_cover_art),
        "supplement_album_metadata": bool(options.supplement_album_metadata),
        "transcode_sample_rate_hz": options.sample_rate_hz,
        "transcode_bitrate_kbps": options.bitrate_kbps,
        "auto_transcode_after_decode": True,
        "qq_fetch_missing_ekey": bool(options.qq_fetch_missing_ekey),
        "qq_cache_ekeys": bool(options.qq_cache_ekeys),
    }
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
