"""
The main pattern-viewing canvas: renders a loaded PatFile with pan/zoom,
optional travel-move display, start/end markers, and a user-adjustable
constant-on-screen-pixel line width.

Rendering is built for large patterns:
  * The pattern is drawn as short polylines ("runs") with bounding boxes, so
    runs outside the view are skipped. Short runs also keep wide pens
    affordable: Qt strokes cosmetic pens wider than 1 px with a general
    stroker that slows down sharply on long self-overlapping polylines.
  * The last rendering is kept as an image. While the user drags or zooms it
    is moved or scaled, and redrawn properly once they pause. Patterns that
    are slow to draw are redrawn on a worker thread.
"""

import time
from typing import Optional

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import (
    Qt, QPointF, QRectF, QSize, QTimer, QObject, QRunnable, QThreadPool, Signal,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QColor, QTransform, QImage,
    QWheelEvent, QMouseEvent, QKeyEvent, QFont,
)

from models import PatFile
from geometry import sample_arc

# Points per run: small enough that culling skips most of a zoomed-in pattern.
MAX_RUN_POINTS = 64
# Patterns slower than this to draw are redrawn on a worker thread, and moved
# as an image while the user drags or zooms.
LIVE_RENDER_BUDGET_MS = 12.0
SETTLE_DELAY_MS = 120
WHEEL_STEP = 1.15
# Arcs are drawn with segments that stray at most this far from the true arc
# (0.0001 PAT units ≈ 2.5 µm), and at most 2° each. Small arcs need far fewer
# points than a fixed 2° step gives them.
ARC_TOLERANCE = 1e-4


class Run:
    """
    A continuous stretch of stitches or travel, in PAT coordinates (y up),
    held as a QPainterPath of line segments: building it with lineTo(x, y)
    is several times faster in Python than creating a QPointF per point.
    """
    __slots__ = ("path", "bounds", "travel")

    def __init__(self, path: QPainterPath, travel: bool):
        self.path = path
        self.travel = travel
        # Padded: QRectF.intersects() is False for the zero-height or
        # zero-width rectangle of a straight run.
        self.bounds = path.controlPointRect().adjusted(-1e-6, -1e-6, 1e-6, 1e-6)


def build_runs(pat: Optional[PatFile], arc_degrees: float = 2.0) -> list:
    """
    Split a pattern into short polylines. Pure computation on value types, so
    it can run on a worker thread (the file loader and thumbnailer do this).
    """
    runs: list = []
    if not pat or not pat.moves:
        return runs
    path = None
    count = 0             # points in the current run
    travel = False
    end = None            # where the current run ends
    cur_x = cur_y = 0.0
    for mv in pat.moves:
        gcode = mv.gcode
        if gcode == 0 or gcode == 1:
            kind = gcode == 0
            segment = ((mv.x, mv.y),)
        elif gcode in (2, 3) and mv.i is not None and mv.j is not None:
            kind = False
            segment = sample_arc(cur_x, cur_y, mv.x, mv.y, mv.i, mv.j, gcode,
                                 arc_degrees, ARC_TOLERANCE)[1:]
        else:
            cur_x, cur_y = mv.x, mv.y
            continue
        # Continue the current run when this move starts where it ended.
        if path is None or kind != travel or end != (cur_x, cur_y):
            if count > 1:
                runs.append(Run(path, travel))
            path = QPainterPath()
            path.moveTo(cur_x, cur_y)
            count = 1
            travel = kind
        line_to = path.lineTo
        for x, y in segment:
            line_to(x, y)
            count += 1
            if count >= MAX_RUN_POINTS:
                runs.append(Run(path, travel))
                path = QPainterPath()
                path.moveTo(x, y)
                line_to = path.lineTo
                count = 1
        cur_x, cur_y = mv.x, mv.y
        end = (cur_x, cur_y)
    if count > 1:
        runs.append(Run(path, travel))
    return runs


