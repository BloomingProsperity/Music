from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


APP_VERSION = "0.15"
VERSION_MARKER_FILE = ".qkk-version"
UPDATE_REPO_URL = "https://github.com/BloomingProsperity/Music.git"
UPDATE_BRANCH = "music-gateway"
REMOTE_VERSION_FILE = "src/Infrastructure/update_service.py"


@dataclass(frozen=True, slots=True)
class UpdateCommand:
    mode: str
    command: list[str]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    ok: bool
    message: str
    return_code: int | None = None


@dataclass(frozen=True, slots=True)
class RestartCommand:
    command: list[str]
    cwd: pathlib.Path


@dataclass(frozen=True, slots=True)
class RestartResult:
    ok: bool
    message: str
    pid: int | None = None


@dataclass(frozen=True, slots=True)
class UpdateAvailability:
    ok: bool
    update_available: bool
    current_version: str
    latest_version: str
    current_revision: str = ""
    latest_revision: str = ""
    message: str = ""


def _subprocess_window_kwargs() -> dict[str, object]:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startupinfo,
        }
    return {}


def _short_revision(value: str) -> str:
    normalized = "".join(char for char in str(value or "").strip() if char.isalnum() or char in {".", "-", "_"})
    if len(normalized) >= 12 and all(char in "0123456789abcdefABCDEF" for char in normalized[:12]):
        return normalized[:7]
    return normalized


def _run_git_revision(root_dir: pathlib.Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root_dir), *args],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            **_subprocess_window_kwargs(),
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return _short_revision(str(completed.stdout or "").strip())


def resolve_app_version(root_dir: pathlib.Path) -> str:
    _ = pathlib.Path(root_dir).resolve()
    return APP_VERSION


def _local_revision_id(root_dir: pathlib.Path) -> str:
    root_dir = pathlib.Path(root_dir).resolve()
    if not (root_dir / ".git").exists():
        return _read_local_version_marker(root_dir)
    return _run_git_revision(root_dir, "rev-parse", "--short", "HEAD")


def _fetch_remote_revision_id() -> str:
    branch = urllib.parse.quote(UPDATE_BRANCH, safe="")
    api_url = f"https://api.github.com/repos/BloomingProsperity/Music/commits/{branch}"
    try:
        request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "QKKDecrypt"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        sha = str(payload.get("sha") or "")
        if sha:
            return _short_revision(sha)
    except Exception:
        pass
    try:
        completed = subprocess.run(
            ["git", "ls-remote", UPDATE_REPO_URL, UPDATE_BRANCH],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            **_subprocess_window_kwargs(),
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    first = str(completed.stdout or "").strip().split()
    return _short_revision(first[0]) if first else ""


def _fetch_remote_app_version() -> str:
    branch = urllib.parse.quote(UPDATE_BRANCH, safe="")
    raw_url = f"https://raw.githubusercontent.com/BloomingProsperity/Music/{branch}/{REMOTE_VERSION_FILE}"
    try:
        request = urllib.request.Request(raw_url, headers={"User-Agent": "QKKDecrypt"})
        with urllib.request.urlopen(request, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    match = re.search(r'^\s*APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _read_local_version_marker(root_dir: pathlib.Path) -> str:
    marker_path = pathlib.Path(root_dir) / VERSION_MARKER_FILE
    try:
        value = marker_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except Exception:
        return ""
    return _short_revision(value)


def _write_local_version_marker(root_dir: pathlib.Path, marker: str | None = None) -> str:
    marker = _short_revision(marker or "") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    marker_path = pathlib.Path(root_dir) / VERSION_MARKER_FILE
    marker_path.write_text(marker + "\n", encoding="utf-8")
    return marker


def check_update_availability(root_dir: pathlib.Path) -> UpdateAvailability:
    root_dir = pathlib.Path(root_dir).resolve()
    current_revision = _local_revision_id(root_dir)
    latest_revision = _fetch_remote_revision_id()
    if not latest_revision:
        return UpdateAvailability(
            ok=False,
            update_available=False,
            current_version=APP_VERSION,
            latest_version=APP_VERSION,
            current_revision=current_revision,
            latest_revision="",
            message="暂时无法检查更新",
        )
    latest_version = APP_VERSION
    update_available = current_revision != latest_revision
    if not update_available:
        remote_version = _fetch_remote_app_version()
        if remote_version:
            latest_version = remote_version
            update_available = remote_version != APP_VERSION
    return UpdateAvailability(
        ok=True,
        update_available=update_available,
        current_version=APP_VERSION,
        latest_version=latest_version,
        current_revision=current_revision,
        latest_revision=latest_revision,
        message="已有版本更新" if update_available else "已是最新版本",
    )


def build_update_command(root_dir: pathlib.Path) -> UpdateCommand | None:
    root_dir = pathlib.Path(root_dir).resolve()
    if (root_dir / ".git").exists():
        return UpdateCommand("git", ["git", "-C", str(root_dir), "pull", "--ff-only"])
    for script_name in ("update.ps1", "deploy.ps1"):
        script_path = root_dir / script_name
        if script_path.exists() and script_path.is_file():
            return UpdateCommand(
                "script",
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
            )
    return None


def build_restart_command(
    root_dir: pathlib.Path,
    *,
    executable: str | None = None,
    argv: list[str] | None = None,
) -> RestartCommand:
    root_dir = pathlib.Path(root_dir).resolve()
    executable = executable or sys.executable
    argv = list(sys.argv if argv is None else argv)
    if not argv:
        ui_entry = root_dir / "ui_main.py"
        argv = [str(ui_entry if ui_entry.exists() else root_dir / "main.py")]
    return RestartCommand([executable, *argv], root_dir)


def restart_application(
    root_dir: pathlib.Path,
    *,
    executable: str | None = None,
    argv: list[str] | None = None,
) -> RestartResult:
    command = build_restart_command(root_dir, executable=executable, argv=argv)
    try:
        process = subprocess.Popen(
            command.command,
            cwd=str(command.cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_subprocess_window_kwargs(),
        )
    except Exception as exc:
        return RestartResult(False, f"重启失败：{exc}", None)
    return RestartResult(True, f"更新完成，正在重启到 {resolve_app_version(root_dir)}", int(getattr(process, "pid", 0) or 0))


def run_update(root_dir: pathlib.Path) -> UpdateResult:
    root_dir = pathlib.Path(root_dir).resolve()
    update_command = build_update_command(root_dir)
    if update_command is None:
        return UpdateResult(
            False,
            f"没有可用的更新入口。当前版本 {APP_VERSION}；源码目录可使用 git pull，一键部署包请重新运行部署脚本。",
            None,
        )
    try:
        completed = subprocess.run(
            update_command.command,
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
            **_subprocess_window_kwargs(),
        )
    except Exception as exc:
        return UpdateResult(False, f"更新启动失败：{exc}", None)

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    if completed.returncode == 0:
        if not (root_dir / ".git").exists():
            _write_local_version_marker(root_dir, _fetch_remote_revision_id())
        details = f"\n{output}" if output else ""
        return UpdateResult(True, f"更新完成（{update_command.mode}），当前版本 {resolve_app_version(root_dir)}。{details}", completed.returncode)
    details = f"\n{output}" if output else ""
    return UpdateResult(False, f"更新失败（{update_command.mode}, code={completed.returncode}）。{details}", completed.returncode)
