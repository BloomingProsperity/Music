from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Callable

from src.Application.decrypt_service import run_batch
from src.Application.sample_verification_service import (
    DEFAULT_SAMPLE_PLATFORMS,
    run_sample_verification,
    write_sample_verification_reports,
)
from src.Application.transcode_batch_service import (
    ALL_SOURCE_FORMAT,
    run_transcode_batch,
)
from src.Application.models import BatchRunConfig
from src.Infrastructure.config_repository import (
    PROJECT_ADDRESS,
    PROJECT_NAME_EN,
    PROJECT_NAME_ZH,
    PROJECT_QQ,
    auto_find_kgg_db_path,
    auto_find_kugou_key,
    build_banner,
    format_help_epilog,
    load_config,
    save_config,
    save_default_config_if_missing,
    supported_transcode_formats,
    TRANSCODE_BITRATE_OPTIONS,
    TRANSCODE_SAMPLE_RATE_OPTIONS,
    validate_target_format,
)
from src.Infrastructure.kugou_key_refresh import default_refreshed_kugou_key_path, refresh_kugou_key
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths


PLATFORM_LABELS = {"qq": "QQ音乐", "kugou": "酷狗音乐", "netease": "网易云音乐", "kuwo": "酷我音乐"}


def pause_exit(code: int = 0, message: str | None = None) -> int:
    if message:
        print(message)
    try:
        input("按任意键退出...")
    except EOFError:
        pass
    return code


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def prompt_bool(prompt: str, default: bool) -> bool:
    label = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{label}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def prompt_choice(prompt: str, default: str, choices: list[str]) -> str:
    allowed = {choice.lower() for choice in choices}
    value = input(f"{prompt} [{default}]: ").strip().lower()
    if not value:
        return default
    if value not in allowed:
        raise ValueError(f"unsupported option: {value}")
    return value


def prompt_optional_choice_int(prompt: str, default: int | None, choices: tuple[int, ...]) -> int | None:
    default_label = str(default) if default is not None else "关闭"
    raw = input(f"{prompt} [{default_label}，输入 off 关闭]: ").strip().lower()
    if not raw:
        return default
    if raw in {"off", "none", "disable", "close", "关闭"}:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported numeric option: {raw}") from exc
    if value not in choices:
        allowed = ", ".join(str(item) for item in choices)
        raise ValueError(f"unsupported numeric option: {value}; allowed: {allowed}")
    return value


def configure_platform_transcode_profile(settings: dict[str, Any]) -> None:
    settings["transcode_sample_rate_hz"] = prompt_optional_choice_int(
        "指定采样率（仅在转码时生效）",
        settings.get("transcode_sample_rate_hz"),
        TRANSCODE_SAMPLE_RATE_OPTIONS,
    )
    settings["transcode_bitrate_kbps"] = prompt_optional_choice_int(
        "指定比特率（仅在转码到有损格式时生效）",
        settings.get("transcode_bitrate_kbps"),
        TRANSCODE_BITRATE_OPTIONS,
    )


def parse_transcode_rule_spec(spec: str) -> dict[str, Any]:
    parts = [segment.strip() for segment in str(spec or "").split(":")]
    if len(parts) < 2 or len(parts) > 4:
        raise ValueError("rule format must be <source>:<target>[:sample_rate_hz[:bitrate_kbps]]")
    source_format = parts[0] or ALL_SOURCE_FORMAT
    if source_format.lower() == "all":
        source_format = ALL_SOURCE_FORMAT
    target_format = parts[1] or "m4a"
    sample_rate_hz = int(parts[2]) if len(parts) >= 3 and parts[2] else None
    bitrate_kbps = int(parts[3]) if len(parts) >= 4 and parts[3] else None
    return {
        "source_format": source_format,
        "target_format": target_format,
        "sample_rate_hz": sample_rate_hz,
        "bitrate_kbps": bitrate_kbps,
    }


def _transcode_rule_label(rule: dict[str, Any]) -> str:
    parts = [f"{rule.get('source_format', ALL_SOURCE_FORMAT)} -> {rule.get('target_format', 'm4a')}"]
    if rule.get("sample_rate_hz"):
        parts.append(f"{rule['sample_rate_hz']} Hz")
    if rule.get("bitrate_kbps"):
        parts.append(f"{rule['bitrate_kbps']} kbps")
    return " | ".join(parts)