def render_runs(runs: list, view: QTransform, size: QSize, ratio: float,
                background: QColor, color: QColor, travel_color: QColor,
                line_width: float, show_travel: bool) -> QImage:
    """Draw runs into a new image. Thread-safe: also runs on worker threads."""
    image = QImage(size, QImage.Format_RGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(background)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setTransform(view)
    scale = max(abs(view.m11()), 1e-12)
    margin = (line_width + 2.0) / scale
    inverse, _ = view.inverted()
    visible = inverse.mapRect(QRectF(0, 0, size.width() / ratio, size.height() / ratio))
    visible.adjust(-margin, -margin, margin, margin)
    passes = []
    if show_travel:
        pen = QPen(travel_color, line_width, Qt.DashLine)
        pen.setCosmetic(True)
        passes.append((True, pen))
    pen = QPen(color, line_width)
    pen.setCosmetic(True)
    passes.append((False, pen))
    for travel, pen in passes:
        painter.setPen(pen)
        for run in runs:
            if run.travel == travel and run.bounds.intersects(visible):
                painter.drawPath(run.path)
    painter.end()
    return image


class _RenderSignals(QObject):
    done = Signal(object, object, int, int, float)   # image, view, pattern, style, ms


class _RenderTask(QRunnable):
    def __init__(self, signals, args, view, pattern_rev, style_rev):
        super().__init__()
        self._signals = signals
        self._args = args
        self._view = view
        self._pattern_rev = pattern_rev
        self._style_rev = style_rev

    def run(self):
        start = time.perf_counter()
        image = render_runs(*self._args)
        ms = (time.perf_counter() - start) * 1000
        try:
            self._signals.done.emit(image, self._view, self._pattern_rev,
                                    self._style_rev, ms)
        except RuntimeError:
            pass   # canvas already destroyed


class PatCanvas(QWidget):
    TRAVEL_COLOR = QColor(130, 130, 190, 200)
    DEFAULT_STITCH_COLOR = QColor(0, 0, 0)
    START_COLOR  = QColor(0,   200,   0)
    END_COLOR    = QColor(220,  50,  50)
    BACKGROUND   = QColor(245, 245, 250)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pat: Optional[PatFile] = None
        self._runs: list = []
        self._bounds      = QRectF()
        self._scale       = 1.0
        self._offset      = QPointF(0, 0)
        self._drag_start: Optional[QPointF] = None
        self._drag_offset = QPointF(0, 0)
        self._user_view   = False   # zoomed/panned: resizing keeps the view
        self._show_travel = False
        self._line_width  = 100.0
        self._stitch_color = QColor(self.DEFAULT_STITCH_COLOR)
        self.show_markers = False

        # The last rendering and what it was drawn for.
        self._pattern_rev = 0
        self._style_rev = 0
        self._cache: Optional[QImage] = None
        self._cache_view = QTransform()
        self._cache_pattern_rev = -1
        self._cache_style_rev = -1
        self._interacting = False
        self._render_in_flight = False
        self._last_render_ms = 0.0
        self._render_signals = _RenderSignals(self)
        self._render_signals.done.connect(self._on_render_done, Qt.QueuedConnection)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(SETTLE_DELAY_MS)
        self._settle_timer.timeout.connect(self._settle)

        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    # ------------------------------------------------------------------ style

    def _style_changed(self):
        self._style_rev += 1
        self.update()

    @property
    def show_travel(self) -> bool:
        return self._show_travel

    @show_travel.setter
    def show_travel(self, value: bool):
        self._show_travel = bool(value)
        self._update_bounds()
        self._style_changed()

    @property
    def line_width(self) -> float:
        return self._line_width

    @line_width.setter
    def line_width(self, value: float):
        self._line_width = float(value)
        self._style_changed()

    @property
    def stitch_color(self) -> QColor:
        return self._stitch_color

    @stitch_color.setter
    def stitch_color(self, value: QColor):
        self._stitch_color = QColor(value)
        self._style_changed()

    # ------------------------------------------------------------------ load

    def load(self, pat: PatFile, runs: Optional[list] = None):
        """Show a pattern; `runs` may be prebuilt with build_runs() off the GUI thread."""
        self.pat = pat
        self._runs = runs if runs is not None else build_runs(pat)
        self._pattern_rev += 1
        self._update_bounds()
        self._fit()
        self.update()

    def _build_paths(self):
        """Rebuild the drawing data (kept for callers of the old API)."""
        self._runs = build_runs(self.pat)
        self._pattern_rev += 1
        self._update_bounds()
        self.update()

    def _update_bounds(self):
        bounds = QRectF()
        for run in self._runs:
            if run.travel and not self._show_travel:
                continue
            bounds = run.bounds if bounds.isNull() else bounds.united(run.bounds)
        self._bounds = bounds

    def _fit(self):
        self._user_view = False
        if self._bounds.isEmpty():
            return
        w, h = self.width(), self.height()
        if w < 10 or h < 10:
            return
        margin = 40
        bw = self._bounds.width()
        bh = self._bounds.height()
        sx = (w - 2 * margin) / bw if bw > 0 else 1.0
        sy = (h - 2 * margin) / bh if bh > 0 else 1.0
        self._scale = min(sx, sy)

        # After scale(s,-s) transform:
        #   screen_x = offset_x + pat_x * s
        #   screen_y = offset_y - pat_y * s
        # We want the PAT bounds center to map to screen center:
        bcx = self._bounds.center().x()
        bcy = self._bounds.center().y()
        self._offset = QPointF(
            w / 2.0 - bcx * self._scale,
            h / 2.0 + bcy * self._scale
        )
        self._drag_offset = QPointF(0, 0)

    def resizeEvent(self, event):
        # Keep the pattern fitted until the user has zoomed or panned.
        if not self._user_view:
            self._fit()
        self.update()

    # ----------------------------------------------------------------- draw

    def _view_transform(self) -> QTransform:
        t = QTransform()
        t.translate(self._offset.x() + self._drag_offset.x(),
                    self._offset.y() + self._drag_offset.y())
        t.scale(self._scale, -self._scale)   # Y-up PAT → Y-down screen
        return t

    def _pixels(self) -> QSize:
        ratio = self.devicePixelRatioF()
        return QSize(round(self.width() * ratio), round(self.height() * ratio))

    def _render_args(self, view: QTransform):
        # Cosmetic pens draw at a constant on-screen width; the slider's
        # 10-400 range maps to 0.1-4.0 px regardless of zoom or file size.
        return (self._runs, view, self._pixels(), self.devicePixelRatioF(),
                self.BACKGROUND, self._stitch_color, self.TRAVEL_COLOR,
                self._line_width / 100.0, self._show_travel)

    def _render_now(self, view: QTransform):
        start = time.perf_counter()
        self._cache = render_runs(*self._render_args(view))
        self._last_render_ms = (time.perf_counter() - start) * 1000
        self._cache_view = view
        self._cache_pattern_rev = self._pattern_rev
        self._cache_style_rev = self._style_rev

    def _request_render(self, view: QTransform):
        # One render at a time; when it lands, paintEvent asks again if the
        # view or style changed meanwhile.
        if self._render_in_flight:
            return
        self._render_in_flight = True
        QThreadPool.globalInstance().start(_RenderTask(
            self._render_signals, self._render_args(view), view,
            self._pattern_rev, self._style_rev))

    def _on_render_done(self, image, view, pattern_rev, style_rev, ms):
        self._render_in_flight = False
        if pattern_rev == self._pattern_rev:
            self._cache = image
            self._cache_view = view
            self._cache_pattern_rev = pattern_rev
            self._cache_style_rev = style_rev
            self._last_render_ms = ms
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.pat or not self.pat.moves:
            painter.fillRect(self.rect(), self.BACKGROUND)
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Arial", 14))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Open a .PAT file to view it")
            return

        view = self._view_transform()
        same_pattern = (self._cache is not None
                        and self._cache_pattern_rev == self._pattern_rev)
        current = (same_pattern and self._cache_style_rev == self._style_rev
                   and self._cache_view == view and self._cache.size() == self._pixels())
        if current:
            painter.drawImage(QPointF(0, 0), self._cache)
        elif same_pattern and (self._interacting
                               or self._last_render_ms > LIVE_RENDER_BUDGET_MS):
            # Show the last rendering moved or scaled to the current view.
            painter.fillRect(self.rect(), self.BACKGROUND)
            inverse, _ = self._cache_view.inverted()
            painter.setTransform(inverse * view)
            painter.drawImage(QPointF(0, 0), self._cache)
            painter.resetTransform()
            if not self._interacting:
                self._request_render(view)
        else:
            self._render_now(view)
            painter.drawImage(QPointF(0, 0), self._cache)

        # Start / end markers
        if self.show_markers:
            painter.setRenderHint(QPainter.Antialiasing)
            first = self.pat.moves[0]
            last  = self.pat.moves[-1]
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.START_COLOR)
            painter.drawEllipse(view.map(QPointF(first.x, first.y)), 6.0, 6.0)
            painter.setBrush(self.END_COLOR)
            painter.drawEllipse(view.map(QPointF(last.x, last.y)), 6.0, 6.0)

    # ----------------------------------------------------------------- interaction

    def _begin_interaction(self):
        self._user_view = True
        self._interacting = True
        self._settle_timer.start()

    def _settle(self):
        self._interacting = False
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:   # horizontal scrolling
            event.ignore()
            return
        self._begin_interaction()
        # Proportional to the wheel delta, so high-resolution touchpads that
        # send many small steps zoom smoothly rather than 15% per step.
        factor = WHEEL_STEP ** (delta / 120.0)
        pos = event.position()
        ox = self._offset.x() + self._drag_offset.x()
        oy = self._offset.y() + self._drag_offset.y()
        self._offset = QPointF(
            pos.x() - (pos.x() - ox) * factor,
            pos.y() - (pos.y() - oy) * factor,
        )
        self._drag_offset = QPointF(0, 0)
        self._scale *= factor
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start is not None:
            self._begin_interaction()
            d = event.position() - self._drag_start
            self._drag_offset = QPointF(d.x(), d.y())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self._drag_start is not None:
            self._offset += self._drag_offset
            self._drag_offset = QPointF(0, 0)
            self._drag_start = None
            self.setCursor(Qt.CrossCursor)
            self._settle_timer.stop()
            self._settle()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_F, Qt.Key_Home):
            self._fit()
            self.update()
        elif event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self._zoom(1.2)
        elif event.key() == Qt.Key_Minus:
            self._zoom(1 / 1.2)

    def _zoom(self, factor: float):
        self._user_view = True
        cx, cy = self.width() / 2, self.height() / 2
        ox = self._offset.x() + self._drag_offset.x()
        oy = self._offset.y() + self._drag_offset.y()
        self._offset = QPointF(
            cx - (cx - ox) * factor,
            cy - (cy - oy) * factor
        )
        self._drag_offset = QPointF(0, 0)
        self._scale *= factor
        self.update()
