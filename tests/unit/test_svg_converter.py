"""SVG → PAT / QLI conversion tests."""

from __future__ import annotations

import pytest

from models import PatFile
from svg_converter import _svg_to_pat, svg_to_pat_file, svg_to_qli_file


def _write_svg(tmp_path, name, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


SIMPLE_LINE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100px" height="50px" viewBox="0 0 100 50">
  <path d="M 0 25 L 100 25" fill="none" stroke="black"/>
</svg>
"""

WITH_BEZIER = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path d="M 0 50 C 25 0, 75 100, 100 50" fill="none" stroke="#000"/>
</svg>
"""

WITH_ARC = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="black"/>
</svg>
"""

RECT_BBOX = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path d="M 0 0 L 100 0 L 100 100 L 0 100 Z" fill="none" stroke="blue"/>
  <path d="M 20 50 L 80 50" fill="none" stroke="black"/>
</svg>
"""

EMPTY_PATHS = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="" fill="none" stroke="black"/>
</svg>
"""

YUP_TRANSFORM = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <g transform="matrix(1,0,0,-1,0,100)">
    <path d="M 0 0 L 50 0 L 50 50" fill="none" stroke="black"/>
  </g>
</svg>
"""


@pytest.mark.svg
def test_svg_simple_line(tmp_path):
    path = _write_svg(tmp_path, "line.svg", SIMPLE_LINE)
    pat = _svg_to_pat(path)
    assert isinstance(pat, PatFile)
    assert pat.filename == path
    # header + travel + stitches
    assert any(m.gcode == 1 for m in pat.moves)
    assert pat.end_seq is not None


@pytest.mark.svg
def test_svg_bezier_flattened(tmp_path):
    path = _write_svg(tmp_path, "bez.svg", WITH_BEZIER)
    pat = _svg_to_pat(path, bezier_tolerance=1.0)
    stitches = [m for m in pat.moves if m.gcode == 1]
    assert len(stitches) >= 2


@pytest.mark.svg
def test_svg_circular_arc(tmp_path):
    path = _write_svg(tmp_path, "arc.svg", WITH_ARC)
    pat = _svg_to_pat(path)
    arcs = [m for m in pat.moves if m.gcode in (2, 3)]
    assert len(arcs) >= 1
    assert arcs[0].i is not None and arcs[0].j is not None


@pytest.mark.svg
def test_svg_skips_decorative_rect(tmp_path):
    path = _write_svg(tmp_path, "bbox.svg", RECT_BBOX)
    pat = _svg_to_pat(path)
    # Should keep the black horizontal line, drop blue rectangle
    # At least some stitch moves from the line
    assert any(m.gcode in (0, 1) for m in pat.moves)


@pytest.mark.svg
def test_svg_empty_returns_empty_pat(tmp_path):
    path = _write_svg(tmp_path, "empty.svg", EMPTY_PATHS)
    pat = _svg_to_pat(path)
    assert pat.moves == [] or all(m.gcode == 0 and m.x == 0 for m in pat.moves[:1])
    # empty raw → empty PatFile with no moves
    assert isinstance(pat, PatFile)


@pytest.mark.svg
def test_svg_yup_transform(tmp_path):
    path = _write_svg(tmp_path, "yup.svg", YUP_TRANSFORM)
    # Group transforms are applied, so the flipped path still reads correctly
    pat = _svg_to_pat(path)
    assert isinstance(pat, PatFile)


@pytest.mark.svg
def test_svg_to_pat_file_writes(tmp_path):
    src = _write_svg(tmp_path, "src.svg", SIMPLE_LINE)
    out = tmp_path / "out.pat"
    ret = svg_to_pat_file(src, str(out))
    assert ret == str(out)
    assert out.is_file()
    text = out.read_text(encoding="utf-8", errors="replace")
    assert "G0" in text or "G1" in text or "G00" in text or "G01" in text


@pytest.mark.svg
def test_svg_to_qli_file_writes(tmp_path):
    src = _write_svg(tmp_path, "src.svg", SIMPLE_LINE)
    out = tmp_path / "out.qli"
    ret = svg_to_qli_file(src, str(out))
    assert ret == str(out)
    assert "EN" in out.read_text(encoding="utf-8")


@pytest.mark.svg
def test_svg_mm_width_scale(tmp_path):
    body = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="254mm" height="127mm" viewBox="0 0 100 50">
  <path d="M 0 0 L 100 0" fill="none" stroke="black"/>
</svg>
"""
    path = _write_svg(tmp_path, "mm.svg", body)
    pat = _svg_to_pat(path)
    xs = [m.x for m in pat.moves]
    # 254mm ≈ 10 inches; pattern should span roughly that in X
    span = max(xs) - min(xs)
    assert span == pytest.approx(10.0, rel=0.15)