def build_transcode_batch_event_sink() -> Callable[[str, dict[str, Any]], None]:
    def _sink(event_name: str, payload: dict[str, Any]) -> None:
        if event_name == "plan_ready":
            print(f"已生成批量转码计划：任务 {payload.get('total_jobs', 0)} 个，并发 {payload.get('worker_count', 0)} 路")
        elif event_name == "warning":
            print(f"警告：{payload.get('message', '')}")
        elif event_name == "job_started":
            extras: list[str] = []
            if payload.get("sample_rate_hz"):
                extras.append(f"{payload['sample_rate_hz']} Hz")
            if payload.get("bitrate_kbps"):
                extras.append(f"{payload['bitrate_kbps']} kbps")
            extra_text = f"（{' / '.join(extras)}）" if extras else ""
            print(f"开始转码：{payload.get('input_path', '')} -> {payload.get('output_path', '')}{extra_text}")
        elif event_name == "job_succeeded":
            print(f"转码成功：{payload.get('output_path', '')}（{payload.get('elapsed_sec', 0)}s）")
        elif event_name == "job_failed":
            print(f"转码失败：{payload.get('input_path', '')}，原因：{payload.get('reason', '')}")
        elif event_name == "batch_finished":
            print(
                f"批量转码完成：成功 {payload.get('success_count', 0)}，失败 {payload.get('failed_count', 0)}，总耗时 {payload.get('elapsed_sec', 0)}s"
            )
    return _sink


def _run_transcode_batch_cli(paths: RuntimePaths, config: dict[str, Any], args: argparse.Namespace) -> int:
    transcode_config = dict(config.get("transcode_batch", {}))
    input_values = list(args.input or transcode_config.get("input_paths", []))
    if not input_values:
        print("请通过 --input 指定至少一个输入目录，或者先在配置文件里保存 transcode_batch.input_paths。", file=sys.stderr)
        return 2
    output_dir = pathlib.Path(args.output or transcode_config.get("output_dir") or (paths.output_dir / "transcode"))
    recursive = not bool(args.no_recursive)
    max_workers = max(1, int(args.max_workers or transcode_config.get("max_workers", 2) or 2))
    rules = [parse_transcode_rule_spec(item) for item in (args.rule or [])] or list(transcode_config.get("rules", []))
    if not rules:
        rules = [{"source_format": ALL_SOURCE_FORMAT, "target_format": "m4a", "sample_rate_hz": None, "bitrate_kbps": None}]

    config.setdefault("transcode_batch", {})["input_paths"] = [str(item) for item in input_values]
    config["transcode_batch"]["output_dir"] = str(output_dir)
    config["transcode_batch"]["recursive"] = recursive
    config["transcode_batch"]["max_workers"] = max_workers
    config["transcode_batch"]["rules"] = rules
    root, _ = load_config(paths)
    save_config(paths, root, config)

    print("批量转码配置：")
    for index, rule in enumerate(rules, start=1):
        print(f"  规则 {index}: {_transcode_rule_label(rule)}")
    result = run_transcode_batch(
        input_paths=[pathlib.Path(item) for item in input_values],
        output_dir=output_dir,
        rules=rules,
        recursive=recursive,
        max_workers=max_workers,
        event_sink=build_transcode_batch_event_sink(),
    )
    return 0 if result.failed_count == 0 else 1


