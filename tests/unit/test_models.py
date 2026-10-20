"""Basic model smoke tests."""

from models import Move, PatFile


def test_move_defaults():
    m = Move(1, 1, 3.0, 4.0)
    assert m.i is None and m.j is None
    assert m.is_stitch is False


def test_patfile_defaults():
    p = PatFile()
    assert p.moves == []
    assert p.comments == []
    assert p.end_seq is None
    assert p.filename == ""


def test_patfile_with_moves():
    p = PatFile(filename="x.pat", end_seq=3)
    p.moves.append(Move(1, 0, 0, 0))
    p.comments.append("c")
    assert len(p.moves) == 1
    assert p.comments == ["c"]
