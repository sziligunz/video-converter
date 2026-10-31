"""Read video metadata and, when necessary, measure video-only packet bitrate."""

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from .runtime import process_environment


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None
    video_duration: float | None
    video_bitrate_bps: int | None


def positive_number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def tag_duration(value) -> float | None:
    try:
        hours, minutes, seconds = str(value).split(":")
        return positive_number(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    except (ValueError, TypeError):
        return None


def probe_arguments(path: Path) -> list[str]:
    return [
        "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,duration,bit_rate:stream_disposition=attached_pic:stream_tags",
        "-of", "json", str(path),
    ]


def parse_media_info(data: bytes) -> MediaInfo:
    info = json.loads(data)
    videos = [
        stream for stream in info.get("streams", [])
        if stream.get("codec_type") == "video"
        and not stream.get("disposition", {}).get("attached_pic", 0)
    ]
    if not videos:
        raise ValueError("This file does not contain a video stream.")
    video = videos[0]
    tags = {key.upper(): value for key, value in video.get("tags", {}).items()}
    duration = positive_number(info.get("format", {}).get("duration"))
    video_duration = positive_number(video.get("duration")) or tag_duration(tags.get("DURATION"))
    bitrate = positive_number(video.get("bit_rate")) or positive_number(tags.get("BPS")) or positive_number(tags.get("BPS-ENG"))
    if not bitrate and video_duration and (byte_count := positive_number(tags.get("NUMBER_OF_BYTES"))):
        bitrate = byte_count * 8 / video_duration
    return MediaInfo(duration or video_duration, video_duration, max(1, round(bitrate)) if bitrate else None)


def parse_probe(data: bytes) -> float | None:
    return parse_media_info(data).duration


def packet_probe_arguments(path: Path) -> list[str]:
    return [
        "-v", "error", "-select_streams", "V:0", "-show_packets",
        "-show_entries", "packet=pts_time,dts_time,duration_time,size:packet_side_data=",
        "-of", "compact=p=0:nk=0", str(path),
    ]


class PacketBitrate:
    """Bound memory while counting compressed video packet bytes and their span."""

    def __init__(self):
        self.buffer = bytearray()
        self.byte_count = 0
        self.first_time = None
        self.last_time = None
        self.has_packet_duration = False

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        lines = self.buffer.split(b"\n")
        self.buffer = bytearray(lines.pop())
        for line in lines:
            self._packet(line)

    def _packet(self, line):
        line = bytes(line)
        values = dict(field.split(b"=", 1) for field in line.split(b"|") if b"=" in field)
        size = positive_number(values.get(b"size"))
        if not size:
            return
        self.byte_count += int(size)
        timestamp = None
        for key in (b"pts_time", b"dts_time"):
            try:
                candidate = float(values.get(key))
                if math.isfinite(candidate):
                    timestamp = candidate
                    break
            except (ValueError, TypeError):
                continue
        if timestamp is None:
            return
        duration = positive_number(values.get(b"duration_time"))
        self.has_packet_duration |= duration is not None
        end = timestamp + (duration or 0)
        self.first_time = timestamp if self.first_time is None else min(self.first_time, timestamp)
        self.last_time = end if self.last_time is None else max(self.last_time, end)

    def bitrate(self, duration: float | None, fallback_duration: float | None = None) -> int:
        if self.buffer:
            self._packet(bytes(self.buffer))
            self.buffer.clear()
        # Stream duration is more precise than the millisecond packet clock in MKV.
        if not duration and self.has_packet_duration and self.first_time is not None and self.last_time is not None:
            duration = positive_number(self.last_time - self.first_time) or duration
        duration = duration or fallback_duration
        if not self.byte_count or not duration:
            raise ValueError("Could not determine the source video's bitrate; choose Simple or Balanced.")
        return max(1, round(self.byte_count * 8 / duration))


class SourceProbe(QObject):
    finished = pyqtSignal(object, str)
    reading_packets = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(30_000)
        self.timeout.timeout.connect(self._timed_out)
        self.running = False
        self.path: Path | None = None
        self._pending = None
        self._cancelled = False
        self._phase = ""
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._failure = ""

    def start(self, path: Path, binaries, require_bitrate=True):
        if self.running:
            if self.path == path and self.binaries.ffprobe == binaries.ffprobe and not self._cancelled:
                return
            self._pending = (path, binaries, require_bitrate)
            self._cancelled = True
            self._stop()
            return
        self.path, self.binaries, self.require_bitrate = path, binaries, require_bitrate
        self.running, self._cancelled = True, False
        self._launch("metadata", probe_arguments(path))

    def cancel(self):
        self._pending = None
        if self.running:
            self._cancelled = True
            self._stop()

    def _stop(self):
        self.timeout.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        else:
            self._finish(None, "Cancelled")

    def _launch(self, phase, arguments):
        self._phase, self._failure = phase, ""
        self._stdout.clear()
        self._stderr.clear()
        self.process.setProcessEnvironment(process_environment(self.binaries.ffprobe))
        self.process.start(str(self.binaries.ffprobe), arguments)
        self.timeout.start()

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput())
        if self._phase == "packets":
            self._packets.feed(data)
            if data:
                self.timeout.start()
        else:
            self._stdout.extend(data)

    def _read_stderr(self):
        self._stderr.extend(bytes(self.process.readAllStandardError()))
        del self._stderr[:-16_384]

    def _process_error(self, error):
        if self.running and error == QProcess.ProcessError.FailedToStart:
            self._finish(None, f"Could not start ffprobe: {self.process.errorString()}")

    def _timed_out(self):
        self._failure = "Reading the source video timed out after 30 seconds without progress."
        self.process.kill()

    def _process_finished(self, exit_code, exit_status):
        if not self.running:
            return
        self._read_stdout()
        self._read_stderr()
        self.timeout.stop()
        if self._cancelled:
            self._finish(None, "Cancelled")
            return
        if exit_code != 0 or exit_status == QProcess.ExitStatus.CrashExit:
            self._finish(None, self._failure or self._stderr.decode("utf-8", errors="replace").strip() or "Could not inspect the source video.")
            return
        try:
            if self._phase == "metadata":
                self._info = parse_media_info(bytes(self._stdout))
                if self.require_bitrate and self._info.video_bitrate_bps is None:
                    self._packets = PacketBitrate()
                    self.reading_packets.emit()
                    if self.running and not self._cancelled:
                        self._launch("packets", packet_probe_arguments(self.path))
                    return
                info = self._info
            else:
                info = replace(self._info, video_bitrate_bps=self._packets.bitrate(self._info.video_duration, self._info.duration))
        except (ValueError, TypeError, AttributeError) as error:
            self._finish(None, f"Could not read video information: {error}")
            return
        self._finish(info, "")

    def _finish(self, info, error):
        self.timeout.stop()
        self.running, self._phase = False, ""
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self.start(*pending)
        else:
            self.finished.emit(info, error)
