import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PyQt6.QtCore import QProcess

from tests.support import BINARIES, make_video, wait_for
from video_converter.media import PacketBitrate, SourceProbe, parse_media_info


class MediaParsingTests(unittest.TestCase):
    def test_video_bitrate_excludes_audio_container_rate_and_cover_art(self):
        info = parse_media_info(json.dumps({
            "format": {"duration": "10", "bit_rate": "9000000"},
            "streams": [
                {"codec_type": "audio", "bit_rate": "192000"},
                {"codec_type": "video", "bit_rate": "10000", "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "duration": "9.5", "bit_rate": "6543210"},
                {"codec_type": "video", "bit_rate": "1234567"},
            ],
        }).encode())
        self.assertEqual(info.video_bitrate_bps, 6_543_210)
        self.assertEqual(info.video_duration, 9.5)

    def test_mkv_bps_tags_and_byte_count_duration_are_supported(self):
        info = parse_media_info(b'{"streams":[{"codec_type":"video","tags":{"BPS-eng":"8123456","DURATION":"00:00:02.5"}}]}')
        self.assertEqual(info.video_bitrate_bps, 8_123_456)
        self.assertEqual(info.video_duration, 2.5)
        info = parse_media_info(b'{"streams":[{"codec_type":"video","tags":{"NUMBER_OF_BYTES":"1250000","DURATION":"00:00:10"}}]}')
        self.assertEqual(info.video_bitrate_bps, 1_000_000)

    def test_container_bitrate_is_not_used_as_an_original_video_bitrate(self):
        info = parse_media_info(b'{"format":{"duration":"10","bit_rate":"8000000"},"streams":[{"codec_type":"video","bit_rate":"N/A"}]}')
        self.assertIsNone(info.video_bitrate_bps)

    def test_packet_counter_handles_chunks_reordered_timestamps_and_no_final_newline(self):
        counter = PacketBitrate()
        data = (
            b"pts_time=0.200000|duration_time=0.100000|size=500\n"
            b"pts_time=0.000000|duration_time=0.100000|size=500\n"
            b"pts_time=N/A|dts_time=0.100000|duration_time=0.100000|size=500"
        )
        for position in range(0, len(data), 7):
            counter.feed(data[position:position + 7])
        self.assertEqual(counter.bitrate(None), 40_000)
        self.assertEqual(counter.buffer, b"")

    def test_unknown_packet_timing_uses_known_video_duration_or_reports_failure(self):
        counter = PacketBitrate()
        counter.feed(b"pts_time=N/A|size=1000\nsize=500\n")
        self.assertEqual(counter.bitrate(1.5), 8_000)
        with self.assertRaisesRegex(ValueError, "source video's bitrate"):
            counter.bitrate(None)


@unittest.skipUnless(BINARIES, "FFmpeg and ffprobe are required")
class SourceProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.source = make_video(Path(cls.directory.name) / "source with audio.mkv")
        cls.mp4 = Path(cls.directory.name) / "source.mp4"
        subprocess.run([
            str(BINARIES.ffmpeg), "-v", "error", "-i", str(cls.source),
            "-c:v", "libx264", "-c:a", "aac", str(cls.mp4),
        ], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.probe = SourceProbe()
        self.results, self.packet_reads = [], []
        self.probe.finished.connect(lambda info, error: self.results.append((info, error)))
        self.probe.reading_packets.connect(lambda: self.packet_reads.append(True))

    def tearDown(self):
        self.probe.cancel()
        wait_for(lambda: not self.probe.running)

    def test_mkv_without_bps_measures_video_packets_without_audio(self):
        self.probe.start(self.source, BINARIES)
        wait_for(lambda: bool(self.results))
        info, error = self.results[-1]
        self.assertEqual(error, "")
        self.assertTrue(self.packet_reads)
        packets = subprocess.run([
            str(BINARIES.ffprobe), "-v", "error", "-select_streams", "V:0", "-show_packets",
            "-show_entries", "packet=size", "-of", "json", str(self.source),
        ], check=True, capture_output=True, timeout=10)
        video_bytes = sum(int(packet["size"]) for packet in json.loads(packets.stdout)["packets"])
        self.assertEqual(info.video_bitrate_bps, round(video_bytes * 8 / 1.5))
        self.assertLess(info.video_bitrate_bps, self.source.stat().st_size * 8 / 1.5)

    def test_mp4_uses_stream_bitrate_without_scanning_packets(self):
        self.probe.start(self.mp4, BINARIES)
        wait_for(lambda: bool(self.results))
        info, error = self.results[-1]
        self.assertEqual(error, "")
        self.assertGreater(info.video_bitrate_bps, 0)
        self.assertFalse(self.packet_reads)

    def test_cancel_between_metadata_and_packet_scan_leaves_no_process_running(self):
        self.probe.reading_packets.connect(self.probe.cancel)
        self.probe.start(self.source, BINARIES)
        wait_for(lambda: bool(self.results))
        self.assertEqual(self.results[-1], (None, "Cancelled"))
        self.assertEqual(self.probe.process.state(), QProcess.ProcessState.NotRunning)

    def test_selecting_another_source_discards_the_cancelled_source_result(self):
        self.probe.start(self.source, BINARIES)
        self.probe.start(self.mp4, BINARIES)
        wait_for(lambda: bool(self.results))
        self.assertEqual(self.probe.path, self.mp4)
        self.assertEqual(len(self.results), 1)
        self.assertEqual(self.results[-1][1], "")
        self.assertGreater(self.results[-1][0].video_bitrate_bps, 0)
