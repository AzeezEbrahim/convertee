"""X11 probes for the drag monitor on Linux.

The Windows build samples public Win32 state; this module answers the same
questions on an X11 session (Ubuntu on Xorg, or XWayland) without input
hooks or grabs:

* pointer button and modifier keys come from ``XQueryPointer`` on the root
  window, which reports the server-side state whichever client is focused;
* an XFixes watch on ``XdndSelection`` tells a real drag-and-drop session
  apart from any other button-down movement. Every XDND source (Nautilus,
  GTK, Qt, Firefox, Chromium/Electron) takes ownership of that selection when
  a drag starts, the way Explorer publishes ``InShellDragLoop``;
* the dragged URIs can be read early by converting ``XdndSelection`` to
  ``text/uri-list``, like the OLE ``CF_HDROP`` read on Windows;
* the full-screen gate reads ``_NET_ACTIVE_WINDOW`` / ``_NET_WM_STATE``.

Everything fails open: without a display the monitor simply never fires and
the wheel stays reachable through the tray and the file manager scripts.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import unquote, urlparse

log = logging.getLogger("tangerine")

try:
    from Xlib import X, Xatom, XK
    from Xlib import display as _xdisplay
    from Xlib.ext import xfixes as _xfixes
except Exception:  # pragma: no cover - python-xlib missing
    X = Xatom = XK = _xdisplay = _xfixes = None

# Window classes of real file surfaces: never treated as games/full screen.
SHELL_CLASSES = frozenset(
    {
        "org.gnome.nautilus",
        "nautilus",
        "nemo",
        "caja",
        "thunar",
        "pcmanfm",
        "pcmanfm-qt",
        "dolphin",
        "ding",  # GNOME desktop icons (Ubuntu)
        "desktop_window",
        "xfdesktop",
    }
)

GAME_CLASSES = frozenset(
    {
        "steam_app",
        "unitywndclass",
        "unrealwindow",
        "sdl_app",
        "glfw30",
        "lwjgl",
        "valve001",
        "osu!",
        "minecraft",
    }
)

_URI_TIMEOUT_S = 0.12
_MASK_TTL_S = 0.004


def _uri_to_path(uri: str) -> str | None:
    uri = uri.strip()
    if not uri or uri.startswith("#"):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    if parsed.netloc not in ("", "localhost"):
        return None
    path = unquote(parsed.path)
    return path or None


def parse_uri_list(data: bytes | str) -> list[str]:
    """Local file paths from a ``text/uri-list`` payload (RFC 2483)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    files = []
    for line in data.replace("\r\n", "\n").split("\n"):
        path = _uri_to_path(line)
        if path:
            files.append(path)
    return files


