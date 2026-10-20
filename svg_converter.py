"""
SVG -> PAT converter.

Pure Python (a port of the C++ viewer's importer); no third-party packages.

Conversion strategy
-------------------
SVG paths support line, arc, and Bézier segments. PAT only supports
straight lines (G01) and circular arcs (G02/G03), so:

  SVG Line / H / V / Z (close) → G01 straight stitch
  SVG Arc (A)                  → G02 or G03 circular arc
                                 (elliptic arcs are flattened to lines)
  SVG Cubic/Quadratic Bézier   → adaptively flattened to G01 lines

Besides <path>, the basic shapes (line, polyline, polygon, rect, circle,
ellipse) are imported. Transforms and the stroke / fill / display properties
(also from style="...") are inherited from enclosing groups. Content of
defs, clipPath, mask, symbol, marker, pattern and text is not drawn.

Coordinate system
-----------------
Transforms are applied, so coordinates are in SVG user space (y down). The
SVG viewBox (or width×height) is read to determine physical dimensions. If
the SVG has explicit mm/in/cm sizes, those set the PAT scale (1 PAT unit ≈ 1
inch); otherwise the pattern is normalised so its largest dimension is 12
PAT units (≈ 12 inches). The Y axis is flipped (SVG Y-down → PAT Y-up) and
the pattern is translated so its bottom-left corner sits at the PAT origin.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from geometry import sample_arc
from models import Move, PatFile
from pat_writer import _write_pat_file

_TRAVEL, _STITCH, _ARC = 0, 1, 2


class _Raw:
    """One travel, stitch or circular-arc segment in SVG coordinates."""
    __slots__ = ("kind", "sx", "sy", "ex", "ey", "cx", "cy", "positive")

    def __init__(self, kind, sx, sy, ex, ey, cx=0.0, cy=0.0, positive=False):
        self.kind = kind
        self.sx, self.sy = sx, sy
        self.ex, self.ey = ex, ey
        self.cx, self.cy = cx, cy
        # Arcs only: sweeps towards increasing angle in SVG coordinates
        # (y down), which looks clockwise on screen.
        self.positive = positive


# ------------------------------------------------------------------ path data

_SEPARATORS = re.compile(r"[\s,]*")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


class _PathTokens:
    def __init__(self, text: str):
        self._text = text
        self._pos = 0

    def _skip(self):
        self._pos = _SEPARATORS.match(self._text, self._pos).end()

    def empty(self) -> bool:
        self._skip()
        return self._pos >= len(self._text)

    def command_ahead(self) -> bool:
        self._skip()
        return self._pos < len(self._text) and self._text[self._pos].isalpha()

    def command(self) -> str:
        if not self.command_ahead():
            raise ValueError("Expected SVG path command")
        self._pos += 1
        return self._text[self._pos - 1]

    def number(self) -> float:
        self._skip()
        match = _NUMBER.match(self._text, self._pos)
        if match is None:
            raise ValueError("Invalid or missing SVG path number")
        self._pos = match.end()
        return float(match.group())

    def flag(self) -> bool:
        """Arc flags are single characters, so "0110" is two flags then 10."""
        self._skip()
        if self._pos < len(self._text) and self._text[self._pos] in "01":
            self._pos += 1
            return self._text[self._pos - 1] == "1"
        raise ValueError("Invalid SVG arc flag")


def _point_line_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return math.hypot(px - ax, py - ay)
    return abs(dx * (ay - py) - (ax - px) * dy) / length


def _flatten_cubic(p0, p1, p2, p3, tol, depth, out):
    flatness = max(_point_line_distance(*p1, *p0, *p3), _point_line_distance(*p2, *p0, *p3))
    if depth >= 12 or flatness <= tol:
        out.append(p3)
        return
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    a, b, d = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    e, f = mid(a, b), mid(b, d)
    m = mid(e, f)
    _flatten_cubic(p0, a, e, m, tol, depth + 1, out)
    _flatten_cubic(m, f, d, p3, tol, depth + 1, out)


def _flatten_quad(p0, p1, p2, tol, depth, out):
    if depth >= 12 or _point_line_distance(*p1, *p0, *p2) <= tol:
        out.append(p2)
        return
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    a, b = mid(p0, p1), mid(p1, p2)
    m = mid(a, b)
    _flatten_quad(p0, a, m, tol, depth + 1, out)
    _flatten_quad(m, b, p2, tol, depth + 1, out)


def _endpoint_arc(p0, p1, rx, ry, rotation_deg, large, sweep):
    """SVG endpoint arc → (cx, cy, rx, ry, phi, start, delta), or None."""
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12 or math.hypot(p0[0] - p1[0], p0[1] - p1[1]) < 1e-12:
        return None
    phi = math.radians(rotation_deg)
    cs, sn = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    xp, yp = cs * dx + sn * dy, -sn * dx + cs * dy
    lam = xp * xp / (rx * rx) + yp * yp / (ry * ry)
    if lam > 1.0:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    numerator = max(0.0, rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp)
    denominator = rx * rx * yp * yp + ry * ry * xp * xp
    factor = math.sqrt(numerator / denominator) if denominator > 0 else 0.0
    if large == sweep:
        factor = -factor
    cxp, cyp = factor * rx * yp / ry, factor * -ry * xp / rx
    cx = cs * cxp - sn * cyp + (p0[0] + p1[0]) / 2.0
    cy = sn * cxp + cs * cyp + (p0[1] + p1[1]) / 2.0

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (xp - cxp) / rx, (yp - cyp) / ry
    vx, vy = (-xp - cxp) / rx, (-yp - cyp) / ry
    start = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    if sweep and delta < 0:
        delta += 2 * math.pi
    return cx, cy, rx, ry, phi, start, delta


def _arc_point(arc, t):
    cx, cy, rx, ry, phi, start, delta = arc
    theta = start + delta * t
    x, y = rx * math.cos(theta), ry * math.sin(theta)
    return (cx + math.cos(phi) * x - math.sin(phi) * y,
            cy + math.sin(phi) * x + math.cos(phi) * y)


def _parse_path(data: str, tol: float, raw: list):
    tokens = _PathTokens(data)
    cur = (0.0, 0.0)
    subpath = (0.0, 0.0)
    last_cubic = last_quad = (0.0, 0.0)
    cmd = previous = ""

    def point(relative):
        p = (tokens.number(), tokens.number())
        return (cur[0] + p[0], cur[1] + p[1]) if relative else p

    def stitch(to):
        nonlocal cur
        if math.hypot(to[0] - cur[0], to[1] - cur[1]) > 1e-12:
            raw.append(_Raw(_STITCH, cur[0], cur[1], to[0], to[1]))
        cur = to

    while not tokens.empty():
        if tokens.command_ahead():
            cmd = tokens.command()
        elif not cmd:
            raise ValueError("SVG path begins without a command")
        relative = cmd.islower()
        op = cmd.upper()
        if op == "Z":
            stitch(subpath)
            previous, cmd = op, ""
            continue
        if op == "M":
            cur = point(relative)
            subpath = cur
            raw.append(_Raw(_TRAVEL, 0.0, 0.0, cur[0], cur[1]))
            cmd = "l" if relative else "L"   # further pairs are line-tos
            previous = "M"
            continue
        if op == "L":
            stitch(point(relative))
        elif op == "H":
            x = tokens.number()
            stitch((cur[0] + x if relative else x, cur[1]))
        elif op == "V":
            y = tokens.number()
            stitch((cur[0], cur[1] + y if relative else y))
        elif op in ("C", "S"):
            if op == "C":
                c1 = point(relative)
            else:
                c1 = ((2 * cur[0] - last_cubic[0], 2 * cur[1] - last_cubic[1])
                      if previous in ("C", "S") else cur)
            c2, end = point(relative), point(relative)
            points: list = []
            _flatten_cubic(cur, c1, c2, end, tol, 0, points)
            for p in points:
                stitch(p)
            last_cubic = c2
        elif op in ("Q", "T"):
            if op == "Q":
                control = point(relative)
            else:
                control = ((2 * cur[0] - last_quad[0], 2 * cur[1] - last_quad[1])
                           if previous in ("Q", "T") else cur)
            end = point(relative)
            points = []
            _flatten_quad(cur, control, end, tol, 0, points)
            for p in points:
                stitch(p)
            last_quad = control
        elif op == "A":
            rx, ry, rotation = tokens.number(), tokens.number(), tokens.number()
            large, sweep = tokens.flag(), tokens.flag()
            end = point(relative)
            arc = _endpoint_arc(cur, end, rx, ry, rotation, large, sweep)
            if arc is None:
                stitch(end)
            elif abs(arc[2] - arc[3]) <= 0.02 * max(arc[2], arc[3]):
                # Circular (within 2%): keep as a true arc.
                raw.append(_Raw(_ARC, cur[0], cur[1], end[0], end[1], arc[0], arc[1], sweep))
                cur = end
            else:
                segments = max(16, math.ceil(abs(arc[6]) * 8.0))
                for i in range(1, segments + 1):
                    stitch(end if i == segments else _arc_point(arc, i / segments))
        else:
            raise ValueError(f"Unsupported SVG path command: {op}")
        previous = op


# ------------------------------------------------------------------ attributes

_DIMENSION = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z%]*)\s*$")
_UNITS = {"": 1.0, "px": 1.0, "pt": 96.0 / 72, "mm": 96.0 / 25.4,
          "cm": 96.0 / 2.54, "in": 96.0}


def _dimension_px(text) -> float:
    match = _DIMENSION.match(text or "")
    if not match:
        return 0.0
    factor = _UNITS.get(match.group(2).lower())
    return float(match.group(1)) * factor if factor else 0.0


def _local(name: str) -> str:
    """Tag or attribute name without its namespace."""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _property(elem, name: str) -> str:
    """A presentation property; a style="..." declaration wins over the attribute."""
    for declaration in (elem.get("style") or "").split(";"):
        key, sep, value = declaration.partition(":")
        if sep and key.strip().lower() == name:
            return value.strip()
    return (elem.get(name) or "").strip()


_NON_STITCH_COLOURS = {
    "red", "green", "blue", "yellow", "cyan", "magenta", "lime", "fuchsia",
    "aqua", "orange", "purple", "gray", "grey", "silver", "maroon", "navy",
    "teal", "olive",
}


def _decorative(stroke: str, fill: str) -> bool:
    """
    Shapes that are not stitch lines: unstroked, filled without a stroke, or
    stroked in a named colour (registration and guide marks). `stroke` and
    `fill` are the effective values, inherited from enclosing groups.
    """
    if stroke == "none":
        return True
    if fill not in ("", "none") and not stroke:
        return True
    return stroke in _NON_STITCH_COLOURS


_HIDDEN_CONTAINERS = {"defs", "clippath", "mask", "symbol", "marker", "pattern",
                      "lineargradient", "radialgradient", "style", "script",
                      "text", "metadata"}


# ------------------------------------------------------------------ transforms

# An SVG matrix(a b c d e f): x' = a x + c y + e, y' = b x + d y + f.
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_FUNCTION = re.compile(r"\s*,?\s*([A-Za-z]+)\s*\(([^)]*)\)")


def _multiply(m, n):
    """m * n applies n first, then m."""
    a, b, c, d, e, f = m
    na, nb, nc, nd, ne, nf = n
    return (a * na + c * nb, b * na + d * nb,
            a * nc + c * nd, b * nc + d * nd,
            a * ne + c * nf + e, b * ne + d * nf + f)


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _similarity(m) -> bool:
    """Rotation, uniform scale, translation and reflection keep circles circular."""
    a, b, c, d = m[:4]
    size = a * a + b * b + c * c + d * d
    return (abs(a * a + b * b - c * c - d * d) <= 1e-9 * size
            and abs(a * c + b * d) <= 1e-9 * size)


def _parse_transform(text) -> tuple:
    """Parse e.g. "translate(10 5) rotate(30)"; anything unreadable ends the list."""
    result = _IDENTITY
    pos = 0
    text = text or ""
    while True:
        match = _FUNCTION.match(text, pos)
        if not match:
            break
        pos = match.end()
        name = match.group(1).lower()
        args = [float(v) for v in _NUMBER.findall(match.group(2))]
        arg = lambda i, fallback: args[i] if i < len(args) else fallback
        if name == "matrix" and len(args) == 6:
            m = tuple(args)
        elif name == "translate" and args:
            m = (1.0, 0.0, 0.0, 1.0, args[0], arg(1, 0.0))
        elif name == "scale" and args:
            m = (args[0], 0.0, 0.0, arg(1, args[0]), 0.0, 0.0)
        elif name == "rotate" and args:
            r = math.radians(args[0])
            cx, cy = arg(1, 0.0), arg(2, 0.0)
            m = _multiply(_multiply((1.0, 0.0, 0.0, 1.0, cx, cy),
                                    (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)),
                          (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        elif name == "skewx" and args:
            m = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif name == "skewy" and args:
            m = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            break
        result = _multiply(result, m)
    return result


def _transform_raws(m, raw: list, first: int):
    """
    Move raw[first:] from an element's own coordinates to document
    coordinates. Arcs stay arcs under rotation, uniform scaling and
    reflection; otherwise they are no longer circular and are flattened.
    """
    if m == _IDENTITY:
        return
    keep_arcs = _similarity(m)
    mirrored = (m[0] * m[3] - m[1] * m[2]) < 0
    moved = []
    for r in raw[first:]:
        if r.kind == _ARC and not keep_arcs:
            # sample_arc's G03 sweeps towards increasing angle, like `positive`.
            points = sample_arc(r.sx, r.sy, r.ex, r.ey, r.cx, r.cy, 3 if r.positive else 2)
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                moved.append(_Raw(_STITCH, *_apply(m, x0, y0), *_apply(m, x1, y1)))
            continue
        r.sx, r.sy = _apply(m, r.sx, r.sy)
        r.ex, r.ey = _apply(m, r.ex, r.ey)
        if r.kind == _ARC:
            r.cx, r.cy = _apply(m, r.cx, r.cy)
            if mirrored:
                r.positive = not r.positive
        moved.append(r)
    raw[first:] = moved


# ------------------------------------------------------------------ shapes

def _length(elem, name: str) -> float:
    """A length attribute in user units; units such as mm are converted."""
    return _dimension_px(elem.get(name))


def _shape_path(name: str, elem) -> str:
    """Path data equivalent to a basic shape element, or "" for other elements."""
    n = repr  # repr(float) is locale-independent and round-trips exactly
    if name == "path":
        return elem.get("d") or ""
    if name == "line":
        return (f"M{n(_length(elem, 'x1'))} {n(_length(elem, 'y1'))}"
                f"L{n(_length(elem, 'x2'))} {n(_length(elem, 'y2'))}")
    if name in ("polyline", "polygon"):
        points = (elem.get("points") or "").strip()
        if not points:
            return ""
        # "M x y x y ..." continues with implicit line-tos.
        return "M" + points + ("Z" if name == "polygon" else "")
    if name == "rect":
        x, y = _length(elem, "x"), _length(elem, "y")
        w, h = _length(elem, "width"), _length(elem, "height")
        if w <= 0 or h <= 0:
            return ""
        rx, ry = _length(elem, "rx"), _length(elem, "ry")
        if elem.get("rx") is None:
            rx = ry
        if elem.get("ry") is None:
            ry = rx
        rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
        if rx <= 0 or ry <= 0:
            return f"M{n(x)} {n(y)}H{n(x + w)}V{n(y + h)}H{n(x)}Z"
        corner = f"A{n(rx)} {n(ry)} 0 0 1 "
        return (f"M{n(x + rx)} {n(y)}H{n(x + w - rx)}"
                f"{corner}{n(x + w)} {n(y + ry)}V{n(y + h - ry)}"
                f"{corner}{n(x + w - rx)} {n(y + h)}H{n(x + rx)}"
                f"{corner}{n(x)} {n(y + h - ry)}V{n(y + ry)}"
                f"{corner}{n(x + rx)} {n(y)}Z")
    if name in ("circle", "ellipse"):
        cx, cy = _length(elem, "cx"), _length(elem, "cy")
        if name == "circle":
            rx = ry = _length(elem, "r")
        else:
            rx, ry = _length(elem, "rx"), _length(elem, "ry")
        if rx <= 0 or ry <= 0:
            return ""
        # Two half arcs; a single arc cannot start and end at the same point.
        arc = f"A{n(rx)} {n(ry)} 0 1 1 "
        return (f"M{n(cx + rx)} {n(cy)}{arc}{n(cx - rx)} {n(cy)}"
                f"{arc}{n(cx + rx)} {n(cy)}Z")
    return ""


def _is_rectangle(raw: list, first: int) -> bool:
    """A closed axis-aligned rectangle made of one subpath: a frame's shape."""
    if len(raw) != first + 5 or raw[first].kind != _TRAVEL:
        return False
    for r in raw[first + 1:]:
        if r.kind != _STITCH or (abs(r.ex - r.sx) >= 0.01 and abs(r.ey - r.sy) >= 0.01):
            return False
    start, end = raw[first], raw[-1]
    return math.hypot(start.ex - end.ex, start.ey - end.ey) < 0.01


