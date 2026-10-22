"""Linux port: X11 probes, Nautilus integration and wheel requests."""

import os
import stat
import sys
import time

import pytest
from PySide6.QtCore import QPoint

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux port")

from tangerine import linux_shell, requests, x11
from tangerine import monitor as monitor_module
from tangerine.monitor import VK_LBUTTON, VK_SHIFT, DragMonitor


# -- text/uri-list -----------------------------------------------------------

def test_uri_list_keeps_local_files_only():
    payload = (
        b"# comment\r\n"
        b"file:///home/me/Photo%20one.jpg\r\n"
        b"file://localhost/tmp/a.png\r\n"
        b"https://example.com/b.png\r\n"
        b"file://otherhost/c.png\r\n"
        b"\r\n"
    )
    assert x11.parse_uri_list(payload) == ["/home/me/Photo one.jpg", "/tmp/a.png"]


def test_uri_list_decodes_utf8_names():
    payload = "file:///home/me/%D9%88%D8%B1%D8%AF%D8%A9.png\n".encode()
    assert x11.parse_uri_list(payload) == ["/home/me/وردة.png"]


# -- XdndSelection gate --------------------------------------------------------

def _offline_session():
    session = x11.X11Session.__new__(x11.X11Session)
    session.available = True
    session._xfixes = True
    session._dnd_owner = 0
    session._dnd_since = 0.0
    session._uri_cache = None
    session._pump = lambda: None
    return session


def test_drag_must_start_after_the_last_button_up():
    session = _offline_session()
    last_up = time.monotonic()
    assert not session.drag_started_since(last_up)

    session._dnd_owner = 0x2a00001
    session._dnd_since = last_up - 5.0  # owner left over from an earlier drag
    assert not session.drag_started_since(last_up)

    session._dnd_since = last_up + 0.02  # this press started a drag
    assert session.drag_started_since(last_up)


def test_without_xfixes_the_gate_fails_open():
    session = _offline_session()
    session._xfixes = False
    assert session.drag_started_since(time.monotonic())


def test_unavailable_display_never_reports_a_drag():
    session = _offline_session()
    session.available = False
    assert not session.drag_started_since(0.0)
    assert session.drag_files() is None
    assert session.wheel_allowed()