def _run_sample_verify_cli(paths: RuntimePaths, config: dict[str, Any], args: argparse.Namespace) -> int:
    input_values = list(args.input or [])
    if not input_values:
        print("请通过 --input 指定至少一个待扫描目录。", file=sys.stderr)
        return 2
    output_dir = pathlib.Path(args.output or (pathlib.Path("C:/") / "qkk_sample_verify"))
    raw_platforms = tuple(args.verify_platform or ("all",))
    platforms = DEFAULT_SAMPLE_PLATFORMS if "all" in raw_platforms else tuple(raw_platforms)
    summary = run_sample_verification(
        input_paths=[pathlib.Path(item) for item in input_values],
        output_dir=output_dir,
        config=config,
        paths=paths,
        platforms=platforms,
        recursive=not bool(args.no_recursive),
        max_workers=int(args.max_workers or 2),
        bitrate_kbps=int(args.bitrate or 320),
    )
    for item in summary.results:
        label = PLATFORM_LABELS.get(item.platform_id, item.platform_id)
        if item.status == "not_found":
            print(f"{label}: 未发现样本 {item.input_path}")
        elif item.status == "verified":
            print(f"{label}: 验证通过 {len(item.verified_outputs)} 个输出")
            for output_path in item.verified_outputs:
                print(f"  {output_path}")
        else:
            print(f"{label}: 验证失败 {item.reason}")
    print(f"样本扫描完成：候选 {summary.total_candidates}，严格验证通过 {summary.verified_count}，失败 {summary.failed_count}")
    json_report, text_report = write_sample_verification_reports(summary, output_dir)
    print(f"验证报告：{json_report}")
    print(f"文本报告：{text_report}")
    if summary.exit_code == 3:
        print("未找到可验证样本，不能视为平台真实样本验证完成。")
    return summary.exit_code


def _run_kugou_refresh_key_cli(paths: RuntimePaths, config: dict[str, Any], args: argparse.Namespace) -> int:
    configured = str(config.get("kugou", {}).get("key_file", "") or "").strip()
    configured_path = pathlib.Path(configured).expanduser() if configured else None
    if args.output:
        output_path = pathlib.Path(args.output)
    elif configured_path and configured_path.name.lower() != "kugou_key.xz":
        output_path = configured_path
    else:
        output_path = default_refreshed_kugou_key_path(paths)
    try:
        result = refresh_kugou_key(paths, destination=output_path)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    config.setdefault("kugou", {})["key_file"] = str(result.output_path)
    root, _ = load_config(paths)
    save_config(paths, root, config)
    print("已抓取新的 kugou_key.xz")
    print(f"输出路径：{result.output_path}")
    print(f"来源：{result.source_url}")
    print(f"大小：{result.file_size} bytes")
    print(f"SHA256：{result.sha256}")
    return 0

def choose_platform() -> str:
    print("请选择平台:")
    print("1. QQ音乐")
    print("2. 酷狗音乐")
    print("3. 网易云音乐")
    print("4. 酷我音乐")
    mapping = {
        "1": "qq",
        "2": "kugou",
        "3": "netease",
        "4": "kuwo",
        "qq": "qq",
        "kugou": "kugou",
        "netease": "netease",
        "wangyiyun": "netease",
        "kuwo": "kuwo",
    }
    value = input("平台 [1]: ").strip().lower() or "1"
    return mapping.get(value, "")


def collision_prompt(base_name: str, extension: str, existing_platform: str | None) -> str:
    print(f"检测到共享输出冲突: {base_name}.{extension}")
    print(f"现有来源平台: {existing_platform or '未知'}")
    print("1. 加平台后缀")
    print("2. 分平台子目录")
    print("3. 覆盖")
    value = input("选择 [1]: ").strip() or "1"
    return {"1": "suffix", "2": "subdir", "3": "overwrite"}.get(value, "suffix")


def build_transcode_confirmation_resolver(
    *,
    paths: RuntimePaths,
    config: dict[str, Any],
    platform_id: str,
) -> Callable[[dict[str, Any]], tuple[bool, bool]] | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    def _resolver(payload: dict[str, Any]) -> tuple[bool, bool]:
        pending_count = int(payload.get("pending_count", 0) or 0)
        ready_count = int(payload.get("ready_count", 0) or 0)
        title = PLATFORM_LABELS.get(platform_id, platform_id)
        transcode_enabled_setting = bool(payload.get("transcode_enabled_setting", True))
        if pending_count <= 0:
            if transcode_enabled_setting:
                print(f"{title} 已完成解密：共 {ready_count} 个文件，当前批次无需转码，将直接输出解码结果。")
            else:
                print(f"{title} 已完成解密：共 {ready_count} 个文件，当前处于仅解码模式，本批不会转码。")
            try:
                input("按回车继续...")
            except EOFError:
                pass
            return False, False
        print(f"{title} 已完成解密：共 {ready_count} 个文件，其中 {pending_count} 个需要按当前设置转码。")
        should_transcode = prompt_bool("是否现在统一转码", True)
        remember_choice = False
        if should_transcode:
            remember_choice = prompt_bool(
                "下次该平台解密完成后是否直接转码且不再提醒",
                bool(config.get(platform_id, {}).get("auto_transcode_after_decode", False)),
            )
            if remember_choice != bool(config.get(platform_id, {}).get("auto_transcode_after_decode", False)):
                config[platform_id]["auto_transcode_after_decode"] = remember_choice
                root, _ = load_config(paths)
                save_config(paths, root, config)
        return should_transcode, remember_choice

    return _resolver


