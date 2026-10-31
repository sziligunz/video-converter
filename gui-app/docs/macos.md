# Distributing on macOS

Build on a Mac. A Linux or Windows build cannot produce a working macOS `.app`.

## Create the bundle

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python scripts/build.py --ffmpeg-dir /absolute/path/to/macos-ffmpeg/bin
```

The selected folder must contain executable **ffmpeg** and **ffprobe** for the Mac's architecture, with H.264/AAC and VP9/Opus encoders. The builder checks that both tools run and that all four encoders exist before packaging. It bundles both tools as binaries so PyInstaller can collect their library dependencies. Prefer standalone builds; test on a Mac with no Python, Qt, FFmpeg, or Homebrew installed.

Outputs:

- `dist/Video Converter.app`: drag this into Applications.
- `dist/Video-Converter-Darwin-arm64.zip` (or `x86_64`): preserves the bundle's permissions and symlinks using `ditto`.

The app discovers its bundled tools when opened from Finder, without relying on the user's shell PATH. No FFmpeg installation is needed by the recipient.

For **Hardware encoding**, include an FFmpeg build with `h264_videotoolbox`. On launch the app tests a short VideoToolbox encode with `-allow_sw 0`, a native `-q:v` quality mapped from CRF, and the selected bitrate limit. Only a successful combined quality-and-bitrate test enables the toggle for MP4/MKV/MOV. FFmpeg's VideoToolbox `-q:v` mode requires Apple Silicon; Intel Macs can use software encoding when that check fails. VideoToolbox is not used for WebM; that format uses software on macOS. The recipient's macOS installation supplies the VideoToolbox framework; no extra Python package is required. Simple caps video bitrate at 4,000 kb/s, Balanced at 8,000 kb/s, and Original reads each source video's bitrate independently. CRF stays visible in both encoding modes; hardware quality mapping is approximate.

## Apple Silicon and Intel

The default build uses the current Python architecture. Build an arm64 version on Apple Silicon and an x86_64 version using Intel Python and Intel FFmpeg. `--target-arch arm64`, `--target-arch x86_64`, or `--target-arch universal2` can be passed on macOS; every Python extension, Qt library, and FFmpeg binary must support the selected architecture. `universal2` is only possible with a complete universal2 toolchain and dependencies.

## Sign and notarize for distribution

For a Developer ID distribution, build using your own signing identity:

```sh
python scripts/build.py \
  --ffmpeg-dir /absolute/path/to/macos-ffmpeg/bin \
  --codesign-identity "Developer ID Application: Your Name (TEAMID)"
```

PyInstaller signs the bundled executable and collected binaries. Without an identity it uses ad-hoc signing; that is suitable for local testing, but does not establish a trusted Developer ID distribution.

With a previously configured `notarytool` keychain profile:

```sh
xcrun notarytool submit "dist/Video-Converter-Darwin-arm64.zip" \
  --keychain-profile "AC_NOTARY" --wait
xcrun stapler staple "dist/Video Converter.app"

# Re-create the archive after stapling.
ditto -c -k --keepParent "dist/Video Converter.app" \
  "dist/Video-Converter-Darwin-arm64.zip"
```

Use `x86_64` or `universal2` in the archive name when appropriate. Verify the bundle before sharing:

```sh
codesign --verify --deep --strict --verbose=2 "dist/Video Converter.app"
spctl --assess --type execute --verbose=2 "dist/Video Converter.app"
```

Test multiple files, a non-ASCII filename, each output format, errors, cancellation, and output-folder permissions on a clean Mac. Check that Hardware encoding becomes available on a supported Mac, stays off until enabled, uses VideoToolbox, and records any software retry in Details. This repository's automated conversion tests run offscreen, and hardware-positive paths use simulated driver responses; they do not replace real macOS accelerator, signing, Gatekeeper, or clean-machine testing.

References: [PyInstaller macOS signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing), [Apple notarization workflow](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), and [FFmpeg downloads](https://ffmpeg.org/download.html).
