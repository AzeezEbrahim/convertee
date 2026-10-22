"""The radial conversion/tools wheel (v1.7.0 HUD language).

Geometry and behaviour follow ``docs/PLAN-V1.7.0.md`` §1.1-1.2:

* a 460x460 frameless window; petals are annular wedges between ``R_IN`` and
  ``R_OUT`` with 8 degree gaps and rounded corners;
* every surface is *material*: the blurred desktop behind the wheel plus the
  theme ``wash`` (see :class:`~tangerine.hud.HudBackdrop`), the halo is the
  translucent ``halo`` token and the hub uses the ``hub`` token;
* conversion petals show only the upper-case format name; tool petals show a
  vector line icon plus a small upper-case label;
* hover paints the petal flat ``ACCENT`` with dark content (120 ms animated),
  the hub grows a capsule with the hovered label that slides in from that
  petal (140 ms) and fades out;
* appearance is a staggered 260 ms bloom (radius 0.6R → R with a slight
  overshoot), the farewell shrinks to 0.94 with a fade.

The wheel also keeps the hardened drag-and-drop behaviour (copy-only drags,
highlight sound with a one-way disable, :meth:`file_paths`, :meth:`reset`)
and adds keyboard operation (:meth:`set_keyboard_mode`): arrows rotate the
hover with wrap-around, Enter activates and Escape hides.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote

from PySide6.QtCore import (
    QEasingCurve,
    QMimeDatabase,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import i18n, paths as app_paths, settings, theme
from .hud import HudBackdrop
from .icons import icon_for, paint_icon  # noqa: F401 (icon_for is a re-export)

log = logging.getLogger("tangerine")

# Windows rejects move-only drops: Explorer deletes the source after an
# accepted move. On X11 deletion is requested by the target, never assumed.
_MOVE_ONLY_DROPS_ARE_SAFE = sys.platform.startswith("linux")

# -- geometry (460x460 window) ------------------------------------------------
# Proportions follow the macOS app: wide orange-segment petals around a small
# hub (inner/outer ≈ 0.42), constant-width gaps, strongly rounded corners and
# a translucent rind around the petals.
EXTENT = 230.0
R_OUT = 180.0
R_IN = 76.0
BAND = R_OUT - R_IN
RIND_R = R_OUT + 12.0
HALO_R = RIND_R              # outer edge of the painted wheel
HUB_R = R_IN - 8.0
GAP_PX = 8.0                 # same width between petals at every radius
GAP_DEG = math.degrees(GAP_PX / ((R_IN + R_OUT) / 2.0))  # mid-band equivalent
CORNER_OUT = 0.27 * BAND
CORNER_IN = 0.17 * BAND
CORNER_R = CORNER_OUT
SHADOW_SPREAD = 22.0

# -- motion ----------------------------------------------------------------
APPEAR_MS = 240.0            # the whole wheel blooms from APPEAR_SCALE
APPEAR_STAGGER_MS = 0.0
APPEAR_SCALE = 0.55
DEPART_MS = 150.0
DEPART_SCALE = 0.94
HOVER_MS = 110.0
HOVER_LIFT = 0.0
CAPSULE_MS = 130.0
CAPSULE_FADE_MS = 110.0
CAPSULE_H = 28.0
CAPSULE_MIN_W = 78.0
CAPSULE_SLIDE = 14.0
CAPSULE_DROP = 26.0          # pill below the centre when a preview is shown
HUB_VEIL = 0.45
PREVIEW_BOX = 66.0

ICON_SIZE = 24.0
ICON_LABEL_GAP = 4.0
TOOL_LABEL_SIZE = 10
CONVERT_LABEL_SIZE = 20

HOVER_INK = "#FFFFFF"


@dataclass
class FanItem:
    """One petal: ``kind`` is ``"conversion"`` or ``"tool"``."""

    key: str
    label: str
    icon: str = ""
    kind: str = "conversion"
    short: str = ""  # petal text when the full label is long; the hub shows ``label``


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _ease_out(value: float) -> float:
    value = _clamp01(value)
    return 1.0 - (1.0 - value) ** 3


def _ease_out_back(value: float, strength: float = 0.35) -> float:
    """Ease-out with a subtle overshoot (petal bloom)."""
    value = _clamp01(value)
    delta = value - 1.0
    return 1.0 + (strength + 1.0) * delta**3 + strength * delta**2


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = _clamp01(amount)
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
        round(first.alpha() + (second.alpha() - first.alpha()) * amount),
    )


def _wedge_path(
    r_in: float,
    r_out: float,
    a1: float,
    a2: float,
    corner_r: float,
) -> QPainterPath:
    """Annular wedge with rounded corners, ready to clip and fill.

    Angles are degrees in the screen convention of :func:`math.atan2` (0° at
    3 o'clock, growing clockwise).  The marching path is inset by *corner_r*
    so that stroking it with a ``2 * corner_r`` round-capped/round-joined pen
    (and filling it) rounds every corner with that radius.  The two sides are
    inset per radius (``asin(corner / radius)``) so the stroked outline lands
    exactly on the radial gap edges at every radius.
    """
    corner = max(1.5, float(corner_r))
    inner = float(r_in) + corner
    outer = float(r_out) - corner
    if outer - inner < 2.0 * corner:  # degenerate band: keep a slim wedge
        middle = (float(r_in) + float(r_out)) / 2.0
        inner, outer = middle - corner, middle + corner
    inset_out = math.degrees(math.asin(min(1.0, corner / max(1.0, outer))))
    inset_in = math.degrees(math.asin(min(1.0, corner / max(1.0, inner))))
    span = float(a2) - float(a1)
    if span <= inset_out + inset_in + 1.0 or span <= 2.0 * inset_in + 1.0:
        inset_out = inset_in = 0.0
    start_out = a1 + inset_out
    end_out = a2 - inset_out
    start_in = a1 + inset_in
    end_in = a2 - inset_in
    outer_rect = QRectF(-outer, -outer, 2.0 * outer, 2.0 * outer)
    inner_rect = QRectF(-inner, -inner, 2.0 * inner, 2.0 * inner)
    # Qt arcs measure counter-clockwise from 3 o'clock; screen angles flip.
    marching = QPainterPath()
    marching.arcMoveTo(outer_rect, -start_out)
    marching.arcTo(outer_rect, -start_out, -(end_out - start_out))
    marching.lineTo(
        inner * math.cos(math.radians(end_in)),
        inner * math.sin(math.radians(end_in)),
    )
    marching.arcTo(inner_rect, -end_in, end_in - start_in)
    marching.closeSubpath()
    stroker = QPainterPathStroker()
    stroker.setWidth(2.0 * corner)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(marching).united(marching)


def _petal_path(
    r_in: float,
    r_out: float,
    a1: float,
    a2: float,
    half_gap: float,
    corner_out: float,
    corner_in: float,
) -> QPainterPath:
    """One petal between the rays *a1* and *a2* (screen degrees, clockwise).

    The sides run parallel to the rays, *half_gap* inside them, so the gap
    between neighbours has the same width at every radius (as in the macOS
    wheel). Corners are rounded with cubic curves that hug the arcs, which
    reads like Apple's continuous corners rather than circular fillets.
    """
    if a2 - a1 >= 359.0:  # a single petal: the full ring
        ring = QPainterPath()
        ring.addEllipse(QPointF(0.0, 0.0), r_out, r_out)
        ring.addEllipse(QPointF(0.0, 0.0), r_in, r_in)
        ring.setFillRule(Qt.FillRule.OddEvenFill)
        return ring

    def polar(radius: float, angle: float) -> QPointF:
        radians = math.radians(angle)
        return QPointF(radius * math.cos(radians), radius * math.sin(radians))

    def side_angle(radius: float) -> float:
        return math.degrees(math.asin(min(1.0, half_gap / max(1.0, radius))))

    o1, o2 = a1 + side_angle(r_out), a2 - side_angle(r_out)
    i1, i2 = a1 + side_angle(r_in), a2 - side_angle(r_in)
    side = max(1.0, (r_out - r_in))
    c_out = min(corner_out, side * 0.45, math.radians(o2 - o1) * r_out * 0.45)
    c_in = min(corner_in, side * 0.45, max(0.0, math.radians(i2 - i1) * r_in * 0.45))
    d_out = math.degrees(c_out / r_out)
    d_in = math.degrees(c_in / max(1.0, r_in))

    v1o, v2o = polar(r_out, o1), polar(r_out, o2)  # sharp corners
    v1i, v2i = polar(r_in, i1), polar(r_in, i2)

    def toward(start: QPointF, end: QPointF, distance: float) -> QPointF:
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy) or 1.0
        return QPointF(start.x() + dx / length * distance, start.y() + dy / length * distance)

    def corner(path: QPainterPath, vertex: QPointF, end: QPointF) -> None:
        begin = path.currentPosition()
        k = 0.62
        c1 = QPointF(begin.x() + (vertex.x() - begin.x()) * k, begin.y() + (vertex.y() - begin.y()) * k)
        c2 = QPointF(end.x() + (vertex.x() - end.x()) * k, end.y() + (vertex.y() - end.y()) * k)
        path.cubicTo(c1, c2, end)

    outer_rect = QRectF(-r_out, -r_out, 2.0 * r_out, 2.0 * r_out)
    inner_rect = QRectF(-r_in, -r_in, 2.0 * r_in, 2.0 * r_in)
    path = QPainterPath()
    start = polar(r_out, o1 + d_out)
    path.moveTo(start)
    # Qt arcs measure counter-clockwise from 3 o'clock; screen angles flip.
    path.arcTo(outer_rect, -(o1 + d_out), -((o2 - d_out) - (o1 + d_out)))
    corner(path, v2o, toward(v2o, v2i, c_out))
    path.lineTo(toward(v2i, v2o, c_in))
    corner(path, v2i, polar(r_in, i2 - d_in))
    path.arcTo(inner_rect, -(i2 - d_in), (i2 - d_in) - (i1 + d_in))
    corner(path, v1i, toward(v1i, v1o, c_in))
    path.lineTo(toward(v1o, v1i, c_out))
    corner(path, v1o, start)
    path.closeSubpath()
    return path


def _human_size(total: int) -> str:
    """Decimal sizes like Finder: 830 KB, 2.2 MB."""
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if total >= factor:
            value = total / factor
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
    return f"{total} B"


def _thumbnail_candidates(path: str):
    """freedesktop.org thumbnail cache entries (written by Files/Nautilus)."""
    uri = "file://" + quote(os.path.abspath(path), safe="/!$&'()*+,;=:@~")
    digest = hashlib.md5(uri.encode("utf-8")).hexdigest()
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "thumbnails"
    for size in ("large", "x-large", "normal", "xx-large"):
        yield cache / size / f"{digest}.png"


def _load_preview(path: str) -> tuple[QImage, bool] | None:
    """A small picture for the hub: (image, is_photo) or None.

    Cached system thumbnails first, then a fast scaled read for formats Qt
    decodes itself, then the file type's icon from the icon theme.
    """
    try:
        if not os.path.isdir(path):
            for candidate in _thumbnail_candidates(path):
                if candidate.is_file():
                    image = QImage(str(candidate))
                    if not image.isNull():
                        return image, True
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            if reader.canRead():
                size = reader.size()
                if size.isValid() and size.width() > 0 and size.height() > 0:
                    factor = min(1.0, 192.0 / max(size.width(), size.height()))
                    reader.setScaledSize(QSize(max(1, int(size.width() * factor)),
                                               max(1, int(size.height() * factor))))
                image = reader.read()
                if not image.isNull():
                    return image, True
        if os.path.isdir(path):
            names = ["folder"]
        else:
            mime = QMimeDatabase().mimeTypeForFile(path)
            names = [mime.iconName(), mime.genericIconName(), "text-x-generic"]
        for name in names:
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                image = icon.pixmap(96, 96).toImage()
                if not image.isNull():
                    return image, False
    except Exception:
        log.debug("No hub preview for %s", path, exc_info=True)
    return None


class TangerineWheel(QWidget):
    activated = Signal(list, str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(None)
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if sys.platform.startswith("linux"):
            # Override-redirect on X11: the window manager can neither move
            # the wheel away from the cursor nor refuse it keyboard focus in
            # keyboard mode, and it stacks above full-screen surfaces.
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.resize(int(EXTENT * 2), int(EXTENT * 2))

        self._items: list[FanItem] = []
        self._file_paths: list[str] = []
        # Compatibility record of the last drop context (title, subtitle); the
        # hub no longer paints it, `set_prompt` keeps it for older callers.
        self._prompt: tuple[str, str] = ("", "")
        self._mode = "conversion"
        self._factory: Callable[[list[str]], Sequence[FanItem]] | None = None
        self._sound = None
        self._sound_disabled = False
        self._dark = theme.is_dark()

        self._backdrop = HudBackdrop()
        self._material: QImage | None = None
        self._capture_rect = self.frameGeometry()
        self._capture_stamp = 0.0

        self._hover = -1
        self._hover_values: dict[int, float] = {}
        self._capsule_t = 0.0
        self._capsule_label = ""
        self._capsule_dir = QPointF(0.0, -1.0)
        self._appear_ms = 0.0
        self._appear_total = 0.0
        self._depart = 0.0
        self._depart_token = 0
        self._keyboard_mode = False

        self._appear_anim: QVariantAnimation | None = None
        self._hover_anim: QVariantAnimation | None = None
        self._capsule_anim: QVariantAnimation | None = None
        self._depart_anim: QVariantAnimation | None = None

        self._convert_font: QFont | None = None
        self._tool_font: QFont | None = None
        self._capsule_font: QFont | None = None
        self._summary_font: QFont | None = None
        self._summary = ""
        self._preview_key: str | None = None
        self._preview: tuple[QImage, bool] | None = None

        theme.add_theme_listener(self._on_theme_change)
        self.destroyed.connect(lambda *_args: theme.remove_theme_listener(self._on_theme_change))

    # -- public API ----------------------------------------------------

    def set_factory(self, factory: Callable[[list[str]], Sequence[FanItem]]) -> None:
        self._factory = factory

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def set_items(self, items: Sequence[FanItem], animate: bool = False) -> None:
        """Replace the petals; *animate* replays the bloom (mode switches)."""
        previous = len(self._items)
        self._items = list(items)
        self._hover = -1
        self._hover_values = {index: 0.0 for index in range(len(self._items))}
        self._capsule_t = 0.0
        self._capsule_label = ""
        if animate or (self.isVisible() and len(self._items) != previous):
            self.play_appearance()
        elif self._appear_total <= 0.0:
            self._appear_ms = 0.0
        self.update()

    def set_prompt(self, main: str, sub: str = "") -> None:
        """Compatibility no-op: the hub only shows the hovered petal label.

        The subtitle is still recorded so drag hovers keep the historical
        ``_prompt`` contract alive for older callers.
        """
        self._prompt = (str(main), str(sub))

    def center_at(self, global_pos: QPoint) -> None:
        center = QPoint(global_pos)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            margin = int(HALO_R)
            left, right = area.left() + margin, area.right() - margin
            if left <= right:
                center.setX(min(max(center.x(), left), right))
            top, bottom = area.top() + margin, area.bottom() - margin
            if top <= bottom:
                center.setY(min(max(center.y(), top), bottom))
        self.move(center.x() - int(EXTENT), center.y() - int(EXTENT))
        self._capture_backdrop()

    def update_cursor(self, global_pos: QPoint) -> None:
        self._set_hover_index(self.hit_test(QPointF(self.mapFromGlobal(global_pos))))

    def file_paths(self) -> list[str]:
        return list(self._file_paths)

    def set_files(self, files: Sequence[str] | None) -> None:
        """Record the files the wheel acts on (keyboard/selection triggers).

        Real drag-and-drop overwrites this from :meth:`dragEnterEvent`.
        """
        self._file_paths = [str(path) for path in (files or [])]
        self._refresh_summary()

    def reset(self) -> None:
        self._stop_animations()
        self._items = []
        self._file_paths = []
        self._summary = ""
        self._hover = -1
        self._hover_values = {}
        self._capsule_t = 0.0
        self._capsule_label = ""
        self._appear_ms = 0.0
        self._appear_total = 0.0
        self._depart = 0.0
        self.update()

    def set_keyboard_mode(self, flag: bool) -> None:
        """Keyboard mode takes focus (drag mode never steals it)."""
        flag = bool(flag)
        if flag == self._keyboard_mode:
            return
        self._keyboard_mode = flag
        visible = self.isVisible()
        if flag:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.clearFocus()
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            if visible:
                self.show()

    # -- motion ---------------------------------------------------------

    def play_appearance(self) -> None:
        """Staggered bloom: petals 0.6R → R with a fade (plan 1.2)."""
        self._stop_animations()
        self._depart_token += 1
        self._depart = 0.0
        self._hover = -1
        self._hover_values = {index: 0.0 for index in range(len(self._items))}
        self._capsule_t = 0.0
        self._capsule_label = ""
        if not self._items:
            return
        self._appear_total = APPEAR_MS + APPEAR_STAGGER_MS * (len(self._items) - 1)
        self._appear_ms = 0.0
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(float(self._appear_total))
        animation.setDuration(int(round(self._appear_total)))
        animation.setEasingCurve(QEasingCurve.Type.Linear)
        animation.valueChanged.connect(self._on_appear_tick)
        self._appear_anim = animation
        animation.start()

    def dismiss(self) -> None:
        """Farewell (0.94 scale + fade) followed by hide + reset."""
        if not self.isVisible():
            return
        self._depart_token += 1
        token = self._depart_token

        def finished() -> None:
            if token == self._depart_token:
                self.hide()
                self.reset()

        self._animate_depart(finished)

    def cancel_dismiss(self) -> None:
        """Abort a farewell in flight so the wheel stays for this gesture."""
        self._depart_token += 1
        self._depart = 0.0
        if self._depart_anim is not None:
            self._depart_anim.stop()
        self.update()

    def _close(self) -> None:
        """User-dismissed wheel: clear state and announce the close."""
        self.reset()
        self.hide()
        self.closed.emit()

    # -- interaction ---------------------------------------------------

    def hit_test(self, local: QPointF) -> int:
        count = len(self._items)
        if count == 0:
            return -1
        dx = local.x() - self.width() / 2.0
        dy = local.y() - self.height() / 2.0
        radius = math.hypot(dx, dy)
        if radius < R_IN - 6.0 or radius > R_OUT + 10.0:
            return -1
        angle = math.degrees(math.atan2(dy, dx))
        step = 360.0 / count
        # Half the gap, at this radius, stays droppable (forgiving edges).
        half = step / 2.0 - math.degrees(GAP_PX * 0.25 / max(1.0, radius))
        for index in range(count):
            target = -90.0 + (index + 0.5) * step
            difference = (angle - target + 540.0) % 360.0 - 180.0
            if abs(difference) <= half:
                return index
        return -1

    def _set_hover_index(self, index: int) -> None:
        if index == self._hover:
            return
        self._hover = index
        self._animate_hover()
        count = len(self._items)
        if 0 <= index < count:
            item = self._items[index]
            self._capsule_label = item.label
            step = 360.0 / count
            radians = math.radians(-90.0 + (index + 0.5) * step)
            self._capsule_dir = QPointF(math.cos(radians), math.sin(radians))
            self._animate_capsule(1.0)
            self._play_highlight()
        else:
            self._animate_capsule(0.0)
        self.update()

    def _play_highlight(self) -> None:
        if self._sound_disabled or not settings.get("soundsAndHapticsEnabled"):
            return
        try:
            if self._sound is None:
                from PySide6.QtMultimedia import QSoundEffect

                self._sound = QSoundEffect(self)
                self._sound.setSource(QUrl.fromLocalFile(str(app_paths.highlight_sound())))
                self._sound.setVolume(0.55)
            self._sound.play()
        except Exception:
            self._sound_disabled = True
            log.warning("Highlight sound disabled", exc_info=True)

    def _accept_copy(self, event) -> bool:
        if event.possibleActions() & Qt.CopyAction:
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return True
        if _MOVE_ONLY_DROPS_ARE_SAFE and event.possibleActions() & Qt.MoveAction:
            # GTK (Nautilus, the desktop) offers a Shift-drag as move-only,
            # and Shift is the gesture. A GTK source never deletes by itself:
            # it waits for the target to request the XDND "DELETE" target,
            # which Qt never does, so the originals stay where they are.
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return True
        event.ignore()
        return False

    def _merge_files(self, payload: list[str]) -> list[str]:
        """Keep the full selection when the OLE payload collapsed it."""
        known = self._file_paths
        if known and len(payload) < len(known) and set(payload) <= set(known):
            return list(known)
        return payload

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        files: list[str] = []
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    files.append(url.toLocalFile())
        if not files:
            event.ignore()
            return
        files = self._merge_files(files)
        self._file_paths = files
        self._refresh_summary()
        if self._factory is not None:
            self.set_items(self._factory(files))
        if not self._items:
            event.ignore()
            return
        subtitle = (
            Path(files[0]).name
            if len(files) == 1
            else i18n.tr("wheel.files_count", n=len(files))
        )
        self._prompt = (self._prompt[0], subtitle)
        self.update()
        self._accept_copy(event)

    def dragMoveEvent(self, event) -> None:
        self._set_hover_index(self.hit_test(event.position()))
        self._accept_copy(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_hover_index(-1)

    def dropEvent(self, event) -> None:
        if not self._accept_copy(event):
            return
        index = self.hit_test(event.position())
        files = list(self._file_paths)
        if 0 <= index < len(self._items):
            key = self._items[index].key
            self.reset()
            self.hide()
            self.activated.emit(files, key)
        else:
            self.reset()
            self.hide()

    def mouseMoveEvent(self, event) -> None:
        self._set_hover_index(self.hit_test(event.position()))

    def leaveEvent(self, event) -> None:
        self._set_hover_index(-1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.hit_test(event.position())
            if 0 <= index < len(self._items):
                key = self._items[index].key
                files = list(self._file_paths)
                self.reset()
                self.hide()
                self.activated.emit(files, key)
            else:
                self._close()
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        """Scroll over the wheel moves the hover like the arrow keys."""
        count = len(self._items)
        if count:
            delta = event.angleDelta().y()
            if delta:
                step = 1 if delta < 0 else -1
                if 0 <= self._hover < count:
                    self._set_hover_index((self._hover + step) % count)
                else:
                    self._set_hover_index(0 if step > 0 else count - 1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._close()
            event.accept()
            return
        count = len(self._items)
        if count == 0:
            super().keyPressEvent(event)
            return
        step = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            if 0 <= self._hover < count:
                self._set_hover_index((self._hover + step) % count)
            else:  # first arrow press: land on the first (or last) petal
                self._set_hover_index(0 if step > 0 else count - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if 0 <= self._hover < count:
                key_name = self._items[self._hover].key
                files = list(self._file_paths)
                self.reset()
                self.hide()
                self.activated.emit(files, key_name)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # -- animations -----------------------------------------------------

    def _stop_animations(self) -> None:
        for animation in (
            self._appear_anim,
            self._hover_anim,
            self._capsule_anim,
            self._depart_anim,
        ):
            if animation is not None:
                animation.stop()

    def _on_appear_tick(self, value) -> None:
        self._appear_ms = float(value)
        self.update()

    def _animate_hover(self) -> None:
        count = len(self._items)
        start = {index: self._hover_value(index) for index in range(count)}
        target = {index: (1.0 if index == self._hover else 0.0) for index in range(count)}
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(int(HOVER_MS))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def tick(value) -> None:
            factor = float(value)
            self._hover_values = {
                index: start[index] + (target[index] - start[index]) * factor
                for index in start
            }
            self.update()

        animation.valueChanged.connect(tick)
        animation.finished.connect(lambda: self._hover_values.update(target))
        self._hover_anim = animation
        animation.start()

    def _animate_capsule(self, target: float) -> None:
        animation = QVariantAnimation(self)
        animation.setStartValue(float(self._capsule_t))
        animation.setEndValue(float(target))
        duration = CAPSULE_MS if target > self._capsule_t else CAPSULE_FADE_MS
        animation.setDuration(int(duration))
        animation.setEasingCurve(QEasingCurve.Type.Linear)
        animation.valueChanged.connect(self._on_capsule_tick)
        self._capsule_anim = animation
        animation.start()

    def _on_capsule_tick(self, value) -> None:
        self._capsule_t = _clamp01(float(value))
        self.update()

    def _animate_depart(self, finished) -> None:
        animation = QVariantAnimation(self)
        animation.setStartValue(float(self._depart))
        animation.setEndValue(1.0)
        animation.setDuration(int(DEPART_MS))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._on_depart_tick)
        animation.finished.connect(finished)
        self._depart_anim = animation
        animation.start()

    def _on_depart_tick(self, value) -> None:
        self._depart = _clamp01(float(value))
        self.update()

    # -- appearance hooks ------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._dark = theme.is_dark()
        self._material = None
        self._capture_backdrop()
        if self._items and self._appear_total <= 0.0:
            self.play_appearance()
        if sys.platform.startswith("linux"):
            # Like Finder's drag image on macOS, the file icon under the
            # cursor stays above the wheel instead of disappearing behind it.
            QTimer.singleShot(0, self._raise_drag_icons)
            QTimer.singleShot(120, self._raise_drag_icons)

    def _raise_drag_icons(self) -> None:
        if not self.isVisible():
            return
        try:
            from . import x11

            x11.session().raise_drag_icons()
        except Exception:
            log.debug("Could not restack the drag icon", exc_info=True)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._capture_backdrop()

    def _capture_backdrop(self) -> None:
        """Re-grab only when the wheel moved or the throttle expired.

        Mirrors :class:`HudBackdrop`'s own throttle so the material composite
        is not rebuilt for every move event during a drag.
        """
        rect = self.frameGeometry()
        now = time.monotonic()
        if rect == self._capture_rect and (now - self._capture_stamp) < 0.25:
            return
        self._capture_rect = rect
        self._capture_stamp = now
        self._material = None
        self._backdrop.capture(rect)

    def _on_theme_change(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._material = None
        self.update()

    # -- painting --------------------------------------------------------

    def _paint_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self.width()), float(self.height()))

    def _material_image(self, token: str = "wash") -> QImage | None:
        """Cache of "blurred desktop + *token* wash" (petals, rind)."""
        if not isinstance(self._material, dict):
            self._material = {}
        cached = self._material.get(token)
        if cached is not None:
            return cached
        ratio = float(self.devicePixelRatioF() or 1.0)
        image = QImage(
            max(2, int(round(self.width() * ratio))),
            max(2, int(round(self.height() * ratio))),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        if image.isNull():
            return None
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        wash = theme.qcolor(theme.hud(self._dark)[token])
        self._backdrop.paint(painter, self._paint_rect(), wash, 0.0)
        painter.end()
        self._material[token] = image
        return image

    def _appear_progress(self, index: int) -> float:
        if self._appear_total <= 0.0:
            return 1.0
        local = (self._appear_ms - index * APPEAR_STAGGER_MS) / APPEAR_MS
        return _clamp01(local)

    def _global_appear(self) -> float:
        if self._appear_total <= 0.0:
            return 1.0
        return _clamp01(self._appear_ms / APPEAR_MS)

    def _hover_value(self, index: int) -> float:
        return _clamp01(float(self._hover_values.get(index, 0.0)))

    def _base_font(self) -> QFont:
        family = theme.ensure_fonts()
        font = QFont(self.font())
        if family:
            font.setFamily(family)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return font

    def _fonts(self) -> tuple[QFont, QFont, QFont]:
        if self._convert_font is None:
            convert = self._base_font()
            convert.setPixelSize(CONVERT_LABEL_SIZE)
            convert.setWeight(QFont.Weight.Bold)
            convert.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103.0)
            self._convert_font = convert
        if self._tool_font is None:
            tool = self._base_font()
            tool.setPixelSize(TOOL_LABEL_SIZE)
            tool.setWeight(QFont.Weight.Bold)
            tool.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106.0)
            self._tool_font = tool
        if self._capsule_font is None:
            capsule = self._base_font()
            capsule.setPixelSize(12)
            capsule.setWeight(QFont.Weight.Bold)
            capsule.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104.0)
            self._capsule_font = capsule
        if self._summary_font is None:
            summary = self._base_font()
            summary.setPixelSize(13)
            summary.setWeight(QFont.Weight.DemiBold)
            self._summary_font = summary
        return self._convert_font, self._tool_font, self._capsule_font

    def _wheel_scale(self) -> float:
        grow = _ease_out_back(self._global_appear(), 0.55)
        return (APPEAR_SCALE + (1.0 - APPEAR_SCALE) * grow) * (
            1.0 - (1.0 - DEPART_SCALE) * self._depart
        )

    def _wheel_alpha(self) -> float:
        return min(1.0, self._global_appear() * 2.4) * (1.0 - self._depart)

    def paintEvent(self, event) -> None:
        count = len(self._items)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        tokens = theme.hud(self._dark)
        scale = self._wheel_scale()
        alpha = self._wheel_alpha()
        if alpha <= 0.02:
            painter.end()
            return
        # The rind is always painted, petals or not. The window is layered
        # (WA_TranslucentBackground) and Windows derives both mouse
        # hit-testing and OLE drop targeting from the painted pixels; a fully
        # transparent window is click-through, so it would never receive
        # ``dragEnter`` and could never fill its petals.
        self._paint_halo(painter, center, tokens, scale, alpha)
        if count:
            step = 360.0 / count
            for index, item in enumerate(self._items):
                self._paint_petal(painter, center, index, item, step, tokens, scale, alpha)
        self._paint_hub(painter, center, tokens, scale, alpha)
        self._paint_capsule(painter, center, tokens, scale, alpha)
        painter.end()

    def _paint_halo(self, painter: QPainter, center: QPointF, tokens: dict,
                    scale: float = 1.0, alpha: float = 1.0) -> None:
        """Soft shadow plus the translucent rind behind the petals."""
        radius = RIND_R * scale
        painter.save()
        painter.setOpacity(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = theme.qcolor(tokens["shadow"])
        clear = QColor(shadow)
        clear.setAlpha(0)
        reach = radius + SHADOW_SPREAD * scale
        origin = QPointF(center.x(), center.y() + 5.0 * scale)
        gradient = QRadialGradient(origin, reach)
        edge = radius / reach
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(max(0.0, edge - 0.10), clear)
        gradient.setColorAt(edge, shadow)
        gradient.setColorAt(1.0, clear)
        painter.setBrush(gradient)
        painter.drawEllipse(origin, reach, reach)

        disc = QPainterPath()
        disc.addEllipse(center, radius, radius)
        painter.setClipPath(disc)
        material = self._material_image("rind")
        if material is not None:
            painter.drawImage(self._paint_rect(), material)
        painter.setClipping(False)
        painter.setPen(QPen(theme.qcolor(tokens["petal_edge"]), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius - 0.5, radius - 0.5)
        painter.restore()

    def _paint_petal(
        self,
        painter: QPainter,
        center: QPointF,
        index: int,
        item: FanItem,
        step: float,
        tokens: dict,
        scale: float = 1.0,
        alpha: float = 1.0,
    ) -> None:
        hover = self._hover_value(index)
        first = -90.0 + index * step
        angle = first + step / 2.0
        lift = HOVER_LIFT * hover
        radius_in = R_IN * scale + lift
        radius_out = R_OUT * scale + lift
        path = _petal_path(
            radius_in, radius_out, first, first + step,
            GAP_PX * scale / 2.0, CORNER_OUT * scale, CORNER_IN * scale,
        )
        path.translate(center.x(), center.y())

        painter.save()
        painter.setOpacity(alpha)
        painter.setClipPath(path)
        material = self._material_image("petal")
        if material is not None:
            painter.drawImage(self._paint_rect(), material)
        if hover > 0.01:
            fill = QRadialGradient(center, radius_out)
            fill.setColorAt(radius_in / max(1.0, radius_out), theme.qcolor(tokens["hover_inner"]))
            fill.setColorAt(1.0, theme.qcolor(tokens["hover_outer"]))
            painter.setOpacity(alpha * hover)
            painter.fillPath(path, fill)
        painter.setClipping(False)
        painter.setOpacity(alpha * (1.0 - hover))
        painter.strokePath(path, QPen(theme.qcolor(tokens["petal_edge"]), 1.0))
        painter.restore()

        ink = _mix(theme.qcolor(tokens["ink"]), theme.qcolor(tokens["hover_ink"]), hover)
        painter.save()
        painter.setOpacity(alpha)
        if item.kind == "tool":
            self._paint_tool_content(
                painter, center, angle, radius_in, radius_out, item, ink, step, scale
            )
        else:
            self._paint_conversion_content(
                painter, center, angle, radius_in, radius_out, item, ink, step, scale
            )
        painter.restore()

    def _radial_point(
        self, center: QPointF, angle: float, radius: float
    ) -> QPointF:
        radians = math.radians(angle)
        return QPointF(
            center.x() + math.cos(radians) * radius,
            center.y() + math.sin(radians) * radius,
        )

    @staticmethod
    def _chord(radius: float, half_angle: float) -> float:
        return 2.0 * radius * math.sin(math.radians(max(0.0, half_angle)))

    @staticmethod
    def _scaled_font(font: QFont, factor: float) -> QFont:
        """A copy of *font* at *factor* size (petal bloom zoom)."""
        if abs(factor - 1.0) < 0.02:
            return font
        scaled = QFont(font)
        scaled.setPixelSize(max(6, int(round(font.pixelSize() * factor))))
        return scaled

    def _paint_conversion_content(
        self,
        painter: QPainter,
        center: QPointF,
        angle: float,
        radius_in: float,
        radius_out: float,
        item: FanItem,
        ink: QColor,
        step: float,
        scale: float = 1.0,
    ) -> None:
        font, _, _ = self._fonts()
        font = self._scaled_font(font, max(0.62, min(1.0, scale)))
        radius = radius_in + (radius_out - radius_in) * 0.52
        point = self._radial_point(center, angle, radius)
        half = step / 2.0 - GAP_DEG / 2.0
        width = max(24.0, self._chord(radius, half) * 0.92)
        line = max(18.0, font.pixelSize() * 1.35)
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(
            QRectF(point.x() - width / 2.0, point.y() - line / 2.0, width, line),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            item.label.upper(),
        )

    def _paint_tool_content(
        self,
        painter: QPainter,
        center: QPointF,
        angle: float,
        radius_in: float,
        radius_out: float,
        item: FanItem,
        ink: QColor,
        step: float,
        scale: float = 1.0,
    ) -> None:
        _, font, _ = self._fonts()
        factor = max(0.55, min(1.0, scale))
        half = step / 2.0 - GAP_DEG / 2.0
        anchor_radius = (radius_in + radius_out) / 2.0
        anchor = self._radial_point(center, angle, anchor_radius)
        icon_size = ICON_SIZE * factor
        width = max(24.0, self._chord(anchor_radius, half) * 0.86)
        label_font = self._scaled_font(font, factor)
        text = (item.short or item.label).upper()
        metrics = QFontMetricsF(label_font)
        lines = self._wrap(metrics, text, width, 2)
        widest = max(metrics.horizontalAdvance(line) for line in lines)
        if widest > width and label_font.pixelSize() > 7:  # one shrink step, then wrap
            label_font = self._scaled_font(label_font, max(0.62, width / widest))
            metrics = QFontMetricsF(label_font)
            lines = self._wrap(metrics, text, width, 2)
        line_height = max(11.0, label_font.pixelSize() * 1.25)
        block = icon_size + ICON_LABEL_GAP + line_height * len(lines)
        top = anchor.y() - block / 2.0
        paint_icon(
            painter,
            item.key,
            QRectF(
                anchor.x() - icon_size / 2.0,
                top,
                icon_size,
                icon_size,
            ),
            ink,
            2.0,
        )
        painter.setFont(label_font)
        painter.setPen(ink)
        label_top = top + icon_size + ICON_LABEL_GAP
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(
                    anchor.x() - width / 2.0,
                    label_top + index * line_height,
                    width,
                    line_height,
                ),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                line,
            )

    @staticmethod
    def _wrap(metrics: QFontMetricsF, text: str, width: float, limit: int) -> list[str]:
        words = str(text).split()
        if not words:
            return [""]
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if metrics.horizontalAdvance(candidate) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > limit:
            lines = lines[:limit]
            lines[-1] = lines[-1] + "\u2026"
        return lines

    def _refresh_summary(self) -> None:
        """File size (or count) shown in the hub pill when nothing is hovered."""
        files = self._file_paths
        if not files:
            self._summary = ""
            return
        total, sized = 0, False
        for name in files[:2000]:
            try:
                info = os.stat(name)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                sized = True
        size = _human_size(total) if sized else ""
        if len(files) == 1:
            self._summary = size or Path(files[0]).name
        else:
            count = i18n.tr("wheel.files_count", n=len(files))
            self._summary = f"{count} · {size}" if size else count

    def _preview_image(self) -> tuple[QImage, bool] | None:
        key = self._file_paths[0] if self._file_paths else None
        if key != self._preview_key:
            self._preview_key = key
            self._preview = _load_preview(key) if key else None
        return self._preview

    def _paint_hub(self, painter: QPainter, center: QPointF, tokens: dict,
                   scale: float = 1.0, alpha: float = 1.0) -> None:
        """The dragged file's picture in the hub (the rind shows around it)."""
        preview = self._preview_image()
        if preview is None:
            return
        image, photo = preview
        box = PREVIEW_BOX * scale
        if image.width() <= 0 or image.height() <= 0:
            return
        factor = box / max(image.width(), image.height())
        width, height = image.width() * factor, image.height() * factor
        rect = QRectF(center.x() - width / 2.0, center.y() - height / 2.0 - 8.0 * scale,
                      width, height)
        painter.save()
        painter.setOpacity(alpha)
        if photo:
            corner = 9.0 * scale
            painter.setPen(Qt.PenStyle.NoPen)
            for grow, shade in ((4.0, 16), (2.5, 22), (1.2, 30)):
                painter.setBrush(QColor(0, 0, 0, shade))
                painter.drawRoundedRect(rect.adjusted(-grow, -grow + 2.0, grow, grow + 2.0),
                                        corner + grow, corner + grow)
            clip = QPainterPath()
            clip.addRoundedRect(rect, corner, corner)
            painter.setClipPath(clip)
        painter.drawImage(rect, image)
        painter.restore()

    def _paint_capsule(self, painter: QPainter, center: QPointF, tokens: dict,
                       scale: float = 1.0, alpha: float = 1.0) -> None:
        """Frosted pill: the file size, or the hovered petal's name."""
        label = self._capsule_label.upper() if self._capsule_label else ""
        shown = self._capsule_t if label else 0.0
        summary = self._summary
        if not summary and shown <= 0.01:
            return
        _, _, label_font = self._fonts()
        summary_font = self._summary_font
        label_width = QFontMetricsF(label_font).horizontalAdvance(label) + 28.0 if label else 0.0
        summary_width = QFontMetricsF(summary_font).horizontalAdvance(summary) + 28.0 if summary else 0.0
        width = max(CAPSULE_MIN_W, summary_width + (label_width - summary_width) * shown) * scale
        if not summary:
            width = max(CAPSULE_MIN_W, label_width) * scale
        height = CAPSULE_H * scale
        drop = CAPSULE_DROP * scale if self._preview_image() is not None else 0.0
        rect = QRectF(center.x() - width / 2.0, center.y() + drop - height / 2.0, width, height)
        painter.save()
        painter.setOpacity(alpha * (1.0 if summary else min(1.0, shown * 1.8)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60 if self._dark else 30))
        painter.drawRoundedRect(rect.translated(0.0, 1.5 * scale), height / 2.0, height / 2.0)
        painter.setBrush(theme.qcolor(tokens["capsule"]))
        painter.drawRoundedRect(rect, height / 2.0, height / 2.0)
        ink = theme.qcolor(tokens["capsule_ink"])
        painter.setPen(ink)
        if summary and shown < 0.99:
            painter.setOpacity(alpha * (1.0 - shown))
            painter.setFont(self._scaled_font(summary_font, scale))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), summary)
        if label and shown > 0.01:
            slide = CAPSULE_SLIDE * scale * (1.0 - _ease_out(shown))
            painter.setOpacity(alpha * shown)
            painter.setFont(self._scaled_font(label_font, scale))
            painter.drawText(
                rect.translated(self._capsule_dir.x() * slide, self._capsule_dir.y() * slide),
                int(Qt.AlignmentFlag.AlignCenter),
                label,
            )
        painter.restore()
