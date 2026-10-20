"""Regression tests for fixes ported from the C++ viewer (parsers, writers, SVG)."""

from __future__ import annotations

import math
import os

import pytest

from database import FileDatabase, normalize_path
from geometry import sample_arc
from models import Move, PatFile
from pat_parser import parse_pat
from pat_writer import _write_pat_file
from qli_converter import QliConverter, write_qli_file
from svg_converter import _svg_to_pat


def _extent(pat: PatFile):
    """(width, height) of a pattern, sampling arcs like the canvas does."""
    xs, ys = [], []
    cur = (0.0, 0.0)
    for mv in pat.moves:
        if mv.gcode in (2, 3) and mv.i is not None:
            pts = sample_arc(cur[0], cur[1], mv.x, mv.y, mv.i, mv.j, mv.gcode)
        else:
            pts = [(mv.x, mv.y)]
        for x, y in pts:
            xs.append(x)
            ys.append(y)
        cur = (mv.x, mv.y)
    return max(xs) - min(xs), max(ys) - min(ys)


def _arcs(pat: PatFile):
    return [m for m in pat.moves if m.gcode in (2, 3)]


# ------------------------------------------------------------------ PAT

def test_pat_numbers_without_leading_digit(tmp_path):
    p = tmp_path / "flex.pat"
    p.write_text("n20 g00 x+1.5 y-.25\nN21G01X.5Y-.75\nN22M02\n", encoding="utf-8")
    pat = parse_pat(str(p))
    assert len(pat.moves) == 2
    assert (pat.moves[0].x, pat.moves[0].y) == (1.5, -0.25)
    assert (pat.moves[1].x, pat.moves[1].y) == (0.5, -0.75)


def test_pat_writer_keeps_every_move(tmp_path):
    pat = PatFile(moves=[Move(10, 0, 0.0, 0.0), Move(11, 2, 1.0, 0.0),
                         Move(12, 92, 2.0, 0.0)])
    out = tmp_path / "odd.pat"
    _write_pat_file(pat, str(out))
    back = parse_pat(str(out))
    assert [m.gcode for m in back.moves] == [0, 1, 92]   # centre-less arc → G01


def test_pat_comments_cannot_inject_moves(tmp_path):
    pat = PatFile(moves=[Move(10, 0, 0.0, 0.0), Move(11, 1, 1.0, 1.0)],
                  comments=["Line one\r\nN99G01X5Y5 (note)"])
    out = tmp_path / "comment.pat"
    _write_pat_file(pat, str(out))
    back = parse_pat(str(out))
    assert len(back.moves) == 2
    assert back.comments == ["Line one  N99G01X5Y5 [note]"]


# ------------------------------------------------------------------ QLI

def test_qli_comments_cannot_inject_commands(tmp_path):
    pat = PatFile(moves=[Move(10, 0, 0.0, 0.0), Move(11, 1, 1.0, 1.0)],
                  comments=["Designed by Ann; VP 99000,99000"])
    out = tmp_path / "semi.qli"
    write_qli_file(pat, str(out))
    back = QliConverter().convert(str(out))
    assert len(back.moves) == 3
    assert back.comments == ["Designed by Ann  VP 99000,99000"]


def test_qli_arc_with_needle_off_is_travel(tmp_path):
    p = tmp_path / "needle.qli"
    p.write_text("NO NEEDLE OFF\nCR 1000,0,90\nEN\n", encoding="utf-8")
    last = QliConverter().convert(str(p)).moves[-1]
    assert last.gcode == 0 and last.i is None


def test_qli_full_circle_survives_pat_round_trip(tmp_path):
    # A PAT arc whose end meets its start is sewn as a semicircle, so a full
    # circle must be written as pieces of at most 180°.
    p = tmp_path / "circle.qli"
    p.write_text("VP 1000,0\nCR 1000,0,360\nVP 5000,0\nEN\n", encoding="utf-8")
    pat = QliConverter().convert(str(p))
    arcs = _arcs(pat)
    assert len(arcs) == 2
    out = tmp_path / "circle.pat"
    _write_pat_file(pat, str(out))
    back = parse_pat(str(out))
    cur = (0.0, 0.0)
    for mv in back.moves:
        if mv.gcode in (2, 3):
            assert math.hypot(mv.x - cur[0], mv.y - cur[1]) > 1e-3   # never degenerate
        cur = (mv.x, mv.y)
    assert _extent(back)[1] == pytest.approx(2.0, abs=1e-3)


