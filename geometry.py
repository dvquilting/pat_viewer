"""
Arc-drawing geometry shared by the live canvas and the thumbnail renderer.
Converts a PAT G02/G03 arc block (absolute I/J center, Y-up convention)
into a QPainterPath, and computes signed arc sweep angles.
"""

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

def arc_sweep(start_deg: float, end_deg: float, ccw: bool) -> float:
    """
    Compute the signed arc sweep in degrees.

    For CCW (G03): returns a value in (0, 360] — positive.
    For CW  (G02): returns a value in [-360, 0) — negative.

    When start == end (degenerate — both points on same ray from center),
    returns ±180 rather than ±360, because the PAT machine always produces
    a semicircle in this case, never a full circle.
    """
    diff = end_deg - start_deg

    if ccw:
        # Normalise to (0, 360]
        sweep = diff % 360.0
        if sweep == 0.0:
            sweep = 180.0   # degenerate: semicircle, not full circle
    else:
        # Normalise to [-360, 0)
        sweep = -((-diff) % 360.0)
        if sweep == 0.0:
            sweep = -180.0  # degenerate: semicircle, not full circle

    return sweep


def arc_to_path(x0: float, y0: float,
                x1: float, y1: float,
                ci: float, cj: float,
                gcode: int,
                degrees_per_segment: float = 2.0) -> QPainterPath:
    """
    Build a QPainterPath for one PAT arc block.

    Arc center: (ci, cj) -- I/J are absolute coordinates from the origin,
                            NOT relative to the arc start point.
    Verified: N12 G03 (0,2)->(0,4) I=0 J=3 -> center=(0,3) r=1.0 ✓
              (if relative to start: center=(0,5) r=3.0 ✗)

    Arcs are sampled in PAT Y-up space with line segments.  Qt's arcTo
    loses precision on very large radii (e.g. r>100 PAT units with a tiny
    sweep) and can miss the programmed endpoint, producing a visible jog
    at the junction with the next move.
    """
    cx = ci
    cy = cj
    r  = math.hypot(x0 - cx, y0 - cy)

    if r < 1e-9:
        path = QPainterPath(QPointF(x0, y0))
        path.lineTo(x1, y1)
        return path

    start_deg = math.degrees(math.atan2(y0 - cy, x0 - cx))
    end_deg   = math.degrees(math.atan2(y1 - cy, x1 - cx))
    sweep_deg = arc_sweep(start_deg, end_deg, ccw=(gcode == 3))
    sweep_rad = math.radians(sweep_deg)

    # Sample the arc in degree steps (2° for the canvas, coarser for thumbnails).
    n = max(4, int(abs(sweep_deg) / degrees_per_segment) + 1)
    a0 = math.atan2(y0 - cy, x0 - cx)
    path = QPainterPath(QPointF(x0, y0))
    for i in range(1, n):
        t = i / n
        ang = a0 + sweep_rad * t
        path.lineTo(cx + r * math.cos(ang), cy + r * math.sin(ang))
    path.lineTo(x1, y1)
    return path