class X11Session:
    """One X connection owned by the GUI thread (python-xlib is not thread-safe)."""

    def __init__(self, display_name: str | None = None):
        self.available = False
        self._display = None
        self._root = None
        self._xfixes = False
        self._xfixes_event = -1
        self._dnd_owner = 0
        self._dnd_since = 0.0
        self._masks = {"shift": 0, "ctrl": 0, "alt": 0, "win": 0}
        self._button1 = 0
        self._uri_cache: tuple[int, list[str] | None] | None = None
        self._mask = 0
        self._mask_stamp = 0.0
        self._display_name = display_name or os.environ.get("DISPLAY")
        if _xdisplay is None or not self._display_name:
            return
        if display_name is None and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            # Headless runs must never read or react to the real desktop.
            return
        try:
            self._display = _xdisplay.Display(self._display_name)
            self._root = self._display.screen().root
            self._atoms = {
                name: self._display.intern_atom(name)
                for name in (
                    "XdndSelection",
                    "_NET_ACTIVE_WINDOW",
                    "_NET_WM_STATE",
                    "_NET_WM_STATE_FULLSCREEN",
                    "_NET_WM_PID",
                    "_NET_WM_WINDOW_TYPE",
                    "_NET_WM_WINDOW_TYPE_DND",
                    "WM_CLASS",
                )
            }
            self._button1 = X.Button1Mask
            self._masks = self._modifier_masks()
            self.available = True
        except Exception:
            log.warning("X11 display %s unavailable; drag gestures are off", self._display_name, exc_info=True)
            self._display = None
            return
        try:
            if self._display.has_extension("XFIXES"):
                self._display.xfixes_query_version()
                self._display.xfixes_select_selection_input(
                    self._root,
                    self._atoms["XdndSelection"],
                    _xfixes.XFixesSetSelectionOwnerNotifyMask,
                )
                self._display.flush()
                self._xfixes_event = (
                    self._display.query_extension("XFIXES").first_event + _xfixes.XFixesSelectionNotify
                )
                self._xfixes = True
        except Exception:
            log.warning("XFixes selection watch unavailable", exc_info=True)

    # -- modifiers -------------------------------------------------------

    def _modifier_masks(self) -> dict[str, int]:
        """Map Alt/Super to whichever ModN bits this keyboard layout uses."""
        masks = {"shift": X.ShiftMask, "ctrl": X.ControlMask, "alt": X.Mod1Mask, "win": X.Mod4Mask}
        try:
            mapping = self._display.get_modifier_mapping()
            wanted = {
                "alt": ("Alt_L", "Alt_R", "Meta_L", "Meta_R"),
                "win": ("Super_L", "Super_R", "Hyper_L", "Hyper_R"),
            }
            bits = [X.ShiftMask, X.LockMask, X.ControlMask, X.Mod1Mask, X.Mod2Mask, X.Mod3Mask, X.Mod4Mask, X.Mod5Mask]
            for name, keysym_names in wanted.items():
                codes = set()
                for keysym_name in keysym_names:
                    keysym = XK.string_to_keysym(keysym_name)
                    code = self._display.keysym_to_keycode(keysym) if keysym else 0
                    if code:
                        codes.add(code)
                for index, keycodes in enumerate(mapping):
                    if index >= 3 and codes.intersection(int(code) for code in keycodes if code):
                        masks[name] = bits[index]
                        break
        except Exception:
            log.debug("Modifier mapping unavailable; using Mod1=Alt, Mod4=Super", exc_info=True)
        return masks

    def _query_mask(self) -> int:
        # The monitor asks for the button and each modifier in one tick:
        # answer them all from a single round trip.
        now = time.monotonic()
        if now - self._mask_stamp < _MASK_TTL_S:
            return self._mask
        try:
            self._mask = int(self._root.query_pointer().mask)
        except Exception:
            self._mask = 0
        self._mask_stamp = now
        return self._mask

    def button_down(self) -> bool:
        if not self.available:
            return False
        return bool(self._query_mask() & self._button1)

    def held_modifiers(self) -> set[str]:
        if not self.available:
            return set()
        mask = self._query_mask()
        return {name for name, bit in self._masks.items() if bit and mask & bit}

    def is_key_down(self, keysym_name: str) -> bool:
        if not self.available:
            return False
        try:
            keysym = XK.string_to_keysym(keysym_name)
            code = self._display.keysym_to_keycode(keysym)
            if not code:
                return False
            keymap = self._display.query_keymap()
            return bool(keymap[code // 8] & (1 << (code % 8)))
        except Exception:
            return False

    # -- drag-and-drop session -------------------------------------------

    def _pump(self) -> None:
        if not self._xfixes:
            return
        try:
            while self._display.pending_events():
                event = self._display.next_event()
                # python-xlib imports extension modules on its own, so the
                # event class is not ours: match the wire codes instead.
                if (
                    event.type == self._xfixes_event
                    and getattr(event, "sub_code", None) == _xfixes.XFixesSetSelectionOwnerNotify
                ):
                    owner = int(getattr(event.owner, "id", event.owner) or 0)
                    self._dnd_owner = owner
                    self._dnd_since = time.monotonic() if owner else 0.0
                    self._uri_cache = None
        except Exception:
            log.debug("XFixes event read failed", exc_info=True)

    def drag_started_since(self, since: float) -> bool:
        """True when an XDND drag began after the button press at *since*.

        Without XFixes there is no way to tell, so the answer fails open and
        the wheel's own dragEnter decides whether files are involved.
        """
        if not self.available:
            return False
        if not self._xfixes:
            return True
        self._pump()
        # *since* is the last moment the button was seen up: an owner set
        # before it belongs to an earlier drag.
        return bool(self._dnd_owner) and self._dnd_since >= since

    def drag_files(self) -> list[str] | None:
        """URIs of the drag in flight, read from ``XdndSelection``.

        Read once per drag (cached by owner) on a short-lived private
        connection with a tight timeout, so the wheel can bloom with its
        petals already filled; if the source is slow, dragEnter fills them.
        """
        if not self.available:
            return None
        self._pump()
        owner = self._dnd_owner
        if not owner:
            return None
        cached = self._uri_cache
        if cached is not None and cached[0] == owner:
            return cached[1]
        files: list[str] | None = None
        try:
            files = read_selection_uris(self._display_name, "XdndSelection")
        except Exception:
            log.debug("XdndSelection read failed", exc_info=True)
        self._uri_cache = (owner, files or None)
        return self._uri_cache[1]

    def raise_drag_icons(self) -> int:
        """Restack the drag source's icon window above everything (the wheel).

        GTK and Qt draw the icon under the cursor in an override-redirect
        window of type _NET_WM_WINDOW_TYPE_DND owned by the drag source.
        """
        if not self.available:
            return 0
        self._pump()
        owner = self._dnd_owner
        if not owner:
            try:
                found = self._display.get_selection_owner(self._atoms["XdndSelection"])
                owner = int(getattr(found, "id", found) or 0)
            except Exception:
                owner = 0
        if not owner:
            return 0
        mask = int(getattr(self._display.display.info, "resource_id_mask", 0x1FFFFF) or 0x1FFFFF)
        base = owner & ~mask
        raised = 0
        try:
            children = self._root.query_tree().children
        except Exception:
            return 0
        for child in children:
            if (child.id & ~mask) != base:
                continue
            try:
                if child.get_attributes().map_state != X.IsViewable:
                    continue
                kind = child.get_full_property(self._atoms["_NET_WM_WINDOW_TYPE"], Xatom.ATOM)
                if kind is None or self._atoms["_NET_WM_WINDOW_TYPE_DND"] not in [int(a) for a in kind.value]:
                    continue
                child.configure(stack_mode=X.Above)
                raised += 1
            except Exception:
                continue
        try:
            self._display.flush()
        except Exception:
            pass
        return raised

    # -- foreground context ------------------------------------------------

    def _window_property(self, window, atom_name: str, kind):
        try:
            prop = window.get_full_property(self._atoms[atom_name], kind)
        except Exception:
            return None
        return None if prop is None else prop.value

    def active_window(self):
        value = self._window_property(self._root, "_NET_ACTIVE_WINDOW", Xatom.WINDOW)
        if not value:
            return None
        window_id = int(value[0])
        if not window_id:
            return None
        return self._display.create_resource_object("window", window_id)

    def window_classes(self, window) -> set[str]:
        try:
            klass = window.get_wm_class()
        except Exception:
            klass = None
        return {part.casefold() for part in (klass or ()) if part}

    def window_pid(self, window) -> int:
        value = self._window_property(window, "_NET_WM_PID", Xatom.CARDINAL)
        return int(value[0]) if value else 0

    def is_fullscreen(self, window) -> bool:
        value = self._window_property(window, "_NET_WM_STATE", Xatom.ATOM)
        if not value:
            return False
        return self._atoms["_NET_WM_STATE_FULLSCREEN"] in [int(atom) for atom in value]

    def wheel_allowed(self) -> bool:
        """Same policy as the Windows gate: file surfaces yes, games and
        full-screen apps no, and fail open on any error."""
        if not self.available:
            return True
        window = self.active_window()
        if window is None:
            return True
        if self.window_pid(window) == os.getpid():
            return True
        classes = self.window_classes(window)
        if classes & SHELL_CLASSES:
            return True
        if classes & GAME_CLASSES:
            log.debug("Wheel suppressed: game window class %s", classes)
            return False
        if self.is_fullscreen(window):
            log.debug("Wheel suppressed: full-screen window %s", classes)
            return False
        return True

    def foreground_is_file_manager(self) -> bool:
        window = self.active_window() if self.available else None
        return bool(window is not None and self.window_classes(window) & SHELL_CLASSES)


def read_selection_uris(display_name: str | None, selection: str, timeout: float = _URI_TIMEOUT_S) -> list[str]:
    """Convert *selection* to ``text/uri-list`` and return local paths."""
    disp = _xdisplay.Display(display_name)
    try:
        root = disp.screen().root
        window = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        sel_atom = disp.intern_atom(selection)
        target = disp.intern_atom("text/uri-list")
        prop = disp.intern_atom("TANGERINE_XDND")
        window.convert_selection(sel_atom, target, prop, X.CurrentTime)
        disp.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while disp.pending_events():
                event = disp.next_event()
                if event.type == X.SelectionNotify and int(getattr(event.requestor, "id", event.requestor)) == window.id:
                    if event.property == X.NONE:
                        return []
                    data = window.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
                    if data is None:
                        return []
                    value = data.value
                    if isinstance(value, str):
                        value = value.encode("utf-8", "surrogateescape")
                    return parse_uri_list(bytes(value))
            time.sleep(0.005)
        return []
    finally:
        try:
            disp.close()
        except Exception:
            pass


_session: X11Session | None = None


def session() -> X11Session:
    """The process-wide X11 session (created on first use, GUI thread)."""
    global _session
    if _session is None:
        _session = X11Session()
    return _session
