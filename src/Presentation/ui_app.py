from __future__ import annotations

import pathlib
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.Application.decrypt_service import run_batch
from src.Infrastructure.config_repository import (
    TRANSCODE_BITRATE_OPTIONS,
    TRANSCODE_SAMPLE_RATE_OPTIONS,
    load_config,
    save_config,
    save_default_config_if_missing,
)
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Presentation.ui_state import (
    PlatformRunOptions,
    PlatformSpec,
    build_qq_batch_config,
    platform_specs,
    validate_writable_output_dir,
)


APP_BG = "#F7F8F6"
PANEL_BG = "#FFFFFF"
PANEL_ALT = "#FBF7F7"
BORDER = "#E6E2DF"
TEXT = "#1F2933"
MUTED = "#667085"
GREEN = "#79C69B"
GREEN_DARK = "#257A53"
GREEN_SOFT = "#E4F6EC"
RED = "#E9A0A7"
RED_DARK = "#AA4754"
RED_SOFT = "#FBE8EA"


def _format_seconds(value: float) -> str:
    if value <= 0:
        return "0.0s"
    return f"{value:.1f}s"


class UiBridge(QObject):
    event_received = Signal(str, object)
    run_finished = Signal(int)
    log_message = Signal(str)


class PathRow(QWidget):
    def __init__(self, label: str, *, allow_file: bool = False) -> None:
        super().__init__()
        self.allow_file = allow_file
        self.setMinimumHeight(62)
        root = QGridLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setHorizontalSpacing(10)
        root.setVerticalSpacing(5)
        title = QLabel(label)
        title.setObjectName("FieldLabel")
        self.edit = QLineEdit()
        self.edit.setObjectName("Input")
        self.edit.setMinimumWidth(120)
        self.edit.setFixedHeight(32)
        self.edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dir_button = QPushButton("目录")
        self.dir_button.setObjectName("SmallButton")
        self.dir_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dir_button.setFixedWidth(60)
        self.dir_button.setFixedHeight(32)
        self.file_button = QPushButton("文件")
        self.file_button.setObjectName("SmallButton")
        self.file_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_button.setFixedWidth(60)
        self.file_button.setFixedHeight(32)
        self.file_button.setVisible(allow_file)
        root.addWidget(title, 0, 0, 1, 3)
        root.addWidget(self.edit, 1, 0)
        root.addWidget(self.dir_button, 1, 1)
        root.addWidget(self.file_button, 1, 2)
        root.setColumnStretch(0, 1)
        self.dir_button.clicked.connect(self._choose_dir)
        self.file_button.clicked.connect(self._choose_file)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(str(value or ""))
        self.edit.setCursorPosition(0)

    def _choose_dir(self) -> None:
        start = self.text() or str(pathlib.Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if selected:
            self.set_text(selected)

    def _choose_file(self) -> None:
        start = self.text() or str(pathlib.Path.home())
        selected, _ = QFileDialog.getOpenFileName(self, "选择文件", start, "音频文件 (*.*)")
        if selected:
            self.set_text(selected)


class PlatformPage(QWidget):
    start_requested = Signal(str)

    def __init__(self, spec: PlatformSpec) -> None:
        super().__init__()
        self.spec = spec
        self.format_widgets: dict[str, QComboBox] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QFrame()
        header.setObjectName("Panel")
        header.setMinimumHeight(84)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title = QLabel(spec.title)
        title.setObjectName("PageTitle")
        source = QLabel(" ".join(spec.source_extensions))
        source.setObjectName("Muted")
        source.setWordWrap(True)
        source.setMinimumWidth(0)
        source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_box.addWidget(title)
        title_box.addWidget(source)
        self.status = QLabel(spec.status_text)
        self.status.setObjectName("StatusOk" if spec.enabled else "StatusOff")
        header_layout.addLayout(title_box, 1)
        header_layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        form = QFrame()
        form.setObjectName("Panel")
        form.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.form_layout = QVBoxLayout(form)
        self.form_layout.setContentsMargins(16, 14, 16, 14)
        self.form_layout.setSpacing(10)

        self.input_path = PathRow("输入路径", allow_file=True)
        self.output_dir = PathRow("输出目录")
        self.form_layout.addWidget(self.input_path)
        self.form_layout.addWidget(self.output_dir)

        self.config_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.config_layout.setContentsMargins(0, 0, 0, 0)
        self.config_layout.setSpacing(14)

        self.format_box = QWidget()
        self.format_box.setMinimumWidth(260)
        self.format_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        formats = QGridLayout()
        formats.setContentsMargins(0, 0, 0, 0)
        formats.setHorizontalSpacing(12)
        formats.setVerticalSpacing(8)
        for row, control in enumerate(spec.format_controls):
            label = QLabel(control.label)
            label.setObjectName("FieldLabel")
            label.setWordWrap(True)
            label.setMinimumWidth(126)
            label.setMaximumWidth(170)
            combo = QComboBox()
            combo.setObjectName("Combo")
            combo.setFixedHeight(32)
            combo.setMinimumWidth(128)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.addItems(list(control.options))
            combo.setCurrentText(control.default)
            formats.addWidget(label, row, 0)
            formats.addWidget(combo, row, 1)
            formats.setRowMinimumHeight(row, 38)
            self.format_widgets[control.key] = combo
        formats.setColumnStretch(1, 1)
        self.format_box.setLayout(formats)

        self.options_panel = QFrame()
        self.options_panel.setObjectName("SoftPanel")
        self.options_panel.setMinimumWidth(320)
        self.options_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        option_grid = QGridLayout(self.options_panel)
        option_grid.setContentsMargins(14, 12, 14, 12)
        option_grid.setHorizontalSpacing(12)
        option_grid.setVerticalSpacing(8)

        self.recursive = QCheckBox("递归扫描")
        self.recursive.setChecked(True)
        self.transcode = QCheckBox("转码")
        self.transcode.setChecked(True)
        self.cover = QCheckBox("封面")
        self.cover.setChecked(False)
        self.album = QCheckBox("专辑信息")
        self.album.setChecked(False)
        self.fetch_ekey = QCheckBox("补取 ekey")
        self.fetch_ekey.setChecked(True)
        self.cache_ekey = QCheckBox("缓存 ekey")
        self.cache_ekey.setChecked(True)

        self.sample_rate = QComboBox()
        self.sample_rate.setObjectName("Combo")
        self.sample_rate.setFixedHeight(32)
        self.sample_rate.setMinimumWidth(112)
        self.sample_rate.addItem("原采样率", None)
        for value in TRANSCODE_SAMPLE_RATE_OPTIONS:
            self.sample_rate.addItem(str(value), value)
        self.bitrate = QComboBox()
        self.bitrate.setObjectName("Combo")
        self.bitrate.setFixedHeight(32)
        self.bitrate.setMinimumWidth(112)
        for value in TRANSCODE_BITRATE_OPTIONS:
            self.bitrate.addItem(str(value), value)
        self.bitrate.setCurrentText("320")

        self.workers = QSpinBox()
        self.workers.setObjectName("Spin")
        self.workers.setFixedHeight(32)
        self.workers.setMinimumWidth(80)
        self.workers.setRange(1, 4)
        self.workers.setValue(2)

        option_grid.addWidget(self.recursive, 0, 0)
        option_grid.addWidget(self.transcode, 0, 1)
        option_grid.addWidget(self.cover, 0, 2)
        option_grid.addWidget(self.album, 0, 3)
        option_grid.addWidget(QLabel("采样率"), 1, 0)
        option_grid.addWidget(self.sample_rate, 1, 1)
        option_grid.addWidget(QLabel("码率"), 1, 2)
        option_grid.addWidget(self.bitrate, 1, 3)
        option_grid.addWidget(QLabel("并发"), 2, 0)
        option_grid.addWidget(self.workers, 2, 1)
        option_grid.addWidget(self.fetch_ekey, 2, 2)
        option_grid.addWidget(self.cache_ekey, 2, 3)
        option_grid.setColumnStretch(1, 1)
        option_grid.setColumnStretch(3, 1)
        self.config_layout.addWidget(self.format_box, 1)
        self.config_layout.addWidget(self.options_panel, 0)
        self.form_layout.addLayout(self.config_layout)

        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("FormScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setMinimumHeight(250)
        self.form_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.form_scroll.setWidget(form)
        root.addWidget(self.form_scroll, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 14, 0)
        button_row.addStretch(1)
        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("PrimaryButton" if spec.enabled else "DisabledButton")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setEnabled(spec.enabled)
        self.start_button.setFixedHeight(36)
        self.start_button.setFixedWidth(126)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(spec.platform_id))
        button_row.addWidget(self.start_button)
        root.addLayout(button_row)

        if spec.platform_id != "qq":
            self.fetch_ekey.setVisible(False)
            self.cache_ekey.setVisible(False)

        QTimer.singleShot(0, self._update_responsive_layout)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        QTimer.singleShot(0, self._update_responsive_layout)

    def _update_responsive_layout(self) -> None:
        if not hasattr(self, "form_scroll"):
            return
        viewport_width = self.form_scroll.viewport().width()
        if viewport_width <= 0:
            return
        margins = self.form_layout.contentsMargins()
        inner_width = max(0, viewport_width - margins.left() - margins.right())
        inline_width = self.format_box.minimumWidth() + self.options_panel.sizeHint().width() + self.config_layout.spacing()
        compact = inner_width < inline_width

        target_direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        if self.config_layout.direction() != target_direction:
            self.config_layout.setDirection(target_direction)

        self.config_layout.setStretch(0, 0 if compact else 1)
        self.config_layout.setStretch(1, 0)
        self.options_panel.setMinimumWidth(320 if compact else self.options_panel.sizeHint().width())
        self.options_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding if compact else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )

    def format_values(self) -> dict[str, str]:
        return {key: widget.currentText().strip().lower() for key, widget in self.format_widgets.items()}

    def set_format_values(self, values: dict[str, Any]) -> None:
        for key, widget in self.format_widgets.items():
            value = str(values.get(key, widget.currentText()) or widget.currentText()).strip().lower()
            if value in [widget.itemText(index) for index in range(widget.count())]:
                widget.setCurrentText(value)

    def sample_rate_value(self) -> int | None:
        value = self.sample_rate.currentData()
        return int(value) if value else None

    def bitrate_value(self) -> int | None:
        value = self.bitrate.currentData()
        return int(value) if value else None

    def set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(self.spec.enabled and not busy)
        self.start_button.setText("运行中" if busy else "开始")


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.paths = RuntimePaths.discover()
        self.paths.ensure_runtime_dirs()
        save_default_config_if_missing(self.paths)
        self.root_config, self.config = load_config(self.paths)
        self.bridge = UiBridge()
        self.stop_event = threading.Event()
        self.running = False
        self.started_at = 0.0
        self.pages: dict[str, PlatformPage] = {}
        self.specs = platform_specs()
        self._build_ui()
        self._connect()
        self._load_config()
        self._append_log("客户端已启动")

    def _build_ui(self) -> None:
        self.setWindowTitle("QKKDecrypt")
        self.setObjectName("RootWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.resize(900, 680)
        self.setMinimumSize(760, 600)
        self.move(32, 24)
        self.setStyleSheet(build_stylesheet())

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(150)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(10)
        brand = QLabel("QKK")
        brand.setObjectName("Brand")
        side_layout.addWidget(brand)
        self.platform_list = QListWidget()
        self.platform_list.setObjectName("PlatformList")
        for spec in self.specs:
            item = QListWidgetItem(spec.title)
            item.setData(Qt.ItemDataRole.UserRole, spec.platform_id)
            self.platform_list.addItem(item)
        side_layout.addWidget(self.platform_list, 1)
        root.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)

        self.stack = QStackedWidget()
        self.stack.setObjectName("Stack")
        self.stack.setMinimumHeight(400)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for spec in self.specs:
            page = PlatformPage(spec)
            self.pages[spec.platform_id] = page
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)

        run_panel = QFrame()
        run_panel.setObjectName("Panel")
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(16, 12, 16, 12)
        run_layout.setSpacing(7)

        top = QHBoxLayout()
        self.progress_label = QLabel("进度 0 / 0")
        self.progress_label.setObjectName("Muted")
        self.current_file = QLabel("当前文件 -")
        self.current_file.setObjectName("Muted")
        self.current_file.setMinimumWidth(120)
        self.current_file.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        top.addWidget(self.progress_label)
        top.addWidget(self.current_file, 1)
        top.addWidget(self.stop_button)
        run_layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setObjectName("Progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        run_layout.addWidget(self.progress)

        stats = QHBoxLayout()
        self.success_label = QLabel("成功 0")
        self.failed_label = QLabel("失败 0")
        self.skipped_label = QLabel("跳过 0")
        self.elapsed_label = QLabel("耗时 0.0s")
        for label in (self.success_label, self.failed_label, self.skipped_label, self.elapsed_label):
            label.setObjectName("Stat")
            stats.addWidget(label)
        stats.addStretch(1)
        run_layout.addLayout(stats)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(52)
        self.log_view.setMaximumHeight(72)
        run_layout.addWidget(self.log_view, 1)
        run_panel.setMinimumHeight(152)
        content.addWidget(run_panel, 0)
        root.addLayout(content, 1)

    def _connect(self) -> None:
        self.platform_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.platform_list.setCurrentRow(0)
        for page in self.pages.values():
            page.start_requested.connect(self._start_platform)
        self.stop_button.clicked.connect(self._stop_run)
        self.bridge.event_received.connect(self._handle_run_event)
        self.bridge.run_finished.connect(self._handle_run_finished)
        self.bridge.log_message.connect(self._append_log)

    def _load_config(self) -> None:
        shared = self.config.get("shared", {})
        qq = self.config.get("qq", {})
        qq_page = self.pages["qq"]
        qq_page.input_path.set_text(str(qq.get("input_dir", "")))
        qq_page.output_dir.set_text(str(shared.get("output_dir", self.paths.output_dir)))
        qq_page.set_format_values(dict(qq.get("format_rules", {})))
        qq_page.recursive.setChecked(bool(shared.get("recursive", True)))
        qq_page.transcode.setChecked(bool(shared.get("transcode_enabled", True)))
        qq_page.workers.setValue(int(shared.get("transcode_max_workers", 2) or 2))
        qq_page.cover.setChecked(bool(shared.get("embed_cover_art", False)))
        qq_page.album.setChecked(bool(shared.get("supplement_album_metadata", False)))
        bitrate = qq.get("transcode_bitrate_kbps", 320)
        if bitrate:
            qq_page.bitrate.setCurrentText(str(bitrate))
        sample_rate = qq.get("transcode_sample_rate_hz")
        if sample_rate:
            qq_page.sample_rate.setCurrentText(str(sample_rate))
        qq_page.fetch_ekey.setChecked(bool(qq.get("qq_fetch_missing_ekey", True)))
        qq_page.cache_ekey.setChecked(bool(qq.get("qq_cache_ekeys", True)))

        for platform_id in ("kugou", "netease"):
            page = self.pages[platform_id]
            values = self.config.get(platform_id, {})
            page.input_path.set_text(str(values.get("input_dir", "")))
            page.output_dir.set_text(str(values.get("output_dir", self.paths.output_dir / platform_id)))
            page.set_format_values(values)
        kuwo_page = self.pages["kuwo"]
        kuwo_page.output_dir.set_text(str(self.paths.output_dir / "kuwo"))

    def _save_qq_config(self, page: PlatformPage) -> None:
        self.root_config, self.config = load_config(self.paths)
        self.config["shared"]["output_dir"] = page.output_dir.text() or str(self.paths.output_dir)
        self.config["shared"]["recursive"] = page.recursive.isChecked()
        self.config["shared"]["transcode_enabled"] = page.transcode.isChecked()
        self.config["shared"]["transcode_max_workers"] = page.workers.value()
        self.config["shared"]["embed_cover_art"] = page.cover.isChecked()
        self.config["shared"]["supplement_album_metadata"] = page.album.isChecked()
        self.config["qq"]["input_dir"] = page.input_path.text()
        self.config["qq"]["output_dir"] = page.output_dir.text()
        self.config["qq"]["format_rules"] = page.format_values()
        self.config["qq"]["transcode_sample_rate_hz"] = page.sample_rate_value()
        self.config["qq"]["transcode_bitrate_kbps"] = page.bitrate_value()
        self.config["qq"]["qq_fetch_missing_ekey"] = page.fetch_ekey.isChecked()
        self.config["qq"]["qq_cache_ekeys"] = page.cache_ekey.isChecked()
        save_config(self.paths, self.root_config, self.config)

    def _start_platform(self, platform_id: str) -> None:
        if platform_id != "qq":
            self._append_log(f"{self.pages[platform_id].spec.title}: 暂不可用")
            return
        if self.running:
            return
        page = self.pages["qq"]
        input_text = page.input_path.text()
        output_text = page.output_dir.text()
        if not input_text:
            QMessageBox.warning(self, "输入路径", "请选择输入路径")
            return
        if not output_text:
            QMessageBox.warning(self, "输出目录", "请选择输出目录")
            return
        input_path = pathlib.Path(input_text)
        output_dir = pathlib.Path(output_text)
        if not input_path.exists():
            QMessageBox.warning(self, "输入路径", "输入路径不存在")
            return
        try:
            validate_writable_output_dir(output_dir)
        except OSError as exc:
            QMessageBox.warning(self, "输出目录", f"输出目录不可写：{output_dir}\n{exc}")
            return
        self._save_qq_config(page)
        self.stop_event.clear()
        self.running = True
        self.started_at = time.perf_counter()
        self._set_busy(True)
        self._reset_progress()
        self._append_log("QQ音乐: 开始")

        options = PlatformRunOptions(
            input_path=input_path,
            output_dir=output_dir,
            recursive=page.recursive.isChecked(),
            transcode_enabled=page.transcode.isChecked(),
            transcode_max_workers=page.workers.value(),
            embed_cover_art=page.cover.isChecked(),
            supplement_album_metadata=page.album.isChecked(),
            sample_rate_hz=page.sample_rate_value(),
            bitrate_kbps=page.bitrate_value(),
            qq_fetch_missing_ekey=page.fetch_ekey.isChecked(),
            qq_cache_ekeys=page.cache_ekey.isChecked(),
            format_rules=page.format_values(),
            event_sink=lambda event, payload: self.bridge.event_received.emit(event, dict(payload)),
            stop_requested=lambda: self.stop_event.is_set(),
        )
        thread = threading.Thread(target=self._run_qq, args=(options,), daemon=True)
        thread.start()

    def _run_qq(self, options: PlatformRunOptions) -> None:
        result_code = 2
        try:
            adapter = build_platform_adapter("qq")
            result_code = run_batch(build_qq_batch_config(options), adapter)
        except Exception as exc:
            self.bridge.log_message.emit(f"QQ音乐: {exc}")
        finally:
            self.bridge.run_finished.emit(result_code)

    def _stop_run(self) -> None:
        if self.running:
            self.stop_event.set()
            self._append_log("停止请求已发送")

    def _reset_progress(self) -> None:
        self.progress.setValue(0)
        self.progress_label.setText("进度 0 / 0")
        self.current_file.setText("当前文件 -")
        self.success_label.setText("成功 0")
        self.failed_label.setText("失败 0")
        self.skipped_label.setText("跳过 0")
        self.elapsed_label.setText("耗时 0.0s")

    def _set_busy(self, busy: bool) -> None:
        self.pages["qq"].set_busy(busy)
        self.stop_button.setEnabled(busy)

    def _handle_run_event(self, event_name: str, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        total = int(data.get("total") or data.get("candidate_count") or 0)
        index = int(data.get("index") or 0)
        if event_name == "batch_started":
            self.progress.setValue(0)
            self.progress_label.setText(f"进度 0 / {total}")
            self._append_log(f"候选文件 {total}")
            return
        if event_name == "file_started":
            self.current_file.setText(f"当前文件 {pathlib.Path(str(data.get('input_path', ''))).name}")
            self.progress_label.setText(f"进度 {max(0, index - 1)} / {total}")
            if total:
                self.progress.setValue(int(max(0, index - 1) / total * 100))
            return
        if event_name in {"file_decrypted", "batch_transcode_progress"}:
            name = pathlib.Path(str(data.get("input_path", ""))).name
            self.current_file.setText(f"当前文件 {name}")
            message = str(data.get("message") or event_name)
            self._append_log(message)
            return
        if event_name == "file_finished":
            result = str(data.get("result") or "")
            if total:
                self.progress.setValue(int(index / total * 100))
                self.progress_label.setText(f"进度 {index} / {total}")
            name = pathlib.Path(str(data.get("input_path", data.get("output_path", "")))).name
            reason = str(data.get("reason") or result)
            self._append_log(f"{name}: {reason}")
            return
        if event_name == "batch_transcode_started":
            self._append_log(f"统一转码 {int(data.get('pending_count') or 0)}")
            return
        if event_name == "batch_finished":
            self.progress.setValue(100)
            count = int(data.get("candidate_count") or 0)
            self.progress_label.setText(f"进度 {count} / {count}")
            self.success_label.setText(f"成功 {int(data.get('success_count') or 0)}")
            self.failed_label.setText(f"失败 {int(data.get('failed_count') or 0)}")
            self.skipped_label.setText(f"跳过 {int(data.get('skipped_count') or 0)}")
            hotspot = data.get("timing_hotspot_stage") or {}
            if isinstance(hotspot, dict) and hotspot.get("stage"):
                self._append_log(f"耗时热点 {hotspot.get('stage')} {_format_seconds(float(hotspot.get('total_sec') or 0.0))}")
            report = str(data.get("batch_report_txt") or "")
            if report:
                self._append_log(f"报告 {report}")

    def _handle_run_finished(self, result_code: int) -> None:
        self.running = False
        self._set_busy(False)
        elapsed = time.perf_counter() - self.started_at if self.started_at else 0.0
        self.elapsed_label.setText(f"耗时 {_format_seconds(elapsed)}")
        self._append_log(f"QQ音乐: 结束 code={result_code}")

    def _append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.log_view.appendPlainText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def build_stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: Microsoft YaHei UI, Segoe UI; font-size: 13px; }}
    QWidget#RootWindow {{ background: {APP_BG}; }}
    QFrame#Sidebar, QFrame#Panel {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QFrame#SoftPanel {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QStackedWidget#Stack, QScrollArea#FormScroll {{ background: transparent; border: 0; }}
    QLabel, QCheckBox {{ background: transparent; }}
    QLabel#Brand {{ font-size: 22px; font-weight: 700; color: {GREEN_DARK}; padding: 6px 6px; }}
    QLabel#PageTitle {{ font-size: 21px; font-weight: 700; }}
    QLabel#Muted, QLabel#FieldLabel {{ color: {MUTED}; }}
    QLabel#StatusOk {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; border: 1px solid {GREEN}; border-radius: 8px; padding: 6px 10px; font-weight: 600; }}
    QLabel#StatusOff {{ background: {RED_SOFT}; color: {RED_DARK}; border: 1px solid {RED}; border-radius: 8px; padding: 6px 10px; font-weight: 600; }}
    QLabel#Stat {{ background: {GREEN_SOFT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; color: {GREEN_DARK}; font-weight: 600; }}
    QLineEdit#Input, QComboBox#Combo, QSpinBox#Spin {{ background: white; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px; min-height: 22px; }}
    QLineEdit#Input:focus, QComboBox#Combo:focus, QSpinBox#Spin:focus {{ border: 1px solid {GREEN}; }}
    QPushButton {{ border: 0; border-radius: 8px; padding: 7px 14px; background: {GREEN_SOFT}; color: {GREEN_DARK}; font-weight: 600; }}
    QPushButton#PrimaryButton {{ background: {GREEN}; color: white; min-width: 112px; }}
    QPushButton#PrimaryButton:hover {{ background: {GREEN_DARK}; }}
    QPushButton#DangerButton {{ background: {RED_SOFT}; color: {RED_DARK}; }}
    QPushButton#SmallButton {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; padding: 6px 10px; }}
    QPushButton#DisabledButton, QPushButton:disabled {{ background: #EEF0F2; color: #98A2B3; }}
    QCheckBox {{ spacing: 8px; }}
    QListWidget#PlatformList {{ background: transparent; border: 0; outline: 0; }}
    QListWidget#PlatformList::item {{ padding: 12px 10px; border-radius: 8px; margin: 2px 0; }}
    QListWidget#PlatformList::item:selected {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; }}
    QListWidget#PlatformList::item:hover {{ background: {RED_SOFT}; }}
    QProgressBar#Progress {{ background: #EEF0F2; border: 0; border-radius: 6px; height: 12px; }}
    QProgressBar#Progress::chunk {{ background: {GREEN}; border-radius: 6px; }}
    QPlainTextEdit#Log {{ background: #101828; color: #E6F2EB; border: 1px solid #1D2939; border-radius: 8px; padding: 8px; font-family: Consolas, Microsoft YaHei UI; }}
    """


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
