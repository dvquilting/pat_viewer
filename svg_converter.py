"""
SVG -> PAT converter.

Requires the optional `svgpathtools` package (pip install svgpathtools),
imported lazily inside _svg_to_pat() so the rest of the app works fine
without it installed until the user actually tries to convert an SVG.
"""

import re

from models import Move, PatFile
from pat_writer import _write_pat_file

def _svg_to_pat(svg_path: str, bezier_tolerance: float = 0.5) -> PatFile:
    """
    Convert a .svg file to a PatFile.

    Conversion strategy
    -------------------
    SVG paths support line, arc, and Bézier segments. PAT only supports
    straight lines (G01) and circular arcs (G02/G03), so:

      SVG Line / H / V / Z (close) → G01 straight stitch
      SVG Arc (A)                  → G02 or G03 circular arc
                                     (non-circular / elliptic arcs are
                                      flattened to lines)
      SVG Cubic/Quadratic Bézier   → adaptively flattened to G01 lines

    Coordinate system
    -----------------
    SVG user coords are used as-is (before any display transform).  The
    SVG viewBox (or width×height) is read to determine physical dimensions.
    If the SVG has explicit mm/in/cm sizes, those are used to set the PAT
    scale (1 PAT unit ≈ 1 inch); otherwise the pattern is normalised so its
    largest dimension is 12 PAT units (≈ 12 inches).

    The Y axis is negated (SVG Y-down → PAT Y-up) and the whole pattern is
    translated so its bottom-left corner sits at the PAT origin.

    Requires the `svgpathtools` package (pip install svgpathtools).
    """
    try:
        from svgpathtools import svg2paths2, Line as SvgLine, Arc as SvgArc
        from svgpathtools import CubicBezier, QuadraticBezier
    except ImportError:
        raise ImportError(
            "The 'svgpathtools' package is required for SVG import.\n"
            "Install it with:  pip install svgpathtools"
        )

    import xml.etree.ElementTree as ET

    # ------------------------------------------------------------------ helpers

    def _bezier_flatness(seg) -> float:
        """Max perpendicular distance from chord to Bézier midpoints."""
        start = seg.start
        end   = seg.end
        chord = end - start
        if abs(chord) < 1e-12:
            return float(abs(seg.point(0.5) - start))
        chord_len = abs(chord)
        chord_dir = chord / chord_len
        max_d = 0.0
        for t in (0.25, 0.5, 0.75):
            pt = seg.point(t)
            v    = pt - start
            proj = v.real * chord_dir.real + v.imag * chord_dir.imag
            perp = abs(v - proj * chord_dir)
            max_d = max(max_d, perp)
        return max_d

    def _flatten_bezier(seg, tol: float):
        """
        Adaptively subdivide a Bézier until all sub-segments are within
        `tol` SVG user units of a straight line.
        Returns list of endpoint complex coords (excluding start).
        """
        def _sub(s, depth=0):
            if depth > 10 or _bezier_flatness(s) <= tol:
                return [s.end]
            s1, s2 = s.split(0.5)
            return _sub(s1, depth + 1) + _sub(s2, depth + 1)
        return _sub(seg)

    # ------------------------------------------------------------------ parse SVG

    paths, attrs, svg_attrs = svg2paths2(svg_path)

    # Determine SVG viewport size (for physical-unit scaling)
    vb    = svg_attrs.get('viewBox', '')
    w_str = svg_attrs.get('width',  '')
    h_str = svg_attrs.get('height', '')

    _DPI = 96.0
    _UNIT = {'px': 1.0, 'pt': _DPI/72, 'mm': _DPI/25.4,
             'cm': _DPI/2.54, 'in': _DPI, '': 1.0}

    def _parse_dim(s):
        m = re.match(r'^\s*([\d.]+)\s*(px|pt|mm|cm|in)?\s*$', s or '')
        if not m:
            return None
        return float(m.group(1)) * _UNIT.get(m.group(2) or 'px', 1.0)

    svg_w_px = _parse_dim(w_str)
    svg_h_px = _parse_dim(h_str)

    if vb:
        vb_vals = [float(v) for v in re.split(r'[\s,]+', vb.strip()) if v]
        if len(vb_vals) == 4:
            vb_w, vb_h = vb_vals[2], vb_vals[3]
        else:
            vb_w = vb_h = None
    else:
        vb_w = vb_h = None

    # Physical scale: if width is given in real units, use it
    # 1 PAT unit = 1 inch. SVG px → inches = px / DPI.
    if svg_w_px and vb_w and vb_w > 0:
        px_per_vbu = svg_w_px / vb_w      # pixels per viewBox user unit
        svg_scale  = px_per_vbu / _DPI    # inches per viewBox unit = PAT units
    elif svg_h_px and vb_h and vb_h > 0:
        px_per_vbu = svg_h_px / vb_h
        svg_scale  = px_per_vbu / _DPI
    else:
        # Fallback: auto-scale after collecting points
        svg_scale  = None

    # ------------------------------------------------------------------ filter paths

    def _is_axis_aligned_rect(path_obj) -> bool:
        """True if path is a closed axis-aligned rectangle (bounding box / frame)."""
        from svgpathtools import Line as _Line
        segs = list(path_obj)
        # A rectangle has 4 or 5 segments (the 5th closes back to start)
        n = len(segs)
        if n not in (4, 5):
            return False
        check = segs[:4]
        if not all(isinstance(s, _Line) for s in check):
            return False
        for s in check:
            dx = abs(s.end.real - s.start.real)
            dy = abs(s.end.imag - s.start.imag)
            # Both dx and dy significant → diagonal → not axis-aligned
            if dx > 0.01 and dy > 0.01:
                return False
        return True

    # Named colours that are clearly decorative / non-stitch (bounding boxes, guides)
    _NON_STITCH_COLOURS = {
        'red', 'green', 'blue', 'yellow', 'cyan', 'magenta',
        'lime', 'fuchsia', 'aqua', 'orange', 'purple', 'gray',
        'grey', 'silver', 'maroon', 'navy', 'teal', 'olive',
    }

    def _is_stitch_path(path_obj, attr: dict) -> bool:
        """
        Return True if this path should be converted to PAT stitch moves.
        Excludes:
          - Axis-aligned rectangles (bounding boxes / frames)
          - Paths with fill="none" stroke="none" (invisible)
          - Paths with obviously decorative stroke colours (blue, red, etc.)
        """
        # Geometric exclusion: axis-aligned closed rectangles are always bboxes
        if _is_axis_aligned_rect(path_obj):
            return False

        stroke = attr.get('stroke', '').strip().lower()
        fill   = attr.get('fill',   '').strip().lower()

        # Explicit stroke="none" → invisible outline
        if stroke == 'none':
            return False
        # Fully invisible
        if stroke == 'none' and fill in ('', 'none'):
            return False
        # Clearly decorative/guide stroke colour
        if stroke in _NON_STITCH_COLOURS:
            return False
        # Solid fill with no stroke → filled decoration, not a stitch path
        if fill not in ('', 'none') and not stroke:
            return False
        return True

    # ------------------------------------------------------------------ detect Y orientation

    # Many quilting SVGs embed their path data in Y-up coordinates and use a
    # display transform with a negative Y scale (e.g. matrix(s,0,0,-s,tx,ty))
    # to flip the image for screen rendering.  If we then flip Y again in our
    # coordinate transform, the result is upside-down.
    #
    # Detection: scan all transform attributes for a negative Y scale component.
    # If found, the path data is already Y-up → no flip needed.
    # If not found, standard SVG Y-down → flip Y to get PAT Y-up.

    def _has_negative_y_scale(svg_path_: str) -> bool:
        try:
            tree = ET.parse(svg_path_)
            for elem in tree.iter():
                t = elem.get('transform', '')
                if not t:
                    continue
                m = re.search(r'matrix\s*\(\s*([^)]+)\)', t)
                if m:
                    parts = [float(v) for v in re.split(r'[\s,]+', m.group(1).strip()) if v]
                    if len(parts) >= 4 and parts[3] < 0:
                        return True
                m = re.search(r'scale\s*\(\s*([^)]+)\)', t)
                if m:
                    parts = [float(v) for v in re.split(r'[\s,]+', m.group(1).strip()) if v]
                    if len(parts) >= 2 and parts[1] < 0:
                        return True
        except Exception:
            pass
        return False

    y_up_in_user_coords = _has_negative_y_scale(svg_path)
    # y_up_in_user_coords = True  → paths already Y-up → don't flip
    # y_up_in_user_coords = False → standard Y-down  → flip



    # Each entry: ('move'|'line'|'arc', x0,y0, x1,y1, [gcode, ci, cj])
    raw: list = []          # list of ('travel'|'stitch'|'arc', ...) tuples
    all_x: list = []
    all_y: list = []

    for path_obj, attr in zip(paths, attrs):
        if len(path_obj) == 0:
            continue
        # Skip decorative/bounding-box paths
        if not _is_stitch_path(path_obj, attr):
            continue

        cur = path_obj.start          # complex: real=x, imag=y  (SVG Y-down)
        raw.append(('travel', cur.real, cur.imag))
        all_x.append(cur.real); all_y.append(cur.imag)

        for seg in path_obj:
            start = seg.start

            if isinstance(seg, SvgLine):
                end = seg.end
                raw.append(('stitch', start.real, start.imag,
                             end.real,   end.imag))
                all_x += [end.real]; all_y += [end.imag]

            elif isinstance(seg, SvgArc):
                rx, ry = seg.radius.real, seg.radius.imag
                if abs(rx - ry) > max(0.5, 0.02 * rx):
                    # Elliptic arc → flatten
                    tol = bezier_tolerance / (svg_scale or 1.0)
                    pts = [seg.point(t / 16) for t in range(1, 17)]
                    prev = start
                    for pt in pts:
                        raw.append(('stitch', prev.real, prev.imag,
                                    pt.real,  pt.imag))
                        all_x.append(pt.real); all_y.append(pt.imag)
                        prev = pt
                else:
                    # Circular arc → G02/G03
                    # svgpathtools always interprets sweep in SVG Y-down convention:
                    #   sweep=1 = CW in Y-down
                    #   sweep=0 = CCW in Y-down
                    #
                    # Case A: Y-up user coords (quilting SVG, no Y-flip applied)
                    #   CW in Y-down = CCW in Y-up = G03
                    #   gcode = 3 if seg.sweep else 2
                    #
                    # Case B: Y-down user coords (standard SVG, Y-flip applied)
                    #   CW in Y-down → after Y-flip → CW in Y-up = G02
                    #   gcode = 2 if seg.sweep else 3
                    if y_up_in_user_coords:
                        gcode = 3 if seg.sweep else 2
                    else:
                        gcode = 2 if seg.sweep else 3
                    ctr   = seg.center
                    end   = seg.end
                    raw.append(('arc', start.real, start.imag,
                                end.real,   end.imag,
                                gcode, ctr.real, ctr.imag))
                    all_x += [end.real, ctr.real - rx, ctr.real + rx]
                    all_y += [end.imag, ctr.imag - ry, ctr.imag + ry]

            elif isinstance(seg, (CubicBezier, QuadraticBezier)):
                tol = bezier_tolerance / (svg_scale or 1.0)
                pts = _flatten_bezier(seg, tol)
                prev = start
                for pt in pts:
                    raw.append(('stitch', prev.real, prev.imag,
                                pt.real,  pt.imag))
                    all_x.append(pt.real); all_y.append(pt.imag)
                    prev = pt

    if not raw:
        return PatFile(filename=svg_path)

    # ------------------------------------------------------------------ coordinate transform

    # Translate so the bottom-left of the bounding box → PAT origin (0, 0).
    min_x_svg = min(all_x)
    min_y_svg = min(all_y)
    max_y_svg = max(all_y)

    if svg_scale is None:
        # Auto-scale: largest dimension → 12 PAT units (≈ 12 inches)
        w_svg = max(all_x) - min_x_svg
        h_svg = max_y_svg  - min_y_svg
        longest = max(w_svg, h_svg)
        svg_scale = 12.0 / longest if longest > 0 else 1.0

    if y_up_in_user_coords:
        # Path data already Y-up: just shift so min_y → 0, no flip needed.
        def to_pat(x_svg: float, y_svg: float):
            return (x_svg - min_x_svg) * svg_scale, \
                   (y_svg - min_y_svg) * svg_scale
    else:
        # Standard SVG Y-down: flip Y so max_y → 0 (bottom in PAT) and min_y → top.
        def to_pat(x_svg: float, y_svg: float):
            return (x_svg - min_x_svg) * svg_scale, \
                   (max_y_svg - y_svg) * svg_scale

    # ------------------------------------------------------------------ emit PAT moves

    pat  = PatFile(filename=svg_path)
    seq  = 10
    moves = []

    # Header
    moves.append(Move(seq, 0, 0.0, 0.0, is_stitch=False))
    seq += 1

    for entry in raw:
        kind = entry[0]

        if kind == 'travel':
            x, y = to_pat(entry[1], entry[2])
            moves.append(Move(seq, 0, round(x, 4), round(y, 4), is_stitch=False))
            seq += 1

        elif kind == 'stitch':
            x0, y0 = to_pat(entry[1], entry[2])
            x1, y1 = to_pat(entry[3], entry[4])
            if abs(x1 - x0) < 1e-6 and abs(y1 - y0) < 1e-6:
                continue                       # skip zero-length segments
            moves.append(Move(seq, 1,
                              round(x1, 4), round(y1, 4),
                              is_stitch=True))
            seq += 1

        elif kind == 'arc':
            x0, y0   = to_pat(entry[1], entry[2])
            x1, y1   = to_pat(entry[3], entry[4])
            gcode_svg = entry[5]
            ci, cj   = to_pat(entry[6], entry[7])   # center in PAT coords

            if abs(x1 - x0) < 1e-6 and abs(y1 - y0) < 1e-6:
                continue

            # When we flip Y, CW ↔ CCW swaps:
            # SVG sweep=True (CW Y-down) → G03 (CCW Y-up) ← already set above
            # Y-flip doesn't change this because we already account for it via gcode
            moves.append(Move(seq, gcode_svg,
                              round(x1, 4), round(y1, 4),
                              round(ci, 4), round(cj, 4),
                              is_stitch=True))
            seq += 1

    pat.moves   = moves
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
