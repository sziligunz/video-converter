"""Discover working hardware encoders with short, asynchronous FFmpeg encodes."""

from pathlib import Path
import sys

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from .models import CompressionPreset, HardwareBackend, HardwareCapabilities, HardwareEncoder
from .runtime import process_environment


def hardware_candidates() -> list[HardwareEncoder]:
    if sys.platform == "darwin":
        return [HardwareEncoder(HardwareBackend.VIDEOTOOLBOX, "h264_videotoolbox")]
    if sys.platform not in {"win32", "linux"}:
        return []
    candidates = [
        HardwareEncoder(HardwareBackend.NVENC, "h264_nvenc"),
        HardwareEncoder(HardwareBackend.QSV, "h264_qsv"),
        HardwareEncoder(HardwareBackend.AMF, "h264_amf"),
    ]
    if sys.platform == "win32":
        candidates.append(HardwareEncoder(HardwareBackend.MEDIA_FOUNDATION, "h264_mf"))
        devices = []
    else:
        devices = sorted(Path("/dev/dri").glob("renderD*"))
        candidates.extend(HardwareEncoder(HardwareBackend.VAAPI, "h264_vaapi", str(path)) for path in devices)
    candidates.append(HardwareEncoder(HardwareBackend.QSV, "vp9_qsv"))
    candidates.extend(HardwareEncoder(HardwareBackend.VAAPI, "vp9_vaapi", str(path)) for path in devices)
    return candidates


def hardware_device_arguments(encoder: HardwareEncoder | None) -> list[str]:
    if encoder is None:
        return []
    if encoder.backend == HardwareBackend.VAAPI:
        if not encoder.device:
            raise ValueError("VA-API requires a render device.")
        return ["-vaapi_device", encoder.device]
    if encoder.backend == HardwareBackend.QSV:
        # Explicitly require a hardware implementation, never a QSV software fallback.
        return ["-init_hw_device", "qsv:hw"]
    return []


def video_filter_arguments(encoder: HardwareEncoder | None = None) -> list[str]:
    video_filter = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    if encoder and encoder.backend == HardwareBackend.VAAPI:
        return ["-vf", f"{video_filter},format=nv12,hwupload"]
    return ["-vf", video_filter, "-pix_fmt", "nv12" if encoder else "yuv420p"]


def hardware_quality_arguments(encoder: HardwareEncoder, preset: CompressionPreset) -> list[str]:
    # CRF is not a hardware API. Translate its relative quality to native controls.
    quality = preset.crf
    if encoder.codec.startswith("vp9_"):
        quality = max(1, min(51, round(preset.webm_crf * 51 / 63)))
    if encoder.backend == HardwareBackend.VIDEOTOOLBOX:
        return ["-q:v", str(max(1, round((51 - quality) * 100 / 51)))]
    if encoder.backend == HardwareBackend.NVENC:
        return ["-rc", "vbr", "-cq", str(quality)]
    if encoder.backend == HardwareBackend.QSV:
        return ["-global_quality", str(quality)]
    if encoder.backend == HardwareBackend.AMF:
        return ["-rc", "qvbr", "-qvbr_quality_level", str(quality)]
    if encoder.backend == HardwareBackend.VAAPI:
        return ["-rc_mode", "QVBR", "-global_quality", str(quality)]
    raise ValueError(f"{encoder.backend.value} is not enabled for combined quality and bitrate control; use software encoding.")


def hardware_quality_description(encoder: HardwareEncoder, preset: CompressionPreset) -> str:
    crf = preset.webm_crf if encoder.codec.startswith("vp9_") else preset.crf
    arguments = " ".join(hardware_quality_arguments(encoder, preset))
    return f"CRF {crf} mapped to {encoder.backend.value} quality ({arguments}); quality scales are approximate."


