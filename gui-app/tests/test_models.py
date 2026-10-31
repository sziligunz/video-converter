import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_converter.ffmpeg import FFmpegNotFoundError, find_binaries, parse_probe, process_environment
from video_converter.models import (
    CompressionPreset, ConversionJob, ConversionSettings, OutputFormat, unique_output_path,
)


class ModelTests(unittest.TestCase):
    def test_names_preserve_existing_files_and_reserve_batch_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "holiday.mp4"
            source.write_bytes(b"original")
            existing = root / "holiday_simple.mp4"
            existing.write_bytes(b"previous conversion")
            settings = ConversionSettings(OutputFormat.MP4, CompressionPreset.SIMPLE)
            first = unique_output_path(source, settings)
            second = unique_output_path(source, settings, [first])
            self.assertEqual(first.name, "holiday_simple_2.mp4")
            self.assertEqual(second.name, "holiday_simple_3.mp4")
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(existing.read_bytes(), b"previous conversion")

    def test_shared_folder_avoids_case_insensitive_batch_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = ConversionSettings(output_directory=Path(directory))
            first = unique_output_path(Path("/one/Clip.mkv"), settings)
            second = unique_output_path(Path("/two/clip.mkv"), settings, [first])
            self.assertEqual(second.name, "clip_balanced_2.mp4")

    def test_size_comparison_reports_growth_and_shrinkage(self):
        job = ConversionJob(Path("movie.mkv"), input_size=1000, output_size=250)
        self.assertEqual(job.size_change, "75.0% smaller")
        job.output_size = 1250
        self.assertEqual(job.size_change, "25.0% larger")
        job.output_size = None
        self.assertEqual(job.size_change, "—")

    def test_probe_requires_video_and_accepts_unknown_duration(self):
        with self.assertRaises(ValueError):
            parse_probe(b'{"streams":[{"codec_type":"audio"}]}')
        with self.assertRaises(ValueError):
            parse_probe(b'{"streams":[{"codec_type":"video","disposition":{"attached_pic":1}}]}')
        self.assertIsNone(parse_probe(b'{"streams":[{"codec_type":"video"}],"format":{"duration":"N/A"}}'))
        self.assertEqual(parse_probe(b'{"streams":[{"codec_type":"video","duration":"2.5"}]}'), 2.5)
        self.assertIsNone(parse_probe(b'{"streams":[{"codec_type":"video"}],"format":{"duration":"inf"}}'))

    def test_invalid_explicit_ffmpeg_directory_does_not_silently_use_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FFmpegNotFoundError):
                find_binaries(Path(directory))

    def test_frozen_bundled_tools_keep_libraries_and_external_tools_use_original_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(bundle), create=True),
                patch.object(sys, "platform", "linux"),
                patch.dict(os.environ, {"LD_LIBRARY_PATH": str(bundle), "LD_LIBRARY_PATH_ORIG": "/original-libraries"}),
            ):
                bundled = process_environment(bundle / "bin" / "ffmpeg")
                external = process_environment(Path(directory) / "external" / "ffmpeg")
                self.assertEqual(bundled.value("LD_LIBRARY_PATH"), str(bundle))
                self.assertEqual(external.value("LD_LIBRARY_PATH"), "/original-libraries")
