"""Resolve bundled resources and persistent per-user application data."""

import os
import sys
from pathlib import Path


APP_NAME = "Swarif"
BUNDLE_DIR = Path(__file__).resolve().parent
IS_PACKAGED = bool(getattr(sys, "frozen", False))


def application_data_dir():
    """Return a writable, persistent directory for this user's Swarif data."""
    if not IS_PACKAGED:
        return BUNDLE_DIR
    if sys.platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


APP_DATA_DIR = application_data_dir()


def writable_path(value):
    """Resolve a configured relative path inside persistent application data."""
    path = Path(value)
    return path if path.is_absolute() else APP_DATA_DIR / path