def test_offscreen_platform_does_not_touch_the_real_display(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DISPLAY", ":0")
    assert x11.X11Session().available is False


# -- monitor wired to the gate -------------------------------------------------

class _Keys:
    def __init__(self):
        self.down = set()

    def __call__(self, vk):
        return vk in self.down


@pytest.fixture
def x11_rig(qapp, monkeypatch):
    keys = _Keys()
    session = {"started": False}
    state = {"pos": QPoint(100, 100)}
    monkeypatch.setattr(monitor_module, "_is_down", keys)
    monkeypatch.setattr(monitor_module.context, "wheel_allowed", lambda: True)
    monkeypatch.setattr(DragMonitor, "_drag_files", lambda self: None)
    monkeypatch.setattr(DragMonitor, "_cursor", lambda self: QPoint(state["pos"]))
    monkeypatch.setattr(
        monitor_module.x11, "session",
        lambda: type("S", (), {"drag_started_since": lambda self, since: session["started"]})(),
    )
    monitor = DragMonitor()
    monitor.stop()
    events = []
    monitor.wheelShown.connect(lambda pos, files, mode: events.append(("shown", mode)))
    return {"monitor": monitor, "keys": keys, "session": session, "state": state, "events": events}


def _shift_drag(rig):
    rig["keys"].down = {VK_LBUTTON, VK_SHIFT}
    rig["monitor"]._tick()  # press
    rig["state"]["pos"] = QPoint(100, 160)
    rig["monitor"]._tick()  # past the movement threshold


def test_shift_movement_without_a_drag_session_keeps_the_wheel_hidden(x11_rig):
    _shift_drag(x11_rig)
    assert x11_rig["events"] == []


def test_shift_drag_with_an_xdnd_session_shows_the_conversion_wheel(x11_rig):
    x11_rig["session"]["started"] = True
    _shift_drag(x11_rig)
    assert x11_rig["events"] == [("shown", "conversion")]


# -- foreground gate -------------------------------------------------------------

class _Window:
    def __init__(self, classes, fullscreen=False, pid=1):
        self.classes, self.fullscreen, self.pid = classes, fullscreen, pid


def _gate_session(window):
    session = _offline_session()
    session.active_window = lambda: window
    session.window_classes = lambda w: {c.casefold() for c in w.classes}
    session.is_fullscreen = lambda w: w.fullscreen
    session.window_pid = lambda w: w.pid
    return session


@pytest.mark.parametrize(
    "window, allowed",
    [
        (None, True),
        (_Window(("org.gnome.Nautilus", "Org.gnome.Nautilus"), fullscreen=True), True),
        (_Window(("steam_app_570", "steam_app")), False),
        (_Window(("firefox", "Firefox"), fullscreen=True), False),
        (_Window(("firefox", "Firefox")), True),
        (_Window(("anything",), fullscreen=True, pid=os.getpid()), True),
    ],
)
def test_foreground_gate(window, allowed):
    assert _gate_session(window).wheel_allowed() is allowed


# -- Nautilus integration --------------------------------------------------------

def test_register_installs_scripts_launcher_and_optional_autostart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    folder = linux_shell.register_shell("/opt/tangerine/bin/tangerine", autostart=True)

    convert = folder / "Convert…"
    assert convert.stat().st_mode & stat.S_IXUSR
    text = convert.read_text()
    assert "exec /opt/tangerine/bin/tangerine --wheel conversion \"$@\"" in text
    assert "--pdf-quick" in (folder / "PDF from images (by name)").read_text()
    assert "Exec=/opt/tangerine/bin/tangerine" in linux_shell.desktop_file().read_text()
    assert linux_shell.autostart_enabled()

    linux_shell.register_shell("/opt/tangerine/bin/tangerine", autostart=False)
    assert not linux_shell.autostart_enabled()

    linux_shell.unregister_shell()
    assert not folder.exists()
    assert not linux_shell.desktop_file().exists()


def test_register_never_overwrites_foreign_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    foreign = linux_shell.nautilus_scripts_dir() / "Convert…"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("#!/bin/sh\necho mine\n")
    linux_shell.register_shell("/x/tangerine")
    assert foreign.read_text() == "#!/bin/sh\necho mine\n"
    linux_shell.unregister_shell()
    assert foreign.exists()


# -- --wheel requests -------------------------------------------------------------

def test_wheel_request_round_trips(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    request = requests.parse_request(["--wheel", "tools", str(photo)])
    assert request["action"] == "wheel"
    assert request["mode"] == "tools"
    assert request["paths"] == [str(photo)]
    assert requests.validate_request(dict(request)) == request


def test_wheel_request_needs_files():
    with pytest.raises(SystemExit):
        requests.parse_request(["--wheel", "conversion"])
    with pytest.raises(ValueError):
        requests.validate_request({"id": "0" * 32, "action": "wheel", "mode": "nope", "paths": ["/a"]})


# -- move-only drops (GTK Shift-drags) ---------------------------------------------

def test_gtk_move_only_shift_drag_is_accepted_without_touching_the_file(qapp, tmp_path):
    """GTK offers a Shift-drag as move-only; the wheel must still take it.

    Covered end to end against a real GTK source in a nested X server: the
    source never deletes, because deletion is requested by the target.
    """
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    from tangerine.wheel import FanItem, TangerineWheel

    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG fixture")
    wheel = TangerineWheel()
    wheel.set_factory(lambda files: [FanItem("jpg", "JPG"), FanItem("webp", "WEBP")])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(photo))])
    centre = QPoint(wheel.width() // 2, wheel.height() // 2)
    enter = QDragEnterEvent(centre, Qt.DropAction.MoveAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    wheel.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert wheel.file_paths() == [str(photo)]

    activated = []
    wheel.activated.connect(lambda files, key: activated.append((files, key)))
    top_petal = QPointF(wheel.width() / 2.0 + 60, wheel.height() / 2.0 - 150)
    drop = QDropEvent(top_petal, Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    wheel.dropEvent(drop)
    assert drop.isAccepted()
    assert activated == [([str(photo)], "jpg")]
    assert photo.exists()
    wheel.deleteLater()


def test_quit_request_round_trips():
    request = requests.parse_request(["--quit"])
    assert request["action"] == "quit"
    assert requests.validate_request(dict(request)) == request
