"""Format-dispatching load_file tests."""

from __future__ import annotations

import pytest

from file_loader import load_file
from models import PatFile


def test_load_pat(tmp_path):
    p = tmp_path / "a.pat"
    p.write_text("N1G00X0Y0\nN2G01X1Y0\nN3M02\n", encoding="utf-8")
    pat = load_file(str(p))
    assert isinstance(pat, PatFile)
    assert len(pat.moves) == 2


def test_load_qli(tmp_path):
    p = tmp_path / "a.qli"
    p.write_text("NO NEEDLE ON\nVP 1000,0\nEN\n", encoding="utf-8")
    pat = load_file(str(p))
    assert isinstance(pat, PatFile)
    assert any(m.gcode == 1 for m in pat.moves)


def test_load_unknown_ext_as_pat(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("N1G01X2Y3\nN2M02\n", encoding="utf-8")
    pat = load_file(str(p))
    assert pat.moves[0].x == 2.0


@pytest.mark.svg
def test_load_svg(tmp_path):
    svg = tmp_path / "line.svg"
    svg.write_text(
        """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
  <path d="M 10 10 L 90 10 L 90 90" fill="none" stroke="black"/>
</svg>
""",
        encoding="utf-8",
    )
    pat = load_file(str(svg))
    assert isinstance(pat, PatFile)
    assert len(pat.moves) >= 2
