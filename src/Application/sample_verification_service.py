from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.Application.decrypt_service import run_batch
from src.Application.models import BatchRunConfig, PlatformAdapter
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Infrastructure.transcoder import resolve_ffmpeg_path


DEFAULT_SAMPLE_PLATFORMS = ("qq", "kugou", "netease", "kuwo")


@dataclass(slots=True)
class StrictDecodeResult:
    ok: bool
    reason: str = ""


@dataclass(slots=True)
class SamplePlatformResult:
    platform_id: str
    input_path: pathlib.Path
    candidate_count: int
    status: str
    result_code: int | None = None
    verified_outputs: list[pathlib.Path] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class SampleVerificationSummary:
    results: list[SamplePlatformResult]

    @property
    def total_candidates(self) -> int:
        return sum(item.candidate_count for item in self.results)

    @property
    def verified_count(self) -> int:
        return sum(len(item.verified_outputs) for item in self.results)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")

    @property
    def not_found_count(self) -> int:
        return sum(1 for item in self.results if item.status == "not_found")

    @property
    def exit_code(self) -> int:
        if self.failed_count:
            return 2
        if self.not_found_count or self.total_candidates == 0 or self.verified_count == 0:
            return 3
        return 0


def _dedupe_paths(paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    seen: set[str] = set()
    result: list[pathlib.Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _target_settings(platform_id: str, base_settings: dict[str, Any], *, max_workers: int, bitrate_kbps: int) -> dict[str, Any]:
    settings = dict(base_settings)
    settings.update(
        {
            "transcode_enabled": True,
            "auto_transcode_after_decode": True,
            "transcode_max_workers": max(1, min(int(max_workers or 1), 4)),
            "transcode_bitrate_kbps": int(bitrate_kbps),
            "embed_cover_art": False,
            "supplement_album_metadata": False,
        }
    )
    if platform_id == "qq":
        rules = dict(settings.get("format_rules", {}))
        for source in ("mflac", "mgg", "mmp4"):
            rules[source] = "mp3"
        settings["format_rules"] = rules
    elif platform_id == "kugou":
        settings["target_format_kgma"] = "mp3"
        settings["target_format_kgg"] = "mp3"
    elif platform_id == "netease":
        settings["target_format_ncm"] = "mp3"
    elif platform_id == "kuwo":
        settings["target_format_kwm"] = "mp3"
    return settings


def _strict_decode(ffmpeg_path: pathlib.Path, output_path: pathlib.Path) -> StrictDecodeResult:
    completed = subprocess.run(
        [str(ffmpeg_path), "-v", "error", "-xerror", "-i", str(output_path), "-f", "null", os.devnull],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode == 0:
        return StrictDecodeResult(True)
    return StrictDecodeResult(False, (completed.stderr or completed.stdout or f"ffmpeg rc={completed.returncode}").strip())


def _collect_candidates(adapter: PlatformAdapter, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
    try:
        return adapter.collect_files(input_path, recursive)
    except Exception:
        return []


def _output_paths_from_events(events: list[tuple[str, dict[str, Any]]]) -> list[pathlib.Path]:
    outputs: list[pathlib.Path] = []
    for event_name, payload in events:
        if event_name != "file_finished":
            continue
        result = str(payload.get("result") or "")
        if result != "success":
            continue
        output_path = str(payload.get("output_path") or "").strip()
        if output_path:
            outputs.append(pathlib.Path(output_path))
    return _dedupe_paths(outputs)


def _verify_platform_input(
    *,
    platform_id: str,
    adapter: PlatformAdapter,
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    settings: dict[str, Any],
    recursive: bool,
    ffmpeg_path: pathlib.Path | None,
) -> SamplePlatformResult:
    candidates = _collect_candidates(adapter, input_path, recursive)
    if not candidates:
        return SamplePlatformResult(platform_id, input_path, 0, "not_found")

    runtime_ok, runtime_reason = adapter.validate_runtime(settings)
    if not runtime_ok:
        return SamplePlatformResult(platform_id, input_path, len(candidates), "failed", reason=runtime_reason or "runtime unavailable")
    if ffmpeg_path is None:
        return SamplePlatformResult(platform_id, input_path, len(candidates), "failed", reason="missing ffmpeg executable")

    events: list[tuple[str, dict[str, Any]]] = []

    def _sink(event_name: str, payload: dict[str, Any]) -> None:
        events.append((event_name, dict(payload)))

    platform_output = output_dir / platform_id
    config = BatchRunConfig(
        platform_id=platform_id,
        input_path=input_path,
        output_dir=platform_output,
        recursive=recursive,
        collision_policy="suffix",
        settings=settings,
        interactive=False,
        event_sink=_sink,
    )
    result_code = run_batch(config, adapter)
    if result_code != 0:
        return SamplePlatformResult(platform_id, input_path, len(candidates), "failed", result_code=result_code, reason=f"batch failed code={result_code}")

    verified: list[pathlib.Path] = []
    failures: list[str] = []
    for output_path in _output_paths_from_events(events):
        decode = _strict_decode(ffmpeg_path, output_path)
        if decode.ok:
            verified.append(output_path)
        else:
            failures.append(f"{output_path}: {decode.reason}")

    if failures:
        return SamplePlatformResult(platform_id, input_path, len(candidates), "failed", result_code=result_code, verified_outputs=verified, reason="; ".join(failures))
    if not verified:
        return SamplePlatformResult(platform_id, input_path, len(candidates), "failed", result_code=result_code, reason="no verified outputs")
    return SamplePlatformResult(platform_id, input_path, len(candidates), "verified", result_code=result_code, verified_outputs=verified)


def run_sample_verification(
    *,
    input_paths: Iterable[pathlib.Path],
    output_dir: pathlib.Path,
    config: dict[str, Any],
    paths: RuntimePaths,
    platforms: Iterable[str] = DEFAULT_SAMPLE_PLATFORMS,
    recursive: bool = True,
    max_workers: int = 2,
    bitrate_kbps: int = 320,
) -> SampleVerificationSummary:
    roots = _dedupe_paths(input_paths)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_path = resolve_ffmpeg_path(paths)
    results: list[SamplePlatformResult] = []

    for platform_id in platforms:
        normalized_platform = str(platform_id or "").strip().lower()
        adapter = build_platform_adapter(normalized_platform)
        settings = _target_settings(
            normalized_platform,
            dict(config.get(normalized_platform, {})),
            max_workers=max_workers,
            bitrate_kbps=bitrate_kbps,
        )
        for input_path in roots:
            results.append(
                _verify_platform_input(
                    platform_id=normalized_platform,
                    adapter=adapter,
                    input_path=input_path,
                    output_dir=output_dir,
                    settings=settings,
                    recursive=recursive,
                    ffmpeg_path=ffmpeg_path,
                )
            )

    return SampleVerificationSummary(results)
