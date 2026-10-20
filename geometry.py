"""
Arc-drawing geometry shared by the live canvas and the thumbnail renderer.
Converts a PAT G02/G03 arc block (absolute I/J center, Y-up convention)
into a QPainterPath, and computes signed arc sweep angles.
"""

import cmath
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


def sample_arc(x0: float, y0: float,
               x1: float, y1: float,
               ci: float, cj: float,
               gcode: int,
               degrees_per_segment: float = 2.0,
               tolerance: float = 0.0) -> list[tuple[float, float]]:
    """
    Points along one PAT arc block, from (x0, y0) to exactly (x1, y1).

    Arc center: (ci, cj) -- I/J are absolute coordinates from the origin,
                            NOT relative to the arc start point.
    Verified: N12 G03 (0,2)->(0,4) I=0 J=3 -> center=(0,3) r=1.0 ✓
              (if relative to start: center=(0,5) r=3.0 ✗)

    Arcs are sampled with line segments rather than Qt's arcTo, which loses
    precision on very large radii (e.g. r>100 PAT units with a tiny sweep)
    and can miss the programmed endpoint, producing a visible jog at the
    junction with the next move.
    """
    r = math.hypot(x0 - ci, y0 - cj)
    if r < 1e-9:
        return [(x0, y0), (x1, y1)]

    a0 = math.atan2(y0 - cj, x0 - ci)
    end_deg = math.degrees(math.atan2(y1 - cj, x1 - ci))
    sweep_deg = arc_sweep(math.degrees(a0), end_deg, ccw=(gcode == 3))
    sweep_rad = math.radians(sweep_deg)

    # Sample the arc in degree steps (2° for the canvas, coarser for thumbnails).
    # With a tolerance, small arcs use longer steps as long as no segment
    # strays more than `tolerance` from the true arc.
    step_deg = degrees_per_segment
    if 0.0 < tolerance < r:
        step_deg = max(step_deg, math.degrees(2.0 * math.acos(1.0 - tolerance / r)))
    # Rotating a complex offset by a fixed step needs one multiply per point
    # instead of a cos and a sin.
    n = max(4, int(abs(sweep_deg) / step_deg) + 1)
    step = cmath.rect(1.0, sweep_rad / n)
    offset = complex(x0 - ci, y0 - cj)
    points = [(x0, y0)]
    append = points.append
    for _ in range(1, n):
        offset *= step
        append((ci + offset.real, cj + offset.imag))
    points.append((x1, y1))
    return points


def arc_to_path(x0: float, y0: float,
                x1: float, y1: float,
                ci: float, cj: float,
                gcode: int,
                degrees_per_segment: float = 2.0) -> QPainterPath:
    """Build a QPainterPath for one PAT arc block (see sample_arc)."""
    points = sample_arc(x0, y0, x1, y1, ci, cj, gcode, degrees_per_segment)
    path = QPainterPath(QPointF(*points[0]))
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path
