from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from src.Infrastructure.update_service import (
    APP_VERSION,
    build_restart_command,
    build_update_command,
    check_update_availability,
    resolve_app_version,
    restart_application,
    run_update,
)


def test_app_version_defaults_to_v006() -> None:
    assert APP_VERSION == "0.06"


def test_resolve_app_version_includes_git_commit_when_available(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr("src.Infrastructure.update_service.subprocess.run", fake_run)

    assert resolve_app_version(tmp_path) == "0.06"


def test_resolve_app_version_uses_local_update_marker_without_git(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".qkk-version").write_text("20260704163300", encoding="utf-8")

    assert resolve_app_version(tmp_path) == "0.06"


def test_check_update_availability_reports_new_remote_revision(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / ".qkk-version").write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr("src.Infrastructure.update_service._fetch_remote_revision_id", lambda: "def5678")

    result = check_update_availability(tmp_path)

    assert result.ok is True
    assert result.update_available is True
    assert result.current_version == "0.06"
    assert result.latest_version == "0.06"
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


def test_update_script_downloads_changed_files_without_redownloading_existing_ffmpeg(tmp_path: pathlib.Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    shutil.copy(pathlib.Path(__file__).resolve().parents[1] / "update.ps1", install_dir / "update.ps1")
    ffmpeg_path = install_dir / "assets" / "ffmpeg-win-x86_64-v7.1.exe"
    ffmpeg_path.parent.mkdir()
    ffmpeg_path.write_bytes(b"existing-ffmpeg")
    (install_dir / "unchanged.py").write_text("local", encoding="utf-8")
    (install_dir / ".qkk-update-manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "unchanged.py": "same-sha",
                    "assets/ffmpeg-win-x86_64-v7.1.exe": "ffmpeg-sha",
                }
            }
        ),
        encoding="utf-8",
    )
    requested_paths: list[str] = []
    tree_payload = {
        "sha": "abcdef1234567890",
        "tree": [
            {"path": "changed.py", "type": "blob", "sha": "changed-sha"},
            {"path": "unchanged.py", "type": "blob", "sha": "same-sha"},
            {"path": "assets/ffmpeg-win-x86_64-v7.1.exe", "type": "blob", "sha": "ffmpeg-sha"},
            {"path": "assets/kugou_key.xz", "type": "blob", "sha": "key-sha"},
        ],
    }
    raw_payloads = {
        "changed.py": b"changed",
        "assets/kugou_key.xz": b"key-data",
        "unchanged.py": b"unexpected",
        "assets/ffmpeg-win-x86_64-v7.1.exe": b"unexpected-ffmpeg",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = unquote(self.path)
            if path == "/tree":
                body = json.dumps(tree_payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/raw/"):
                relative_path = path.removeprefix("/raw/")
                requested_paths.append(relative_path)
                body = raw_payloads[relative_path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(install_dir / "update.ps1"),
                "-TreeApiUrl",
                f"http://127.0.0.1:{port}/tree",
                "-RawContentBaseUrl",
                f"http://127.0.0.1:{port}/raw",
            ],
            cwd=str(install_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert requested_paths == ["changed.py", "assets/kugou_key.xz"]
    assert (install_dir / "changed.py").read_text(encoding="utf-8") == "changed"
    assert ffmpeg_path.read_bytes() == b"existing-ffmpeg"
    manifest = json.loads((install_dir / ".qkk-update-manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["changed.py"] == "changed-sha"
    assert (install_dir / ".qkk-version").read_text(encoding="utf-8").strip() == "abcdef1"


def test_deploy_script_writes_update_manifest_for_future_incremental_updates(tmp_path: pathlib.Path) -> None:
    source_root = tmp_path / "source" / "Music-music-gateway"
    (source_root / "assets").mkdir(parents=True)
    (source_root / "assets" / "ffmpeg-win-x86_64-v7.1.exe").write_bytes(b"fake-ffmpeg")
    (source_root / "requirements.txt").write_text("PySide6\n", encoding="utf-8")
    (source_root / "update.ps1").write_text("Write-Host update\n", encoding="utf-8")
    (source_root / "ui_main.py").write_text("print('ui')\n", encoding="utf-8")
    (source_root / "config.json").write_text("{}", encoding="utf-8")
    zip_path = tmp_path / "source.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source_root.parent).as_posix())

    tree_payload = {
        "sha": "abcdef1234567890",
        "tree": [
            {"path": "assets/ffmpeg-win-x86_64-v7.1.exe", "type": "blob", "sha": "ffmpeg-sha"},
            {"path": "requirements.txt", "type": "blob", "sha": "requirements-sha"},
            {"path": "update.ps1", "type": "blob", "sha": "update-sha"},
            {"path": "ui_main.py", "type": "blob", "sha": "ui-sha"},
            {"path": "config.json", "type": "blob", "sha": "config-sha"},
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/source.zip":
                body = zip_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/tree":
                body = json.dumps(tree_payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        install_dir = tmp_path / "install"
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(pathlib.Path(__file__).resolve().parents[1] / "deploy.ps1"),
                "-InstallDir",
                str(install_dir),
                "-RepoZipUrl",
                f"http://127.0.0.1:{port}/source.zip",
                "-TreeApiUrl",
                f"http://127.0.0.1:{port}/tree",
                "-NoLaunch",
                "-NoShortcut",
                "-SkipDependencyInstall",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads((install_dir / ".qkk-update-manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == "abcdef1"
    assert manifest["files"]["assets/ffmpeg-win-x86_64-v7.1.exe"] == "ffmpeg-sha"
    assert manifest["files"]["update.ps1"] == "update-sha"
    assert "config.json" not in manifest["files"]
    assert (install_dir / ".qkk-version").read_text(encoding="utf-8").strip() == "abcdef1"


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
    assert "0.06" in result.message


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
