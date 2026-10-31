"""FFmpeg discovery, commands and a nonblocking, cancellable conversion queue."""

from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import tempfile

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from .hardware import (
    hardware_device_arguments, hardware_encoding_arguments,
    hardware_quality_description, video_filter_arguments,
)
from .media import SourceProbe, parse_probe, probe_arguments
from .models import (
    CompressionPreset, ConversionJob, ConversionSettings, JobStatus,
    OutputFormat, format_bitrate, unique_output_path,
)
from .runtime import process_environment


class FFmpegNotFoundError(RuntimeError):
    pass


class FFmpegBinaries:
    def __init__(self, ffmpeg: Path, ffprobe: Path):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe


def find_binaries(directory: Path | None = None) -> FFmpegBinaries:
    """Prefer bundled tools; also find Homebrew tools when launched from Finder."""
    suffix = ".exe" if sys.platform == "win32" else ""
    if directory is not None:
        directories = [directory.expanduser().resolve()]
    else:
        directories = []
        if bundle_root := getattr(sys, "_MEIPASS", None):
            directories.append(Path(bundle_root) / "bin")
        directories.extend([
            Path(sys.executable).parent / "bin",
            Path(__file__).resolve().parents[2] / "bin",
        ])
        if configured := os.environ.get("VIDEO_CONVERTER_FFMPEG_DIR"):
            directories.append(Path(configured).expanduser())
        found = shutil.which("ffmpeg")
        if found:
            directories.append(Path(found).parent)
        if sys.platform != "win32":
            directories.extend([Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin")])
    for folder in directories:
        ffmpeg, ffprobe = (folder / f"{name}{suffix}" for name in ("ffmpeg", "ffprobe"))
        if all(path.is_file() and os.access(path, os.X_OK) for path in (ffmpeg, ffprobe)):
            return FFmpegBinaries(ffmpeg.resolve(), ffprobe.resolve())
    raise FFmpegNotFoundError(
        "FFmpeg and ffprobe were not found. Install FFmpeg or choose the folder "
        "containing both executables with ‘Locate FFmpeg’."
    )


def conversion_arguments(source: Path, output: Path, settings: ConversionSettings) -> list[str]:
    preset = settings.preset
    hardware = settings.hardware_encoder
    bitrate = settings.video_bitrate_bps
    if bitrate is None or bitrate <= 0:
        raise ValueError("The Original preset requires the source video's bitrate.")
    if hardware and not hardware.supports(settings.output_format):
        raise ValueError(f"{hardware.codec} cannot encode {settings.output_format.value.upper()} output.")
    arguments = [
        "-hide_banner", "-nostdin", "-y", "-xerror", "-loglevel", "error", "-nostats",
        "-stats_period", "0.25", "-progress", "pipe:1",
        *hardware_device_arguments(hardware), "-i", str(source),
        "-map", "0:V:0", "-map", "0:a?", "-map_metadata", "0", "-sn", "-dn",
        *video_filter_arguments(hardware),
        "-fps_mode", "passthrough",
    ]
    if hardware:
        arguments.extend(hardware_encoding_arguments(hardware, preset, bitrate))
    elif settings.output_format == OutputFormat.WEBM:
        arguments.extend([
            "-c:v", "libvpx-vp9", "-crf", str(preset.webm_crf), "-b:v", str(bitrate),
            "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
        ])
    else:
        arguments.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", str(preset.crf),
        ])
    if not hardware:
        arguments.extend(["-maxrate:v", str(bitrate), "-bufsize:v", str(bitrate * 2)])
    arguments.extend(["-c:a", "libopus" if settings.output_format == OutputFormat.WEBM else "aac", "-b:a", f"{preset.audio_kbps}k"])
    if settings.output_format in {OutputFormat.MP4, OutputFormat.MOV}:
        arguments.extend(["-movflags", "+faststart"])
    arguments.append(str(output))
    return arguments


