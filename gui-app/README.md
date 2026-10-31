# Video Converter

A small Python desktop app with a dark **Qt6 / PyQt6** interface and **FFmpeg** conversion. The starting app only selected files and printed their names; it now converts batches and reports each result.

- Browse for one or many videos, or drag files into the window. MKV → MP4 is the default.
- Choose an input filter (MKV, MP4/M4V, MOV, AVI, WebM, all video, or any file).
- Convert to MP4, MKV, MOV, or WebM.
- Choose **Simple** (4,000 kb/s), **Balanced** (8,000 kb/s), or **Original** (source video bitrate).
- Enable **Hardware encoding** when a compatible accelerator passes a test encode.
- Save beside the originals or in a shared output folder.
- Follow the current filename, per-file percentage, and batch progress without freezing the interface.
- See original/output filenames and sizes, percentage smaller **or larger**, green success text, and red errors in the results table. Double-click a row for details.
- Stop a batch and retry incomplete/failed files. Successful files stay in the table and are not converted again.

Everything runs locally. The app does not upload files or download tools automatically.

## Run from source

Use Python **3.10 or later**, then install the Qt6 dependency:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/app.py
```

On Windows, activate with `.venv\Scripts\activate` and use `python` instead of `python3`.

Install **ffmpeg and ffprobe** (FFmpeg 6 or newer) for your operating system, or choose the folder containing both with **Locate FFmpeg**. On a Mac with Homebrew:

```sh
brew install ffmpeg
```

Both tools are discovered in a bundled `bin/` directory, `VIDEO_CONVERTER_FFMPEG_DIR`, PATH, and common macOS Homebrew locations. Your chosen tool folder and conversion settings are remembered. If FFmpeg is missing, browsing still works and conversion is disabled until it is available.

You can also install the app and launch it with its command:

```sh
python -m pip install -e .
video-converter
# Or: python -m video_converter
# Optionally pass file paths: python src/app.py "/path/to/video.mkv"
```

## Compression and formats

Presets and formats are defined as Python enums in `src/video_converter/models.py`.

| Preset | H.264 CRF | VP9 CRF | Video bitrate limit | Audio bitrate | Intended use |
| --- | ---: | ---: | ---: | ---: | --- |
| Simple | 28 | 40 | 4,000 kb/s | 96 kb/s | Smaller files, some detail loss |
| Balanced | 23 | 32 | 8,000 kb/s | 128 kb/s | Everyday size/quality balance |
| Original | 18 | 24 | Each source video's bitrate | 192 kb/s | More detail at the source bitrate limit |

The compression hint always displays both video bitrate and CRF, including when hardware encoding is enabled. FFmpeg uses the bitrate as a ceiling with `-maxrate:v` and a two-second `-bufsize:v`; the encoded video's average bitrate can be lower depending on its content and quality target. See [FFmpeg rate-control options](https://www.ffmpeg.org/ffmpeg-codecs.html).

**Original** resolves a separate video bitrate for every input file. It reads the video stream's bitrate or Matroska bitrate tags. When this metadata is missing, it counts compressed video packet bytes and divides by the video duration, excluding audio and container overhead. That scan can read the whole video; the UI remains responsive and conversion can be stopped. The hint previews the selected input's bitrate in the background. If the bitrate cannot be determined, that file reports an error instead of using an invented rate. Original still re-encodes with CRF 18 (VP9 CRF 24); it is not a lossless copy.

All presets retain the source resolution and frame rate. Odd frame dimensions are padded by at most one pixel for encoder compatibility. File size depends on the source, so a smaller output is not guaranteed. Saved Compact/High res preferences migrate to Simple/Original.

MP4/MKV/MOV use H.264 (`libx264`) and AAC. WebM uses VP9 (`libvpx-vp9`) and Opus (`libopus`). The FFmpeg build must include these encoders. The first video stream and all audio tracks are encoded; audio is optional. Subtitles, attachments, additional video streams, and data tracks are omitted. Output is 8-bit YUV 4:2:0; HDR/10-bit mastering and subtitle preservation are outside this simple app's scope.

Outputs use names such as `holiday_simple.mp4` or `holiday_original.mp4`, with `_2`, `_3`, etc. for collisions. Originals and existing output files are preserved. A zero-byte destination is reserved while FFmpeg writes a hidden temporary file in that folder. The app checks the output with ffprobe, then replaces its own reservation atomically. Failed/cancelled jobs clean up their temporary files and reservations; cleanup failures are recorded in Details. A forced process/OS crash can leave these incomplete files behind.

## Hardware encoding

The **Hardware encoding** toggle starts off and becomes available after FFmpeg successfully encodes three generated frames using a compatible accelerator. Detection runs in the background on startup, when changing FFmpeg, or when pressing **Recheck**. Each check has a five-second timeout. An encoder appearing in `ffmpeg -encoders` alone is not sufficient: missing drivers, inaccessible devices, and unusable hardware leave the toggle disabled. Hover over the status for diagnostics.

| Platform | Hardware backends checked | Output formats |
| --- | --- | --- |
| macOS | Apple VideoToolbox (`h264_videotoolbox`) | MP4, MKV, MOV |
| Windows | NVIDIA NVENC, Intel Quick Sync, AMD AMF | MP4, MKV, MOV; WebM if Intel VP9 works |
| Linux | NVIDIA NVENC, Intel Quick Sync, AMD AMF, VA-API render devices | MP4, MKV, MOV; WebM if Intel/VA-API VP9 works |

The app uses the first working backend for each output codec. Integrated media engines, including Apple Silicon and Intel graphics, qualify; a discrete graphics card is not required. Your FFmpeg build must include the corresponding encoder, and the host must provide its hardware and drivers. Switching to an unsupported output format temporarily disables hardware encoding; returning to a supported format restores your saved choice after verification.

Both quality and bitrate apply in hardware mode. CRF is a software-encoder control, so the app maps the selected quality to the hardware encoder's native quality option while preserving the bitrate ceiling:

| Backend | Native quality setting |
| --- | --- |
| Apple VideoToolbox | `-q:v`, a 1–100 quality score derived from CRF |
| NVIDIA NVENC | `-rc vbr -cq` with the selected quality |
| Intel Quick Sync | `-global_quality` in quality-constrained VBR mode |
| AMD AMF | `-rc qvbr -qvbr_quality_level` |
| VA-API | `-rc_mode QVBR -global_quality` |

These quality scales are approximate equivalents, not identical CRF algorithms. H.264 uses the preset's 0–51 quality value; VP9's 0–63 CRF is normalized to 0–51 before hardware mapping. VideoToolbox inverts this scale to a percentage. Quick Sync uses a target of 90% of the displayed bitrate ceiling to select QVBR: making its target equal to the ceiling would select CBR and discard the quality setting. The hint tooltip and each file's Details show the native mapping used. Hardware and software results can differ in size and quality. Decoding and the resolution-padding filter continue to use the CPU.

The test encode checks the combined quality and bitrate options. Devices/drivers that reject them leave hardware encoding unavailable. The VideoToolbox `-q:v` path requires Apple Silicon in FFmpeg; an Intel Mac may therefore use software encoding. Windows Media Foundation is excluded from this combined mode because this app cannot reliably enforce both controls through that backend.

The VideoToolbox check and conversions explicitly use `-allow_sw 0`, and Quick Sync device creation requires a hardware implementation. This prevents their own software implementations from being reported as hardware support. If a hardware conversion fails for a particular file, the app retries that file **once with software**, retaining its bitrate limit and CRF. A successful retry is green and marked **Success (CPU)**; Details records the original hardware error and the encoder used. If software also fails, the file is marked red and the batch continues.

Hardware detection and driver failures are covered by asynchronous subprocess tests with simulated driver responses; real FFmpeg tests cover software fallback and file cleanup. GPU encoding still needs validation on machines providing the respective hardware. References: [FFmpeg hardware device options](https://www.ffmpeg.org/ffmpeg.html#Advanced-Video-options), [VideoToolbox hardware requirement](https://ffmpeg.org/doxygen/8.1/videotoolboxenc_8c_source.html), [FFmpeg encoder documentation](https://www.ffmpeg.org/ffmpeg-codecs.html), and the [macOS guide supplied with this request](https://salivity.github.io/ffmpeg/article/how-to-use-h-264-hardware-acceleration-in-f-fmpeg-on-mac-os).

## Build an app to share

The builder includes Python, Qt, **ffmpeg and ffprobe**. Recipients do not need to install Python or FFmpeg. Build **on each target operating system** using binaries for that OS and architecture:

```sh
python -m pip install -r requirements-build.txt
python scripts/build.py --ffmpeg-dir /path/to/ffmpeg/bin
```

If both tools are already discoverable, `python scripts/build.py` is sufficient. To inspect the command without building, add `--dry-run`.

On **macOS**, the result is `dist/Video Converter.app` and a ZIP ready for distribution. Build on Apple Silicon for arm64 and on Intel for x86_64. Prefer standalone FFmpeg binaries for distribution; if using a dynamically linked build, verify all its libraries are collected by PyInstaller and test on a clean Mac without Homebrew. See [macOS distribution](docs/macos.md) for signing, notarization, and architecture options.

On **Windows**, share the ZIP containing the whole `dist/Video Converter/` folder. On **Linux**, share the generated `.tar.gz` to retain executable permissions; run `dist/Video Converter/Video Converter`. Linux bundles need a compatible distribution/glibc and the usual Qt platform libraries.

Review the redistribution terms and include the notices/source required by your particular [FFmpeg build](https://ffmpeg.org/legal.html), [PyQt6](https://www.riverbankcomputing.com/static/Docs/PyQt6/introduction.html#license), and [Qt](https://doc.qt.io/qt-6/licensing.html). FFmpeg binaries and third-party licenses are not vendored in this repository.

## Verify

Tests use the standard library, Qt's offscreen platform, and real FFmpeg-generated video fixtures:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m unittest discover -s tests -v
```

On Windows PowerShell, set `$env:QT_QPA_PLATFORM="offscreen"` and `$env:PYTHONPATH="src"` before the `python -m unittest` command. Integration tests skip if FFmpeg/ffprobe are unavailable.

The application separates enum/data definitions (`models.py`), source metadata and bitrate inspection (`media.py`), asynchronous `QProcess` conversion (`ffmpeg.py`), hardware detection and backend options (`hardware.py`), subprocess environments (`runtime.py`), presentation (`app.py`), and the dark theme (`theme.py`). No shell commands are used to convert user files; filenames are passed as separate process arguments.

Implementation references: [FFmpeg progress and stream selection](https://www.ffmpeg.org/ffmpeg.html), [FFmpeg codec options](https://www.ffmpeg.org/ffmpeg-codecs.html), [PyInstaller bundling options](https://pyinstaller.org/en/stable/usage.html), and [macOS architecture/signing support](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support).
