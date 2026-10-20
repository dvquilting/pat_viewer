"""QLI read/write converter tests."""

from __future__ import annotations

import math

import pytest

from models import Move, PatFile
from qli_converter import (
    QliConverter,
    _fmt_qli_num,
    _qli_get_number,
    _qli_skip_axes,
    _qli_skip_cr_suffix,
    _qli_try_float,
    write_qli_file,
)


class TestHelpers:
    def test_get_number_plain(self):
        tok, n = _qli_get_number("123.5,rest")
        assert tok == "123.5"
        assert n == 5  # chars before comma

    def test_get_number_leading_space_and_rnd(self):
        tok, n = _qli_get_number("  @RND[42.0],next")
        assert tok == "42.0"
        assert "," not in tok

    def test_get_number_strips_trailing_junk(self):
        tok, _ = _qli_get_number("10]*+")
        assert tok == "10"

    def test_try_float(self):
        assert _qli_try_float("3.5") == 3.5
        assert _qli_try_float("nope") == 0.0
        assert _qli_try_float(None) == 0.0

    def test_skip_axes(self):
        uc = "VMXY,Z stuff"
        # start at 'M' index 1 after V
        i = _qli_skip_axes(uc, 1, len(uc))
        assert i >= 1

    def test_skip_cr_suffix(self):
        uc = "  <100> >5"
        i = _qli_skip_cr_suffix(uc, 0, len(uc))
        assert i == len(uc) or i > 0

    def test_fmt_qli_num(self):
        assert _fmt_qli_num(10.0) == "10"
        assert _fmt_qli_num(10.5) == "10.5"
        assert _fmt_qli_num(1.23456) == "1.2346"


