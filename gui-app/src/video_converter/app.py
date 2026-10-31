"""Minimal Qt6 desktop interface for batch video conversion."""

import os
from pathlib import Path
import sys

from PyQt6.QtCore import QPointF, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QKeySequence, QPainter, QPalette, QPen, QPolygonF,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .ffmpeg import ConversionQueue, FFmpegNotFoundError, find_binaries
from .hardware import HardwareDetector, hardware_quality_description
from .icons import app_icon
from .media import SourceProbe
from .models import (
    CompressionPreset, ConversionJob, ConversionSettings, HardwareCapabilities, InputFormat, JobStatus,
    OutputFormat, format_bitrate, format_size, unique_output_path,
)
from .theme import apply_theme


STATUS_COLORS = {
    JobStatus.QUEUED: "#9da3b3", JobStatus.PROBING: "#c2b6ff",
    JobStatus.CONVERTING: "#c2b6ff", JobStatus.VERIFYING: "#c2b6ff",
    JobStatus.READING_BITRATE: "#c2b6ff",
    JobStatus.SUCCESS: "#7cdbab", JobStatus.FAILED: "#ff8792",
    JobStatus.CANCELLED: "#f0c985",
}


def label(text="", name="", parent=None):
    widget = QLabel(text, parent)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class FormatComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#b9adff" if self.isEnabled() else "#72788a"), 1.5))
        x, y = self.width() - 17, self.height() / 2
        painter.drawPolyline(QPolygonF([
            QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2),
        ]))
        painter.end()


class ResultDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # Keep green/red outcome text visible even on the selected row.
        if foreground := index.data(Qt.ItemDataRole.ForegroundRole):
            option.palette.setBrush(QPalette.ColorRole.HighlightedText, foreground)


