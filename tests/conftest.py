"""Shared pytest setup for the Tangerine test suite.

Runs Qt in offscreen mode so widget construction works without a desktop,
adds the repository root to ``sys.path`` (so ``import tangerine`` works when
pytest is invoked from anywhere) and exposes a session-scoped QApplication.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import tempfile
from PySide6.QtCore import qInstallMessageHandler


def _qt_message_handler(mode, context, message):
    """Keep native Qt diagnostics visible when a Windows test aborts."""
    try:
        kind = getattr(mode, "name", str(mode))
        category = getattr(context, "category", "") or "default"
        source = getattr(context, "file", None)
        line = getattr(context, "line", 0)
        location = f" {source}:{line}" if source else ""
        print(f"[Qt {kind} {category}{location}] {message}", file=sys.stderr, flush=True)
    except Exception:
        # The handler must never turn a Qt diagnostic into a second failure.
        pass


qInstallMessageHandler(_qt_message_handler)

from tangerine import paths as _paths

_SETTINGS_TMP = Path(tempfile.mkdtemp(prefix="tangerine_test_settings_"))
_paths.settings_file = lambda: _SETTINGS_TMP / "settings.json"


@pytest.fixture(autouse=True)
def cleanup_test_qt_windows(monkeypatch):
    """Destroy windows made by each test before its monkeypatches are restored.

    Qt widgets can outlive ``close()`` when a module keeps a Python reference.
    Since this fixture depends on pytest's ``monkeypatch``, its teardown runs
    first: close handlers and deferred Qt events still see the test's patched
    functions, then monkeypatch restores module state.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    baseline = list(app.topLevelWidgets()) if app is not None else []
    yield

    app = QApplication.instance()
    if app is None:
        return
    created = [
        widget for widget in app.topLevelWidgets()
        if not any(widget is previous for previous in baseline)
    ]
    if not created:
        return

    labels = [widget.objectName() or widget.windowTitle() or type(widget).__name__
              for widget in created]
    print(f"[qt-window-cleanup] closing {len(created)} test window(s): {labels}",
          file=sys.stderr, flush=True)
    from PySide6.QtCore import QCoreApplication, QEvent
    import shiboken6

    for widget in created:
        if shiboken6.isValid(widget):
            widget.close()
            widget.deleteLater()
    # Deliver deferred destruction while test monkeypatches are still active;
    # do not pump unrelated paint/timer events during teardown.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    remaining = [
        widget for widget in app.topLevelWidgets()
        if not any(widget is previous for previous in baseline)
    ]
    if remaining:
        names = [widget.objectName() or widget.windowTitle() or type(widget).__name__
                 for widget in remaining]
        pytest.fail(f"Qt test windows survived deferred deletion: {names}")


@pytest.fixture(autouse=True)
def _scripted_drag_session(request, monkeypatch):
    """Monitor tests script Win32-style input; on X11 they also need the
    XDND session gate to report a drag, which a headless run never sees."""
    if sys.platform != "win32" and request.module.__name__.startswith("test_monitor"):
        from tangerine.monitor import DragMonitor

        monkeypatch.setattr(DragMonitor, "_drag_session_started", lambda self: True)
    yield


@pytest.fixture(scope="session")
def qapp():
    """Return the process-wide QApplication, creating it on first use."""
    import PySide6
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    # Same as app start-up: numbers follow the interface language even on a
    # system whose numeric locale differs (e.g. LC_NUMERIC=ar_SA).
    from tangerine import i18n

    i18n.apply_number_locale()
    font_probes = []
    from PySide6.QtGui import QFont, QFontDatabase
    # Qt's Windows offscreen plugin has no system font database. Load installed
    # fonts explicitly so widget rendering checks cover legible text as well.
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
            font = fonts / name
            font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else None
            font_probes.append((name, font.exists(), font_id))
        app.setFont(QFont("Segoe UI", 10))
    families = QFontDatabase.families()
    print(
        "[qapp-probe] "
        f"python={sys.version.split()[0]} PySide6={PySide6.__version__} Qt={qVersion()} "
        f"qpa={os.environ.get('QT_QPA_PLATFORM', '<default>')} "
        f"appFont={app.font().family()!r} families={len(families)} "
        f"segoeUI={'Segoe UI' in families} fontFiles={font_probes}",
        file=sys.stderr,
        flush=True,
    )
    return app