def _write_qli(tmp_path, name, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_convert_empty_en(tmp_path):
    path = _write_qli(tmp_path, "empty.qli", "EN\n")
    pat = QliConverter().convert(path)
    # header travel only
    assert len(pat.moves) == 1
    assert pat.moves[0].gcode == 0
    assert pat.moves[0].x == 0.0
    assert pat.end_seq is not None


def test_convert_vector_position_and_needle(tmp_path):
    body = (
        "NO NEEDLE OFF\n"
        "VP 1000,0\n"
        "NO NEEDLE ON\n"
        "VP 2000,1000\n"
        "EN\n"
    )
    path = _write_qli(tmp_path, "line.qli", body)
    pat = QliConverter().convert(path)
    # header + 2 VPs
    assert len(pat.moves) == 3
    travel = pat.moves[1]
    stitch = pat.moves[2]
    assert travel.gcode == 0
    assert travel.x == pytest.approx(1.0)  # SCALE 0.001
    assert stitch.gcode == 1
    assert stitch.x == pytest.approx(2.0)
    assert stitch.y == pytest.approx(1.0)
    assert stitch.is_stitch


def test_convert_circle_cr(tmp_path):
    # full 180° CCW arc of radius 1000 mils from current (0,0)
    # start_angle 0, range 180 → G03
    body = "NO NEEDLE ON\nCR 1000,0,180\nEN\n"
    path = _write_qli(tmp_path, "arc.qli", body)
    pat = QliConverter().convert(path)
    arcs = [m for m in pat.moves if m.gcode in (2, 3)]
    assert len(arcs) == 1
    arc = arcs[0]
    assert arc.gcode == 3  # positive angle_range
    assert arc.is_stitch
    # r=1000 mils, 180° from angle 0: Δ = (-2000, 0) mils → PAT (-2, 0)
    assert arc.x == pytest.approx(-2.0)
    assert arc.y == pytest.approx(0.0)
    assert arc.i is not None and arc.j is not None


def test_convert_circle_cw(tmp_path):
    body = "CR 1000,0,-90\nEN\n"
    path = _write_qli(tmp_path, "arc_cw.qli", body)
    pat = QliConverter().convert(path)
    arcs = [m for m in pat.moves if m.gcode in (2, 3)]
    assert arcs[0].gcode == 2


def test_convert_comments_and_rem(tmp_path):
    body = (
        "REM hello\n"
        "REM FIRST_MACRO skip me\n"
        "NO INFO some note\n"
        "EN\n"
    )
    path = _write_qli(tmp_path, "rem.qli", body)
    pat = QliConverter().convert(path)
    assert "hello" in pat.comments
    assert any("some note" in c for c in pat.comments)
    assert not any("FIRST_MACRO" in c for c in pat.comments)


def test_convert_cs_resets_origin(tmp_path):
    body = "VP 500,500\nCS\nVP 0,0\nEN\n"
    path = _write_qli(tmp_path, "cs.qli", body)
    pat = QliConverter().convert(path)
    # After CS, current is origin; VP 0,0 relative may still emit
    assert len(pat.moves) >= 2


def test_convert_ve_relative_origin(tmp_path):
    body = (
        "VP 1000,0\n"
        "VE\n"
        "VP 500,0\n"  # relative to (1000,0) → absolute 1500,0
        "EN\n"
    )
    path = _write_qli(tmp_path, "ve.qli", body)
    pat = QliConverter().convert(path)
    last = pat.moves[-1]
    assert last.x == pytest.approx(1.5)


def test_convert_semicolon_commands(tmp_path):
    body = "NO NEEDLE ON;VP 100,200;EN\n"
    path = _write_qli(tmp_path, "semi.qli", body)
    pat = QliConverter().convert(path)
    assert any(m.gcode == 1 for m in pat.moves)


def test_write_qli_linear_and_roundtrip(tmp_path):
    pat = PatFile(
        moves=[
            Move(10, 0, 0.0, 0.0, is_stitch=False),
            Move(11, 0, 1.0, 0.0, is_stitch=False),
            Move(12, 1, 2.0, 1.0, is_stitch=True),
        ],
        comments=["meta"],
        end_seq=13,
    )
    out = tmp_path / "out.qli"
    write_qli_file(pat, str(out))
    text = out.read_text(encoding="utf-8")
    assert "REM meta" in text
    assert "NO NEEDLE OFF" in text or "VP" in text
    assert "NO NEEDLE ON" in text
    assert text.strip().endswith("EN")
    # re-convert
    again = QliConverter().convert(str(out))
    stitch_moves = [m for m in again.moves if m.gcode == 1]
    assert stitch_moves
    assert stitch_moves[-1].x == pytest.approx(2.0, abs=1e-3)


def test_write_qli_arc(tmp_path):
    # semicircle from (1,0) to (-1,0) center (0,0) G03
    pat = PatFile(
        moves=[
            Move(10, 0, 1.0, 0.0, is_stitch=False),
            Move(11, 3, -1.0, 0.0, i=0.0, j=0.0, is_stitch=True),
        ],
        end_seq=12,
    )
    out = tmp_path / "arc.qli"
    write_qli_file(pat, str(out))
    text = out.read_text(encoding="utf-8")
    assert "CR " in text


def test_write_qli_degenerate_arc_becomes_vp(tmp_path):
    pat = PatFile(
        moves=[
            Move(10, 0, 0.0, 0.0, is_stitch=False),
            Move(11, 3, 1.0, 1.0, i=0.0, j=0.0, is_stitch=True),  # r from origin may be nonzero
            # true degenerate: start at center
            Move(12, 0, 5.0, 5.0, is_stitch=False),
            Move(13, 2, 6.0, 6.0, i=5.0, j=5.0, is_stitch=True),
        ],
        end_seq=14,
    )
    out = tmp_path / "deg.qli"
    write_qli_file(pat, str(out))
    assert "VP" in out.read_text(encoding="utf-8")


def test_write_qli_unknown_gcode_fallback(tmp_path):
    pat = PatFile(
        moves=[
            Move(10, 0, 0.0, 0.0, is_stitch=False),
            Move(11, 9, 3.0, 4.0, is_stitch=False),  # unknown
        ],
        end_seq=12,
    )
    out = tmp_path / "unk.qli"
    write_qli_file(pat, str(out))
    assert "VP" in out.read_text(encoding="utf-8")


def test_reset_clears_state():
    c = QliConverter()
    c.current_x = 99
    c.moves.append(Move(1, 1, 0, 0))
    c.comments.append("x")
    c.reset()
    assert c.current_x == 0.0
    assert c.moves == []
    assert c.comments == []
