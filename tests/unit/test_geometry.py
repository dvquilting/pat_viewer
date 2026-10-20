"""Unit tests for arc geometry helpers."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from geometry import arc_sweep, arc_to_path


class TestArcSweep:
    def test_ccw_quarter_circle(self):
        # start 0° end 90° CCW → +90
        assert arc_sweep(0.0, 90.0, ccw=True) == pytest.approx(90.0)

    def test_cw_quarter_circle(self):
        # start 0° end 90° CW → go the long way: -270
        assert arc_sweep(0.0, 90.0, ccw=False) == pytest.approx(-270.0)

    def test_ccw_crossing_zero(self):
        # 350 → 10 CCW = +20
        assert arc_sweep(350.0, 10.0, ccw=True) == pytest.approx(20.0)

    def test_cw_same_ray_is_semicircle(self):
        # start == end → degenerate semicircle, not full circle
        assert arc_sweep(45.0, 45.0, ccw=False) == pytest.approx(-180.0)

    def test_ccw_same_ray_is_semicircle(self):
        assert arc_sweep(45.0, 45.0, ccw=True) == pytest.approx(180.0)

    def test_full_almost_circle_ccw(self):
        # tiny remainder after nearly full rotation
        s = arc_sweep(0.0, 359.0, ccw=True)
        assert 0.0 < s <= 360.0
        assert s == pytest.approx(359.0)

    def test_cw_negative_sweep_bounds(self):
        s = arc_sweep(90.0, 0.0, ccw=False)
        assert -360.0 <= s < 0.0
        assert s == pytest.approx(-90.0)


class TestArcToPath:
    def test_line_fallback_when_radius_zero(self):
        # start == center → r≈0 → straight line
        path = arc_to_path(0, 0, 10, 0, 0, 0, gcode=3)
        assert isinstance(path, QPainterPath)
        assert path.elementCount() >= 2
        last = path.elementAt(path.elementCount() - 1)
        assert last.x == pytest.approx(10.0)
        assert last.y == pytest.approx(0.0)

    def test_ccw_semicircle_ends_at_programmed_point(self):
        # center (0,0), start (1,0) → end (-1,0), G03 CCW upper half
        path = arc_to_path(1, 0, -1, 0, 0, 0, gcode=3, degrees_per_segment=5.0)
        assert path.elementCount() > 2
        last = path.elementAt(path.elementCount() - 1)
        assert last.x == pytest.approx(-1.0)
        assert last.y == pytest.approx(0.0)
        first = path.elementAt(0)
        assert first.x == pytest.approx(1.0)
        assert first.y == pytest.approx(0.0)

    def test_cw_arc_g02(self):
        path = arc_to_path(1, 0, 0, 1, 0, 0, gcode=2, degrees_per_segment=10.0)
        last = path.elementAt(path.elementCount() - 1)
        assert last.x == pytest.approx(0.0)
        assert last.y == pytest.approx(1.0)

    def test_absolute_ij_center(self):
        # Doc example: (0,2)->(0,4) I=0 J=3, G03, center=(0,3) r=1
        path = arc_to_path(0, 2, 0, 4, 0, 3, gcode=3, degrees_per_segment=5.0)
        last = path.elementAt(path.elementCount() - 1)
        assert last.x == pytest.approx(0.0)
        assert last.y == pytest.approx(4.0)
        # Midpoint of upper semicircle should be near x=+1 or x=-1 depending on dir
        # G03 from south to north around (0,3): start angle -90°, end +90° → CCW sweep 180
        # Mid angle 0° → point (1, 3)
        mid_idx = path.elementCount() // 2
        mid = path.elementAt(mid_idx)
        assert mid.x == pytest.approx(1.0, abs=0.15)
