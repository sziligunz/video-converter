import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import time

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from video_converter.ffmpeg import FFmpegNotFoundError, find_binaries

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
try:
    BINARIES = find_binaries()
except FFmpegNotFoundError:
    BINARIES = None


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for the Qt conversion queue")
        QTest.qWait(10)


def make_video(path: Path, *, audio=True, odd=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(BINARIES.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
    command.append("testsrc=size=161x91:rate=24" if odd else "testsrc2=size=320x180:rate=24")
    if audio:
        command.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"])
    command.extend(["-t", "1.5", "-c:v", "ffv1"])
    if audio:
        command.extend(["-c:a", "pcm_s16le"])
    subprocess.run([*command, str(path)], check=True, capture_output=True, timeout=20)
    return path
