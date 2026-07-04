from __future__ import annotations

import pathlib
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
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
from src.Infrastructure.update_service import APP_VERSION, run_update
from src.Presentation.ui_state import (
    PlatformRunOptions,
    PlatformSpec,
    build_platform_batch_config,
    platform_specs,
    validate_platform_runtime_for_ui,
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
    update_finished = Signal(bool)


class PathRow(QWidget):
    def __init__(self, label: str, *, allow_file: bool = False, allow_open: bool = False) -> None:
        super().__init__()
        self.allow_file = allow_file
        self.allow_open = allow_open
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
        self.open_button = QPushButton("打开")
        self.open_button.setObjectName("OpenOutputButton")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setFixedWidth(60)
        self.open_button.setFixedHeight(32)
        self.open_button.setVisible(allow_open)
        root.addWidget(title, 0, 0, 1, 4)
        root.addWidget(self.edit, 1, 0)
        root.addWidget(self.dir_button, 1, 1)
        root.addWidget(self.file_button, 1, 2)
        root.addWidget(self.open_button, 1, 3)
        root.setColumnStretch(0, 1)
        self.dir_button.clicked.connect(self._choose_dir)
        self.file_button.clicked.connect(self._choose_file)
        self.open_button.clicked.connect(self._open_path)

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

    def _open_path(self) -> None:
        value = self.text()
        if not value:
            return
        path = pathlib.Path(value)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


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
        self.output_dir = PathRow("输出目录", allow_open=True)
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
        option_grid.setContentsMargins(12, 12, 12, 12)
        option_grid.setHorizontalSpacing(8)
        option_grid.setVerticalSpacing(8)

        self.recursive = QCheckBox("递归扫描")
        self.recursive.setChecked(True)
        self.transcode = QCheckBox("转码")
        self.transcode.setChecked(True)
        self.cover = QCheckBox("封面")
        self.cover.setChecked(False)
        self.album = QCheckBox("专辑信息")
        self.album.setChecked(False)
        self.group_by_artist = QCheckBox("按音乐作者分类")
        self.group_by_artist.setObjectName("GroupByArtist")
        self.group_by_artist.setChecked(False)
        self.fetch_ekey = QCheckBox("补取 ekey")
        self.fetch_ekey.setChecked(True)
        self.cache_ekey = QCheckBox("缓存 ekey")
        self.cache_ekey.setChecked(True)

        self.sample_rate = QComboBox()
        self.sample_rate.setObjectName("Combo")
        self.sample_rate.setFixedHeight(32)
        self.sample_rate.setMinimumWidth(96)
        self.sample_rate.addItem("原采样率", None)
        for value in TRANSCODE_SAMPLE_RATE_OPTIONS:
            self.sample_rate.addItem(str(value), value)
        self.bitrate = QComboBox()
        self.bitrate.setObjectName("Combo")
        self.bitrate.setFixedHeight(32)
        self.bitrate.setMinimumWidth(96)
        for value in TRANSCODE_BITRATE_OPTIONS:
            self.bitrate.addItem(str(value), value)
        self.bitrate.setCurrentText("320")

        self.workers = QSpinBox()
        self.workers.setObjectName("Spin")
        self.workers.setObjectName("TranscodeWorkers")
        self.workers.setFixedHeight(32)
        self.workers.setMinimumWidth(72)
        self.workers.setRange(1, 9999)
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
        option_grid.addWidget(self.group_by_artist, 3, 0, 1, 2)
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
        try:
            viewport_width = self.form_scroll.viewport().width()
        except RuntimeError:
            return
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
        self.updating = False
        self.started_at = 0.0
        self._run_counts = {"success": 0, "failed": 0, "skipped": 0}
        self._run_total = 0
        self._decoded_inputs: set[str] = set()
        self._transcode_counts = {"success": 0, "failed": 0, "waiting": 0}
        self._transcode_total = 0
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
        self.version_label = QLabel(APP_VERSION)
        self.version_label.setObjectName("AppVersion")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_button = QPushButton("更新系统")
        self.update_button.setObjectName("UpdateButton")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setFixedHeight(34)
        side_layout.addWidget(self.version_label)
        side_layout.addWidget(self.update_button)
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
        run_layout.setSpacing(10)

        top = QHBoxLayout()
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_message = QLabel("客户端已启动")
        self.status_message.setObjectName("StatusMessage")
        self.status_message.setWordWrap(True)
        self.status_message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self.status_message, 1)
        top.addWidget(self.stop_button)
        run_layout.addLayout(top)

        self.run_progress_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.run_progress_layout.setSpacing(12)

        decrypt_card = QFrame()
        decrypt_card.setObjectName("StatusBlock")
        decrypt_layout = QVBoxLayout(decrypt_card)
        decrypt_layout.setContentsMargins(12, 10, 12, 10)
        decrypt_layout.setSpacing(8)
        decrypt_head = QHBoxLayout()
        self.progress_label = QLabel("解密 0 / 0")
        self.progress_label.setObjectName("DecryptProgressLabel")
        self.current_file = QLabel("当前文件 -")
        self.current_file.setObjectName("Muted")
        self.current_file.setMinimumWidth(120)
        self.current_file.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        decrypt_head.addWidget(self.progress_label)
        decrypt_head.addWidget(self.current_file, 1)
        decrypt_layout.addLayout(decrypt_head)
        self.progress = QProgressBar()
        self.progress.setObjectName("DecryptProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        decrypt_layout.addWidget(self.progress)
        decrypt_stats = QHBoxLayout()
        self.success_label = QLabel("成功 0")
        self.failed_label = QLabel("失败 0")
        self.skipped_label = QLabel("跳过 0")
        self.decode_rate_label = QLabel("解密成功率 0%")
        self.decode_rate_label.setObjectName("DecodeSuccessRate")
        for label in (self.success_label, self.failed_label, self.skipped_label):
            label.setObjectName("Stat")
            decrypt_stats.addWidget(label)
        decrypt_stats.addWidget(self.decode_rate_label)
        decrypt_stats.addStretch(1)
        decrypt_layout.addLayout(decrypt_stats)

        transcode_card = QFrame()
        transcode_card.setObjectName("StatusBlock")
        transcode_layout = QVBoxLayout(transcode_card)
        transcode_layout.setContentsMargins(12, 10, 12, 10)
        transcode_layout.setSpacing(8)
        transcode_head = QHBoxLayout()
        self.transcode_progress_label = QLabel("转码 0 / 0")
        self.transcode_progress_label.setObjectName("TranscodeProgressLabel")
        self.transcode_current_file = QLabel("当前文件 -")
        self.transcode_current_file.setObjectName("Muted")
        self.transcode_current_file.setMinimumWidth(120)
        self.transcode_current_file.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        transcode_head.addWidget(self.transcode_progress_label)
        transcode_head.addWidget(self.transcode_current_file, 1)
        transcode_layout.addLayout(transcode_head)
        self.transcode_progress = QProgressBar()
        self.transcode_progress.setObjectName("TranscodeProgress")
        self.transcode_progress.setRange(0, 100)
        self.transcode_progress.setValue(0)
        self.transcode_progress.setTextVisible(False)
        transcode_layout.addWidget(self.transcode_progress)
        transcode_stats = QHBoxLayout()
        self.transcode_success_label = QLabel("成功 0")
        self.transcode_failed_label = QLabel("失败 0")
        self.transcode_waiting_label = QLabel("等待 0")
        self.transcode_rate_label = QLabel("转码成功率 0%")
        self.transcode_rate_label.setObjectName("TranscodeSuccessRate")
        self.elapsed_label = QLabel("耗时 0.0s")
        for label in (
            self.transcode_success_label,
            self.transcode_failed_label,
            self.transcode_waiting_label,
            self.elapsed_label,
        ):
            label.setObjectName("Stat")
            transcode_stats.addWidget(label)
        transcode_stats.addWidget(self.transcode_rate_label)
        transcode_stats.addStretch(1)
        transcode_layout.addLayout(transcode_stats)

        self.run_progress_layout.addWidget(decrypt_card, 1)
        self.run_progress_layout.addWidget(transcode_card, 1)
        run_layout.addLayout(self.run_progress_layout)
        self.run_panel = run_panel
        run_panel.setMinimumHeight(166)
        content.addWidget(run_panel, 0)
        root.addLayout(content, 1)
        QTimer.singleShot(0, self._update_run_layout)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        QTimer.singleShot(0, self._update_run_layout)

    def _update_run_layout(self) -> None:
        if not hasattr(self, "run_progress_layout") or not hasattr(self, "run_panel"):
            return
        compact = self.run_panel.width() < 720
        target_direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        if self.run_progress_layout.direction() != target_direction:
            self.run_progress_layout.setDirection(target_direction)
        self.run_panel.setMinimumHeight(244 if compact else 166)

    def _connect(self) -> None:
        self.platform_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.platform_list.setCurrentRow(0)
        for page in self.pages.values():
            page.start_requested.connect(self._start_platform)
        self.stop_button.clicked.connect(self._stop_run)
        self.update_button.clicked.connect(self._start_update)
        self.bridge.event_received.connect(self._handle_run_event)
        self.bridge.run_finished.connect(self._handle_run_finished)
        self.bridge.log_message.connect(self._append_log)
        self.bridge.update_finished.connect(self._handle_update_finished)

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
        qq_page.group_by_artist.setChecked(bool(shared.get("group_by_artist", False)))
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
            page.recursive.setChecked(bool(shared.get("recursive", True)))
            page.transcode.setChecked(bool(shared.get("transcode_enabled", True)))
            page.workers.setValue(int(shared.get("transcode_max_workers", 2) or 2))
            page.cover.setChecked(bool(shared.get("embed_cover_art", False)))
            page.album.setChecked(bool(shared.get("supplement_album_metadata", False)))
            page.group_by_artist.setChecked(bool(shared.get("group_by_artist", False)))
        kuwo_page = self.pages["kuwo"]
        kuwo_page.output_dir.set_text(str(self.paths.output_dir / "kuwo"))

    def _save_platform_config(self, platform_id: str, page: PlatformPage) -> None:
        self.root_config, self.config = load_config(self.paths)
        self.config["shared"]["output_dir"] = page.output_dir.text() or str(self.paths.output_dir)
        self.config["shared"]["recursive"] = page.recursive.isChecked()
        self.config["shared"]["transcode_enabled"] = page.transcode.isChecked()
        self.config["shared"]["transcode_max_workers"] = page.workers.value()
        self.config["shared"]["embed_cover_art"] = page.cover.isChecked()
        self.config["shared"]["supplement_album_metadata"] = page.album.isChecked()
        self.config["shared"]["group_by_artist"] = page.group_by_artist.isChecked()
        platform_config = self.config.setdefault(platform_id, {})
        platform_config["input_dir"] = page.input_path.text()
        platform_config["output_dir"] = page.output_dir.text()
        if platform_id == "qq":
            platform_config["format_rules"] = page.format_values()
            platform_config["qq_fetch_missing_ekey"] = page.fetch_ekey.isChecked()
            platform_config["qq_cache_ekeys"] = page.cache_ekey.isChecked()
        else:
            platform_config.update(page.format_values())
        platform_config["transcode_sample_rate_hz"] = page.sample_rate_value()
        platform_config["transcode_bitrate_kbps"] = page.bitrate_value()
        save_config(self.paths, self.root_config, self.config)

    def _start_platform(self, platform_id: str) -> None:
        if platform_id not in self.pages:
            return
        page = self.pages[platform_id]
        if not page.spec.enabled:
            self._append_log(f"{page.spec.title}: 暂不可用")
            return
        if self.running:
            return
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
        adapter = build_platform_adapter(platform_id)
        platform_settings = dict(self.config.get(platform_id, {}))
        if platform_id == "qq":
            platform_settings["format_rules"] = page.format_values()
        else:
            platform_settings.update(page.format_values())
        validation = validate_platform_runtime_for_ui(
            platform_id,
            adapter,
            platform_settings,
            input_path,
            output_dir,
            page.recursive.isChecked(),
        )
        if not validation.ok:
            QMessageBox.warning(self, page.spec.title, validation.reason or "运行环境不可用")
            return
        self.config.setdefault(platform_id, {}).update(validation.settings)
        self._save_platform_config(platform_id, page)
        self.stop_event.clear()
        self.running = True
        self.started_at = time.perf_counter()
        self._set_busy(True)
        self._reset_progress()
        self._append_log(f"{page.spec.title}: 开始")

        options = PlatformRunOptions(
            input_path=input_path,
            output_dir=output_dir,
            recursive=page.recursive.isChecked(),
            transcode_enabled=page.transcode.isChecked(),
            transcode_max_workers=page.workers.value(),
            embed_cover_art=page.cover.isChecked(),
            supplement_album_metadata=page.album.isChecked(),
            group_by_artist=page.group_by_artist.isChecked(),
            sample_rate_hz=page.sample_rate_value(),
            bitrate_kbps=page.bitrate_value(),
            qq_fetch_missing_ekey=page.fetch_ekey.isChecked(),
            qq_cache_ekeys=page.cache_ekey.isChecked(),
            format_rules=page.format_values(),
            platform_settings=dict(validation.settings),
            event_sink=lambda event, payload: self.bridge.event_received.emit(event, dict(payload)),
            stop_requested=lambda: self.stop_event.is_set(),
        )
        thread = threading.Thread(target=self._run_platform, args=(platform_id, options), daemon=True)
        thread.start()

    def _run_platform(self, platform_id: str, options: PlatformRunOptions) -> None:
        result_code = 2
        title = self.pages[platform_id].spec.title if platform_id in self.pages else platform_id
        try:
            adapter = build_platform_adapter(platform_id)
            result_code = run_batch(build_platform_batch_config(platform_id, options), adapter)
        except Exception as exc:
            self.bridge.log_message.emit(f"{title}: {exc}")
        finally:
            self.bridge.run_finished.emit(result_code)

    def _stop_run(self) -> None:
        if self.running:
            self.stop_event.set()
            self._append_log("停止请求已发送")

    def _start_update(self) -> None:
        if self.running:
            self._append_log("正在转换，更新已跳过")
            return
        if self.updating:
            return
        self.updating = True
        self.update_button.setEnabled(False)
        self._append_log(f"开始更新系统，当前版本 {APP_VERSION}")
        thread = threading.Thread(target=self._run_update_job, daemon=True)
        thread.start()

    def _run_update_job(self) -> None:
        ok = False
        try:
            result = run_update(self.paths.root_dir)
            ok = bool(result.ok)
            self.bridge.log_message.emit(result.message)
        except Exception as exc:
            self.bridge.log_message.emit(f"更新失败：{exc}")
        finally:
            self.bridge.update_finished.emit(ok)

    def _reset_progress(self) -> None:
        self._reset_run_counts()
        self._reset_transcode_counts()
        self.progress.setValue(0)
        self.progress_label.setText("解密 0 / 0")
        self.current_file.setText("当前文件 -")
        self.transcode_progress.setValue(0)
        self.transcode_progress_label.setText("转码 0 / 0")
        self.transcode_current_file.setText("当前文件 -")
        self._render_run_counts()
        self._render_transcode_counts()
        self.elapsed_label.setText("耗时 0.0s")
        self.status_message.setText("准备开始")

    def _reset_run_counts(self) -> None:
        self._run_counts = {"success": 0, "failed": 0, "skipped": 0}
        self._run_total = 0
        self._decoded_inputs = set()

    def _reset_transcode_counts(self) -> None:
        self._transcode_counts = {"success": 0, "failed": 0, "waiting": 0}
        self._transcode_total = 0

    def _render_run_counts(self) -> None:
        self.success_label.setText(f"成功 {self._run_counts['success']}")
        self.failed_label.setText(f"失败 {self._run_counts['failed']}")
        self.skipped_label.setText(f"跳过 {self._run_counts['skipped']}")
        attempts = self._run_counts["success"] + self._run_counts["failed"]
        rate = int(self._run_counts["success"] / attempts * 100) if attempts else 0
        self.decode_rate_label.setText(f"解密成功率 {rate}%")

    def _render_transcode_counts(self) -> None:
        self.transcode_success_label.setText(f"成功 {self._transcode_counts['success']}")
        self.transcode_failed_label.setText(f"失败 {self._transcode_counts['failed']}")
        self.transcode_waiting_label.setText(f"等待 {self._transcode_counts['waiting']}")
        attempts = self._transcode_counts["success"] + self._transcode_counts["failed"]
        rate = int(self._transcode_counts["success"] / attempts * 100) if attempts else 0
        self.transcode_rate_label.setText(f"转码成功率 {rate}%")

    def _payload_int(self, data: dict, *keys: str) -> int:
        for key in keys:
            value = data.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _payload_total(self, data: dict) -> int:
        return self._payload_int(data, "total", "candidate_count", "total_jobs")

    def _completed_count(self) -> int:
        return self._run_counts["success"] + self._run_counts["failed"] + self._run_counts["skipped"]

    def _set_run_progress(self, completed: int | None = None, total: int = 0) -> None:
        if total > 0:
            self._run_total = total
        effective_total = self._run_total
        completed_count = self._completed_count() if completed is None else max(0, int(completed))
        if effective_total <= 0:
            self.progress.setValue(0)
            self.progress_label.setText(f"解密 {completed_count} / 0")
            return
        completed_count = min(completed_count, effective_total)
        self.progress.setValue(int(completed_count / effective_total * 100))
        self.progress_label.setText(f"解密 {completed_count} / {effective_total}")

    def _set_transcode_progress(self, completed: int | None = None, total: int = 0) -> None:
        if total > 0:
            self._transcode_total = total
        effective_total = self._transcode_total
        completed_count = (
            self._transcode_counts["success"] + self._transcode_counts["failed"]
            if completed is None
            else max(0, int(completed))
        )
        if effective_total <= 0:
            self.transcode_progress.setValue(0)
            self.transcode_progress_label.setText(f"转码 {completed_count} / 0")
            return
        completed_count = min(completed_count, effective_total)
        self.transcode_progress.setValue(int(completed_count / effective_total * 100))
        self.transcode_progress_label.setText(f"转码 {completed_count} / {effective_total}")

    def _payload_input_id(self, data: dict) -> str:
        value = str(data.get("input_path") or data.get("output_path") or "").strip()
        return value.lower()

    def _record_decrypted(self, input_id: str) -> None:
        if input_id and input_id in self._decoded_inputs:
            return
        if input_id:
            self._decoded_inputs.add(input_id)
        self._run_counts["success"] += 1
        self._render_run_counts()

    def _record_file_result(self, result: str, input_id: str = "") -> None:
        normalized = result.strip().lower()
        if normalized == "success":
            if input_id and input_id in self._decoded_inputs:
                self._render_run_counts()
                return
            if input_id:
                self._decoded_inputs.add(input_id)
            self._run_counts["success"] += 1
        elif normalized in {"already_decrypted", "skipped"}:
            if input_id and input_id in self._decoded_inputs:
                self._render_run_counts()
                return
            self._run_counts["skipped"] += 1
        elif normalized == "failed":
            if input_id and input_id in self._decoded_inputs:
                self._render_run_counts()
                return
            self._run_counts["failed"] += 1
        self._render_run_counts()

    def _set_busy(self, busy: bool) -> None:
        for page in self.pages.values():
            page.set_busy(busy)
        self.stop_button.setEnabled(busy)
        self.update_button.setEnabled(not busy and not self.updating)

    def _handle_run_event(self, event_name: str, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        total = self._payload_total(data)
        if event_name == "batch_started":
            self._reset_run_counts()
            self._reset_transcode_counts()
            self._render_run_counts()
            self._render_transcode_counts()
            self.transcode_progress.setValue(0)
            self.transcode_progress_label.setText("转码 0 / 0")
            self.transcode_current_file.setText("当前文件 -")
            self._set_run_progress(0, total)
            self._append_log(f"候选文件 {total}")
            return
        if event_name == "file_started":
            self.current_file.setText(f"当前文件 {pathlib.Path(str(data.get('input_path', ''))).name}")
            self._set_run_progress(total=total)
            return
        if event_name == "file_decrypted":
            name = pathlib.Path(str(data.get("input_path", ""))).name
            self.current_file.setText(f"当前文件 {name}")
            self._record_decrypted(self._payload_input_id(data))
            self._set_run_progress(total=total)
            message = str(data.get("message") or event_name)
            self._append_log(message)
            return
        if event_name == "file_finished":
            result = str(data.get("result") or "")
            name = pathlib.Path(str(data.get("input_path", data.get("output_path", "")))).name
            if name:
                self.current_file.setText(f"当前文件 {name}")
            self._record_file_result(result, self._payload_input_id(data))
            completed = self._payload_int(data, "completed")
            self._set_run_progress(completed if completed else None, total)
            reason = str(data.get("reason") or result)
            self._append_log(f"{name}: {reason}")
            return
        if event_name == "batch_transcode_started":
            pending = self._payload_int(data, "pending_count", "total_jobs")
            self._transcode_total = pending
            self._transcode_counts = {"success": 0, "failed": 0, "waiting": pending}
            self._render_transcode_counts()
            self._set_transcode_progress(0, pending)
            self._append_log(f"统一转码 {pending}")
            return
        if event_name == "batch_transcode_progress":
            name = pathlib.Path(str(data.get("input_path", ""))).name
            if name:
                self.transcode_current_file.setText(f"当前文件 {name}")
            transcode_total = self._payload_int(data, "total_jobs", "total")
            completed = self._payload_int(data, "completed")
            success = self._payload_int(data, "success_count")
            failed = self._payload_int(data, "failed_count")
            waiting = self._payload_int(data, "queued", "waiting_count")
            if transcode_total <= 0:
                transcode_total = max(self._transcode_total, completed)
            if waiting <= 0 and transcode_total > completed:
                waiting = transcode_total - completed
            self._transcode_counts = {"success": success, "failed": failed, "waiting": max(0, waiting)}
            self._render_transcode_counts()
            self._set_transcode_progress(completed, transcode_total)
            self._append_log(str(data.get("message") or event_name))
            return
        if event_name == "batch_finished":
            if self._completed_count() <= 0 and not self._decoded_inputs:
                self._run_counts = {
                    "success": int(data.get("success_count") or 0),
                    "failed": int(data.get("failed_count") or 0),
                    "skipped": int(data.get("skipped_count") or 0),
                }
            self._render_run_counts()
            completed = self._payload_int(data, "completed") or self._completed_count()
            self._set_run_progress(completed, total)
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

    def _handle_update_finished(self, ok: bool) -> None:
        self.updating = False
        self.update_button.setEnabled(not self.running)

    def _append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.status_message.setText(text)


def build_stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: Microsoft YaHei UI, Segoe UI; font-size: 13px; }}
    QWidget#RootWindow {{ background: {APP_BG}; }}
    QFrame#Sidebar, QFrame#Panel {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QFrame#SoftPanel {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QFrame#StatusBlock {{ background: transparent; border: 0; }}
    QStackedWidget#Stack, QScrollArea#FormScroll {{ background: transparent; border: 0; }}
    QLabel, QCheckBox {{ background: transparent; }}
    QLabel#Brand {{ font-size: 22px; font-weight: 700; color: {GREEN_DARK}; padding: 6px 6px; }}
    QLabel#PageTitle {{ font-size: 21px; font-weight: 700; }}
    QLabel#Muted, QLabel#FieldLabel {{ color: {MUTED}; }}
    QLabel#StatusMessage {{ color: {MUTED}; }}
    QLabel#DecryptProgressLabel, QLabel#TranscodeProgressLabel {{ color: {TEXT}; font-weight: 700; }}
    QLabel#AppVersion {{ color: {MUTED}; padding: 6px 4px; }}
    QLabel#StatusOk {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; border: 1px solid {GREEN}; border-radius: 8px; padding: 6px 10px; font-weight: 600; }}
    QLabel#StatusOff {{ background: {RED_SOFT}; color: {RED_DARK}; border: 1px solid {RED}; border-radius: 8px; padding: 6px 10px; font-weight: 600; }}
    QLabel#Stat {{ background: {GREEN_SOFT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; color: {GREEN_DARK}; font-weight: 600; }}
    QLabel#DecodeSuccessRate, QLabel#TranscodeSuccessRate {{ background: {RED_SOFT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; color: {RED_DARK}; font-weight: 600; }}
    QLineEdit#Input, QComboBox#Combo, QSpinBox#Spin, QSpinBox#TranscodeWorkers {{ background: white; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px; min-height: 22px; }}
    QLineEdit#Input:focus, QComboBox#Combo:focus, QSpinBox#Spin:focus, QSpinBox#TranscodeWorkers:focus {{ border: 1px solid {GREEN}; }}
    QPushButton {{ border: 0; border-radius: 8px; padding: 7px 14px; background: {GREEN_SOFT}; color: {GREEN_DARK}; font-weight: 600; }}
    QPushButton#PrimaryButton {{ background: {GREEN}; color: white; min-width: 112px; }}
    QPushButton#PrimaryButton:hover {{ background: {GREEN_DARK}; }}
    QPushButton#DangerButton {{ background: {RED_SOFT}; color: {RED_DARK}; }}
    QPushButton#UpdateButton {{ background: {RED_SOFT}; color: {RED_DARK}; }}
    QPushButton#UpdateButton:hover {{ background: {RED}; color: white; }}
    QPushButton#SmallButton {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; padding: 6px 10px; }}
    QPushButton#DisabledButton, QPushButton:disabled {{ background: #EEF0F2; color: #98A2B3; }}
    QCheckBox {{ spacing: 8px; }}
    QListWidget#PlatformList {{ background: transparent; border: 0; outline: 0; }}
    QListWidget#PlatformList::item {{ padding: 12px 10px; border-radius: 8px; margin: 2px 0; }}
    QListWidget#PlatformList::item:selected {{ background: {GREEN_SOFT}; color: {GREEN_DARK}; }}
    QListWidget#PlatformList::item:hover {{ background: {RED_SOFT}; }}
    QProgressBar#DecryptProgress, QProgressBar#TranscodeProgress {{ background: #EEF0F2; border: 0; border-radius: 6px; height: 12px; }}
    QProgressBar#DecryptProgress::chunk {{ background: {GREEN}; border-radius: 6px; }}
    QProgressBar#TranscodeProgress::chunk {{ background: {RED}; border-radius: 6px; }}
    """


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
