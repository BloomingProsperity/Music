from __future__ import annotations

import pathlib
import subprocess

from src.Infrastructure.update_service import (
    APP_VERSION,
    build_restart_command,
    build_update_command,
    check_update_availability,
    resolve_app_version,
    restart_application,
    run_update,
)


def test_app_version_defaults_to_v003() -> None:
    assert APP_VERSION == "0.03"


def test_resolve_app_version_includes_git_commit_when_available(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr("src.Infrastructure.update_service.subprocess.run", fake_run)

    assert resolve_app_version(tmp_path) == "0.03"


def test_resolve_app_version_uses_local_update_marker_without_git(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".qkk-version").write_text("20260704163300", encoding="utf-8")

    assert resolve_app_version(tmp_path) == "0.03"


def test_check_update_availability_reports_new_remote_revision(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / ".qkk-version").write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr("src.Infrastructure.update_service._fetch_remote_revision_id", lambda: "def5678")

    result = check_update_availability(tmp_path)

    assert result.ok is True
    assert result.update_available is True
    assert result.current_version == "0.03"
    assert result.latest_version == "0.03"
    assert result.current_revision == "abc1234"
    assert result.latest_revision == "def5678"


def test_check_update_availability_reports_current_when_revisions_match(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / ".qkk-version").write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr("src.Infrastructure.update_service._fetch_remote_revision_id", lambda: "abc1234")

    result = check_update_availability(tmp_path)

    assert result.ok is True
    assert result.update_available is False


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
    assert (tmp_path / ".qkk-version").exists()
    assert "0.03" in result.message


def test_build_restart_command_reuses_current_python_entry(tmp_path: pathlib.Path) -> None:
    command = build_restart_command(
        tmp_path,
        executable=r"C:\Python\python.exe",
        argv=["ui_main.py"],
    )

    assert command.command == [r"C:\Python\python.exe", "ui_main.py"]
    assert command.cwd == tmp_path.resolve()


def test_restart_application_starts_new_process(tmp_path: pathlib.Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")

        class Process:
            pid = 1234

        return Process()

    monkeypatch.setattr("src.Infrastructure.update_service.subprocess.Popen", fake_popen)

    result = restart_application(
        tmp_path,
        executable=r"C:\Python\python.exe",
        argv=["ui_main.py"],
    )

    assert result.ok is True
    assert captured["command"] == [r"C:\Python\python.exe", "ui_main.py"]
    assert captured["cwd"] == str(tmp_path.resolve())