def _drop_frames(raw: list, rectangles: list) -> list:
    """
    A rectangle around everything else is a registration frame, not part of
    the design. Rectangles inside the design, or a design that is only
    rectangles, are kept.
    """
    in_frame = [False] * len(raw)
    for first, last in rectangles:
        for i in range(first, last):
            in_frame[i] = True
    others = [(r.sx, r.sy) for r, framed in zip(raw, in_frame)
              if not framed and r.kind != _TRAVEL]
    others += [(r.ex, r.ey) for r, framed in zip(raw, in_frame) if not framed]
    if not others:
        return raw
    ox0 = min(p[0] for p in others); ox1 = max(p[0] for p in others)
    oy0 = min(p[1] for p in others); oy1 = max(p[1] for p in others)
    tol = 0.01
    drop = [False] * len(raw)
    for first, last in rectangles:
        xs = [raw[i].ex for i in range(first, last)]
        ys = [raw[i].ey for i in range(first, last)]
        if (min(xs) <= ox0 + tol and min(ys) <= oy0 + tol
                and max(xs) >= ox1 - tol and max(ys) >= oy1 - tol):
            for i in range(first, last):
                drop[i] = True
    return [r for r, dropped in zip(raw, drop) if not dropped]


# ------------------------------------------------------------------ conversion