def _shared_recursive(config: dict) -> bool:
    return bool(config.get("shared", {}).get("recursive", True))


def _validate_kugou_runtime(paths: RuntimePaths, config: dict, input_path: pathlib.Path, recursive: bool, interactive: bool) -> tuple[bool, str | None, dict]:
    adapter = build_platform_adapter("kugou")
    settings = dict(config["kugou"])
    key_file = pathlib.Path(str(settings.get("key_file", "") or "").strip()) if str(settings.get("key_file", "")).strip() else None
    auto_key = auto_find_kugou_key(paths)
    if (key_file is None or not key_file.exists()) and auto_key is not None:
        settings["key_file"] = str(auto_key)
    ok, reason = adapter.validate_runtime(settings)
    if not ok:
        return False, reason, settings
    candidate_files = adapter.collect_files(input_path, recursive)
    has_kgg = any(path.suffix.lower() == ".kgg" for path in candidate_files)
    db_path = pathlib.Path(str(settings.get("kgg_db_path", "") or "").strip()) if str(settings.get("kgg_db_path", "")).strip() else pathlib.Path()
    if has_kgg and (not db_path.exists()):
        found = auto_find_kgg_db_path()
        if found is not None:
            settings["kgg_db_path"] = str(found)
        else:
            return False, "未找到可用的 KGMusicV3.db，无法解密 kgg。", settings
    return True, None, settings


def _run_platform(platform_id: str, config: dict, *, input_override: str | None = None, output_override: str | None = None, recursive_override: bool | None = None, interactive: bool = False) -> int:
    paths = RuntimePaths.discover()
    adapter = build_platform_adapter(platform_id)
    shared = dict(config["shared"])
    settings = dict(config[platform_id])
    settings["transcode_enabled"] = bool(shared.get("transcode_enabled", True))
    settings["transcode_max_workers"] = max(1, int(shared.get("transcode_max_workers", 2) or 2))
    settings["embed_cover_art"] = bool(shared.get("embed_cover_art", True))
    settings["supplement_album_metadata"] = bool(shared.get("supplement_album_metadata", False))
    input_path = pathlib.Path(input_override or settings.get("input_dir") or "")
    output_dir = pathlib.Path(output_override or shared.get("output_dir") or paths.output_dir)
    recursive = _shared_recursive(config) if recursive_override is None else recursive_override
    if platform_id == "kugou":
        ok, reason, settings = _validate_kugou_runtime(paths, config, input_path, recursive, interactive)
        if not ok:
            if not interactive and reason:
                print(reason, file=sys.stderr)
            return pause_exit(2, reason) if interactive else 2
    config[platform_id].update(settings)
    batch_config = BatchRunConfig(
        platform_id=platform_id,
        input_path=input_path,
        output_dir=output_dir,
        recursive=recursive,
        collision_policy=str(shared.get("cli_collision_policy", "suffix") or "suffix").lower(),
        settings=settings,
        interactive=interactive,
        collision_resolver=collision_prompt if interactive else None,
        transcode_confirmation_resolver=build_transcode_confirmation_resolver(
            paths=paths,
            config=config,
            platform_id=platform_id,
        ),
    )
    config["shared"]["output_dir"] = str(output_dir)
    config["shared"]["recursive"] = recursive
    config[platform_id]["input_dir"] = str(input_path)
    root, _ = load_config(paths)
    save_config(paths, root, config)
    return run_batch(batch_config, adapter)


