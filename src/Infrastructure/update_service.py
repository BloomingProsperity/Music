from __future__ import annotations

import pathlib
import subprocess
from dataclasses import dataclass


APP_VERSION = "v0.01"


@dataclass(frozen=True, slots=True)
class UpdateCommand:
    mode: str
    command: list[str]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    ok: bool
    message: str
    return_code: int | None = None


def _subprocess_window_kwargs() -> dict[str, object]:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startupinfo,
        }
    return {}


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
        details = f"\n{output}" if output else ""
        return UpdateResult(True, f"更新完成（{update_command.mode}）。{details}", completed.returncode)
    details = f"\n{output}" if output else ""
    return UpdateResult(False, f"更新失败（{update_command.mode}, code={completed.returncode}）。{details}", completed.returncode)
