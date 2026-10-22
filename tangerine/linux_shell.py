"""Per-user desktop integration on Linux (the Explorer integration's twin).

Windows gets "Send to" shortcuts and a context-menu handler; on GNOME the
same entry points are Nautilus scripts (right-click -> Scripts -> Tangerine),
an application launcher and an optional autostart entry. Everything lives in
the user's home directory, needs no root, and every file written here starts
with a marker line so :func:`unregister_shell` only removes what it owns.
"""
from __future__ import annotations

import logging
import os
import shlex
import stat
import sys
from pathlib import Path

from . import paths

log = logging.getLogger("tangerine")

MARKER = "# Managed by Tangerine for Linux"
DESKTOP_ID = "tangerine-linux.desktop"
SCRIPT_DIR_NAME = "Tangerine"

# Script file name -> arguments for the running Tangerine instance.
SCRIPTS = {
    "Convert…": ["--wheel", "conversion"],
    "Tools…": ["--wheel", "tools"],
    "Create PDF from images…": ["--images-to-pdf"],
    "PDF from images (by name)": ["--images-to-pdf", "--pdf-order", "name", "--pdf-quick"],
}


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def nautilus_scripts_dir() -> Path:
    return _data_home() / "nautilus" / "scripts" / SCRIPT_DIR_NAME


def desktop_file() -> Path:
    return _data_home() / "applications" / DESKTOP_ID


def autostart_file() -> Path:
    return _config_home() / "autostart" / DESKTOP_ID


def launcher_command(executable: str | None = None) -> list[str]:
    """Command that starts (or reaches) Tangerine from a script or launcher."""
    if executable:
        return [executable]
    launcher = os.environ.get("TANGERINE_LAUNCHER")
    if launcher:
        return [launcher]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(paths.app_root() / "main.py")]


def _owned(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
    except OSError:
        return False


def _write(path: Path, text: str, executable: bool = False) -> None:
    if path.exists() and not _owned(path):
        log.info("Leaving %s alone: it was not created by Tangerine", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    if executable:
        temp.chmod(temp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(temp, path)


def _script_text(command: list[str], extra: list[str]) -> str:
    quoted = " ".join(shlex.quote(part) for part in command + extra)
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "# Nautilus passes the selected local files as arguments.\n"
        f'exec {quoted} "$@"\n'
    )


def _desktop_text(command: list[str], autostart: bool) -> str:
    exec_line = " ".join(shlex.quote(part) for part in command)
    lines = [
        "[Desktop Entry]",
        MARKER,
        "Type=Application",
        "Name=Tangerine",
        "Comment=Convert files while you drag them (Shift) and edit them (Alt+Shift)",
        f"Exec={exec_line}",
        f"Icon={paths.assets_dir() / 'icon.png'}",
        "Terminal=false",
        "Categories=Utility;GTK;Qt;",
        "StartupNotify=false",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-Delay=3"]
    return "\n".join(lines) + "\n"


def register_shell(executable=None, autostart: bool | None = None):
    """Install the Nautilus scripts and the launcher; idempotent."""
    command = launcher_command(executable)
    folder = nautilus_scripts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, extra in SCRIPTS.items():
        _write(folder / name, _script_text(command, extra), executable=True)
    _write(desktop_file(), _desktop_text(command, autostart=False))
    if autostart is True:
        _write(autostart_file(), _desktop_text(command, autostart=True))
    elif autostart is False:
        remove_autostart()
    log.info("Linux desktop integration ready: %s", folder)
    return folder


def autostart_enabled() -> bool:
    return autostart_file().is_file()


def remove_autostart() -> None:
    path = autostart_file()
    if path.exists() and _owned(path):
        path.unlink()


def unregister_shell():
    folder = nautilus_scripts_dir()
    for name in SCRIPTS:
        path = folder / name
        if path.exists() and _owned(path):
            path.unlink()
    try:
        folder.rmdir()
    except OSError:
        pass
    for path in (desktop_file(), autostart_file()):
        if path.exists() and _owned(path):
            path.unlink()
