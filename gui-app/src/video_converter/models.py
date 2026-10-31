"""Conversion choices and results, independent of the interface."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class CompressionPreset(Enum):
    SIMPLE = ("simple", "Simple", 28, 40, 4000, 96, "Smaller files · some detail loss")
    BALANCED = ("balanced", "Balanced", 23, 32, 8000, 128, "A balance of size and quality")
    ORIGINAL = ("original", "Original", 18, 24, None, 192, "Use each source video's bitrate")

    def __init__(self, key, label, crf, webm_crf, video_kbps, audio_kbps, description):
        self.key = key
        self.label = label
        self.crf = crf
        self.webm_crf = webm_crf
        self.video_kbps = video_kbps
        self.audio_kbps = audio_kbps
        self.description = description


class OutputFormat(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"
    MOV = "mov"
    WEBM = "webm"

    @property
    def label(self) -> str:
        return f"{self.value.upper()} (.{self.value})"


class HardwareBackend(str, Enum):
    VIDEOTOOLBOX = "Apple VideoToolbox"
    NVENC = "NVIDIA NVENC"
    QSV = "Intel Quick Sync"
    AMF = "AMD AMF"
    VAAPI = "VA-API"
    MEDIA_FOUNDATION = "Windows Media Foundation"


@dataclass(frozen=True)
class HardwareEncoder:
    backend: HardwareBackend
    codec: str
    device: str | None = None

    def supports(self, output_format: OutputFormat) -> bool:
        if output_format == OutputFormat.WEBM:
            return self.codec.startswith("vp9_")
        return self.codec.startswith("h264_")


@dataclass(frozen=True)
class HardwareCapabilities:
    encoders: tuple[HardwareEncoder, ...] = ()
    details: str = ""

    def for_format(self, output_format: OutputFormat) -> HardwareEncoder | None:
        return next((encoder for encoder in self.encoders if encoder.supports(output_format)), None)


class InputFormat(Enum):
    MKV = "Matroska video (*.mkv *.MKV)"
    ALL_VIDEO = "Video files (*.mkv *.mp4 *.mov *.avi *.webm *.m4v *.wmv *.flv *.mpg *.mpeg *.ts *.mts *.m2ts *.3gp *.ogv *.vob *.MKV *.MP4 *.MOV *.AVI *.WEBM *.M4V *.WMV *.FLV *.MPG *.MPEG *.TS *.MTS *.M2TS *.3GP *.OGV *.VOB)"
    MP4 = "MP4 video (*.mp4 *.m4v *.MP4 *.M4V)"
    MOV = "QuickTime video (*.mov *.MOV)"
    AVI = "AVI video (*.avi *.AVI)"
    WEBM = "WebM video (*.webm *.WEBM)"
    OTHER = "All files (*)"

    @property
    def label(self) -> str:
        return {
            self.MKV: "MKV files", self.ALL_VIDEO: "All video formats",
            self.MP4: "MP4 / M4V files", self.MOV: "MOV files", self.AVI: "AVI files",
            self.WEBM: "WebM files", self.OTHER: "Any file type",
        }[self]


class JobStatus(str, Enum):
    QUEUED = "Queued"
    PROBING = "Reading video"
    READING_BITRATE = "Reading source bitrate"
    CONVERTING = "Converting"
    VERIFYING = "Checking output"
    SUCCESS = "Success"
    FAILED = "Error"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class ConversionSettings:
    output_format: OutputFormat = OutputFormat.MP4
    preset: CompressionPreset = CompressionPreset.BALANCED
    output_directory: Path | None = None
    hardware_encoder: HardwareEncoder | None = None
    source_video_bps: int | None = None

    @property
    def video_bitrate_bps(self) -> int | None:
        if self.preset.video_kbps is not None:
            return self.preset.video_kbps * 1000
        return self.source_video_bps


@dataclass
class ConversionJob:
    source: Path
    output: Path | None = None
    input_size: int | None = None
    output_size: int | None = None
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    duration: float | None = None
    error: str = ""
    details: str = ""
    encoding_method: str = ""
    hardware_fallback: str = ""
    source_video_bps: int | None = None
    source_metadata_error: str = ""
    target_video_bps: int | None = None
    quality_description: str = ""

    @property
    def retryable(self) -> bool:
        return self.status in {JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED}

    @property
    def saved_bytes(self) -> int | None:
        if self.input_size is None or self.output_size is None:
            return None
        return self.input_size - self.output_size

    @property
    def size_change(self) -> str:
        saved = self.saved_bytes
        if saved is None or not self.input_size:
            return "—"
        percent = abs(saved) / self.input_size * 100
        if saved == 0:
            return "Same size"
        return f"{percent:.1f}% {'smaller' if saved > 0 else 'larger'}"


def format_bitrate(bitrate_bps: int) -> str:
    kbps = bitrate_bps / 1000
    return f"{kbps:,.0f} kb/s" if kbps.is_integer() else f"{kbps:,.1f} kb/s"


def format_size(size: int | None) -> str:
    if size is None:
        return "—"
    amount = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "—"


def unique_output_path(source: Path, settings: ConversionSettings, reserved=()) -> Path:
    """Plan a unique destination, including when input and output formats match."""
    directory = settings.output_directory or source.parent
    stem = f"{source.stem}_{settings.preset.key}"
    candidate = directory / f"{stem}.{settings.output_format.value}"
    occupied = {str(path.resolve()).casefold() for path in reserved}
    occupied.add(str(source.resolve()).casefold())
    number = 2
    while candidate.exists() or str(candidate.resolve()).casefold() in occupied:
        candidate = directory / f"{stem}_{number}.{settings.output_format.value}"
        number += 1
    return candidate
