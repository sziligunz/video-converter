from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QStyleOptionViewItem

from tests.support import APP, BINARIES, make_video, wait_for
from video_converter.app import MainWindow
from video_converter.ffmpeg import FFmpegNotFoundError
from video_converter.models import (
    CompressionPreset, HardwareBackend, HardwareCapabilities, HardwareEncoder,
    InputFormat, JobStatus, OutputFormat,
)
from video_converter.theme import apply_theme


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_theme(APP)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(self.preferences)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        if self.window.queue.active:
            self.window.queue.cancel()
            wait_for(lambda: not self.window.queue.active)
        self.window.close()
        wait_for(lambda: not self.window.hardware_detector.running)
        wait_for(lambda: not self.window.source_preview.running)
        APP.processEvents()
        self.directory.cleanup()

    def test_browse_multiple_files_deduplicates_and_honors_input_filter(self):
        paths = [self.root / "first.mkv", self.root / "second.mkv"]
        for path in paths:
            path.write_bytes(b"video placeholder")
        self.window.input_format.setCurrentIndex(self.window.input_format.findData(InputFormat.ALL_VIDEO.name))
        with patch("video_converter.app.QFileDialog.getOpenFileNames", return_value=([str(path) for path in paths], InputFormat.ALL_VIDEO.value)) as browse:
            self.window.browse()
            self.assertEqual(browse.call_args.args[4], InputFormat.ALL_VIDEO.value)
        self.window.add_files([paths[0]])
        self.assertEqual(len(self.window.jobs), 2)
        self.assertEqual(self.window.table.rowCount(), 2)
        self.assertEqual(self.window.file_stack.currentWidget(), self.window.table)
        self.assertEqual(self.window.table.item(0, 0).text(), "first.mkv")

    def test_presets_output_format_and_output_folder_update_preview(self):
        source = self.root / "film.mkv"
        source.write_bytes(b"video placeholder")
        self.window.add_files([source])
        self.window.jobs[0].source_video_bps = 12_500_000
        self.window.output_format.setCurrentIndex(self.window.output_format.findData("webm"))
        self.window.preset_buttons.button(2).click()
        folder = self.root / "converted"
        self.window.set_destination(folder)
        self.assertEqual(self.window.settings.output_format, OutputFormat.WEBM)
        self.assertEqual(self.window.settings.preset, CompressionPreset.ORIGINAL)
        self.assertEqual(self.window.jobs[0].output, folder / "film_original.webm")
        self.assertEqual(self.window.preset_hint.text(), "Video bitrate: 12,500 kb/s max (source) · CRF: 24")
        self.window.output_format.setCurrentIndex(self.window.output_format.findData("mp4"))
        self.assertEqual(self.window.preset_hint.text(), "Video bitrate: 12,500 kb/s max (source) · CRF: 18")
        self.window.remove_selected()
        self.assertEqual(self.window.jobs, [])
        self.assertTrue(source.exists())
        self.assertEqual(self.window.file_stack.currentWidget(), self.window.empty)

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_original_previews_selected_source_bitrate_asynchronously(self):
        first = make_video(self.root / "first.mkv")
        second = make_video(self.root / "second.mkv", audio=False, odd=True)
        self.window.add_files([first, second])
        self.window.preset_buttons.button(2).click()
        self.window.table.selectRow(1)
        wait_for(lambda: self.window.jobs[1].source_video_bps is not None)
        self.assertEqual(self.window.settings.source_video_bps, self.window.jobs[1].source_video_bps)
        self.assertIn("(source)", self.window.preset_hint.text())
        self.assertIn("CRF: 18", self.window.preset_hint.text())
        self.assertEqual([button.text() for button in self.window.preset_buttons.buttons()], ["Simple", "Balanced", "Original"])

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_original_batch_converts_while_source_preview_is_still_loading(self):
        first = make_video(self.root / "first.mkv")
        second = make_video(self.root / "second.avi", audio=False, odd=True)
        self.window.add_files([first, second])
        self.window.preset_buttons.button(2).click()
        reading_states = []
        def changed(index):
            if self.window.jobs[index].status == JobStatus.READING_BITRATE:
                reading_states.append(index)
        self.window.queue.job_changed.connect(changed)
        self.window.convert()
        wait_for(lambda: not self.window.queue.active)
        self.assertTrue(reading_states)
        self.assertTrue(all(job.status == JobStatus.SUCCESS for job in self.window.jobs))
        self.assertTrue(all(job.target_video_bps == job.source_video_bps for job in self.window.jobs))
        self.assertNotEqual(self.window.jobs[0].source_video_bps, self.window.jobs[1].source_video_bps)
        self.assertIn("CRF: 18", self.window.preset_hint.text())

    def test_legacy_preset_preferences_migrate_to_new_names(self):
        self.preferences.setValue("preset", "compact")
        self.window._load_preferences()
        self.assertEqual(self.window.settings.preset, CompressionPreset.SIMPLE)
        self.preferences.setValue("preset", "high_res")
        self.window._load_preferences()
        self.assertEqual(self.window.settings.preset, CompressionPreset.ORIGINAL)

    def test_missing_ffmpeg_shows_actionable_state_and_disables_conversion(self):
        with patch("video_converter.app.find_binaries", side_effect=FFmpegNotFoundError("missing")):
            self.window._find_ffmpeg()
        source = self.root / "film.mkv"
        source.write_bytes(b"video placeholder")
        self.window.add_files([source])
        self.assertTrue(self.window.notice.isVisible())
        self.assertEqual(self.window.engine_button.text(), "Locate FFmpeg…")
        self.assertFalse(self.window.convert_button.isEnabled())
        self.assertTrue(self.window.browse_button.isEnabled())
        self.assertFalse(self.window.hardware_toggle.isEnabled())

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_hardware_toggle_uses_verified_capability_and_restores_choice_for_compatible_formats(self):
        self.window.hardware_detector.cancel()
        wait_for(lambda: not self.window.hardware_detector.running)
        encoder = HardwareEncoder(HardwareBackend.VIDEOTOOLBOX, "h264_videotoolbox")
        self.window._hardware_detection_finished(HardwareCapabilities((encoder,)))
        self.assertTrue(self.window.hardware_toggle.isEnabled())
        self.assertFalse(self.window.hardware_toggle.isChecked())
        self.window.hardware_toggle.click()
        self.assertEqual(self.window.settings.hardware_encoder, encoder)
        self.assertEqual(self.window.hardware_status.text(), "Apple VideoToolbox")
        self.assertIn("CRF: 23", self.window.preset_hint.text())
        self.assertIn("-q:v 55", self.window.preset_hint.toolTip())
        self.window.output_format.setCurrentIndex(self.window.output_format.findData("webm"))
        self.assertFalse(self.window.hardware_toggle.isEnabled())
        self.assertFalse(self.window.hardware_toggle.isChecked())
        self.assertIsNone(self.window.settings.hardware_encoder)
        self.assertEqual(self.window.hardware_status.text(), "No hardware encoder for WEBM")
        self.assertIn("CRF: 32", self.window.preset_hint.text())
        self.window.output_format.setCurrentIndex(self.window.output_format.findData("mp4"))
        self.assertTrue(self.window.hardware_toggle.isChecked())
        self.assertEqual(self.window.settings.hardware_encoder, encoder)
        self.window._save_preferences()
        self.assertTrue(self.preferences.value("hardware_encoding", False, type=bool))

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_vp9_hardware_is_only_available_for_webm_and_no_capability_disables_the_toggle(self):
        self.window.hardware_detector.cancel()
        wait_for(lambda: not self.window.hardware_detector.running)
        encoder = HardwareEncoder(HardwareBackend.QSV, "vp9_qsv")
        self.window._hardware_detection_finished(HardwareCapabilities((encoder,)))
        self.assertFalse(self.window.hardware_toggle.isEnabled())
        self.window.output_format.setCurrentIndex(self.window.output_format.findData("webm"))
        self.assertTrue(self.window.hardware_toggle.isEnabled())
        self.window.hardware_toggle.click()
        self.assertEqual(self.window.settings.hardware_encoder, encoder)
        self.window._hardware_detection_finished(HardwareCapabilities(details="No GPU driver"))
        self.assertFalse(self.window.hardware_toggle.isEnabled())
        self.assertFalse(self.window.hardware_toggle.isChecked())
        self.assertIsNone(self.window.settings.hardware_encoder)
        self.assertEqual(self.window.hardware_status.toolTip(), "No GPU driver")

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_real_batch_populates_sizes_and_green_success_red_error(self):
        source = make_video(self.root / "holiday.mkv")
        broken = self.root / "broken.mkv"
        broken.write_text("not a video")
        self.window.add_files([source, broken])
        self.window.convert()
        self.assertFalse(self.window.browse_button.isEnabled())
        self.assertFalse(self.window.settings_panel.isEnabled())
        self.assertTrue(self.window.stop_button.isVisible())
        wait_for(lambda: not self.window.queue.active)
        self.assertEqual([job.status for job in self.window.jobs], [JobStatus.SUCCESS, JobStatus.FAILED])
        self.assertEqual(self.window.table.item(0, 5).foreground().color().name(), "#7cdbab")
        self.assertEqual(self.window.table.item(1, 5).foreground().color().name(), "#ff8792")
        option = QStyleOptionViewItem()
        self.window.table.itemDelegate().initStyleOption(option, self.window.table.model().index(1, 5))
        self.assertEqual(option.palette.color(QPalette.ColorRole.HighlightedText).name(), "#ff8792")
        self.assertIn("smaller", self.window.table.item(0, 4).text())
        self.assertNotEqual(self.window.table.item(0, 3).text(), "—")
        self.assertIn("1 converted", self.window.batch_label.text())
        self.assertIn("1 error", self.window.batch_label.text())
        self.assertEqual(self.window.progress.value(), 100)
        self.assertTrue(self.window.browse_button.isEnabled())
        self.assertEqual(self.window.convert_button.text(), "Retry 1 file  →")
        self.window.grab().save(str(Path(tempfile.gettempdir()) / "video-converter-results.png"))

    @unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
    def test_closing_during_conversion_stops_the_process_before_window_closes(self):
        source = make_video(self.root / "holiday.mkv")
        self.window.add_files([source])
        def close_on_start(index):
            if self.window.jobs[index].status == JobStatus.CONVERTING:
                QTimer.singleShot(0, self.window.close)
        self.window.queue.job_changed.connect(close_on_start)
        self.window.convert()
        wait_for(lambda: not self.window.queue.active)
        wait_for(lambda: not self.window.isVisible())
        self.assertEqual(self.window.jobs[0].status, JobStatus.CANCELLED)
        self.assertEqual(list(self.root.glob("*.mp4")), [])
        self.assertEqual(list(self.root.glob(".video-converter-*")), [])
