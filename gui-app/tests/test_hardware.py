import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QProcess, QTimer

from tests.support import wait_for
from video_converter.ffmpeg import FFmpegBinaries, conversion_arguments
from video_converter.hardware import HardwareDetector, hardware_candidates
from video_converter.models import (
    CompressionPreset, ConversionSettings, HardwareBackend, HardwareEncoder, OutputFormat,
)


# A child process simulates drivers, so detection's timing, cancellation, and
# success/failure handling are tested on hosts without these hardware backends.
DRIVER_SCRIPT = '''
import json, sys, time
from pathlib import Path
scenario_path = Path(sys.argv[1])
scenario = json.loads(scenario_path.read_text())
arguments = sys.argv[2:]
with scenario_path.with_suffix('.calls').open('a') as calls:
    calls.write(json.dumps(arguments) + '\\n')
if '-encoders' in arguments:
    for name in scenario.get('listed', []):
        print(' V....D ' + name + ' test encoder')
else:
    codec = arguments[arguments.index('-c:v') + 1]
    time.sleep(scenario.get('delays', {}).get(codec, 0))
    if codec not in scenario.get('working', []):
        print('Hardware driver is unavailable for ' + codec, file=sys.stderr)
        sys.exit(1)
'''


class HardwareDetectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.script = self.root / "driver.py"
        self.script.write_text(DRIVER_SCRIPT)
        self.scenario = self.root / "scenario.json"
        self.detector = HardwareDetector()
        self.results = []
        self.detector.finished.connect(self.results.append)
        original_launch = self.detector._launch
        self.detector._launch = lambda phase, arguments: original_launch(
            phase, [str(self.script), str(self.scenario), *arguments],
        )
        self.binaries = FFmpegBinaries(Path(sys.executable), Path(sys.executable))

    def tearDown(self):
        self.detector.cancel()
        wait_for(lambda: not self.detector.running)
        self.directory.cleanup()

    def run_detection(self, *, listed, working=(), delays=None, candidates=None):
        self.scenario.write_text(json.dumps({"listed": listed, "working": working, "delays": delays or {}}))
        candidates = candidates or [HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc")]
        with patch("video_converter.hardware.hardware_candidates", return_value=candidates):
            self.detector.start(self.binaries)
            wait_for(lambda: bool(self.results))
        return self.results[-1]

    def calls(self):
        return [json.loads(line) for line in self.scenario.with_suffix(".calls").read_text().splitlines()]

    def test_listing_an_encoder_does_not_enable_it_when_its_driver_fails(self):
        result = self.run_detection(listed=["h264_nvenc"])
        self.assertEqual(result.encoders, ())
        self.assertIn("driver is unavailable", result.details)
        self.assertEqual(len(self.calls()), 2, "A real test encode must follow the encoder list")

    def test_finds_working_h264_and_vp9_backends_after_rejecting_unavailable_gpu(self):
        candidates = [
            HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc"),
            HardwareEncoder(HardwareBackend.QSV, "h264_qsv"),
            HardwareEncoder(HardwareBackend.AMF, "h264_amf"),
            HardwareEncoder(HardwareBackend.QSV, "vp9_qsv"),
        ]
        result = self.run_detection(
            listed=[item.codec for item in candidates], working=["h264_qsv", "vp9_qsv"], candidates=candidates,
        )
        self.assertEqual(result.for_format(OutputFormat.MP4).codec, "h264_qsv")
        self.assertEqual(result.for_format(OutputFormat.MKV).codec, "h264_qsv")
        self.assertEqual(result.for_format(OutputFormat.MOV).codec, "h264_qsv")
        self.assertEqual(result.for_format(OutputFormat.WEBM).codec, "vp9_qsv")
        tested = [arguments[arguments.index("-c:v") + 1] for arguments in self.calls()[1:]]
        self.assertEqual(tested, ["h264_nvenc", "h264_qsv", "vp9_qsv"])

    def test_macos_probe_requires_hardware_videotoolbox(self):
        with patch("video_converter.hardware.sys.platform", "darwin"):
            candidates = hardware_candidates()
        result = self.run_detection(listed=["h264_videotoolbox"], working=["h264_videotoolbox"], candidates=candidates)
        self.assertEqual(result.for_format(OutputFormat.MP4).backend, HardwareBackend.VIDEOTOOLBOX)
        arguments = self.calls()[1]
        self.assertEqual(arguments[arguments.index("-allow_sw") + 1], "0")
        self.assertEqual(arguments[arguments.index("-b:v") + 1], "8000000")
        self.assertEqual(arguments[arguments.index("-q:v") + 1], "55")
        self.assertNotIn("-crf", arguments)
        self.assertIsNone(result.for_format(OutputFormat.WEBM))

    def test_stalled_driver_times_out_without_freezing_the_event_loop(self):
        beats = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: beats.append(True))
        timer.start()
        self.detector.timeout.setInterval(200)
        result = self.run_detection(listed=["h264_nvenc"], working=["h264_nvenc"], delays={"h264_nvenc": 10})
        timer.stop()
        self.assertEqual(result.encoders, ())
        self.assertIn("timed out", result.details)
        self.assertGreater(len(beats), 5)
        self.assertEqual(self.detector.process.state(), QProcess.ProcessState.NotRunning)

    def test_recheck_cancels_an_old_probe_and_uses_only_the_latest_result(self):
        self.scenario.write_text(json.dumps({"listed": ["h264_nvenc"], "delays": {"h264_nvenc": 10}}))
        candidate = HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc")
        with patch("video_converter.hardware.hardware_candidates", return_value=[candidate]):
            self.detector.start(self.binaries)
            wait_for(lambda: len(self.calls()) >= 2 if self.scenario.with_suffix(".calls").exists() else False)
            self.scenario.write_text(json.dumps({"listed": ["h264_nvenc"], "working": ["h264_nvenc"]}))
            self.detector.start(self.binaries)
            wait_for(lambda: bool(self.results))
        self.assertEqual(len(self.results), 1)
        self.assertEqual(self.results[0].encoders, (candidate,))

    def test_cancel_kills_a_running_probe_and_never_reports_it_as_available(self):
        self.scenario.write_text(json.dumps({"listed": ["h264_nvenc"], "working": ["h264_nvenc"], "delays": {"h264_nvenc": 10}}))
        with patch("video_converter.hardware.hardware_candidates", return_value=[HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc")]):
            self.detector.start(self.binaries)
            wait_for(lambda: len(self.calls()) >= 2 if self.scenario.with_suffix(".calls").exists() else False)
            self.detector.cancel()
            wait_for(lambda: not self.detector.running)
        self.assertEqual(self.results[-1].encoders, ())
        self.assertEqual(self.detector.process.state(), QProcess.ProcessState.NotRunning)

    def test_missing_ffmpeg_finishes_with_diagnostics(self):
        self.detector.start(FFmpegBinaries(self.root / "missing-ffmpeg", self.root / "missing-ffprobe"))
        wait_for(lambda: bool(self.results))
        self.assertEqual(self.results[-1].encoders, ())
        self.assertIn("Could not start FFmpeg", self.results[-1].details)


class HardwareCommandTests(unittest.TestCase):
    def test_each_preset_applies_both_quality_and_its_fixed_or_source_bitrate(self):
        for preset, bitrate, quality in [
            (CompressionPreset.SIMPLE, 4_000_000, "28"),
            (CompressionPreset.BALANCED, 8_000_000, "23"),
            (CompressionPreset.ORIGINAL, 6_543_210, "18"),
        ]:
            with self.subTest(preset=preset):
                settings = ConversionSettings(
                    preset=preset, source_video_bps=6_543_210,
                    hardware_encoder=HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc"),
                )
                arguments = conversion_arguments(Path("source.mkv"), Path("output.mp4"), settings)
                self.assertEqual(arguments[arguments.index("-maxrate:v") + 1], str(bitrate))
                self.assertEqual(arguments[arguments.index("-cq") + 1], quality)

    def test_hardware_conversion_applies_native_quality_and_bitrate(self):
        for backend, codec, quality_option, quality_value in [
            (HardwareBackend.VIDEOTOOLBOX, "h264_videotoolbox", "-q:v", "55"),
            (HardwareBackend.NVENC, "h264_nvenc", "-cq", "23"),
            (HardwareBackend.QSV, "h264_qsv", "-global_quality", "23"),
            (HardwareBackend.AMF, "h264_amf", "-qvbr_quality_level", "23"),
        ]:
            with self.subTest(backend=backend):
                arguments = conversion_arguments(Path("source.mkv"), Path("output.mp4"), ConversionSettings(
                    hardware_encoder=HardwareEncoder(backend, codec),
                ))
                self.assertEqual(arguments[arguments.index("-c:v") + 1], codec)
                expected_target = "7200000" if backend == HardwareBackend.QSV else "8000000"
                self.assertEqual(arguments[arguments.index("-b:v") + 1], expected_target)
                self.assertEqual(arguments[arguments.index("-maxrate:v") + 1], "8000000")
                self.assertEqual(arguments[arguments.index(quality_option) + 1], quality_value)
                self.assertEqual(arguments[arguments.index("-c:a") + 1], "aac")
                self.assertNotIn("-crf", arguments)
                self.assertNotIn("-preset", arguments)
                if backend == HardwareBackend.QSV:
                    self.assertEqual(arguments[arguments.index("-init_hw_device") + 1], "qsv:hw")

    def test_media_foundation_is_rejected_instead_of_silently_ignoring_quality(self):
        with self.assertRaisesRegex(ValueError, "combined quality and bitrate"):
            conversion_arguments(Path("source.mkv"), Path("output.mp4"), ConversionSettings(
                hardware_encoder=HardwareEncoder(HardwareBackend.MEDIA_FOUNDATION, "h264_mf"),
            ))

    def test_vaapi_uploads_nv12_frames_to_the_verified_render_device(self):
        encoder = HardwareEncoder(HardwareBackend.VAAPI, "vp9_vaapi", "/dev/dri/renderD129")
        arguments = conversion_arguments(Path("source.mkv"), Path("output.webm"), ConversionSettings(
            output_format=OutputFormat.WEBM, hardware_encoder=encoder,
        ))
        self.assertLess(arguments.index("-vaapi_device"), arguments.index("-i"))
        self.assertEqual(arguments[arguments.index("-vaapi_device") + 1], encoder.device)
        self.assertIn("format=nv12,hwupload", arguments[arguments.index("-vf") + 1])
        self.assertNotIn("-pix_fmt", arguments)
        self.assertNotIn("-crf", arguments)
        self.assertEqual(arguments[arguments.index("-rc_mode") + 1], "QVBR")
        self.assertEqual(arguments[arguments.index("-global_quality") + 1], "26")
        self.assertEqual(arguments[arguments.index("-c:a") + 1], "libopus")

    def test_incompatible_h264_encoder_cannot_be_used_for_webm(self):
        with self.assertRaisesRegex(ValueError, "cannot encode WEBM"):
            conversion_arguments(Path("source.mkv"), Path("output.webm"), ConversionSettings(
                output_format=OutputFormat.WEBM,
                hardware_encoder=HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc"),
            ))