def hardware_encoding_arguments(encoder: HardwareEncoder, preset: CompressionPreset, bitrate_bps: int | None = None) -> list[str]:
    bitrate_bps = bitrate_bps if bitrate_bps is not None else (preset.video_kbps or 0) * 1000
    if bitrate_bps <= 0:
        raise ValueError("The Original preset requires the source video's bitrate.")
    arguments = ["-c:v", encoder.codec]
    if encoder.backend == HardwareBackend.VIDEOTOOLBOX:
        arguments.extend(["-allow_sw", "0"])
    arguments.extend(hardware_quality_arguments(encoder, preset))
    # Equal QSV target/max bitrates select CBR and ignore quality; request QVBR.
    target_bps = max(1, bitrate_bps * 9 // 10) if encoder.backend == HardwareBackend.QSV else bitrate_bps
    arguments.extend([
        "-b:v", str(target_bps), "-maxrate:v", str(bitrate_bps),
        "-bufsize:v", str(bitrate_bps * 2),
    ])
    return arguments


def hardware_probe_arguments(encoder: HardwareEncoder) -> list[str]:
    return [
        "-hide_banner", "-nostdin", "-loglevel", "error",
        *hardware_device_arguments(encoder),
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25", "-frames:v", "3", "-an",
        *video_filter_arguments(encoder),
        *hardware_encoding_arguments(encoder, CompressionPreset.BALANCED),
        "-f", "null", "-",
    ]


class HardwareDetector(QObject):
    finished = pyqtSignal(object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(5_000)
        self.timeout.timeout.connect(self._timed_out)
        self.running = False
        self._pending_binaries = None
        self._cancelled = False
        self._phase = ""
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._working = []
        self._diagnostics = []
        self._candidates = []
        self._current = None
        self._failure = ""

    def start(self, binaries):
        self._pending_binaries = binaries
        if self.running:
            self._cancelled = True
            self._stop_process()
        else:
            self._begin()

    def cancel(self):
        self._pending_binaries = None
        if self.running:
            self._cancelled = True
            self._stop_process()

    def _stop_process(self):
        self.timeout.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        else:
            self._finish()

    def _begin(self):
        self.binaries, self._pending_binaries = self._pending_binaries, None
        if self.binaries is None:
            return
        self.running, self._cancelled = True, False
        self._working, self._diagnostics, self._candidates = [], [], []
        self.status_changed.emit("Checking hardware encoders…")
        if self.running and not self._cancelled:
            self._launch("list", ["-hide_banner", "-encoders"])

    def _launch(self, phase, arguments):
        self._phase, self._failure = phase, ""
        self._stdout.clear()
        self._stderr.clear()
        self.process.setProcessEnvironment(process_environment(self.binaries.ffmpeg))
        self.process.start(str(self.binaries.ffmpeg), arguments)
        self.timeout.start()

    def _read_stdout(self):
        self._stdout.extend(bytes(self.process.readAllStandardOutput()))
        del self._stdout[:-262_144]

    def _read_stderr(self):
        self._stderr.extend(bytes(self.process.readAllStandardError()))
        del self._stderr[:-8_192]

    def _process_error(self, error):
        if self.running and self._phase and error == QProcess.ProcessError.FailedToStart:
            self._failure = f"Could not start FFmpeg: {self.process.errorString()}"
            self._process_finished(-1, QProcess.ExitStatus.CrashExit)

    def _timed_out(self):
        self._failure = "The hardware encoder check timed out."
        self.process.kill()

    def _process_finished(self, exit_code, exit_status):
        if not self.running or not self._phase:
            return
        self.timeout.stop()
        self._read_stdout()
        self._read_stderr()
        if self._cancelled:
            self._finish()
            return
        success = exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        details = self._failure or self._stderr.decode("utf-8", errors="replace").strip()
        phase, self._phase = self._phase, ""
        if phase == "list":
            if not success:
                self._diagnostics.append(details or "Could not list FFmpeg encoders.")
                self._finish()
                return
            available = {
                parts[1] for line in self._stdout.decode("utf-8", errors="replace").splitlines()
                if len(parts := line.split()) > 1 and parts[0].startswith("V")
            }
            self._candidates = [item for item in hardware_candidates() if item.codec in available]
            if not self._candidates:
                self._diagnostics.append("FFmpeg does not include a supported hardware encoder for this system.")
        elif success:
            self._working.append(self._current)
        else:
            name = f"{self._current.backend.value} ({self._current.codec})"
            if self._current.device:
                name += f" on {self._current.device}"
            self._diagnostics.append(f"{name}: {details or 'Hardware test encode failed.'}")
        QTimer.singleShot(0, self._next)

    def _next(self):
        if not self.running:
            return
        if self._cancelled:
            self._finish()
            return
        while self._candidates:
            candidate = self._candidates.pop(0)
            if any(item.codec.split("_", 1)[0] == candidate.codec.split("_", 1)[0] for item in self._working):
                continue
            self._current = candidate
            try:
                arguments = hardware_probe_arguments(candidate)
            except ValueError as error:
                self._diagnostics.append(str(error))
                continue
            self.status_changed.emit(f"Checking {candidate.backend.value}…")
            if self.running and not self._cancelled:
                self._launch("test", arguments)
            return
        self._finish()

    def _finish(self):
        self.timeout.stop()
        self.running, self._phase = False, ""
        if self._pending_binaries is not None:
            self._begin()
            return
        result = HardwareCapabilities(
            tuple(self._working) if not self._cancelled else (),
            "\n\n".join(self._diagnostics),
        )
        self.finished.emit(result)
