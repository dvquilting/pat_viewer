"""
The main pattern-viewing canvas: renders a loaded PatFile with pan/zoom,
optional travel-move display, start/end markers, and a user-adjustable
constant-on-screen-pixel line width.
"""

from typing import Optional

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QPainterPath, QTransform,
    QWheelEvent, QMouseEvent, QKeyEvent, QFont,
)

from models import PatFile
from geometry import arc_to_path

class PatCanvas(QWidget):
    TRAVEL_COLOR = QColor(130, 130, 190, 200)
    DEFAULT_STITCH_COLOR = QColor(0, 0, 0)
    START_COLOR  = QColor(0,   200,   0)
    END_COLOR    = QColor(220,  50,  50)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pat: Optional[PatFile] = None
        self._stitch_path = QPainterPath()
        self._travel_path = QPainterPath()
        self._bounds      = QRectF()
        self._scale       = 1.0
        self._offset      = QPointF(0, 0)
        self._drag_start: Optional[QPointF] = None
        self._drag_offset = QPointF(0, 0)
        self.show_travel  = False
        self.show_markers = False
        self.line_width   = 100.0
        self.stitch_color = QColor(self.DEFAULT_STITCH_COLOR)   # user-changeable

        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------------ load

    def load(self, pat: PatFile):
        self.pat = pat
        self._build_paths()
        self._fit()
        self.update()

    def _build_paths(self):
        self._stitch_path = QPainterPath()
        self._travel_path = QPainterPath()
        if not self.pat or not self.pat.moves:
            return

        cur_x = cur_y = 0.0

        for mv in self.pat.moves:
            tx, ty = mv.x, mv.y

            if mv.gcode == 0:
                self._travel_path.moveTo(cur_x, cur_y)
                self._travel_path.lineTo(tx, ty)

            elif mv.gcode == 1:
                self._stitch_path.moveTo(cur_x, cur_y)
                self._stitch_path.lineTo(tx, ty)

            elif mv.gcode in (2, 3) and mv.i is not None and mv.j is not None:
                arc = arc_to_path(cur_x, cur_y, tx, ty,
                                   mv.i, mv.j, mv.gcode)
                self._stitch_path.addPath(arc)

            cur_x, cur_y = tx, ty

        # Bounding box
        self._bounds = self._stitch_path.boundingRect()
        if self.show_travel and not self._travel_path.isEmpty():
            self._bounds = self._bounds.united(self._travel_path.boundingRect())

    def _fit(self):
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
        self._fit()
        self.update()

    # ----------------------------------------------------------------- draw

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(245, 245, 250))

        if not self.pat or not self.pat.moves:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Arial", 14))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Open a .PAT file to view it")
            return

        ox = self._offset.x() + self._drag_offset.x()
        oy = self._offset.y() + self._drag_offset.y()

        t = QTransform()
        t.translate(ox, oy)
        t.scale(self._scale, -self._scale)   # Y-up PAT → Y-down screen
        painter.setTransform(t)

        # Cosmetic pens draw at a literal, constant on-screen pixel width
        # regardless of the paint transform. Previously this divided by
        # self._scale, which meant the line got THINNER as you zoomed in
        # (scale grows) and THICKER as you zoomed out — backwards — and
        # also made line thickness inconsistent between files of different
        # physical sizes (a huge pattern and a tiny pattern both "fit" to
        # the canvas end up with very different self._scale values).
        # Using a fixed divisor instead makes the line width depend only on
        # the slider, staying constant across zoom levels and across files.
        lw = self.line_width / 100.0   # slider range 10-400 → 0.1-4.0 px

        if self.show_travel and not self._travel_path.isEmpty():
            pen = QPen(self.TRAVEL_COLOR, lw, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(self._travel_path)

        pen = QPen(self.stitch_color, lw)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPath(self._stitch_path)

        # Start / end markers
        if self.show_markers:
            dot_r = 6.0 / self._scale
            first = self.pat.moves[0]
            last  = self.pat.moves[-1]
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.START_COLOR)
            painter.drawEllipse(QPointF(first.x, first.y), dot_r, dot_r)
            painter.setBrush(self.END_COLOR)
            painter.drawEllipse(QPointF(last.x, last.y), dot_r, dot_r)

    # ----------------------------------------------------------------- interaction

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
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
            d = event.position() - self._drag_start
            self._drag_offset = QPointF(d.x(), d.y())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self._drag_start is not None:
            self._offset += self._drag_offset
            self._drag_offset = QPointF(0, 0)
            self._drag_start = None
            self.setCursor(Qt.CrossCursor)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_F, Qt.Key_Home):
            self._fit()
            self.update()
        elif event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self._zoom(1.2)
        elif event.key() == Qt.Key_Minus:
            self._zoom(1 / 1.2)

    def _zoom(self, factor: float):
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