class HardwareToggle(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self).adjusted(1, 1, -1, -1)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = "#b9adff" if self.hasFocus() else "#53596b" if self.isEnabled() else "#3a3e4b"
        painter.setPen(QPen(QColor(border), 1))
        background = ("#b9adff" if self.isEnabled() else "#39364a") if self.isChecked() else "#13151c"
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        if self.isChecked():
            painter.setPen(QPen(QColor("#191526" if self.isEnabled() else "#9e95b6"), 1.8))
            x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
            painter.drawPolyline(QPolygonF([
                QPointF(x + width * 0.2, y + height * 0.5),
                QPointF(x + width * 0.43, y + height * 0.72),
                QPointF(x + width * 0.8, y + height * 0.25),
            ]))
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, preferences=None):
        super().__init__()
        self.preferences = preferences if preferences is not None else QSettings("VideoConverter", "VideoConverter")
        self.jobs: list[ConversionJob] = []
        self.binaries = None
        self.output_directory: Path | None = None
        self._close_when_finished = False
        self._last_active_index = None
        self.hardware_capabilities = HardwareCapabilities()
        self._hardware_requested = self.preferences.value("hardware_encoding", False, type=bool)
        self._hardware_check_message = "Checking hardware…"
        self.hardware_detector = HardwareDetector(self)
        self.hardware_detector.finished.connect(self._hardware_detection_finished)
        self.hardware_detector.status_changed.connect(self._hardware_detection_status)
        self.source_preview = SourceProbe(self)
        self.source_preview.finished.connect(self._source_preview_finished)
        self.queue = ConversionQueue(self)
        self.queue.job_changed.connect(self._job_changed)
        self.queue.finished.connect(self._conversion_finished)
        self.setWindowTitle("Video Converter")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 730)
        self.resize(1120, 860)
        self.setAcceptDrops(True)
        self._build_ui()
        self._load_preferences()
        self._find_ffmpeg()
        self._refresh_ui()
        for key, callback in [
            (QKeySequence.StandardKey.Open, self.browse),
            (QKeySequence.StandardKey.Delete, self.remove_selected),
        ]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(18)
        self.setCentralWidget(central)

        header = QHBoxLayout()
        logo = label()
        logo.setPixmap(self.windowIcon().pixmap(QSize(46, 46)))
        header.addWidget(logo)
        header.addWidget(label("Video Converter", "title"))
        header.addStretch()
        self.engine_button = QPushButton("Locate FFmpeg…")
        self.engine_button.setObjectName("engine")
        self.engine_button.clicked.connect(self.locate_ffmpeg)
        header.addWidget(self.engine_button)
        root.addLayout(header)

        self.notice = label("", "notice")
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)

        self.settings_panel = QFrame()
        self.settings_panel.setObjectName("panel")
        settings = QVBoxLayout(self.settings_panel)
        settings.setContentsMargins(20, 18, 20, 18)
        settings.setSpacing(12)
        format_row = QHBoxLayout()
        format_row.setSpacing(16)
        self.input_format = FormatComboBox()
        self.input_format.setAccessibleName("Input file filter")
        for input_format in InputFormat:
            self.input_format.addItem(input_format.label, input_format.name)
        self.output_format = FormatComboBox()
        self.output_format.setAccessibleName("Output format")
        for output_format in OutputFormat:
            self.output_format.addItem(output_format.label, output_format.value)
        for caption, combo in [("BROWSE FOR", self.input_format), ("CONVERT TO", self.output_format)]:
            column = QVBoxLayout()
            field = label(caption, "fieldLabel")
            field.setBuddy(combo)
            column.addWidget(field)
            column.addWidget(combo)
            format_row.addLayout(column, 1)
        settings.addLayout(format_row)

        settings.addWidget(label("COMPRESSION", "fieldLabel"))
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.preset_buttons = QButtonGroup(self)
        for index, preset in enumerate(CompressionPreset):
            button = QPushButton(preset.label)
            button.setObjectName("preset")
            button.setCheckable(True)
            button.setToolTip(f"{preset.description}. Keeps the original resolution.")
            self.preset_buttons.addButton(button, index)
            preset_row.addWidget(button, 1)
        self.preset_buttons.button(1).setChecked(True)
        self.preset_buttons.idClicked.connect(self._settings_changed)
        settings.addLayout(preset_row)
        self.preset_hint = label("", "hint")
        settings.addWidget(self.preset_hint)

        hardware_row = QHBoxLayout()
        hardware_row.setSpacing(14)
        self.hardware_toggle = HardwareToggle("Hardware encoding")
        self.hardware_toggle.setObjectName("hardwareToggle")
        self.hardware_toggle.setToolTip("Apply the preset's bitrate and quality using a verified hardware encoder's native quality controls.")
        self.hardware_toggle.toggled.connect(self._hardware_toggled)
        hardware_row.addWidget(self.hardware_toggle)
        self.hardware_status = label("Checking hardware…", "hint")
        self.hardware_status.setWordWrap(True)
        hardware_row.addWidget(self.hardware_status, 1)
        self.recheck_hardware_button = QPushButton("Recheck")
        self.recheck_hardware_button.setObjectName("quiet")
        self.recheck_hardware_button.clicked.connect(self.recheck_hardware)
        hardware_row.addWidget(self.recheck_hardware_button)
        settings.addLayout(hardware_row)

        save_row = QHBoxLayout()
        save_row.addWidget(label("Save to", "hint"))
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        self.destination.setAccessibleName("Output folder")
        self.destination.setPlaceholderText("Beside each input file")
        save_row.addWidget(self.destination, 1)
        self.choose_destination_button = QPushButton("Choose…")
        self.choose_destination_button.clicked.connect(self.choose_destination)
        save_row.addWidget(self.choose_destination_button)
        self.reset_destination_button = QPushButton("Reset")
        self.reset_destination_button.setObjectName("quiet")
        self.reset_destination_button.setToolTip("Save beside each input file")
        self.reset_destination_button.clicked.connect(lambda: self.set_destination(None))
        save_row.addWidget(self.reset_destination_button)
        settings.addLayout(save_row)
        root.addWidget(self.settings_panel)
        self.output_format.currentIndexChanged.connect(self._settings_changed)

        toolbar = QHBoxLayout()
        self.files_label = label("Files", "section")
        toolbar.addWidget(self.files_label)
        self.file_count = label("0 files", "hint")
        toolbar.addWidget(self.file_count)
        toolbar.addStretch()
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setObjectName("quiet")
        self.remove_button.clicked.connect(self.remove_selected)
        toolbar.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("quiet")
        self.clear_button.clicked.connect(self.clear_files)
        toolbar.addWidget(self.clear_button)
        self.browse_button = QPushButton("+  Add files…")
        self.browse_button.clicked.connect(self.browse)
        toolbar.addWidget(self.browse_button)
        root.addLayout(toolbar)

        self.file_stack = QStackedWidget()
        self.empty = QFrame()
        self.empty.setObjectName("empty")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(12)
        icon = label("↓", "dropIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(66, 66)
        empty_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(label("Make room for more.", "emptyTitle"), 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(label("Drop videos here, or browse to select one or more files.", "hint"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_browse_button = QPushButton("Browse files…")
        self.empty_browse_button.clicked.connect(self.browse)
        empty_layout.addWidget(self.empty_browse_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.file_stack.addWidget(self.empty)
        self.table = QTableWidget(0, 6)
        self.table.setItemDelegate(ResultDelegate(self.table))
        self.table.setHorizontalHeaderLabels(["Input file", "Output file", "Original", "Converted", "Size change", "Status"])
        self.table.setAccessibleName("Conversion queue and results")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(49)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(6):
            mode = QHeaderView.ResizeMode.Stretch if column < 2 else QHeaderView.ResizeMode.ResizeToContents
            self.table.horizontalHeader().setSectionResizeMode(column, mode)
        self.table.horizontalHeader().setMinimumSectionSize(84)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda row, column: self.show_details(row))
        self.file_stack.addWidget(self.table)
        root.addWidget(self.file_stack, 1)

        selection_row = QHBoxLayout()
        selection_row.addStretch(1)
        self.details_button = QPushButton("Details…")
        self.details_button.setObjectName("quiet")
        self.details_button.clicked.connect(lambda: self.show_details())
        selection_row.addWidget(self.details_button)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setObjectName("quiet")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        selection_row.addWidget(self.open_folder_button)
        root.addLayout(selection_row)

        footer = QHBoxLayout()
        footer.setSpacing(22)
        progress_column = QVBoxLayout()
        progress_column.setSpacing(8)
        self.current_label = label("Ready when you are.", "section")
        self.current_label.setWordWrap(True)
        progress_column.addWidget(self.current_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Batch conversion progress")
        progress_column.addWidget(self.progress)
        self.batch_label = label("Add files to get started.", "batch")
        progress_column.addWidget(self.batch_label)
        footer.addLayout(progress_column, 1)
        self.convert_button = QPushButton("Convert files  →")
        self.convert_button.setObjectName("primary")
        self.convert_button.setMinimumWidth(182)
        self.convert_button.clicked.connect(self.convert)
        footer.addWidget(self.convert_button)
        self.stop_button = QPushButton("Stop conversion")
        self.stop_button.setObjectName("stop")
        self.stop_button.setMinimumWidth(182)
        self.stop_button.clicked.connect(self.stop_conversion)
        footer.addWidget(self.stop_button)
        root.addLayout(footer)

    def _load_preferences(self):
        output = self.output_format.findData(self.preferences.value("output_format", "mp4"))
        self.output_format.setCurrentIndex(max(0, output))
        preset = self.preferences.value("preset", "balanced")
        preset = {"compact": "simple", "high_res": "original"}.get(preset, preset)
        for index, choice in enumerate(CompressionPreset):
            if choice.key == preset:
                self.preset_buttons.button(index).setChecked(True)
        input_index = self.input_format.findData(self.preferences.value("input_format", "MKV"))
        self.input_format.setCurrentIndex(max(0, input_index))
        folder = self.preferences.value("output_directory", "")
        self.set_destination(Path(folder) if folder else None)
        if geometry := self.preferences.value("geometry"):
            self.restoreGeometry(geometry)

    def _save_preferences(self):
        self.preferences.setValue("output_format", self.output_format.currentData())
        self.preferences.setValue("input_format", self.input_format.currentData())
        self.preferences.setValue("preset", self.settings.preset.key)
        self.preferences.setValue("output_directory", str(self.output_directory or ""))
        self.preferences.setValue("hardware_encoding", self._hardware_requested)
        self.preferences.setValue("geometry", self.saveGeometry())
        self.preferences.sync()

    @property
    def settings(self) -> ConversionSettings:
        job = self._selected_job()
        return ConversionSettings(
            OutputFormat(self.output_format.currentData()),
            list(CompressionPreset)[max(0, self.preset_buttons.checkedId())],
            self.output_directory,
            self.hardware_capabilities.for_format(OutputFormat(self.output_format.currentData()))
            if self.hardware_toggle.isChecked() else None,
            job.source_video_bps if job else None,
        )

    def _selected_job(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.jobs):
            return self.jobs[row]
        return self.jobs[0] if self.jobs else None

    def _source_preview_finished(self, info, error):
        if error != "Cancelled":
            for job in self.jobs:
                if job.source == self.source_preview.path:
                    job.source_video_bps = info.video_bitrate_bps if info else None
                    job.source_metadata_error = error
            self._refresh_preset_hint()
        self._maybe_close()

    def _refresh_preset_hint(self):
        settings = self.settings
        preset, job = settings.preset, self._selected_job()
        bitrate = settings.video_bitrate_bps
        if preset == CompressionPreset.ORIGINAL:
            if bitrate:
                bitrate_text = f"{format_bitrate(bitrate)} max (source)"
            elif not job:
                bitrate_text = "source (add files)"
            elif job.source_metadata_error:
                bitrate_text = "source unavailable"
            else:
                bitrate_text = "reading source…"
            if job and bitrate is None and not job.source_metadata_error and self.binaries and not self.queue.active and not self._close_when_finished:
                self.source_preview.start(job.source, self.binaries)
            else:
                self.source_preview.cancel()
        else:
            bitrate_text = f"{format_bitrate(bitrate)} max"
            self.source_preview.cancel()
        crf = preset.webm_crf if settings.output_format == OutputFormat.WEBM else preset.crf
        self.preset_hint.setText(f"Video bitrate: {bitrate_text} · CRF: {crf}")
        tooltip = job.source_metadata_error if job else ""
        if settings.hardware_encoder:
            try:
                tooltip = hardware_quality_description(settings.hardware_encoder, preset)
            except ValueError as error:
                tooltip = str(error)
        self.preset_hint.setToolTip(tooltip)

    def _find_ffmpeg(self):
        configured = self.preferences.value("ffmpeg_directory", "")
        try:
            self.binaries = find_binaries(Path(configured) if configured else None)
        except FFmpegNotFoundError:
            try:
                self.binaries = find_binaries()
            except FFmpegNotFoundError:
                self.binaries = None
        self._update_engine_status()
        self.recheck_hardware()

    def _update_engine_status(self):
        missing = self.binaries is None
        self.engine_button.setText("Locate FFmpeg…" if missing else "FFmpeg ready · Change…")
        self.engine_button.setProperty("missing", missing)
        self.engine_button.style().unpolish(self.engine_button)
        self.engine_button.style().polish(self.engine_button)
        self.engine_button.setToolTip("Choose a folder containing ffmpeg and ffprobe" if missing else str(self.binaries.ffmpeg))
        self.notice.setText("FFmpeg is needed to convert videos. Use ‘Locate FFmpeg’ to select its folder.")
        self.notice.setVisible(missing)

    def locate_ffmpeg(self):
        if self.queue.active:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder containing ffmpeg and ffprobe")
        if folder:
            try:
                self.binaries = find_binaries(Path(folder))
            except FFmpegNotFoundError as error:
                QMessageBox.warning(self, "FFmpeg not found", str(error))
                return
            self.preferences.setValue("ffmpeg_directory", folder)
            self._update_engine_status()
            self.recheck_hardware()

    def recheck_hardware(self):
        if self.queue.active:
            return
        self.hardware_capabilities = HardwareCapabilities()
        if self.binaries is None:
            self.hardware_detector.cancel()
        else:
            self.hardware_detector.start(self.binaries)
        self._settings_changed()

    def _hardware_detection_status(self, text):
        self._hardware_check_message = text
        self._update_hardware_controls()

    def _hardware_detection_finished(self, capabilities):
        self.hardware_capabilities = capabilities
        self._update_hardware_controls()
        self._settings_changed()
        self._maybe_close()

    def _hardware_toggled(self, checked):
        self._hardware_requested = checked
        self.preferences.setValue("hardware_encoding", checked)
        self._settings_changed()

    def _update_hardware_controls(self):
        output = OutputFormat(self.output_format.currentData())
        encoder = self.hardware_capabilities.for_format(output)
        busy, checking = self.queue.active, self.hardware_detector.running
        available = self.binaries is not None and encoder is not None and not checking
        for control, enabled in (
            (self.hardware_toggle, available and not busy),
            (self.recheck_hardware_button, self.binaries is not None and not checking and not busy),
        ):
            # Disabling a focused control otherwise tabs into Save to and selects its path.
            if not enabled and control.hasFocus():
                control.clearFocus()
            control.setEnabled(enabled)
        if not busy:
            self.hardware_toggle.blockSignals(True)
            self.hardware_toggle.setChecked(self._hardware_requested and available)
            self.hardware_toggle.blockSignals(False)
        if self.binaries is None:
            text = "Locate FFmpeg to check hardware"
        elif checking:
            text = self._hardware_check_message
        elif encoder:
            text = encoder.backend.value
        elif self.hardware_capabilities.encoders:
            text = f"No hardware encoder for {output.value.upper()}"
        else:
            text = "No compatible accelerator available"
        self.hardware_status.setText(text)
        self.hardware_status.setToolTip(
            f"Verified {encoder.codec}" + (f" on {encoder.device}" if encoder.device else "")
            if encoder else self.hardware_capabilities.details
        )

    def browse(self):
        if self.queue.active:
            return
        files, selected_filter = QFileDialog.getOpenFileNames(
            self, "Add video files", self.preferences.value("last_directory", str(Path.home())),
            ";;".join(choice.value for choice in InputFormat),
            InputFormat[self.input_format.currentData()].value,
        )
        if files:
            self.preferences.setValue("last_directory", str(Path(files[0]).parent))
            for choice in InputFormat:
                if choice.value == selected_filter:
                    self.input_format.setCurrentIndex(self.input_format.findData(choice.name))
            self.add_files(files)

    def add_files(self, paths):
        if self.queue.active:
            return
        existing = {os.path.normcase(str(job.source)) for job in self.jobs}
        rejected = []
        for value in paths:
            path = Path(value).expanduser().resolve()
            key = os.path.normcase(str(path))
            if key in existing:
                continue
            try:
                if not path.is_file():
                    raise OSError("Select files rather than folders.")
                size = path.stat().st_size
            except OSError:
                rejected.append(path.name)
                continue
            self.jobs.append(ConversionJob(path, input_size=size))
            existing.add(key)
        self._settings_changed()
        if self.jobs and not self.table.selectedIndexes():
            self.table.selectRow(0)
        if rejected:
            QMessageBox.information(self, "Some files could not be added", "Unavailable files or folders:\n" + "\n".join(rejected))

    def choose_destination(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Save converted videos to", str(self.output_directory or Path.home()),
        )
        if folder:
            self.set_destination(Path(folder))

    def set_destination(self, folder: Path | None):
        self.output_directory = folder
        self.destination.setText(str(folder) if folder else "")
        self.destination.setToolTip(str(folder) if folder else "Save beside each original video")
        self.reset_destination_button.setVisible(folder is not None)
        self._settings_changed()

    def _settings_changed(self, *_):
        if self.queue.active:
            return
        self._update_hardware_controls()
        self._refresh_preset_hint()
        reserved = [job.output for job in self.jobs if not job.retryable and job.output]
        self.table.setRowCount(len(self.jobs))
        for index, job in enumerate(self.jobs):
            if job.retryable:
                job.output = unique_output_path(job.source, self.settings, reserved)
                reserved.append(job.output)
            self._update_row(index)
        self._refresh_ui()

    def remove_selected(self):
        if self.queue.active:
            return
        rows = {index.row() for index in self.table.selectedIndexes()}
        self.jobs[:] = [job for index, job in enumerate(self.jobs) if index not in rows]
        self.table.clearSelection()
        self._settings_changed()

    def clear_files(self):
        if not self.queue.active:
            self.jobs.clear()
            self.table.setRowCount(0)
            self._refresh_ui()

    def convert(self):
        if self.queue.active or self.binaries is None or not any(job.retryable for job in self.jobs):
            return
        self._save_preferences()
        self.source_preview.cancel()
        self._last_active_index = None
        self.queue.start(self.jobs, self.settings, self.binaries)
        self._refresh_ui()

    def stop_conversion(self):
        self.queue.cancel()
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stopping…")

    def _job_changed(self, index):
        self._update_row(index)
        if self.queue.current_index == index and self._last_active_index != index:
            self.table.selectRow(index)
            self._last_active_index = index
        self._refresh_ui()

    def _update_row(self, row):
        job = self.jobs[row]
        status = job.status.value
        if job.status == JobStatus.CONVERTING and job.duration:
            status = f"{'Software' if job.hardware_fallback else 'Converting'} · {job.progress}%"
        elif job.status == JobStatus.SUCCESS:
            status = "✓  Success (CPU)" if job.hardware_fallback else "✓  Success"
        elif job.status == JobStatus.FAILED:
            status = "×  Error"
        values = [job.source.name, job.output.name if job.output else "—",
                  format_size(job.input_size), format_size(job.output_size), job.size_change, status]
        for column, value in enumerate(values):
            item = self.table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, column, item)
            item.setText(value)
            color = STATUS_COLORS[job.status] if column == 5 else "#dcdfe8"
            if column == 4 and job.saved_bytes is not None:
                color = "#7cdbab" if job.saved_bytes >= 0 else "#f0c985"
            item.setForeground(QColor(color))
            tooltip = str(job.source) if column == 0 else str(job.output or "") if column == 1 else value
            if column == 5:
                tooltip = job.details or job.status.value
            if column == 4 and job.saved_bytes is not None:
                tooltip = f"{format_size(abs(job.saved_bytes))} {'saved' if job.saved_bytes >= 0 else 'larger'}"
            item.setToolTip(tooltip)
            if column in (2, 3):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _refresh_ui(self):
        busy = self.queue.active
        pending = sum(job.retryable for job in self.jobs)
        queued = sum(job.status == JobStatus.QUEUED for job in self.jobs)
        successes = sum(job.status == JobStatus.SUCCESS for job in self.jobs)
        failures = sum(job.status == JobStatus.FAILED for job in self.jobs)
        cancelled = sum(job.status == JobStatus.CANCELLED for job in self.jobs)
        completed = successes + failures + cancelled
        self.file_count.setText(f"{len(self.jobs)} {'file' if len(self.jobs) == 1 else 'files'}")
        self.file_stack.setCurrentWidget(self.table if self.jobs else self.empty)
        for widget in (self.settings_panel, self.browse_button, self.empty_browse_button, self.engine_button):
            widget.setEnabled(not busy)
        self.clear_button.setEnabled(bool(self.jobs) and not busy)
        self.convert_button.setVisible(not busy)
        self.convert_button.setEnabled(bool(pending) and self.binaries is not None and not busy)
        verb = "Convert" if queued or not pending else "Retry"
        self.convert_button.setText(f"{verb} {pending} {'file' if pending == 1 else 'files'}  →" if pending else "Convert files  →")
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(busy and not self.queue.cancel_requested)
        self.stop_button.setText("Stopping…" if self.queue.cancel_requested else "Stop conversion")
        current = self.jobs[self.queue.current_index] if self.queue.current_index is not None else None
        if busy and self.queue.cancel_requested:
            title = "Stopping conversion…"
        elif busy and current:
            suffix = f" · {current.progress}%" if current.status == JobStatus.CONVERTING and current.duration else ""
            stage = "Converting with software" if current.status == JobStatus.CONVERTING and current.hardware_fallback else current.status.value
            title = f"{stage}: {current.source.name}{suffix}"
        elif busy:
            title = "Preparing conversion…"
        elif queued:
            title = "Ready to convert."
        elif cancelled:
            title = "Conversion stopped."
        elif failures:
            title = "Finished with errors."
        elif successes:
            title = "Conversion complete."
        else:
            title = "Ready when you are."
        self.current_label.setText(title)
        if busy and current and (current.status == JobStatus.READING_BITRATE or (current.status == JobStatus.CONVERTING and not current.duration)):
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            total = sum(100 if job.status in {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED} else job.progress for job in self.jobs)
            self.progress.setValue(int(total / len(self.jobs)) if self.jobs else 0)
        parts = [f"{completed} of {len(self.jobs)} processed"] if busy else []
        if queued and not busy:
            parts.append(f"{queued} ready")
        if successes:
            parts.append(f"{successes} converted")
        if failures:
            parts.append(f"{failures} {'error' if failures == 1 else 'errors'}")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        saved = sum(job.saved_bytes or 0 for job in self.jobs if job.status == JobStatus.SUCCESS)
        if successes:
            parts.append(f"{format_size(abs(saved))} {'saved' if saved >= 0 else 'larger in total'}")
        self.batch_label.setText(" · ".join(parts) or "Add files to get started.")
        self._update_hardware_controls()
        self._selection_changed()

    def _selection_changed(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        row = rows[0] if rows and rows[0] < len(self.jobs) else None
        self.remove_button.setEnabled(bool(rows) and not self.queue.active)
        self.details_button.setEnabled(row is not None)
        job = self.jobs[row] if row is not None else None
        self.open_folder_button.setEnabled(bool(job and job.status == JobStatus.SUCCESS and job.output))
        self._refresh_preset_hint()

    def show_details(self, row=None):
        if row is None:
            row = self.table.currentRow()
        if row < 0 or row >= len(self.jobs):
            return
        job = self.jobs[row]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Conversion details — {job.source.name}")
        dialog.resize(680, 380)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            f"Input: {job.source}\nOutput: {job.output or '—'}\nStatus: {job.status.value}\n"
            f"Original: {format_size(job.input_size)}\nConverted: {format_size(job.output_size)}\n"
            f"Size change: {job.size_change}\n\n{job.details or 'No errors reported.'}"
        )
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def open_output_folder(self):
        row = self.table.currentRow()
        if row >= 0:
            job = self.jobs[row]
            if job.output and job.status == JobStatus.SUCCESS:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.output.parent)))

    def _conversion_finished(self):
        self._settings_changed()
        self._maybe_close()

    def _maybe_close(self):
        if self._close_when_finished and not self.queue.active and not self.hardware_detector.running and not self.source_preview.running:
            QTimer.singleShot(0, self.close)

    def dragEnterEvent(self, event):
        if not self.queue.active and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self._highlight_drop(True)

    def dragLeaveEvent(self, event):
        self._highlight_drop(False)
        event.accept()

    def dropEvent(self, event):
        self._highlight_drop(False)
        if not self.queue.active:
            self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def _highlight_drop(self, active):
        self.empty.setProperty("dragOver", active)
        self.empty.style().unpolish(self.empty)
        self.empty.style().polish(self.empty)

    def closeEvent(self, event):
        self._save_preferences()
        if self.queue.active or self.hardware_detector.running or self.source_preview.running:
            self._close_when_finished = True
            self.source_preview.cancel()
            self.hardware_detector.cancel()
            if self.queue.active:
                self.stop_conversion()
            event.ignore()
            self._maybe_close()
        else:
            event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Video Converter")
    app.setOrganizationName("VideoConverter")
    apply_theme(app)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.add_files(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