def _svg_to_pat(svg_path: str, bezier_tolerance: float = 0.5) -> PatFile:
    """Convert a .svg file to a PatFile (see the module docstring)."""
    root = ET.parse(svg_path).getroot()

    # Physical scale: 1 PAT unit = 1 inch; SVG px → inches = px / 96.
    width_px = _dimension_px(root.get("width"))
    height_px = _dimension_px(root.get("height"))
    viewbox = [float(v) for v in _NUMBER.findall(root.get("viewBox") or "")]
    vb_w = viewbox[2] if len(viewbox) >= 3 else 0.0
    vb_h = viewbox[3] if len(viewbox) >= 4 else 0.0
    if width_px > 0 and vb_w > 0:
        svg_scale = width_px / vb_w / 96.0
    elif height_px > 0 and vb_h > 0:
        svg_scale = height_px / vb_h / 96.0
    else:
        svg_scale = None   # auto-scale after collecting points

    raw: list = []
    rectangles: list = []

    def walk(elem, transform, stroke, fill, hidden):
        name = _local(elem.tag).lower() if isinstance(elem.tag, str) else ""
        own = elem.get("transform")
        if own:
            transform = _multiply(transform, _parse_transform(own))
        value = _property(elem, "stroke").lower()
        if value and value != "inherit":
            stroke = value
        value = _property(elem, "fill").lower()
        if value and value != "inherit":
            fill = value
        if name in _HIDDEN_CONTAINERS or _property(elem, "display").lower() == "none":
            hidden = True
        visible = (not hidden
                   and _property(elem, "visibility").lower() != "hidden"
                   and not _decorative(stroke, fill))
        if visible:
            d = _shape_path(name, elem)
            if d:
                first = len(raw)
                det = abs(transform[0] * transform[3] - transform[1] * transform[2])
                scale = (svg_scale or 1.0) * max(math.sqrt(det), 1e-12)
                _parse_path(d, bezier_tolerance / scale, raw)
                _transform_raws(transform, raw, first)
                if _is_rectangle(raw, first):
                    rectangles.append((first, len(raw)))
        for child in elem:
            walk(child, transform, stroke, fill, hidden)

    walk(root, _IDENTITY, "", "", False)
    if rectangles:
        raw = _drop_frames(raw, rectangles)

    if not raw:
        return PatFile(filename=svg_path)

    xs, ys = [], []
    for r in raw:
        if r.kind != _TRAVEL:
            xs.append(r.sx); ys.append(r.sy)
        xs.append(r.ex); ys.append(r.ey)
        if r.kind == _ARC:
            radius = math.hypot(r.sx - r.cx, r.sy - r.cy)
            xs += [r.cx - radius, r.cx + radius]
            ys += [r.cy - radius, r.cy + radius]
    min_x, max_x, max_y = min(xs), max(xs), max(ys)
    if svg_scale is None:
        # Auto-scale: largest dimension → 12 PAT units (≈ 12 inches)
        longest = max(max_x - min_x, max_y - min(ys))
        svg_scale = 12.0 / longest if longest > 0 else 1.0

    # SVG's y axis points down and PAT's points up.
    def to_pat(x, y):
        return round((x - min_x) * svg_scale, 4), round((max_y - y) * svg_scale, 4)

    seq = 10
    moves = [Move(seq, 0, 0.0, 0.0, is_stitch=False)]
    seq += 1
    for r in raw:
        if r.kind == _TRAVEL:
            moves.append(Move(seq, 0, *to_pat(r.ex, r.ey), is_stitch=False))
            seq += 1
            continue
        x0, y0 = to_pat(r.sx, r.sy)
        x1, y1 = to_pat(r.ex, r.ey)
        if abs(x1 - x0) < 1e-6 and abs(y1 - y0) < 1e-6:
            continue   # skip zero-length segments
        if r.kind == _ARC:
            # Flipping y turns an increasing-angle sweep clockwise: G02.
            ci, cj = to_pat(r.cx, r.cy)
            moves.append(Move(seq, 2 if r.positive else 3, x1, y1, ci, cj, is_stitch=True))
        else:
            moves.append(Move(seq, 1, x1, y1, is_stitch=True))
        seq += 1

    pat = PatFile(filename=svg_path)
    pat.moves = moves
    pat.end_seq = seq
    return pat


def svg_to_pat_file(svg_path: str, out_path: str,
                    bezier_tolerance: float = 0.5) -> str:
    """
    Convert *svg_path* to a PAT file at *out_path*.
    Returns the output path on success, raises on error.
    """
    pat = _svg_to_pat(svg_path, bezier_tolerance)
    _write_pat_file(pat, out_path)
    return out_path


def svg_to_qli_file(svg_path: str, out_path: str,
                    bezier_tolerance: float = 0.5) -> str:
    """
    Convert *svg_path* to a Statler Stitcher QLI file at *out_path*.

    Path: SVG → PatFile (same geometry as SVG→PAT) → QLI text.
    Returns the output path on success, raises on error.
    """
    from qli_converter import write_qli_file

    pat = _svg_to_pat(svg_path, bezier_tolerance)
    write_qli_file(pat, out_path)
    return out_path
