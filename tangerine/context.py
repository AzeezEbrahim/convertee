"""Foreground context gate: keep the wheel out of games and full-screen apps.

The drag gesture (Shift + mouse) is also a core input in many games, and a
full-screen app that covers the desktop has no shell surface to drop files
on anyway. This module answers one question: may the wheel appear for the
current foreground context?

Probe order:

1. No foreground window, or a window that belongs to Tangerine itself ->
   allowed (never interfere with our own UI).
2. Shell surfaces (Explorer, desktop, taskbar, XAML shell hosts) -> always
   allowed. File work must never break, even for maximised Explorer windows
   or with "automatically hide the taskbar" enabled (a maximised window then
   fills the whole work area, but it is still a file surface).
3. Known game engines by window class -> blocked.
4. ``SHQueryUserNotificationState`` in {1, 2, 3, 4} (locked/screensaver,
   busy/presentation, exclusive Direct3D full-screen, presentation mode) ->
   blocked.
5. The foreground window covers its monitor (within a small tolerance) and
   is not merely maximised -> blocked. Measured behaviour on Windows 11:
   borderless full-screen windows report QUNS=5 (ACCEPTS_NOTIFICATIONS), so
   the geometry check is what catches them; a maximised window overshoots
   its monitor edges by ~8 px, stops at the work area, and reports
   SW_SHOWMAXIMIZED, so it is intentionally allowed to avoid false
   positives with "automatically hide the taskbar" setups.

If anything in the probe fails, the gate fails open (wheel allowed): the
cost of a missed suppression is lower than the cost of a wheel that never
shows up.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

from . import settings

log = logging.getLogger("tangerine")

IS_WINDOWS = sys.platform == "win32"
user32 = ctypes.windll.user32 if IS_WINDOWS else None
dwmapi = ctypes.windll.dwmapi if IS_WINDOWS else None

DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONEAREST = 2
SW_SHOWMAXIMIZED = 3

_BLOCKED_QUNS = {1, 2, 3, 4}
_CACHE_TTL_S = 0.25

SHELL_CLASSES = frozenset(
    {
        "CabinetWClass",
        "ExploreWClass",
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "XamlExplorerHostIslandWindow",
    }
)

GAME_CLASSES = frozenset(
    {
        "unitywndclass",
        "unrealwindow",
        "sdl_app",
        "glfw30",
        "lwjgl",
        "valve001",
        "riotwindowclass",
        "yygamemakeryy",
        "prism3d",
        "osu!",
        "warframe",
    }
)

_cache: tuple[float, bool] | None = None


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _foreground_window() -> int:
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    try:
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    except Exception:
        return 0
    return int(pid.value)


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    try:
        length = int(user32.GetClassNameW(int(hwnd), buffer, 256) or 0)
    except Exception:
        return ""
    return buffer.value if length else ""


def _quns_state() -> int | None:
    state = ctypes.c_int()
    try:
        result = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        return None
    return int(state.value) if result == 0 else None


def _is_maximized(hwnd: int) -> bool:
    placement = _WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
    try:
        ok = user32.GetWindowPlacement(int(hwnd), ctypes.byref(placement))
    except Exception:
        return False
    return bool(ok) and placement.showCmd == SW_SHOWMAXIMIZED


def _window_frame(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    try:
        result = dwmapi.DwmGetWindowAttribute(
            int(hwnd),
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:
        result = -1
    if result != 0:
        try:
            if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
                return None
        except Exception:
            return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _monitor_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    try:
        monitor = user32.MonitorFromWindow(int(hwnd), MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
    except Exception:
        return None
    rect = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom)


def _covers_monitor(hwnd: int) -> bool:
    frame = _window_frame(hwnd)
    monitor = _monitor_rect(hwnd)
    if frame is None or monitor is None:
        return False
    left, top, right, bottom = frame
    ml, mt, mr, mb = monitor
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return False
    tolerance = max(2, round(min(width, height) * 0.01))
    return (
        left <= ml + tolerance
        and top <= mt + tolerance
        and right >= mr - tolerance
        and bottom >= mb - tolerance
    )


def _probe() -> bool:
    if not IS_WINDOWS:
        from . import x11

        return x11.session().wheel_allowed()
    hwnd = _foreground_window()
    if not hwnd:
        return True
    if _window_pid(hwnd) == os.getpid():
        return True
    window_class = _window_class(hwnd)
    if window_class in SHELL_CLASSES:
        return True
    if window_class.casefold() in GAME_CLASSES:
        log.debug("Wheel suppressed: game window class %s", window_class)
        return False
    state = _quns_state()
    if state in _BLOCKED_QUNS:
        log.debug("Wheel suppressed: notification state %s", state)
        return False
    if not _is_maximized(hwnd) and _covers_monitor(hwnd):
        log.debug("Wheel suppressed: foreground window covers its monitor")
        return False
    return True


def wheel_allowed() -> bool:
    """Return True when the wheel may appear for the current foreground app."""
    global _cache

    if not bool(settings.get("suppressInFullscreen", True)):
        return True

    now = time.monotonic()
    cached = _cache
    if cached is not None and now - cached[0] < _CACHE_TTL_S:
        return cached[1]

    try:
        allowed = _probe()
    except Exception:
        log.debug("Context probe failed; allowing the wheel", exc_info=True)
        allowed = True
    _cache = (now, allowed)
    return allowed
