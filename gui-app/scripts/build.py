"""Bundle Python, Qt6, ffmpeg and ffprobe on the target operating system."""

import argparse
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_converter.ffmpeg import FFmpegNotFoundError, find_binaries  # noqa: E402
from video_converter.icons import icon_image  # noqa: E402


def build_icon():
    """Create native app icons; QImage drawing works without a display server."""
    directory = ROOT / "build" / "icons"
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        iconset = directory / "VideoConverter.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                if not icon_image(size * scale).save(str(iconset / name)):
                    raise RuntimeError(f"Could not write icon {name}")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(directory / "VideoConverter.icns")], check=True)
    elif not icon_image(256).save(str(directory / "VideoConverter.ico")):
        raise RuntimeError("Could not write the application icon")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", type=Path, help="Folder containing target-platform ffmpeg and ffprobe")
    parser.add_argument("--codesign-identity", help="macOS Developer ID Application signing identity")
    parser.add_argument("--target-arch", choices=["arm64", "x86_64", "universal2"], help="macOS architecture (default: native)")
    parser.add_argument("--no-archive", action="store_true", help="Only build the app folder/bundle")
    parser.add_argument("--dry-run", action="store_true", help="Check FFmpeg and print the build command")
    arguments = parser.parse_args()
    if sys.platform != "darwin" and (arguments.codesign_identity or arguments.target_arch):
        parser.error("Signing and --target-arch are macOS options.")
    try:
        binaries = find_binaries(arguments.ffmpeg_dir)
        for executable in (binaries.ffmpeg, binaries.ffprobe):
            subprocess.run([str(executable), "-version"], check=True, capture_output=True, timeout=15)
        encoders = subprocess.run(
            [str(binaries.ffmpeg), "-hide_banner", "-encoders"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout
        available = {parts[1] for line in encoders.splitlines() if len(parts := line.split()) > 1}
        if missing := {"libx264", "aac", "libvpx-vp9", "libopus"} - available:
            parser.error("FFmpeg is missing required encoders: " + ", ".join(sorted(missing)))
        if sys.platform == "darwin":
            target = arguments.target_arch or platform.machine()
            required = {"arm64", "x86_64"} if target == "universal2" else {target}
            for executable in (binaries.ffmpeg, binaries.ffprobe):
                architectures = subprocess.run(
                    ["lipo", "-archs", str(executable)], check=True, capture_output=True, text=True,
                ).stdout.split()
                if not required.issubset(architectures):
                    parser.error(f"{executable.name} must include {', '.join(sorted(required))}; found {architectures}")
    except (FFmpegNotFoundError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))

    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "Video Converter", "--paths", str(ROOT / "src"),
        "--specpath", str(ROOT / "build"), "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--add-binary", f"{binaries.ffmpeg}{os.pathsep}bin",
        "--add-binary", f"{binaries.ffprobe}{os.pathsep}bin",
        "--add-data", f"{ROOT / 'README.md'}{os.pathsep}.",
    ]
    if sys.platform == "darwin":
        command.extend(["--icon", str(ROOT / "build" / "icons" / "VideoConverter.icns")])
        command.extend(["--osx-bundle-identifier", "org.videoconverter.desktop"])
        if arguments.codesign_identity:
            command.extend(["--codesign-identity", arguments.codesign_identity])
        if arguments.target_arch:
            command.extend(["--target-arch", arguments.target_arch])
    elif sys.platform == "win32":
        command.extend(["--icon", str(ROOT / "build" / "icons" / "VideoConverter.ico")])
    command.append(str(ROOT / "src" / "app.py"))
    print(shlex.join(command), flush=True)
    if arguments.dry_run:
        return 0
    if sys.platform in {"darwin", "win32"}:
        build_icon()
    # Keep build caches inside the project, including in restricted environments.
    environment = os.environ.copy()
    environment.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / "build" / "cache"))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    bundle = ROOT / "dist" / ("Video Converter.app" if sys.platform == "darwin" else "Video Converter")
    print(f"\nBuilt: {bundle}", flush=True)
    if not arguments.no_archive:
        architecture = arguments.target_arch or platform.machine()
        archive = ROOT / "dist" / f"Video-Converter-{platform.system()}-{architecture}"
        if sys.platform == "darwin":
            # ditto preserves the app's symlinks, permissions and bundle metadata.
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(bundle), f"{archive}.zip"], check=True)
            archive = Path(f"{archive}.zip")
        else:
            # Tar retains executable permissions on Linux; ZIP is convenient on Windows.
            archive = Path(shutil.make_archive(
                str(archive), "zip" if sys.platform == "win32" else "gztar",
                root_dir=bundle.parent, base_dir=bundle.name,
            ))
        print(f"Ready to share: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
