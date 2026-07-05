from __future__ import annotations

import pathlib
import random
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
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
    QListView,
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
from src.Infrastructure.update_service import check_update_availability, resolve_app_version, restart_application, run_update
from src.Presentation.ui_state import (
    PlatformRunOptions,
    PlatformSpec,
    QQ_FORMAT_RULE_KEYS,
    build_platform_batch_config,
    platform_specs,
    validate_platform_runtime_for_ui,
)
from src.Presentation.qkk_theme import ACCENT, RAIN_BG, RAIN_RGB, TERMINAL_BG, build_stylesheet


UPDATE_CHECK_DELAY_MS = 100
BOX_GAP = 18
CONTROL_GAP = 10
DENSE_GAP = 8
POPUP_GAP = 4


def _format_seconds(value: float) -> str:
    if value <= 0:
        return "0.0s"
    return f"{value:.1f}s"


def _configure_combo_popup(combo: QComboBox) -> QComboBox:
    combo.setCursor(Qt.CursorShape.PointingHandCursor)
    view = QListView(combo)
    view.setObjectName("ComboPopup")
    view.setMouseTracking(True)
    view.setUniformItemSizes(True)
    view.setSpacing(POPUP_GAP)
    view.setFrameShape(QFrame.Shape.NoFrame)
    combo.setView(view)
    return combo


class UiBridge(QObject):
    event_received = Signal(str, object)
    run_finished = Signal(int)
    log_message = Signal(str)
    update_checked = Signal(object)
    update_finished = Signal(bool)