def run_interactive() -> int:
    paths = RuntimePaths.discover()
    config = save_default_config_if_missing(paths)
    print(build_banner(paths))
    use_config = prompt_bool("是否直接使用配置文件的配置", True)
    platform_id = choose_platform()
    if platform_id not in PLATFORM_LABELS:
        return pause_exit(2, "平台选择无效。")
    if use_config:
        return pause_exit(_run_platform(platform_id, config, interactive=True))

    shared = dict(config["shared"])
    settings = dict(config[platform_id])
    input_dir = pathlib.Path(prompt_with_default("输入文件或目录", str(settings.get("input_dir", ""))))
    output_dir = pathlib.Path(prompt_with_default("共享输出目录", str(shared.get("output_dir", paths.output_dir))))
    recursive = prompt_bool("递归扫描子目录", bool(shared.get("recursive", True)))
    shared["transcode_enabled"] = prompt_bool(
        "是否转码（关闭后直接输出解密后的原始音频格式）",
        bool(shared.get("transcode_enabled", True)),
    )
    shared["embed_cover_art"] = prompt_bool(
        "是否自动补封面（所有平台共用，可能会导致转换明显变慢）",
        bool(shared.get("embed_cover_art", True)),
    )
    shared["supplement_album_metadata"] = prompt_bool(
        "是否补充专辑信息（仅对 m4a/wav 生效，优先本地后网络）",
        bool(shared.get("supplement_album_metadata", False)),
    )

    if not bool(shared.get("transcode_enabled", True)):
        pass
    elif platform_id == "qq":
        rules = dict(settings.get("format_rules", {}))
        rules["mflac"] = prompt_choice("mflac 输出格式 flac/m4a/mp3/wav", str(rules.get("mflac", "mp3")), supported_transcode_formats())
        rules["mgg"] = prompt_choice("mgg 输出格式 flac/m4a/mp3/wav", str(rules.get("mgg", "mp3")), supported_transcode_formats())
        rules["mmp4"] = prompt_choice("mmp4 输出格式 flac/m4a/mp3/wav", str(rules.get("mmp4", "mp3")), supported_transcode_formats())
        settings["format_rules"] = rules
    elif platform_id == "kugou":
        settings["target_format_kgma"] = prompt_choice("kgma/kgm/vpr 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_kgma", "auto")), supported_transcode_formats())
        settings["target_format_kgg"] = prompt_choice("kgg 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_kgg", "auto")), supported_transcode_formats())
        auto_key = auto_find_kugou_key(paths)
        if auto_key is not None:
            settings["key_file"] = str(auto_key)
        if prompt_bool("是否立即抓取新的 kugou_key.xz", False):
            try:
                configured_path = pathlib.Path(str(settings.get("key_file", "") or "")).expanduser() if str(settings.get("key_file", "") or "").strip() else None
                target_path = configured_path if configured_path and configured_path.name.lower() != "kugou_key.xz" else default_refreshed_kugou_key_path(paths)
                result = refresh_kugou_key(
                    paths,
                    destination=target_path,
                )
                settings["key_file"] = str(result.output_path)
                print(f"已更新 kugou_key.xz：{result.output_path}")
            except Exception as exc:
                print(f"抓取 kugou_key.xz 失败：{exc}")
    elif platform_id == "netease":
        settings["target_format_ncm"] = prompt_choice("ncm 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_ncm", "auto")), supported_transcode_formats())
    elif platform_id == "kuwo":
        settings["target_format_kwm"] = prompt_choice("kwm 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_kwm", "auto")), supported_transcode_formats())

    config[platform_id].update(settings)
    config["shared"].update(shared)
    config[platform_id]["input_dir"] = str(input_dir)
    config["shared"]["output_dir"] = str(output_dir)
    config["shared"]["recursive"] = recursive
    root, _ = load_config(paths)
    save_config(paths, root, config)
    if not prompt_bool("立即开始解密", True):
        return pause_exit(0, "配置已保存。")
    return pause_exit(_run_platform(platform_id, config, interactive=True))