def test_qli_large_arc_is_split_but_unchanged(tmp_path):
    p = tmp_path / "big.qli"
    p.write_text("VP 1000,0\nCR 1000,0,-270\nEN\n", encoding="utf-8")
    pat = QliConverter().convert(str(p))
    arcs = _arcs(pat)
    assert len(arcs) == 2 and all(a.gcode == 2 for a in arcs)
    assert (arcs[-1].x, arcs[-1].y) == pytest.approx((0.0, 1.0), abs=1e-9)


# ------------------------------------------------------------------ SVG

def _svg(tmp_path, name: str, body: str) -> PatFile:
    # width="100in" with a 100-unit viewBox makes one user unit one PAT unit.
    path = tmp_path / name
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100in" '
                    f'viewBox="0 0 100 100">{body}</svg>', encoding="utf-8")
    return _svg_to_pat(str(path))


def test_svg_compact_arc_flags(tmp_path):
    pat = _svg(tmp_path, "flags.svg", '<path d="M10 10a5 5 0 0110 0" stroke="black"/>')
    assert _arcs(pat)
    assert _extent(pat) == pytest.approx((10.0, 10.0), abs=1e-3)


def test_svg_long_path(tmp_path):
    d = "M0 0" + "".join(f" L{i % 100} {i * 7 % 100}" for i in range(50000))
    pat = _svg(tmp_path, "long.svg", f'<path stroke="black" d="{d}"/>')
    assert len(pat.moves) > 40000


def test_svg_ellipse_not_treated_as_circle(tmp_path):
    path = tmp_path / "ellipse.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1in" viewBox="0 0 1 1">'
                    '<path d="M0 0.5 A0.3 0.1 0 0 1 0.6 0.5" stroke="black"/></svg>',
                    encoding="utf-8")
    pat = _svg_to_pat(str(path))
    assert not _arcs(pat)
    assert _extent(pat)[1] == pytest.approx(0.1, abs=0.005)


def test_svg_group_transform(tmp_path):
    pat = _svg(tmp_path, "transform.svg",
               '<g transform="translate(10,0) scale(2)"><path stroke="black" d="M0 0 L5 0"/></g>'
               '<path stroke="black" d="M0 0 L1 0"/>')
    assert _extent(pat)[0] == pytest.approx(20.0)


def test_svg_flipped_group_points_up(tmp_path):
    pat = _svg(tmp_path, "flip.svg",
               '<g transform="scale(1,-1)"><path stroke="black" d="M0 0 L10 10"/></g>')
    assert pat.moves[-1].y > pat.moves[-2].y


def test_svg_inherited_styles_and_defs(tmp_path):
    pat = _svg(tmp_path, "styled.svg",
               '<g style="stroke:red"><path d="M0 0 L50 0"/></g>'
               '<path style="fill:#000;stroke:none" d="M0 0 L50 50"/>'
               '<defs><path stroke="black" d="M0 0 L80 0"/></defs>'
               '<path stroke="black" d="M0 0 L5 0"/>')
    assert _extent(pat)[0] == pytest.approx(5.0)


def test_svg_basic_shapes(tmp_path):
    pat = _svg(tmp_path, "shapes.svg",
               '<line x1="0" y1="0" x2="10" y2="0" stroke="black"/>'
               '<polyline points="0,0 0,10 5,10" stroke="black" fill="none"/>'
               '<circle cx="20" cy="5" r="5" stroke="black" fill="none"/>')
    assert _arcs(pat)
    assert _extent(pat) == pytest.approx((25.0, 10.0), abs=1e-3)


def test_svg_arcs_under_transforms(tmp_path):
    rotated = _svg(tmp_path, "rot.svg",
                   '<circle r="5" transform="rotate(30)" stroke="black" fill="none"/>')
    assert _arcs(rotated)
    stretched = _svg(tmp_path, "stretch.svg",
                     '<circle r="5" transform="scale(2,1)" stroke="black" fill="none"/>')
    assert not _arcs(stretched)
    assert _extent(stretched) == pytest.approx((20.0, 10.0), abs=0.01)


