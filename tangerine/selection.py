"""Read the file selection of the foreground Explorer window.

The macOS app reads the files selected in Finder before the user starts a
drag. On Windows the equivalent source is the ``Shell.Application`` COM
automation object, reached through :mod:`comtypes` (never pywin32).

Everything here is defensive by design:

* the COM work runs on a short-lived worker thread with a hard timeout, so
  ``explorer_selection`` can never block a GUI thread or raise at the caller;
* when the foreground window is not an Explorer window the caller gets ``[]``;
* if ``comtypes`` is missing or COM fails, the caller gets ``[]`` too.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes

log = logging.getLogger("tangerine")

# Linux has no Explorer: foreground_hwnd() fails soft to None, so the
# selection-based triggers stay idle and the file manager scripts take over.
user32 = ctypes.windll.user32 if sys.platform == "win32" else None

EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass"}

DEFAULT_TIMEOUT_S = 1.5
COINIT_APARTMENTTHREADED = 0x2


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------

def foreground_hwnd() -> int | None:
    """HWND of the window in the foreground, or ``None``."""
    try:
        hwnd = int(user32.GetForegroundWindow())
    except Exception:
        return None
    return hwnd or None


def window_class_name(hwnd: int | None) -> str:
    """Window class name for *hwnd* (``""`` when unknown)."""
    if not hwnd:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
        return buffer.value
    except Exception:
        return ""


def is_explorer_window(hwnd: int | None) -> bool:
    """True when *hwnd* belongs to a File Explorer window."""
    return window_class_name(hwnd) in EXPLORER_CLASSES


def foreground_explorer_hwnd() -> int | None:
    """Foreground HWND when it is an Explorer window, else ``None``."""
    hwnd = foreground_hwnd()
    return hwnd if is_explorer_window(hwnd) else None


# ---------------------------------------------------------------------------
# Shell.Application COM access (mock-friendly pieces)
# ---------------------------------------------------------------------------

def window_paths(window) -> list[str]:
    """Selected paths of a Shell window object (may raise on COM failure)."""
    items = window.Document.SelectedItems()
    paths: list[str] = []
    for index in range(int(items.Count)):
        item = items.Item(index)
        path = str(item.Path)
        if path:
            paths.append(path)
    return paths


def select_window(shell, hwnd_foreground: int | None = None):
    """Pick the Shell window matching *hwnd_foreground* or the first Explorer."""
    if hwnd_foreground is None:
        hwnd_foreground = foreground_hwnd()
    windows = shell.Windows()
    fallback = None
    for index in range(int(windows.Count)):
        window = windows.Item(index)
        try:
            window_hwnd = int(window.HWND)
        except Exception:
            window_hwnd = 0
        if hwnd_foreground and window_hwnd == hwnd_foreground:
            return window
        if fallback is None and window_class_name(window_hwnd) in EXPLORER_CLASSES:
            fallback = window
    return fallback


def selection_from_shell(shell, hwnd_foreground: int | None = None) -> list[str]:
    """Selected paths from an already-created ``Shell.Application`` object."""
    if hwnd_foreground is None:
        hwnd_foreground = foreground_hwnd()
    window = select_window(shell, hwnd_foreground)
    if window is None:
        return []
    paths = window_paths(window)
    return [path for path in paths if path and os.path.exists(path)]


def _co_initialize() -> bool:
    """Enter an STA on the calling thread (True when it must be undone)."""
    try:
        result = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except Exception:
        return False
    return result in (0, 1)  # S_OK, S_FALSE


def _create_shell():
    """Create the Shell.Application object (typed first, dynamic fallback).

    The typed route is the most faithful, but it needs comtypes' generated
    typelib wrappers, which frozen builds do not always ship; dynamic
    dispatch covers that case.
    """
    import comtypes.client

    try:
        return comtypes.client.CreateObject("Shell.Application")
    except Exception:
        return comtypes.client.CreateObject("Shell.Application", dynamic=True)


def _read_selection(hwnd_foreground: int | None) -> list[str]:
    initialized = _co_initialize()
    try:
        shell = _create_shell()
        try:
            return selection_from_shell(shell, hwnd_foreground)
        finally:
            del shell
    finally:
        if initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def explorer_selection(
    hwnd_foreground: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[str]:
    """Paths selected in the foreground Explorer window.

    Never raises and never blocks longer than *timeout* seconds: the COM read
    happens on a worker thread and ``[]`` is returned when it does not finish
    in time or fails for any reason.
    """
    result: list[str] = []

    def worker() -> None:
        nonlocal result
        try:
            result = _read_selection(hwnd_foreground)
        except Exception:
            log.debug("Explorer selection read failed", exc_info=True)
            result = []

    try:
        thread = threading.Thread(
            target=worker, name="tangerine-selection", daemon=True)
        thread.start()
        thread.join(max(0.05, float(timeout)))
        if thread.is_alive():
            log.debug("Explorer selection timed out after %.2fs", timeout)
            return []
    except Exception:
        return []
    return list(result)


def explorer_has_selection(
    hwnd_foreground: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> bool:
    """True when the foreground Explorer window has files selected."""
    return bool(explorer_selection(hwnd_foreground, timeout))
