"""Environment shared by conversion and hardware-detection subprocesses."""

from pathlib import Path
import sys

from PyQt6.QtCore import QProcessEnvironment


def process_environment(executable: Path | None = None) -> QProcessEnvironment:
    environment = QProcessEnvironment.systemEnvironment()
    # Bundled tools need bundled libraries; external tools need the original paths.
    bundle = getattr(sys, "_MEIPASS", None)
    bundled = bool(bundle and executable and executable.resolve().is_relative_to(Path(bundle).resolve()))
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux") and not bundled:
        if environment.contains("LD_LIBRARY_PATH_ORIG"):
            environment.insert("LD_LIBRARY_PATH", environment.value("LD_LIBRARY_PATH_ORIG"))
        else:
            environment.remove("LD_LIBRARY_PATH")
    return environment