def build_parser(paths: RuntimePaths) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME_EN} / {PROJECT_NAME_ZH}",
        epilog=format_help_epilog(paths),
    )
    sub = parser.add_subparsers(dest="platform")
    for platform_id in ("qq", "kugou", "netease", "kuwo"):
        platform_parser = sub.add_parser(platform_id, help=f"{PLATFORM_LABELS[platform_id]} 解密")
        platform_sub = platform_parser.add_subparsers(dest="command")
        dec = platform_sub.add_parser("decrypt", help="执行解密")
        dec.add_argument("--input", help="输入文件或目录")
        dec.add_argument("--output", help="共享输出目录")
        dec.add_argument("--no-recursive", action="store_true", help="禁用递归扫描")
        if platform_id == "qq":
            dec.add_argument("--format-mflac", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mflac 输出格式")
            dec.add_argument("--format-mgg", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mgg 输出格式")
            dec.add_argument("--format-mmp4", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mmp4 输出格式")
            dec.add_argument("--qq-no-fetch-ekey", action="store_true", help="只使用内嵌/缓存 ekey，不尝试从 QQ 音乐登录态补取")
            dec.add_argument("--qq-ekey-cache-dir", help="QQ ekey 缓存目录（建议放在用户数据目录，不要放项目内）")
        elif platform_id == "kugou":
            dec.add_argument("--kgg-db", help="KGMusicV3.db 路径")
            dec.add_argument("--key-file", help="kugou_key.xz 路径")
            dec.add_argument("--format-kgma", choices=supported_transcode_formats(), help="kgma/kgm/vpr 输出格式")
            dec.add_argument("--format-kgg", choices=supported_transcode_formats(), help="kgg 输出格式")
            refresh_key = platform_sub.add_parser("refresh-key", help="抓取最新的 kugou_key.xz")
            refresh_key.add_argument("--output", help="保存新的 kugou_key.xz 路径")
        elif platform_id == "netease":
            dec.add_argument("--format-ncm", choices=supported_transcode_formats(), help="ncm 输出格式")
        elif platform_id == "kuwo":
            dec.add_argument("--format-kwm", choices=supported_transcode_formats(), help="kwm 输出格式")
        cover_group = dec.add_mutually_exclusive_group()
        cover_group.add_argument("--embed-cover", dest="embed_cover_art", action="store_true", help="自动补封面（所有平台共用），可能会导致转换变慢")
        cover_group.add_argument("--no-embed-cover", dest="embed_cover_art", action="store_false", help="不自动补封面")
        transcode_group = dec.add_mutually_exclusive_group()
        transcode_group.add_argument("--transcode", dest="transcode_enabled", action="store_true", help="转码为目标格式")
        transcode_group.add_argument("--no-transcode", dest="transcode_enabled", action="store_false", help="不转码，直接输出解密后的原始音频格式")
        dec.add_argument("--transcode-workers", type=int, help="平台解密后转码的并发数，正整数")
        dec.add_argument("--sample-rate", type=int, choices=TRANSCODE_SAMPLE_RATE_OPTIONS, help="转码采样率 Hz")
        dec.add_argument("--bitrate", type=int, choices=TRANSCODE_BITRATE_OPTIONS, help="mp3/m4a 转码码率 kbps")
        album_group = dec.add_mutually_exclusive_group()
        album_group.add_argument("--supplement-album", dest="supplement_album_metadata", action="store_true", help="补充专辑信息（m4a/wav）")
        album_group.add_argument("--no-supplement-album", dest="supplement_album_metadata", action="store_false", help="不补充专辑信息")
        dec.set_defaults(embed_cover_art=None, supplement_album_metadata=None, transcode_enabled=None)

    transcode_parser = sub.add_parser("transcode-batch", help="执行批量转码")
    transcode_parser.add_argument("--input", action="append", help="输入文件或目录，可重复传入")
    transcode_parser.add_argument("--output", help="输出目录")
    transcode_parser.add_argument("--no-recursive", action="store_true", help="禁用递归扫描")
    transcode_parser.add_argument("--max-workers", type=int, help="并发转码任务数，正整数")
    transcode_parser.add_argument("--rule", action="append", help="规则格式：<source>:<target>[:sample_rate_hz[:bitrate_kbps]]，例如 全部:m4a:48000:256")

    verify_parser = sub.add_parser("sample-verify", help="扫描真实样本并解密转码为 mp3 后严格验证")
    verify_parser.add_argument("--input", action="append", help="待扫描文件或目录，可重复传入")
    verify_parser.add_argument("--output", help="验证输出目录，默认 C:\\qkk_sample_verify")
    verify_parser.add_argument("--platform", dest="verify_platform", action="append", choices=("all", *DEFAULT_SAMPLE_PLATFORMS), help="验证平台，可重复传入，默认 all")
    verify_parser.add_argument("--no-recursive", action="store_true", help="禁用递归扫描")
    verify_parser.add_argument("--max-workers", type=int, help="平台解密后转码并发数，正整数")
    verify_parser.add_argument("--bitrate", type=int, choices=TRANSCODE_BITRATE_OPTIONS, help="mp3 验证输出码率 kbps")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None and len(sys.argv) == 1:
        # Keep no-arg interactive entry explicit for packaged use.
        return run_interactive()
    paths = RuntimePaths.discover()
    parser = build_parser(paths)
    args = parser.parse_args(argv)
    if args.platform is None:
        return run_interactive()
    _, config = load_config(paths)
    if args.platform == "transcode-batch":
        return _run_transcode_batch_cli(paths, config, args)
    if args.platform == "sample-verify":
        return _run_sample_verify_cli(paths, config, args)
    if args.platform == "kugou" and args.command == "refresh-key":
        return _run_kugou_refresh_key_cli(paths, config, args)
    if args.command != "decrypt":
        parser.print_help()
        return 1
    platform_id = args.platform
    settings = dict(config[platform_id])
    explicit_non_auto_target = False
    if args.transcode_enabled is not None:
        config["shared"]["transcode_enabled"] = bool(args.transcode_enabled)
    if getattr(args, "transcode_workers", None) is not None:
        config["shared"]["transcode_max_workers"] = int(args.transcode_workers)
    if args.embed_cover_art is not None:
        config["shared"]["embed_cover_art"] = bool(args.embed_cover_art)
    if args.supplement_album_metadata is not None:
        config["shared"]["supplement_album_metadata"] = bool(args.supplement_album_metadata)
    if getattr(args, "sample_rate", None) is not None:
        settings["transcode_sample_rate_hz"] = int(args.sample_rate)
    if getattr(args, "bitrate", None) is not None:
        settings["transcode_bitrate_kbps"] = int(args.bitrate)
    if platform_id == "qq":
        rules = dict(settings.get("format_rules", {}))
        for source_key, attr_name in (("mflac", "format_mflac"), ("mgg", "format_mgg"), ("mmp4", "format_mmp4")):
            value = getattr(args, attr_name)
            if value:
                target = validate_target_format(value)
                rules[source_key] = target
                explicit_non_auto_target = True
        settings["format_rules"] = rules
        if getattr(args, "qq_no_fetch_ekey", False):
            settings["qq_fetch_missing_ekey"] = False
        if getattr(args, "qq_ekey_cache_dir", None):
            settings["qq_ekey_cache_dir"] = args.qq_ekey_cache_dir
    elif platform_id == "kugou":
        if args.kgg_db:
            settings["kgg_db_path"] = args.kgg_db
        if args.key_file:
            settings["key_file"] = args.key_file
        if args.format_kgma:
            target = validate_target_format(args.format_kgma)
            settings["target_format_kgma"] = target
            explicit_non_auto_target = explicit_non_auto_target or target != "auto"
        if args.format_kgg:
            target = validate_target_format(args.format_kgg)
            settings["target_format_kgg"] = target
            explicit_non_auto_target = explicit_non_auto_target or target != "auto"
    elif platform_id == "netease":
        if args.format_ncm:
            target = validate_target_format(args.format_ncm)
            settings["target_format_ncm"] = target
            explicit_non_auto_target = target != "auto"
    elif platform_id == "kuwo":
        if args.format_kwm:
            target = validate_target_format(args.format_kwm)
            settings["target_format_kwm"] = target
            explicit_non_auto_target = target != "auto"
    if explicit_non_auto_target and args.transcode_enabled is not False:
        config["shared"]["transcode_enabled"] = True
    if args.transcode_enabled is False:
        settings["auto_transcode_after_decode"] = False
    elif args.transcode_enabled is True or explicit_non_auto_target:
        settings["auto_transcode_after_decode"] = True
    config[platform_id].update(settings)
    recursive = not args.no_recursive
    return _run_platform(platform_id, config, input_override=args.input, output_override=args.output, recursive_override=recursive, interactive=False)




