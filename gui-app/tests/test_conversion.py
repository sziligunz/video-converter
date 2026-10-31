import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QTimer

from tests.support import BINARIES, make_video, wait_for
from video_converter.ffmpeg import ConversionQueue, FFmpegBinaries
from video_converter.models import (
    CompressionPreset, ConversionJob, ConversionSettings, HardwareBackend,
    HardwareEncoder, JobStatus, OutputFormat,
)


@unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
class ConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        cls.source = make_video(Path(cls.fixture_directory.name) / "café & $(literal).mkv")
        cls.silent = make_video(Path(cls.fixture_directory.name) / "odd silent.avi", audio=False, odd=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.queue = ConversionQueue()

    def tearDown(self):
        if self.queue.active:
            self.queue.cancel()
            wait_for(lambda: not self.queue.active)
        self.directory.cleanup()

    def run_jobs(self, jobs, settings=None, binaries=BINARIES):
        self.queue.start(jobs, settings or ConversionSettings(output_directory=self.root), binaries)
        wait_for(lambda: not self.queue.active)
        return jobs

    def inspect(self, path):
        result = subprocess.run(
            [str(BINARIES.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)["streams"]

    def assert_clean(self):
        self.assertEqual(list(self.root.rglob(".video-converter-*")), [])

    def test_all_presets_produce_valid_smaller_mp4_with_audio_and_original_resolution(self):
        sizes = []
        for preset in CompressionPreset:
            with self.subTest(preset=preset):
                job = ConversionJob(self.source)
                seen_progress, heartbeats = [], []
                on_change = lambda index: seen_progress.append(job.progress)
                self.queue.job_changed.connect(on_change)
                heartbeat = QTimer()
                heartbeat.setInterval(10)
                heartbeat.timeout.connect(lambda: heartbeats.append(True))
                heartbeat.start()
                self.run_jobs([job], ConversionSettings(preset=preset, output_directory=self.root))
                heartbeat.stop()
                self.queue.job_changed.disconnect(on_change)
                self.assertEqual(job.status, JobStatus.SUCCESS, job.error)
                # Read x264's embedded settings to check the encoder used the real limit.
                encoded = job.output.read_bytes()
                self.assertIn(f"vbv_maxrate={job.target_video_bps // 1000}".encode(), encoded)
                self.assertIn(f"vbv_bufsize={job.target_video_bps * 2 // 1000}".encode(), encoded)
                self.assertEqual(job.progress, 100)
                self.assertTrue(any(0 < value < 100 for value in seen_progress))
                self.assertTrue(heartbeats, "The Qt event loop must remain responsive")
                self.assertLess(job.output_size, job.input_size)
                streams = self.inspect(job.output)
                video = next(stream for stream in streams if stream["codec_type"] == "video")
                self.assertEqual((video["codec_name"], video["width"], video["height"]), ("h264", 320, 180))
                self.assertEqual(video["r_frame_rate"], "24/1")
                self.assertEqual(next(stream["codec_name"] for stream in streams if stream["codec_type"] == "audio"), "aac")
                sizes.append(job.output_size)
        self.assertLess(sizes[0], sizes[1])
        self.assertLess(sizes[1], sizes[2])
        self.assert_clean()

    def test_alternative_outputs_use_compatible_codecs(self):
        for output in (OutputFormat.MKV, OutputFormat.MOV, OutputFormat.WEBM):
            with self.subTest(output=output):
                job = ConversionJob(self.source)
                self.run_jobs([job], ConversionSettings(output_format=output, output_directory=self.root))
                self.assertEqual(job.status, JobStatus.SUCCESS, job.error)
                self.assertEqual(job.output.suffix, f".{output.value}")
                streams = self.inspect(job.output)
                codecs = {stream["codec_name"] for stream in streams}
                self.assertTrue(({"vp9", "opus"} if output == OutputFormat.WEBM else {"h264", "aac"}) <= codecs)
        self.assert_clean()

    def test_odd_silent_avi_converts_without_audio(self):
        job = ConversionJob(self.silent)
        self.run_jobs([job])
        self.assertEqual(job.status, JobStatus.SUCCESS, job.error)
        streams = self.inspect(job.output)
        self.assertEqual(len(streams), 1)
        self.assertEqual((streams[0]["width"], streams[0]["height"]), (162, 92))

    def test_corrupt_missing_and_valid_files_have_individual_results(self):
        corrupt = self.root / "broken.mkv"
        corrupt.write_text("not a video")
        jobs = [ConversionJob(corrupt), ConversionJob(self.root / "missing.mkv"), ConversionJob(self.source)]
        self.run_jobs(jobs)
        self.assertEqual([job.status for job in jobs], [JobStatus.FAILED, JobStatus.FAILED, JobStatus.SUCCESS])
        self.assertTrue(jobs[0].error)
        self.assertFalse(jobs[0].output.exists())
        self.assertTrue(jobs[2].output.exists())
        self.assert_clean()
        # Repair the failed inputs and retry; the successful output must be untouched.
        saved = jobs[2].output.read_bytes()
        for job in jobs[:2]:
            shutil.copyfile(self.source, job.source)
        self.run_jobs(jobs)
        self.assertTrue(all(job.status == JobStatus.SUCCESS for job in jobs))
        self.assertEqual(jobs[2].output.read_bytes(), saved)

    def test_existing_output_and_same_basename_inputs_are_preserved(self):
        first = self.root / "a" / "holiday.mkv"
        second = self.root / "b" / "holiday.mkv"
        for target in (first, second):
            target.parent.mkdir()
            shutil.copyfile(self.source, target)
        existing = self.root / "holiday_balanced.mp4"
        existing.write_bytes(b"keep this previous output")
        jobs = self.run_jobs([ConversionJob(first), ConversionJob(second)])
        self.assertTrue(all(job.status == JobStatus.SUCCESS for job in jobs))
        self.assertNotEqual(jobs[0].output, jobs[1].output)
        self.assertEqual(existing.read_bytes(), b"keep this previous output")
        self.assertEqual(first.read_bytes(), self.source.read_bytes())
        self.assert_clean()

    def test_stop_cleans_partial_files_and_marks_remaining_jobs_cancelled(self):
        jobs = [ConversionJob(self.source), ConversionJob(self.silent)]
        def stop_on_progress(index):
            if jobs[index].status == JobStatus.CONVERTING and jobs[index].progress > 0:
                QTimer.singleShot(0, self.queue.cancel)
        self.queue.job_changed.connect(stop_on_progress)
        self.run_jobs(jobs)
        self.queue.job_changed.disconnect(stop_on_progress)
        self.assertEqual([job.status for job in jobs], [JobStatus.CANCELLED, JobStatus.CANCELLED])
        self.assertEqual(list(self.root.iterdir()), [])
        self.run_jobs(jobs)
        self.assertTrue(all(job.status == JobStatus.SUCCESS for job in jobs))
        self.assert_clean()

    def test_cancellation_from_a_status_handler_does_not_launch_another_process(self):
        job = ConversionJob(self.source)
        def cancel_on_start(index):
            if job.status == JobStatus.CONVERTING:
                self.queue.cancel()
        self.queue.job_changed.connect(cancel_on_start)
        self.run_jobs([job])
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertEqual(self.queue.process.state(), self.queue.process.ProcessState.NotRunning)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_changed_destination_is_left_untouched(self):
        job = ConversionJob(self.source)
        def change_destination(index):
            if job.status == JobStatus.CONVERTING and job.progress == 0:
                job.output.write_bytes(b"written by another application")
        self.queue.job_changed.connect(change_destination)
        self.run_jobs([job])
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("destination changed", job.error)
        self.assertEqual(job.output.read_bytes(), b"written by another application")
        self.assert_clean()

    def test_missing_executable_is_a_row_error_and_does_not_hang(self):
        job = ConversionJob(self.source)
        self.run_jobs([job], binaries=FFmpegBinaries(self.root / "no-ffmpeg", BINARIES.ffprobe))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("Could not start", job.error)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_hardware_failure_retries_with_software_and_retains_diagnostics(self):
        job = ConversionJob(self.source)
        settings = ConversionSettings(
            output_directory=self.root,
            hardware_encoder=HardwareEncoder(HardwareBackend.NVENC, "h264_missing_test_encoder"),
        )
        self.run_jobs([job], settings)
        self.assertEqual(job.status, JobStatus.SUCCESS, job.error)
        self.assertEqual(job.error, "")
        self.assertIn("h264_missing_test_encoder", job.hardware_fallback)
        self.assertEqual(job.encoding_method, "Software (libx264)")
        self.assertIn("retried with software", job.details)
        self.assertTrue(job.output.exists())
        self.assert_clean()

    def test_original_resolves_a_separate_video_bitrate_for_every_file(self):
        jobs = [ConversionJob(self.source), ConversionJob(self.silent)]
        self.run_jobs(jobs, ConversionSettings(preset=CompressionPreset.ORIGINAL, output_directory=self.root))
        self.assertTrue(all(job.status == JobStatus.SUCCESS for job in jobs))
        self.assertNotEqual(jobs[0].source_video_bps, jobs[1].source_video_bps)
        for job in jobs:
            self.assertEqual(job.target_video_bps, job.source_video_bps)
            self.assertIn(f"vbv_maxrate={job.source_video_bps // 1000}".encode(), job.output.read_bytes())
            self.assertTrue(job.output.name.endswith("_original.mp4"))
            self.assertEqual(job.quality_description, "CRF 18")
        self.assert_clean()

    def test_original_hardware_fallback_keeps_the_source_bitrate_and_quality(self):
        job = ConversionJob(self.source)
        self.run_jobs([job], ConversionSettings(
            preset=CompressionPreset.ORIGINAL, output_directory=self.root,
            hardware_encoder=HardwareEncoder(HardwareBackend.NVENC, "h264_missing_test_encoder"),
        ))
        self.assertEqual(job.status, JobStatus.SUCCESS, job.error)
        self.assertTrue(job.hardware_fallback)
        self.assertEqual(job.target_video_bps, job.source_video_bps)
        self.assertEqual(job.quality_description, "CRF 18")
        self.assertIn(f"vbv_maxrate={job.source_video_bps // 1000}".encode(), job.output.read_bytes())
        self.assert_clean()

    def test_cancelling_during_hardware_fallback_does_not_start_software(self):
        job = ConversionJob(self.source)
        launches = []
        original_launch = self.queue._launch
        def launch(stage, executable, arguments):
            launches.append(stage)
            original_launch(stage, executable, arguments)
        self.queue._launch = launch
        def stop_on_fallback(index):
            if job.hardware_fallback and job.status == JobStatus.CONVERTING:
                self.queue.cancel()
        self.queue.job_changed.connect(stop_on_fallback)
        self.run_jobs([job], ConversionSettings(
            output_directory=self.root,
            hardware_encoder=HardwareEncoder(HardwareBackend.NVENC, "h264_missing_test_encoder"),
        ))
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertEqual(launches.count("convert"), 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stalled_probe_times_out_and_cleans_up(self):
        job = ConversionJob(self.source)
        self.queue.timeout.setInterval(40)
        with patch("video_converter.media.probe_arguments", return_value=["-c", "import time; time.sleep(10)"]):
            self.run_jobs([job], binaries=FFmpegBinaries(BINARIES.ffmpeg, Path(sys.executable)))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("timed out", job.error)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_destination_reports_error_without_altering_source(self):
        job = ConversionJob(self.source)
        self.run_jobs([job], ConversionSettings(output_directory=self.source))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("Could not prepare", job.error)
        self.assertTrue(self.source.exists())