class MatrixRainWidget(QWidget):
    _glyphs = "01010110ABCDEF89"

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("MatrixRain")
        self.setMinimumHeight(42)
        self.setAutoFillBackground(False)
        self._processing = False
        self._rng = random.Random(20260704)
        self._drops: list[int] = []
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setObjectName("MatrixRainTimer")
        self._timer.timeout.connect(self._tick)
        self.setProperty("processing", False)
        self.setProperty("glyphs", self._glyphs)
        self.setProperty("glyphsVisible", False)
        self.setProperty("themeAccent", ACCENT)
        self.setProperty("themeBackground", TERMINAL_BG)

    def set_processing(self, processing: bool) -> None:
        self._processing = bool(processing)
        self.setProperty("processing", self._processing)
        if self._processing:
            self._timer.start(42)
        else:
            self._timer.stop()
        self.setProperty("glyphsVisible", self._processing)
        self.update()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._reset_columns()

    def _reset_columns(self) -> None:
        column_count = max(1, self.width() // 13)
        row_count = max(4, self.height() // 13)
        self._drops = [self._rng.randint(-row_count, row_count) for _ in range(column_count)]

    def _tick(self) -> None:
        if not self._drops:
            self._reset_columns()
        rows = max(4, self.height() // 13)
        speed = 2 if self._processing else 1
        for index, value in enumerate(self._drops):
            next_value = value + speed
            if next_value > rows + self._rng.randint(0, 6):
                next_value = self._rng.randint(-rows, 0)
            self._drops[index] = next_value
        self._frame += speed
        self.update()

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)  # type: ignore[arg-type]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        painter.fillRect(self.rect(), QColor(*RAIN_BG, 245))
        if not self._processing:
            return
        painter.setFont(QFont("Consolas", 9))
        head_alpha = 230 if self._processing else 130
        tail_alpha = 82 if self._processing else 42
        red, green, blue = RAIN_RGB
        for column, drop in enumerate(self._drops):
            x = column * 13 + 2
            for trail in range(5):
                y = (drop - trail) * 13
                if y < -13 or y > self.height() + 13:
                    continue
                glyph = self._glyphs[(column * 7 + trail + self._frame) % len(self._glyphs)]
                alpha = head_alpha if trail == 0 else max(18, tail_alpha - trail * 12)
                painter.setPen(QColor(red, green, blue, alpha))
                painter.drawText(x, y, glyph)


class ProcessingTerminal(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ProcessingTerminal")
        self.setMinimumWidth(180)
        self.setMinimumHeight(118)
        self._rng = random.Random(3917)
        self._processing = False
        self._stage = "IDLE"
        self._file_name = "-"
        self._progress = 0
        self._address = 0x4A90
        self._lines: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        title = QLabel("PROCESSING TERMINAL")
        title.setObjectName("TerminalTitle")
        layout.addWidget(title)
        for _ in range(4):
            label = QLabel("")
            label.setObjectName("TerminalLine")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self._lines.append(label)
            layout.addWidget(label)
        layout.addStretch(1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setProperty("processing", False)
        self.render_event("idle", "-", 0)

    def set_processing(self, processing: bool) -> None:
        self._processing = bool(processing)
        self.setProperty("processing", self._processing)
        if self._processing and not self._timer.isActive():
            self._timer.start(160)
        elif not self._processing and self._timer.isActive():
            self._timer.stop()
        self._refresh_lines()

    def render_event(self, stage: str, file_name: str = "", progress: int = 0) -> None:
        self._stage = (stage or "idle").upper()
        if file_name:
            self._file_name = pathlib.Path(str(file_name)).name or "-"
        self._progress = max(0, min(100, int(progress or 0)))
        self._tick()

    def _tick(self) -> None:
        self._address = (self._address + self._rng.randint(0x20, 0x1FF)) & 0xFFFFFF
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        mask = self._rng.getrandbits(16)
        lane = self._rng.getrandbits(8)
        lines = [
            f"[SYS_XOR_STREAM] ADDR:0x{self._address:06X} MASK:0x{mask:04X}",
            f"[PIPE_STAGE] {self._stage:<11} FILE:{self._file_name}",
            f"[BLOCK_MAP] LANE:{lane:02X} PROGRESS:{self._progress:03d}% CACHE:HOT",
            "[VERIFY_BUS] PCM -> CONTAINER -> PLAYABLE",
        ]
        for label, text in zip(self._lines, lines):
            label.setText(text)


class PathRow(QWidget):
    def __init__(self, label: str, *, allow_file: bool = False, allow_open: bool = False) -> None:
        super().__init__()
        self.allow_file = allow_file
        self.allow_open = allow_open
        self.layout_mode = "inline"
        self.setMinimumHeight(52)
        self.root_layout = QGridLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setHorizontalSpacing(CONTROL_GAP)
        self.root_layout.setVerticalSpacing(6)
        title = QLabel(label)
        title.setObjectName("FieldLabel")
        self.title = title
        self.edit = QLineEdit()
        self.edit.setObjectName("Input")
        self.edit.setMinimumWidth(120)
        self.edit.setFixedHeight(30)
        self.edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dir_button = QPushButton("目录")
        self.dir_button.setObjectName("SmallButton")
        self.dir_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dir_button.setFixedWidth(60)
        self.dir_button.setFixedHeight(30)
        self.file_button = QPushButton("文件")
        self.file_button.setObjectName("SmallButton")
        self.file_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_button.setFixedWidth(60)
        self.file_button.setFixedHeight(30)
        self.file_button.setVisible(allow_file)
        self.open_button = QPushButton("打开")
        self.open_button.setObjectName("OpenOutputButton")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setFixedWidth(60)
        self.open_button.setFixedHeight(30)
        self.open_button.setVisible(allow_open)
        self._apply_layout("inline")
        self.dir_button.clicked.connect(self._choose_dir)
        self.file_button.clicked.connect(self._choose_file)
        self.open_button.clicked.connect(self._open_path)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._update_responsive_layout()

    def _update_responsive_layout(self) -> None:
        mode = "stacked" if self.width() < 760 else "inline"
        if mode != self.layout_mode:
            self._apply_layout(mode)

    def _apply_layout(self, mode: str) -> None:
        self.layout_mode = mode
        layout = self.root_layout
        if mode == "stacked":
            self.setMinimumHeight(86)
            layout.addWidget(self.title, 0, 0, 1, 3)
            layout.addWidget(self.edit, 1, 0, 1, 3)
            layout.addWidget(self.dir_button, 2, 1)
            layout.addWidget(self.file_button, 2, 2)
            layout.addWidget(self.open_button, 2, 2)
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 0)
            layout.setColumnStretch(2, 0)
            return

        self.setMinimumHeight(52)
        layout.addWidget(self.title, 0, 0, 1, 3)
        layout.addWidget(self.edit, 1, 0)
        layout.addWidget(self.dir_button, 1, 1)
        layout.addWidget(self.file_button, 1, 2)
        layout.addWidget(self.open_button, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 0)

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
        root.setSpacing(BOX_GAP)

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
        self.form_layout.setContentsMargins(12, 6, 12, 6)
        self.form_layout.setSpacing(BOX_GAP)

        self.input_path = PathRow("输入路径", allow_file=True)
        self.output_dir = PathRow("输出目录", allow_open=True)
        self.form_layout.addWidget(self.input_path)
        self.form_layout.addWidget(self.output_dir)

        self.config_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.config_layout.setContentsMargins(0, 0, 0, 0)
        self.config_layout.setSpacing(BOX_GAP)

        self.format_box = QFrame()
        self.format_box.setObjectName("FormatPanel")
        self.format_box.setMinimumWidth(260)
        self.format_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        formats = QGridLayout()
        formats.setContentsMargins(12, 10, 12, 12)
        formats.setHorizontalSpacing(CONTROL_GAP)
        formats.setVerticalSpacing(DENSE_GAP)
        format_columns = max(1, min(3, len(spec.format_controls)))
        for index, control in enumerate(spec.format_controls):
            label_row = (index // format_columns) * 2
            column = index % format_columns
            label = QLabel(control.label)
            label.setObjectName("FieldLabel")
            label.setWordWrap(True)
            label.setMinimumWidth(92)
            label.setMaximumWidth(150)
            combo = QComboBox()
            _configure_combo_popup(combo)
            combo.setObjectName("QQOutputFormat" if spec.platform_id == "qq" else "Combo")
            combo.setFixedHeight(28)
            combo.setMinimumWidth(92)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.addItems(list(control.options))
            combo.setCurrentText(control.default)
            formats.addWidget(label, label_row, column)
            formats.addWidget(combo, label_row + 1, column)
            self.format_widgets[control.key] = combo
        for column in range(format_columns):
            formats.setColumnStretch(column, 1)
        self.format_box.setLayout(formats)
        self.format_box.setMinimumHeight(self.format_box.sizeHint().height())

        self.options_panel = QFrame()
        self.options_panel.setObjectName("SoftPanel")
        self.options_panel.setMinimumWidth(320)
        self.options_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        option_grid = QGridLayout(self.options_panel)
        option_grid.setContentsMargins(6, 6, 6, 6)
        option_grid.setHorizontalSpacing(DENSE_GAP)
        option_grid.setVerticalSpacing(POPUP_GAP)

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
        self.delete_source = QCheckBox("完成后删除源文件")
        self.delete_source.setObjectName("DeleteSourceAfterSuccess")
        self.delete_source.setChecked(False)
        self.fetch_ekey = QCheckBox("补取 ekey")
        self.fetch_ekey.setChecked(True)
        self.cache_ekey = QCheckBox("缓存 ekey")
        self.cache_ekey.setChecked(True)
        for check in (
            self.recursive,
            self.transcode,
            self.cover,
            self.album,
            self.group_by_artist,
            self.delete_source,
            self.fetch_ekey,
            self.cache_ekey,
        ):
            check.setFixedHeight(26)

        self.sample_rate = QComboBox()
        _configure_combo_popup(self.sample_rate)
        self.sample_rate.setObjectName("Combo")
        self.sample_rate.setFixedHeight(28)
        self.sample_rate.setMinimumWidth(92)
        self.sample_rate.addItem("原采样率", None)
        for value in TRANSCODE_SAMPLE_RATE_OPTIONS:
            self.sample_rate.addItem(str(value), value)
        self.bitrate = QComboBox()
        _configure_combo_popup(self.bitrate)
        self.bitrate.setObjectName("Combo")
        self.bitrate.setFixedHeight(28)
        self.bitrate.setMinimumWidth(82)
        for value in TRANSCODE_BITRATE_OPTIONS:
            self.bitrate.addItem(str(value), value)
        self.bitrate.setCurrentText("320")

        self.workers = QSpinBox()
        self.workers.setObjectName("Spin")
        self.workers.setObjectName("TranscodeWorkers")
        self.workers.setFixedHeight(28)
        self.workers.setMinimumWidth(64)
        self.workers.setRange(1, 9999)
        self.workers.setValue(2)

        option_grid.addWidget(self.recursive, 0, 0)
        option_grid.addWidget(self.transcode, 0, 1)
        option_grid.addWidget(self.cover, 0, 2)
        option_grid.addWidget(self.album, 0, 3)
        option_grid.addWidget(self.fetch_ekey, 0, 4)
        option_grid.addWidget(self.cache_ekey, 0, 5)
        option_grid.addWidget(self.group_by_artist, 1, 0, 1, 2)
        option_grid.addWidget(self.delete_source, 1, 2, 1, 2)
        option_grid.addWidget(QLabel("采样率"), 1, 4)
        option_grid.addWidget(self.sample_rate, 1, 5)
        option_grid.addWidget(QLabel("码率"), 2, 0)
        option_grid.addWidget(self.bitrate, 2, 1)
        option_grid.addWidget(QLabel("并发"), 2, 2)
        option_grid.addWidget(self.workers, 2, 3)
        for column in range(6):
            option_grid.setColumnStretch(column, 1)
        self.config_layout.addWidget(self.format_box, 1)
        self.config_layout.addWidget(self.options_panel, 0)
        self.form_layout.addLayout(self.config_layout)

        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("FormScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setMinimumHeight(0)
        self.form_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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
        form = self.form_scroll.widget()
        if form is not None:
            self.form_scroll.setMinimumHeight(form.sizeHint().height() + 2)

    def format_values(self) -> dict[str, str]:
        if self.spec.platform_id == "qq":
            widget = self.format_widgets.get("qq_output_format")
            value = widget.currentText().strip().lower() if widget is not None else "mp3"
            return {key: value for key in QQ_FORMAT_RULE_KEYS}
        return {key: widget.currentText().strip().lower() for key, widget in self.format_widgets.items()}

    def set_format_values(self, values: dict[str, Any]) -> None:
        if self.spec.platform_id == "qq":
            widget = self.format_widgets.get("qq_output_format")
            if widget is None:
                return
            options = [widget.itemText(index) for index in range(widget.count())]
            explicit = str(values.get("qq_output_format") or "").strip().lower()
            legacy_values = [
                str(values.get(key) or "").strip().lower()
                for key in QQ_FORMAT_RULE_KEYS
                if str(values.get(key) or "").strip().lower()
            ]
            value = explicit
            if value not in options and legacy_values:
                unique_values = {item for item in legacy_values if item in options}
                value = legacy_values[0] if len(unique_values) != 1 else next(iter(unique_values))
            if value in options:
                widget.setCurrentText(value)
            return
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
        self.app_version = resolve_app_version(self.paths.root_dir)
        self.bridge = UiBridge()
        self.stop_event = threading.Event()
        self.running = False
        self.updating = False
        self.update_available = False
        self.started_at = 0.0
        self.client_hint_shown = False
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
        QTimer.singleShot(UPDATE_CHECK_DELAY_MS, self._start_update_check)

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
        root.setSpacing(BOX_GAP)

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
        self.version_label = QLabel(self.app_version)
        self.version_label.setObjectName("AppVersion")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_button = QPushButton("更新系统")
        self.update_button.setObjectName("UpdateButton")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setFixedHeight(34)
        side_layout.addWidget(self.version_label)
        side_layout.addWidget(self.update_button)
        root.addWidget(sidebar)

        self.content_scroll = QScrollArea()
        self.content_scroll.setObjectName("ContentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_body = QWidget()
        content = QVBoxLayout(content_body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(BOX_GAP)

        self.stack = QStackedWidget()
        self.stack.setObjectName("Stack")
        self.stack.setMinimumHeight(0)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for spec in self.specs:
            page = PlatformPage(spec)
            self.pages[spec.platform_id] = page
            self.stack.addWidget(page)
        content.addWidget(self.stack, 0)

        run_panel = QFrame()
        run_panel.setObjectName("Panel")
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(16, 12, 16, 12)
        run_layout.setSpacing(BOX_GAP)

        top = QHBoxLayout()
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_message = QLabel("客户端已启动")
        self.status_message.setObjectName("StatusMessage")
        self.status_message.setWordWrap(True)
        self.status_message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.matrix_rain = MatrixRainWidget()
        self.matrix_rain.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top.addWidget(self.status_message, 1)
        top.addWidget(self.matrix_rain, 2)
        top.addWidget(self.stop_button)
        run_layout.addLayout(top)

        self.run_progress_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.run_progress_layout.setSpacing(BOX_GAP)

        decrypt_card = QFrame()
        decrypt_card.setObjectName("StatusBlock")
        decrypt_layout = QVBoxLayout(decrypt_card)
        decrypt_layout.setContentsMargins(12, 10, 12, 10)
        decrypt_layout.setSpacing(DENSE_GAP)
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
        decrypt_stats = QGridLayout()
        decrypt_stats.setHorizontalSpacing(CONTROL_GAP)
        decrypt_stats.setVerticalSpacing(DENSE_GAP)
        self.success_label = QLabel("成功 0")
        self.failed_label = QLabel("失败 0")
        self.skipped_label = QLabel("跳过 0")
        self.decode_rate_label = QLabel("解密成功率 0%")
        self.decode_rate_label.setObjectName("DecodeSuccessRate")
        for label in (self.success_label, self.failed_label, self.skipped_label):
            label.setObjectName("Stat")
        decrypt_stats.addWidget(self.success_label, 0, 0)
        decrypt_stats.addWidget(self.failed_label, 0, 1)
        decrypt_stats.addWidget(self.skipped_label, 1, 0)
        decrypt_stats.addWidget(self.decode_rate_label, 1, 1)
        decrypt_stats.setColumnStretch(0, 1)
        decrypt_stats.setColumnStretch(1, 1)
        decrypt_layout.addLayout(decrypt_stats)

        transcode_card = QFrame()
        transcode_card.setObjectName("StatusBlock")
        transcode_layout = QVBoxLayout(transcode_card)
        transcode_layout.setContentsMargins(12, 10, 12, 10)
        transcode_layout.setSpacing(DENSE_GAP)
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
        transcode_stats = QGridLayout()
        transcode_stats.setHorizontalSpacing(CONTROL_GAP)
        transcode_stats.setVerticalSpacing(DENSE_GAP)
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
        transcode_stats.addWidget(self.transcode_success_label, 0, 0)
        transcode_stats.addWidget(self.transcode_failed_label, 0, 1)
        transcode_stats.addWidget(self.transcode_waiting_label, 1, 0)
        transcode_stats.addWidget(self.elapsed_label, 1, 1)
        transcode_stats.addWidget(self.transcode_rate_label, 2, 0, 1, 2)
        transcode_stats.setColumnStretch(0, 1)
        transcode_stats.setColumnStretch(1, 1)
        transcode_layout.addLayout(transcode_stats)

        self.processing_terminal = ProcessingTerminal()
        self.run_progress_layout.addWidget(decrypt_card, 1)
        self.run_progress_layout.addWidget(transcode_card, 1)
        self.run_progress_layout.addWidget(self.processing_terminal, 1)
        run_layout.addLayout(self.run_progress_layout)
        self.run_panel = run_panel
        run_panel.setMinimumHeight(206)
        content.addWidget(run_panel, 0)
        content.addStretch(1)
        self.content_scroll.setWidget(content_body)
        root.addWidget(self.content_scroll, 1)
        QTimer.singleShot(0, self._update_run_layout)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        QTimer.singleShot(0, self._update_run_layout)

    def _update_run_layout(self) -> None:
        if not hasattr(self, "run_progress_layout") or not hasattr(self, "run_panel"):
            return
        compact = self.run_panel.width() < 940
        target_direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        if self.run_progress_layout.direction() != target_direction:
            self.run_progress_layout.setDirection(target_direction)
        self.run_panel.setMinimumHeight(470 if compact else 226)

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
        self.bridge.update_checked.connect(self._handle_update_checked)
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
        qq_page.delete_source.setChecked(bool(shared.get("delete_source_after_success", False)))
        bitrate = qq.get("transcode_bitrate_kbps", 320)
        if bitrate:
            qq_page.bitrate.setCurrentText(str(bitrate))
        sample_rate = qq.get("transcode_sample_rate_hz")
        if sample_rate:
            qq_page.sample_rate.setCurrentText(str(sample_rate))
        qq_page.fetch_ekey.setChecked(bool(qq.get("qq_fetch_missing_ekey", True)))
        qq_page.cache_ekey.setChecked(bool(qq.get("qq_cache_ekeys", True)))

        for platform_id in ("kugou", "netease", "kuwo"):
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
            page.delete_source.setChecked(bool(shared.get("delete_source_after_success", False)))

    def _save_platform_config(self, platform_id: str, page: PlatformPage) -> None:
        self.root_config, self.config = load_config(self.paths)
        self.config["shared"]["output_dir"] = page.output_dir.text() or str(self.paths.output_dir)
        self.config["shared"]["recursive"] = page.recursive.isChecked()
        self.config["shared"]["transcode_enabled"] = page.transcode.isChecked()
        self.config["shared"]["transcode_max_workers"] = page.workers.value()
        self.config["shared"]["embed_cover_art"] = page.cover.isChecked()
        self.config["shared"]["supplement_album_metadata"] = page.album.isChecked()
        self.config["shared"]["group_by_artist"] = page.group_by_artist.isChecked()
        self.config["shared"]["delete_source_after_success"] = page.delete_source.isChecked()
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
        self.client_hint_shown = False
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
            delete_source_after_success=page.delete_source.isChecked(),
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
        self.update_button.setText("更新中")
        self._append_log(f"开始更新系统，当前版本 {self.app_version}")
        thread = threading.Thread(target=self._run_update_job, daemon=True)
        thread.start()

    def _start_update_check(self) -> None:
        if self.running or self.updating:
            return
        thread = threading.Thread(target=self._run_update_check_job, daemon=True)
        thread.start()

    def _run_update_check_job(self) -> None:
        try:
            result = check_update_availability(self.paths.root_dir)
        except Exception:
            return
        self.bridge.update_checked.emit(result)

    def _refresh_update_button_text(self) -> None:
        if self.updating:
            self.update_button.setText("更新中")
        else:
            self.update_button.setText("已有版本更新" if self.update_available else "更新系统")

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
        self.processing_terminal.render_event("idle", "-", 0)

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
        self.matrix_rain.set_processing(busy)
        self.processing_terminal.set_processing(busy)

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
            self.processing_terminal.render_event("scan", f"{total} files", 0)
            self._append_log(f"候选文件 {total}")
            return
        if event_name == "file_started":
            name = pathlib.Path(str(data.get("input_path", ""))).name
            self.current_file.setText(f"当前文件 {name}")
            self._set_run_progress(total=total)
            self.processing_terminal.render_event("decryption", name, self.progress.value())
            return
        if event_name == "file_decrypted":
            name = pathlib.Path(str(data.get("input_path", ""))).name
            self.current_file.setText(f"当前文件 {name}")
            self._record_decrypted(self._payload_input_id(data))
            self._set_run_progress(total=total)
            self.processing_terminal.render_event("xor-ready", name, self.progress.value())
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
            self.processing_terminal.render_event(result or "finished", name, self.progress.value())
            reason = str(data.get("reason") or result)
            self._append_log(f"{name}: {reason}")
            if result == "failed" and "qq_client_required" in reason and not self.client_hint_shown:
                self.client_hint_shown = True
                QMessageBox.warning(self, "QQ音乐", reason.split(":", 1)[-1].strip() or "请安装或启动 QQ音乐后重试")
            return
        if event_name == "batch_transcode_started":
            pending = self._payload_int(data, "pending_count", "total_jobs")
            self._transcode_total = pending
            self._transcode_counts = {"success": 0, "failed": 0, "waiting": pending}
            self._render_transcode_counts()
            self._set_transcode_progress(0, pending)
            self.processing_terminal.render_event("transcoding", f"{pending} jobs", 0)
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
            self.processing_terminal.render_event("transcoding", name, self.transcode_progress.value())
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
            self.processing_terminal.render_event("done", "batch", self.progress.value())

    def _handle_run_finished(self, result_code: int) -> None:
        self.running = False
        self._set_busy(False)
        elapsed = time.perf_counter() - self.started_at if self.started_at else 0.0
        self.elapsed_label.setText(f"耗时 {_format_seconds(elapsed)}")
        self._append_log(f"QQ音乐: 结束 code={result_code}")

    def _handle_update_finished(self, ok: bool) -> None:
        self.updating = False
        self.update_button.setEnabled(not self.running)
        if ok:
            self._restart_after_update()
        else:
            self._refresh_update_button_text()

    def _handle_update_checked(self, result: object) -> None:
        if not bool(getattr(result, "ok", False)):
            return
        self.update_available = bool(getattr(result, "update_available", False))
        self._refresh_update_button_text()

    def _restart_after_update(self) -> None:
        result = restart_application(self.paths.root_dir)
        self._append_log(result.message)
        if result.ok:
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(250, app.quit)

    def _append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.status_message.setText(text)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
