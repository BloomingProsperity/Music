from __future__ import annotations

import pathlib
import subprocess

from src.Infrastructure.update_service import APP_VERSION, build_update_command, run_update


def test_app_version_defaults_to_v001() -> None:
    assert APP_VERSION == "v0.01"


def test_build_update_command_prefers_git_checkout(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()

    command = build_update_command(tmp_path)

    assert command is not None
    assert command.mode == "git"
    assert command.command[:4] == ["git", "-C", str(tmp_path), "pull"]


def test_build_update_command_uses_local_update_script_without_git(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "update.ps1"
    script.write_text("Write-Host update", encoding="utf-8")

    command = build_update_command(tmp_path)

    assert command is not None
    assert command.mode == "script"
    assert command.command[-1] == str(script)


def test_run_update_reports_missing_update_entry(tmp_path: pathlib.Path) -> None:
    result = run_update(tmp_path)

    assert result.ok is False
    assert "没有可用的更新入口" in result.message


def test_run_update_executes_command_and_returns_output(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / "update.ps1").write_text("Write-Host ok", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="updated\n", stderr="")

    monkeypatch.setattr("src.Infrastructure.update_service.subprocess.run", fake_run)

    result = run_update(tmp_path)

    assert result.ok is True
    assert "updated" in result.message
