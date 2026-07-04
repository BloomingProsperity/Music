from __future__ import annotations

import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QBoxLayout, QCheckBox, QFrame, QLabel, QPlainTextEdit, QPushButton, QProgressBar, QScrollArea, QSpinBox

from src.Presentation.ui_app import MainWindow, build_stylesheet


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
    assert scroll.horizontalScrollBar().maximum() == 0
    assert options.width() >= 320


def test_qq_settings_fit_vertically_without_scrolling_at_common_window_size() -> None:
    app = _app()
    window = MainWindow()
    window.resize(1366, 720)
    window.show()
    app.processEvents()

    page = window.pages["qq"]
    scroll = page.findChild(QScrollArea, "FormScroll")
    assert scroll is not None

    assert scroll.verticalScrollBar().maximum() == 0


class _ImmediateThread:
    def __init__(self, *, target, args=(), daemon=None) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self) -> None:
        self.target(*self.args)


class _FakeAdapter:
    def __init__(self, platform_id: str = "netease", display_name: str = "网易云音乐") -> None:
        self.platform_id = platform_id
        self.display_name = display_name

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


def test_starting_kuwo_page_uses_shared_batch_runner(tmp_path: pathlib.Path, monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    page = window.pages["kuwo"]
    input_dir = tmp_path / "kwm"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    page.input_path.set_text(str(input_dir))
    page.output_dir.set_text(str(output_dir))
    page.set_format_values({"target_format_kwm": "mp3"})

    captured: dict[str, object] = {}

    def fake_build_platform_adapter(platform_id: str) -> _FakeAdapter:
        captured["adapter_platform"] = platform_id
        return _FakeAdapter(platform_id, "酷我音乐")

    def fake_run_batch(config, adapter) -> int:
        captured["batch_platform"] = config.platform_id
        captured["target_format_kwm"] = config.settings["target_format_kwm"]
        return 0

    monkeypatch.setattr("src.Presentation.ui_app.build_platform_adapter", fake_build_platform_adapter)
    monkeypatch.setattr("src.Presentation.ui_app.run_batch", fake_run_batch)
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    window._start_platform("kuwo")
    app.processEvents()

    assert captured == {
        "adapter_platform": "kuwo",
        "batch_platform": "kuwo",
        "target_format_kwm": "mp3",
    }


def test_file_decrypted_updates_current_file_without_marking_complete() -> None:
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

    assert window.progress.value() == 25
    assert window.progress_label.text() == "解密 1 / 4"
    assert window.current_file.text() == "当前文件 two.mflac"


def test_ui_splits_decrypt_and_transcode_progress() -> None:
    app = _app()
    window = MainWindow()

    decrypt_progress = window.findChild(QProgressBar, "DecryptProgress")
    transcode_progress = window.findChild(QProgressBar, "TranscodeProgress")
    decrypt_label = window.findChild(QLabel, "DecryptProgressLabel")
    transcode_label = window.findChild(QLabel, "TranscodeProgressLabel")
    assert decrypt_progress is not None
    assert transcode_progress is not None
    assert decrypt_label is not None
    assert transcode_label is not None

    window._handle_run_event("batch_started", {"candidate_count": 20})
    window._handle_run_event(
        "file_decrypted",
        {
            "index": 1,
            "total": 20,
            "input_path": r"C:\music\one.mflac",
        },
    )
    window._handle_run_event("batch_transcode_started", {"pending_count": 12, "worker_count": 8})
    window._handle_run_event(
        "batch_transcode_progress",
        {
            "completed": 3,
            "total_jobs": 12,
            "success_count": 3,
            "failed_count": 0,
            "queued": 9,
            "input_path": r"C:\music\three.mflac",
        },
    )
    app.processEvents()

    assert decrypt_progress.value() == 5
    assert decrypt_label.text() == "解密 1 / 20"
    assert transcode_progress.value() == 25
    assert transcode_label.text() == "转码 3 / 12"
    assert window.findChild(QLabel, "DecodeSuccessRate").text() == "解密成功率 100%"
    assert window.findChild(QLabel, "TranscodeSuccessRate").text() == "转码成功率 100%"


def test_batch_finished_without_total_keeps_existing_total_for_stopped_run() -> None:
    app = _app()
    window = MainWindow()

    window._handle_run_event("batch_started", {"candidate_count": 4})
    window._handle_run_event("batch_finished", {"completed": 2, "success_count": 2, "failed_count": 0, "skipped_count": 0})
    app.processEvents()

    assert window.progress.value() == 50
    assert window.progress_label.text() == "解密 2 / 4"


def test_ui_keeps_output_open_and_artist_grouping_controls() -> None:
    _app()
    window = MainWindow()
    page = window.pages["qq"]

    open_button = page.output_dir.findChild(QPushButton, "OpenOutputButton")
    artist_grouping = page.findChild(QCheckBox, "GroupByArtist")
    delete_source = page.findChild(QCheckBox, "DeleteSourceAfterSuccess")
    workers = page.findChild(QSpinBox, "TranscodeWorkers")

    assert open_button is not None
    assert open_button.text() == "打开"
    assert artist_grouping is not None
    assert artist_grouping.text() == "按音乐作者分类"
    assert delete_source is not None
    assert delete_source.text() == "完成后删除源文件"
    assert delete_source.isChecked() is False
    assert workers is not None
    assert workers.maximum() >= 999


def test_file_finished_updates_progress_counts_immediately() -> None:
    app = _app()
    window = MainWindow()

    window._handle_run_event("batch_started", {"candidate_count": 3})
    window._handle_run_event(
        "file_finished",
        {
            "index": 1,
            "total": 3,
            "result": "success",
            "input_path": r"C:\music\one.mflac",
        },
    )
    window._handle_run_event(
        "file_finished",
        {
            "index": 2,
            "total": 3,
            "result": "failed",
            "input_path": r"C:\music\two.mflac",
            "reason": "decode failed",
        },
    )
    window._handle_run_event(
        "file_finished",
        {
            "index": 3,
            "total": 3,
            "result": "already_decrypted",
            "input_path": r"C:\music\three.mflac",
        },
    )
    app.processEvents()

    assert window.progress.value() == 100
    assert window.progress_label.text() == "解密 3 / 3"
    assert window.success_label.text() == "成功 1"
    assert window.failed_label.text() == "失败 1"
    assert window.skipped_label.text() == "跳过 1"
    assert window.current_file.text() == "当前文件 three.mflac"


def test_file_finished_without_index_uses_recorded_counts_for_progress() -> None:
    app = _app()
    window = MainWindow()

    window._handle_run_event("batch_started", {"candidate_count": 3})
    window._handle_run_event(
        "file_finished",
        {
            "result": "success",
            "input_path": r"C:\music\one.mflac",
        },
    )
    app.processEvents()

    assert window.progress.value() == 33
    assert window.progress_label.text() == "解密 1 / 3"
    assert window.success_label.text() == "成功 1"


def test_batch_finished_accepts_total_jobs_payload() -> None:
    app = _app()
    window = MainWindow()

    window._handle_run_event(
        "batch_finished",
        {
            "total_jobs": 4,
            "success_count": 2,
            "failed_count": 1,
            "skipped_count": 1,
        },
    )
    app.processEvents()

    assert window.progress.value() == 100
    assert window.progress_label.text() == "解密 4 / 4"
    assert window.success_label.text() == "成功 2"
    assert window.failed_label.text() == "失败 1"
    assert window.skipped_label.text() == "跳过 1"


def test_sidebar_shows_default_version_and_update_button() -> None:
    _app()
    window = MainWindow()

    version = window.findChild(QLabel, "AppVersion")
    update_button = window.findChild(QPushButton, "UpdateButton")

    assert version is not None
    assert version.text() == "0.11"
    assert update_button is not None
    assert update_button.text() == "更新系统"


def test_update_button_shows_available_update_when_remote_is_newer(monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    update_button = window.findChild(QPushButton, "UpdateButton")
    assert update_button is not None

    class Result:
        ok = True
        update_available = True
        message = "已有版本更新"

    monkeypatch.setattr("src.Presentation.ui_app.check_update_availability", lambda _root_dir: Result())
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    window._start_update_check()
    app.processEvents()

    assert update_button.text() == "已有版本更新"


def test_update_button_keeps_normal_text_when_no_update(monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    update_button = window.findChild(QPushButton, "UpdateButton")
    assert update_button is not None
    update_button.setText("已有版本更新")

    class Result:
        ok = True
        update_available = False
        message = "已是最新版本"

    monkeypatch.setattr("src.Presentation.ui_app.check_update_availability", lambda _root_dir: Result())
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    window._start_update_check()
    app.processEvents()

    assert update_button.text() == "更新系统"


def test_update_button_runs_update_service_and_logs_result(monkeypatch) -> None:
    app = _app()
    window = MainWindow()
    update_button = window.findChild(QPushButton, "UpdateButton")
    assert update_button is not None
    calls: list[pathlib.Path] = []
    restart_calls: list[pathlib.Path] = []

    class Result:
        ok = True
        message = "已更新到最新版本"

    class RestartResult:
        ok = True
        message = "正在重启"

    def fake_run_update(root_dir: pathlib.Path) -> Result:
        calls.append(root_dir)
        return Result()

    def fake_restart_application(root_dir: pathlib.Path) -> RestartResult:
        restart_calls.append(root_dir)
        return RestartResult()

    monkeypatch.setattr("src.Presentation.ui_app.run_update", fake_run_update)
    monkeypatch.setattr("src.Presentation.ui_app.restart_application", fake_restart_application)
    monkeypatch.setattr("src.Presentation.ui_app.threading.Thread", _ImmediateThread)

    update_button.click()
    app.processEvents()

    assert calls == [window.paths.root_dir]
    assert restart_calls == [window.paths.root_dir]
    status_message = window.findChild(QLabel, "StatusMessage")
    assert status_message is not None
    assert "正在重启" in status_message.text()


def test_form_controls_have_visible_control_frames() -> None:
    stylesheet = build_stylesheet()

    assert "QComboBox#Combo" in stylesheet
    assert "background: #FFFDF9" in stylesheet
    assert "QCheckBox::indicator" in stylesheet
    assert "border: 1px solid #B8C7BC" in stylesheet


def test_run_panel_removes_black_log_box() -> None:
    _app()
    window = MainWindow()

    assert window.findChild(QPlainTextEdit, "Log") is None


def test_run_progress_layout_stacks_when_window_is_narrow() -> None:
    app = _app()
    window = MainWindow()
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.show()
    app.processEvents()

    assert window.run_progress_layout.direction() == QBoxLayout.Direction.TopToBottom

    window.resize(1180, window.height())
    app.processEvents()

    assert window.run_progress_layout.direction() == QBoxLayout.Direction.LeftToRight