def test_svg_rounded_rect(tmp_path):
    pat = _svg(tmp_path, "rounded.svg",
               '<rect width="20" height="10" rx="2" stroke="black" fill="none"/>')
    assert _arcs(pat)
    assert _extent(pat)[0] == pytest.approx(20.0, abs=1e-3)


def test_svg_frames(tmp_path):
    framed = _svg(tmp_path, "framed.svg",
                  '<rect width="100" height="100" stroke="black" fill="none"/>'
                  '<path stroke="black" d="M10 10 L20 20"/>')
    assert _extent(framed)[0] == pytest.approx(10.0)
    inner = _svg(tmp_path, "inner.svg",
                 '<path stroke="black" d="M0 0 L100 100"/>'
                 '<rect x="10" y="10" width="20" height="20" stroke="black" fill="none"/>')
    assert sum(1 for m in inner.moves if m.gcode != 0) == 5
    square = _svg(tmp_path, "square.svg",
                  '<rect width="10" height="10" stroke="black" fill="none"/>')
    assert sum(1 for m in square.moves if m.gcode != 0) == 4


# ------------------------------------------------------------------ geometry

def test_sample_arc_tolerance_keeps_accuracy():
    # A small circle (r = 0.05) needs far fewer points than a 2° step gives it.
    fine = sample_arc(0.05, 0.0, -0.05, 0.0, 0.0, 0.0, 3)
    coarse = sample_arc(0.05, 0.0, -0.05, 0.0, 0.0, 0.0, 3, tolerance=1e-4)
    assert len(coarse) < len(fine) / 2
    assert coarse[0] == (0.05, 0.0) and coarse[-1] == (-0.05, 0.0)
    for (x0, y0), (x1, y1) in zip(coarse, coarse[1:]):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        assert 0.05 - math.hypot(mx, my) <= 1e-4 + 1e-12   # chord stays close to the arc
    # Large radii keep the 2° step.
    big = sample_arc(10.0, 0.0, -10.0, 0.0, 0.0, 0.0, 3, tolerance=1e-4)
    assert len(big) == len(sample_arc(10.0, 0.0, -10.0, 0.0, 0.0, 0.0, 3))


# ------------------------------------------------------------------ database / duplicates

def test_add_many_counts_new_files(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    a, b = tmp_path / "a.pat", tmp_path / "b.pat"
    assert db.add_many([str(a), str(b), str(a)]) == 2
    assert db.add_many([str(b)]) == 0
    assert set(db.all_paths()) == {normalize_path(str(a)), normalize_path(str(b))}


@pytest.mark.qt
def test_duplicates_skip_unique_sizes(tmp_path, qapp, monkeypatch):
    import duplicates
    hashed = []
    real = duplicates._file_content_hash
    monkeypatch.setattr(duplicates, "_file_content_hash",
                        lambda p, *a: hashed.append(p) or real(p, *a))
    same1, same2, unique = tmp_path / "s1.pat", tmp_path / "s2.pat", tmp_path / "u.pat"
    same1.write_bytes(b"abc")
    same2.write_bytes(b"abc")
    unique.write_bytes(b"longer content")
    rows = [(str(p), p.name, "PAT", "") for p in (same1, same2, unique)]
    thread = duplicates.DuplicateFinderThread(rows)
    groups = []
    thread.finished_.connect(groups.append)
    thread.run()
    assert groups == [[[str(same1), str(same2)]]]   # one emission, one duplicate set
    assert str(unique) not in hashed


def test_file_sizes_reads_folders_and_skips_missing(tmp_path):
    from duplicates import _file_sizes
    a = tmp_path / "a.pat"
    a.write_bytes(b"12345")
    (tmp_path / "unrelated.pat").write_bytes(b"x")
    sizes = _file_sizes([str(a), str(tmp_path / "gone.pat"), str(tmp_path / "nofolder" / "b.pat")])
    assert sizes == {os.path.normcase(str(a)): 5}
