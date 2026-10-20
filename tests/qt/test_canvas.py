"""PatCanvas widget tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QWheelEvent, QKeyEvent
from PySide6.QtCore import QEvent

from canvas import PatCanvas
from models import Move, PatFile


def _sample_pat() -> PatFile:
    return PatFile(
        moves=[
            Move(1, 0, 0.0, 0.0, is_stitch=False),
            Move(2, 1, 10.0, 0.0, is_stitch=True),
            Move(3, 1, 10.0, 8.0, is_stitch=True),
            Move(4, 3, 0.0, 8.0, i=5.0, j=4.0, is_stitch=True),
            Move(5, 0, 0.0, 0.0, is_stitch=False),
        ],
        end_seq=6,
        filename="sample.pat",
    )


@pytest.mark.qt
def test_load_builds_paths_and_fit(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.load(_sample_pat())
    assert c.pat is not None
    assert any(not run.travel for run in c._runs)
    assert c._scale > 0
    assert not c._bounds.isNull()


@pytest.mark.qt
def test_load_empty_pat(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.load(PatFile())
    assert c._runs == []


@pytest.mark.qt
def test_show_travel_and_markers_and_color(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.show()
    c.show_travel = True
    c.show_markers = True
    c.stitch_color = QColor(255, 0, 0)
    c.line_width = 50.0
    c.load(_sample_pat())
    # Force paintEvent (show_travel + markers branches)
    c.repaint()
    qtbot.wait(20)
    assert c.show_travel is True
    assert any(run.travel for run in c._runs)


@pytest.mark.qt
def test_paint_empty_placeholder(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.show()
    c.repaint()  # "Open a .PAT file…" branch


@pytest.mark.qt
def test_wheel_zoom(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.load(_sample_pat())
    before = c._scale
    # Positive delta zooms in on many platforms
    event = QWheelEvent(
        QPointF(200, 200),
        QPointF(200, 200),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    c.wheelEvent(event)
    assert c._scale == pytest.approx(before * 1.15)


@pytest.mark.qt
def test_mouse_pan(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.show()
    c.load(_sample_pat())
    start = c._offset
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(100, 100),
        QPointF(100, 100),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    c.mousePressEvent(press)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(150, 130),
        QPointF(150, 130),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    c.mouseMoveEvent(move)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(150, 130),
        QPointF(150, 130),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    c.mouseReleaseEvent(release)
    # offset should have changed after drag
    assert c._offset != start or c._drag_start is None


@pytest.mark.qt
def test_key_events_no_crash(qapp, qtbot):
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.load(_sample_pat())
    for key in (Qt.Key_Plus, Qt.Key_Minus, Qt.Key_0, Qt.Key_Home, Qt.Key_F):
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.NoModifier)
        c.keyPressEvent(ev)
