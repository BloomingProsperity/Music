from __future__ import annotations

import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QScrollArea

from src.Presentation.ui_app import MainWindow


def _app() -> QApplication:
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication([])


def test_minimum_width_keeps_qq_form_inside_scroll_viewport() -> None:
    app = _app()
    window = MainWindow()
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.show()
    app.processEvents()

    page = window.pages["qq"]
    scroll = page.findChild(QScrollArea, "FormScroll")
    assert scroll is not None
    form = scroll.widget()
    assert form is not None

    options = page.findChild(QFrame, "SoftPanel")
    assert options is not None

    assert form.width() <= scroll.viewport().width()
    assert options.width() >= options.sizeHint().width()


class _ImmediateThread:
    def __init__(self, *, target, args=(), daemon=None) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self) -> None:
        self.target(*self.args)


class _FakeAdapter:
    platform_id = "netease"
    display_name = "网易云音乐"

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        return []


def test_starting_netease_page_uses_shared_batch_runner(tmp_path: pathlib.Path, monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    page = window.pages["netease"]
    input_dir = tmp_path / "ncm"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    page.input_path.set_text(str(input_dir))
    page.output_dir.set_text(str(output_dir))
    page.set_format_values({"target_format_ncm": "mp3"})

    captured: dict[str, object] = {}

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        captured["adapter_platform"] = platform_id
        return _FakeAdapter()

    def fake_run_batch(config, adapter) -> int:
        captured["batch_platform"] = config.platform_id
        captured["target_format_ncm"] = config.settings["target_format_ncm"]
        return 0

    monkeypatch.setattr("src.Presentation.ui_app.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Presentation.ui_app.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    window._start_platform("netease")
    app.processEvents()

    assert captured == {
        "adapter_platform": "netease",
        "batch_platform": "netease",
        "target_format_ncm": "mp3",
    }


def test_progress_advances_when_file_decrypted_event_arrives() -> None:
    app = _app()
    window = MainWindow()

    window._handle_run_event("batch_started", {"candidate_count": 4})
    window._handle_run_event(
        "file_decrypted",
        {
            "index": 2,
            "total": 4,
            "input_path": r"C:\music\two.mflac",
            "message": "已完成解密：two.mflac",
        },
    )
    app.processEvents()

    assert window.progress.value() == 50
    assert window.progress_label.text() == "进度 2 / 4"
    assert window.current_file.text() == "当前文件 two.mflac"


def test_sidebar_shows_default_version_and_update_button() -> None:
    _app()
    window = MainWindow()

    version = window.findChild(QLabel, "AppVersion")
    update_button = window.findChild(QPushButton, "UpdateButton")

    assert version is not None
    assert version.text() == "v0.01"
    assert update_button is not None
    assert update_button.text() == "更新系统"


def test_update_button_runs_update_service_and_logs_result(monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    update_button = window.findChild(QPushButton, "UpdateButton")
    assert update_button is not None
    calls: list[pathlib.Path] = []

    class Result:
        ok = True
        message = "已更新到最新版本"

    def fake_run_update(root_dir: pathlib.Path) -> Result:
        calls.append(root_dir)
        return Result()

    monkeypatch.setattr("src.Presentation.ui_app.run_update", fake_run_update)
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    update_button.click()
    app.processEvents()

    assert calls == [window.paths.root_dir]
    assert "已更新到最新版本" in window.log_view.toPlainText()
