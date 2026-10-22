"""Filesystem locations used across Tangerine (Windows and Linux)."""

import os
import sys
from pathlib import Path

APP_NAME = "Convertee"
APP_VERSION = "1.10.2"
APP_BUILD = 15


def app_root() -> Path:
    """Directory containing the application (frozen exe or source tree)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_root())) / "assets"


def tools_dir() -> Path:
    return app_root() / "tools"


def data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "convertee"
        _adopt_legacy_settings(Path(base) / "tangerine", path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _adopt_legacy_settings(legacy: Path, path: Path) -> None:
    """Carry settings over from the pre-rename folder (~/.config/tangerine)."""
    old, new = legacy / "settings.json", path / "settings.json"
    if new.exists() or not old.is_file():
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
        new.write_bytes(old.read_bytes())
    except OSError:
        pass


def settings_file() -> Path:
    return data_dir() / "settings.json"


def log_file() -> Path:
    return data_dir() / "convertee.log"


def icon_path() -> Path:
    # Linux trays (StatusNotifierItem) take the PNG; Windows keeps the ICO.
    return assets_dir() / ("tray.ico" if os.name == "nt" else "tray.png")


def icon_png() -> Path:
    return assets_dir() / "tray.png"


def highlight_sound() -> Path:
    return assets_dir() / "highlight.wav"