class ConversionQueue(QObject):
    job_changed = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.source_probe = SourceProbe(self)
        self.source_probe.finished.connect(self._source_ready)
        self.source_probe.reading_packets.connect(self._reading_source_bitrate)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(30_000)
        self.timeout.timeout.connect(self._probe_timed_out)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.setInterval(2_000)
        self.kill_timer.timeout.connect(self._kill_process)
        self.jobs: list[ConversionJob] = []
        self.indices: list[int] = []
        self.active = False
        self.cancel_requested = False
        self.current_index: int | None = None
        self._temporary: Path | None = None
        self._reservation: tuple[int, int] | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stage = ""
        self._pending_error = ""

    def start(self, jobs: list[ConversionJob], settings: ConversionSettings, binaries: FFmpegBinaries):
        if self.active:
            return
        self.jobs, self.settings, self.binaries = jobs, settings, binaries
        self.indices = [index for index, job in enumerate(jobs) if job.retryable]
        if not self.indices:
            return
        self.active, self.cancel_requested = True, False
        for index in self.indices:
            job = self.jobs[index]
            job.status, job.progress = JobStatus.QUEUED, 0
            job.error, job.details, job.output_size = "", "", None
            job.hardware_fallback, job.encoding_method = "", ""
            job.target_video_bps, job.quality_description = None, ""
            self.job_changed.emit(index)
        QTimer.singleShot(0, self._next)

    def cancel(self):
        if not self.active or self.cancel_requested:
            return
        self.cancel_requested = True
        for index in self.indices:
            self.jobs[index].status = JobStatus.CANCELLED
            self.job_changed.emit(index)
        self.indices.clear()
        if self.source_probe.running:
            self.source_probe.cancel()
        elif self.process.state() != QProcess.ProcessState.NotRunning:
            self.timeout.stop()
            self.process.terminate()
            self.kill_timer.start()
        elif self.current_index is None:
            self._finish()
        else:
            self._complete(JobStatus.CANCELLED)

    def _next(self):
        if not self.active:
            return
        if self.cancel_requested or not self.indices:
            self._finish()
            return
        self.current_index = self.indices.pop(0)
        job = self.jobs[self.current_index]
        self._current_settings = self.settings
        self._temporary, self._reservation = None, None
        try:
            job.input_size = job.source.stat().st_size
            if not job.source.is_file():
                raise ValueError("The input is not a regular file.")
            directory = self.settings.output_directory or job.source.parent
            directory.mkdir(parents=True, exist_ok=True)
            # Reserve exclusively, so two app instances cannot claim the same output.
            reserved = [other.output for other in self.jobs if other is not job and other.output]
            while True:
                job.output = unique_output_path(job.source, self.settings, reserved)
                try:
                    with job.output.open("xb") as reservation:
                        stat = os.fstat(reservation.fileno())
                        self._reservation = (stat.st_dev, stat.st_ino)
                    break
                except FileExistsError:
                    continue
            descriptor, name = tempfile.mkstemp(
                prefix=".video-converter-", suffix=f".part.{self.settings.output_format.value}",
                dir=directory,
            )
            os.close(descriptor)
            self._temporary = Path(name)
        except (OSError, ValueError) as error:
            self._complete(JobStatus.FAILED, f"Could not prepare this conversion: {error}")
            return
        job.status = JobStatus.PROBING
        self.job_changed.emit(self.current_index)
        if self.current_index is not None and not self.cancel_requested:
            self.source_probe.timeout.setInterval(self.timeout.interval())
            self.source_probe.start(job.source, self.binaries, self.settings.preset == CompressionPreset.ORIGINAL)

    def _reading_source_bitrate(self):
        if self.current_index is not None:
            self.jobs[self.current_index].status = JobStatus.READING_BITRATE
            self.job_changed.emit(self.current_index)

    def _source_ready(self, info, error):
        if not self.active or self.current_index is None:
            return
        if self.cancel_requested:
            self._complete(JobStatus.CANCELLED)
            return
        job = self.jobs[self.current_index]
        if info is None:
            job.source_metadata_error = error
            self._complete(JobStatus.FAILED, error)
            return
        job.duration, job.source_video_bps = info.duration, info.video_bitrate_bps
        job.source_metadata_error = ""
        self._current_settings = replace(self.settings, source_video_bps=info.video_bitrate_bps)
        job.target_video_bps = self._current_settings.video_bitrate_bps
        self._start_conversion()

    def _launch(self, stage: str, executable: Path, arguments: list[str]):
        self._stage, self._pending_error = stage, ""
        self._stdout.clear()
        self._stderr.clear()
        self.process.setProcessEnvironment(process_environment(executable))
        self.process.start(str(executable), arguments)
        if stage != "convert":
            self.timeout.start()

    def _read_stdout(self):
        self._stdout.extend(bytes(self.process.readAllStandardOutput()))
        if self._stage != "convert" or self.current_index is None:
            return
        job = self.jobs[self.current_index]
        while b"\n" in self._stdout:
            line, _, remainder = self._stdout.partition(b"\n")
            self._stdout = bytearray(remainder)
            key, _, value = line.partition(b"=")
            if key == b"out_time_us" and job.duration:
                try:
                    progress = max(0, min(99, int(float(value) / (job.duration * 10_000))))
                except ValueError:
                    continue
                if progress > job.progress:
                    job.progress = progress
                    self.job_changed.emit(self.current_index)

    def _read_stderr(self):
        self._stderr.extend(bytes(self.process.readAllStandardError()))
        del self._stderr[:-32_768]

    def _process_error(self, error):
        if self.current_index is None or not self.active:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self.timeout.stop()
            self._complete(JobStatus.FAILED, f"Could not start FFmpeg: {self.process.errorString()}")

    def _probe_timed_out(self):
        self._pending_error = "Reading the video timed out after 30 seconds."
        self._kill_process()

    def _kill_process(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _process_finished(self, exit_code, exit_status):
        self.timeout.stop()
        self.kill_timer.stop()
        self._read_stdout()
        self._read_stderr()
        if self.current_index is None or not self.active:
            return
        job = self.jobs[self.current_index]
        if self.cancel_requested:
            self._complete(JobStatus.CANCELLED)
            return
        diagnostics = self._stderr.decode("utf-8", errors="replace").strip()
        if self._pending_error or exit_code != 0 or exit_status == QProcess.ExitStatus.CrashExit:
            error = self._pending_error or diagnostics or f"FFmpeg exited with code {exit_code}."
            if self._stage == "convert" and self._current_settings.hardware_encoder:
                self._retry_with_software(error)
                return
            self._complete(
                JobStatus.FAILED, error,
            )
            return
        if self._stage == "verify":
            try:
                duration = parse_probe(bytes(self._stdout))
            except (ValueError, TypeError, AttributeError) as error:
                self._complete(JobStatus.FAILED, f"Could not read video information: {error}")
                return
            self._publish()
        else:
            try:
                if not self._temporary or self._temporary.stat().st_size == 0:
                    raise ValueError("FFmpeg did not produce a video file.")
            except (OSError, ValueError) as error:
                self._complete(JobStatus.FAILED, str(error))
                return
            job.status = JobStatus.VERIFYING
            self.job_changed.emit(self.current_index)
            if self.current_index is not None and not self.cancel_requested:
                self._launch("verify", self.binaries.ffprobe, probe_arguments(self._temporary))

    def _start_conversion(self):
        job = self.jobs[self.current_index]
        hardware = self._current_settings.hardware_encoder
        codec = "libvpx-vp9" if self.settings.output_format == OutputFormat.WEBM else "libx264"
        job.encoding_method = f"{hardware.backend.value} ({hardware.codec})" if hardware else f"Software ({codec})"
        job.status = JobStatus.CONVERTING
        self.job_changed.emit(self.current_index)
        if self.current_index is None or self.cancel_requested:
            return
        try:
            crf = self.settings.preset.webm_crf if self.settings.output_format == OutputFormat.WEBM else self.settings.preset.crf
            job.quality_description = hardware_quality_description(hardware, self.settings.preset) if hardware else f"CRF {crf}"
            arguments = conversion_arguments(job.source, self._temporary, self._current_settings)
        except ValueError as error:
            if hardware:
                self._retry_with_software(str(error))
            else:
                self._complete(JobStatus.FAILED, str(error))
            return
        self._launch("convert", self.binaries.ffmpeg, arguments)

    def _retry_with_software(self, error):
        job = self.jobs[self.current_index]
        job.hardware_fallback = error
        self._current_settings = replace(self._current_settings, hardware_encoder=None)
        job.progress = 0
        self._start_conversion()

    def _owns_reservation(self) -> bool:
        if self.current_index is None or self._reservation is None:
            return False
        output = self.jobs[self.current_index].output
        try:
            stat = output.lstat()
            return (stat.st_dev, stat.st_ino) == self._reservation and stat.st_size == 0
        except OSError:
            return False

    def _publish(self):
        job = self.jobs[self.current_index]
        try:
            if not self._owns_reservation():
                raise OSError("The destination changed during conversion; it was left untouched.")
            job.output_size = self._temporary.stat().st_size
            os.replace(self._temporary, job.output)
            self._temporary, self._reservation = None, None
        except OSError as error:
            job.output_size = None
            self._complete(JobStatus.FAILED, f"Could not save the output: {error}")
            return
        self._complete(JobStatus.SUCCESS)

    def _complete(self, status: JobStatus, error: str = ""):
        job = self.jobs[self.current_index]
        cleanup_errors = []
        for path in [self._temporary, job.output if self._owns_reservation() else None]:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"Could not remove incomplete file {path}: {cleanup_error}")
        self._temporary, self._reservation = None, None
        job.status, job.error = status, error
        details = [f"Encoding: {job.encoding_method}" if job.encoding_method else "", error]
        if job.target_video_bps:
            details.append(f"Video bitrate limit: {format_bitrate(job.target_video_bps)}")
        if job.quality_description:
            details.append(f"Quality: {job.quality_description}")
        if job.hardware_fallback:
            details.append(f"Hardware encoding failed; retried with software:\n{job.hardware_fallback}")
        job.details = "\n".join([*details, *cleanup_errors]).strip()
        if status == JobStatus.SUCCESS:
            job.progress = 100
        self.job_changed.emit(self.current_index)
        self.current_index = None
        QTimer.singleShot(0, self._next)

    def _finish(self):
        if not self.active:
            return
        self.active, self.current_index = False, None
        self.finished.emit()
