"""PAT parse / write round-trip tests."""

from __future__ import annotations

import pytest

from models import Move, PatFile
from pat_parser import parse_pat
from pat_writer import _write_pat_file


def test_parse_linear_and_end(tmp_path):
    p = tmp_path / "box.pat"
    p.write_text(
        "N1G00X0Y0\n"
        "N2G01X10Y0\n"
        "N3G01X10Y10\n"
        "N4M02\n",
        encoding="utf-8",
    )
    pat = parse_pat(str(p))
    assert pat.filename == str(p)
    assert len(pat.moves) == 3
    assert pat.moves[0].gcode == 0 and not pat.moves[0].is_stitch
    assert pat.moves[1].gcode == 1 and pat.moves[1].is_stitch
    assert pat.moves[1].x == 10.0 and pat.moves[1].y == 0.0
    assert pat.end_seq == 4
    assert pat.comments == []


def test_parse_arc_and_comments(tmp_path):
    p = tmp_path / "arc.pat"
    p.write_text(
        "N10G00X0.0000Y0.0000\r\n"
        "N11G03X1.0000Y0.0000I0.5000J0.0000\r\n"
        "N12M02\r\n"
        "( hello world )\r\n"
        "( another )\r\n",
        encoding="utf-8",
    )
    pat = parse_pat(str(p))
    assert len(pat.moves) == 2
    arc = pat.moves[1]
    assert arc.gcode == 3
    assert arc.i == pytest.approx(0.5)
    assert arc.j == pytest.approx(0.0)
    assert arc.is_stitch
    assert pat.end_seq == 12
    assert pat.comments == ["hello world", "another"]


def test_parse_g02_cw_arc(tmp_path):
    p = tmp_path / "cw.pat"
    p.write_text("N1G02X0Y1I0J0\nN2M02\n", encoding="utf-8")
    pat = parse_pat(str(p))
    assert pat.moves[0].gcode == 2
    assert pat.moves[0].i == 0.0 and pat.moves[0].j == 0.0


def test_parse_ignores_junk_lines(tmp_path):
    p = tmp_path / "junk.pat"
    p.write_text("not a move\nN1G01X1Y2\n# comment\nN2M02\n", encoding="utf-8")
    pat = parse_pat(str(p))
    assert len(pat.moves) == 1
    assert pat.moves[0].x == 1.0 and pat.moves[0].y == 2.0


def test_write_pat_formats_moves(tmp_path):
    pat = PatFile(
        moves=[
            Move(1, 0, 0.0, 0.0, is_stitch=False),
            Move(2, 1, 1.5, 2.5, is_stitch=True),
            Move(3, 3, 0.0, 1.0, i=0.0, j=0.5, is_stitch=True),
            Move(4, 2, 1.0, 0.0, i=None, j=None, is_stitch=True),  # incomplete arc skipped
        ],
        comments=["note"],
        end_seq=5,
    )
    out = tmp_path / "out.pat"
    _write_pat_file(pat, str(out))
    text = out.read_bytes()
    # CR+LF line endings
    assert b"\r\n" in text
    lines = text.decode("utf-8").split("\r\n")
    assert lines[0] == "N01G00X0.0000Y0.0000"
    assert lines[1] == "N02G01X1.5000Y2.5000"
    assert lines[2].startswith("N03G03X0.0000Y1.0000I0.0000J0.5000")
    # incomplete G02 without I/J is not written
    assert not any("G02" in ln for ln in lines if ln)
    assert "N05M02" in lines
    assert "( note )" in lines


def test_roundtrip_parse_write(tmp_path):
    src = tmp_path / "src.pat"
    src.write_text(
        "N10G00X0.0000Y0.0000\n"
        "N11G01X2.0000Y0.0000\n"
        "N12G02X2.0000Y2.0000I2.0000J1.0000\n"
        "N13M02\n"
        "( roundtrip )\n",
        encoding="utf-8",
    )
    pat = parse_pat(str(src))
    out = tmp_path / "dst.pat"
    _write_pat_file(pat, str(out))
    again = parse_pat(str(out))
    assert len(again.moves) == len(pat.moves)
    for a, b in zip(again.moves, pat.moves):
        assert a.seq == b.seq
        assert a.gcode == b.gcode
        assert a.x == pytest.approx(b.x)
        assert a.y == pytest.approx(b.y)
        if b.i is None:
            assert a.i is None
        else:
            assert a.i == pytest.approx(b.i)
        if b.j is None:
            assert a.j is None
        else:
            assert a.j == pytest.approx(b.j)
    assert again.end_seq == pat.end_seq
    assert again.comments == pat.comments
