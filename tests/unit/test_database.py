"""SQLite FileDatabase and scan/loader threads."""

from __future__ import annotations

import os

import pytest

from database import (
    FileDatabase,
    FileLoaderThread,
    FileScanThread,
    _escape_like,
    normalize_path,
)
from models import PatFile


def test_normalize_path_absolute():
    p = normalize_path(".")
    assert os.path.isabs(p)


def test_escape_like():
    assert _escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


def test_add_and_all_files(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    f = tmp_path / "sample.pat"
    f.write_text("N1G00X0Y0\nN2M02\n")
    assert db.add(str(f)) is True
    assert db.add(str(f)) is False  # duplicate insert ignored
    assert db.count() == 1
    rows = db.all_files()
    assert len(rows) == 1
    path, name, ext, kw = rows[0]
    assert name == "sample.pat"
    assert ext == "PAT"
    assert kw == ""


def test_remove_and_remove_many(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    a = tmp_path / "a.pat"
    b = tmp_path / "b.qli"
    a.write_text("x")
    b.write_text("y")
    db.add(str(a))
    db.add(str(b))
    db.remove(str(a))
    assert db.count() == 1
    db.remove_many([str(b), str(tmp_path / "missing.pat")])
    assert db.count() == 0
    db.remove_many([])  # no-op


def test_keywords_and_search(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    a = tmp_path / "flower.pat"
    b = tmp_path / "leaf.pat"
    a.write_text("x")
    b.write_text("y")
    db.add(str(a))
    db.add(str(b))
    db.set_keywords(str(a), "rose, red")
    assert db.get_keywords(str(a)) == "rose, red"
    assert db.get_keywords(str(tmp_path / "nope.pat")) == ""

    both = db.search("rose", scope="both")
    assert len(both) == 1 and both[0][1] == "flower.pat"

    by_name = db.search("leaf", scope="name")
    assert len(by_name) == 1

    by_kw = db.search("red", scope="keywords")
    assert len(by_kw) == 1

    # default scope both matches name
    assert len(db.search("flower")) == 1

    # LIKE wildcards in query are escaped (literal search)
    db.set_keywords(str(b), "100%")
    assert len(db.search("100%", scope="keywords")) == 1


def test_all_keywords_counts(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    a = tmp_path / "a.pat"
    b = tmp_path / "b.pat"
    a.write_text("x")
    b.write_text("y")
    db.add(str(a))
    db.add(str(b))
    db.set_keywords(str(a), "alpha, beta")
    db.set_keywords(str(b), "beta")
    kws = dict(db.all_keywords())
    assert kws["alpha"] == 1
    assert kws["beta"] == 2


def test_purge_missing_and_clear(tmp_path):
    db = FileDatabase(str(tmp_path / "t.db"))
    real = tmp_path / "real.pat"
    ghost = tmp_path / "ghost.pat"
    real.write_text("x")
    ghost.write_text("y")
    db.add(str(real))
    db.add(str(ghost))
    ghost.unlink()
    removed = db.purge_missing()
    assert removed == 1
    assert db.count() == 1
    n = db.clear()
    assert n == 1
    assert db.count() == 0
    assert db.clear() == 0


def test_path_migration_runs_once(tmp_path):
    db_path = str(tmp_path / "mig.db")
    db = FileDatabase(db_path)
    # meta flag set after init
    assert db._meta_get("paths_normalized") == "1"
    f = tmp_path / "z.pat"
    f.write_text("x")
    db.add(str(f))
    # re-open should not re-migrate destructively
    db2 = FileDatabase(db_path)
    assert db2.count() == 1


@pytest.mark.qt
def test_file_loader_thread_success(tmp_path, qapp, qtbot):
    p = tmp_path / "mini.pat"
    p.write_text("N1G00X0Y0\nN2G01X1Y0\nN3M02\n", encoding="utf-8")
    thread = FileLoaderThread(str(p))
    results = []
    thread.finished_.connect(lambda pat, path, err: results.append((pat, path, err)))
    thread.run()  # direct call for coverage
    pat, path, err = results[0]
    assert err is None
    assert isinstance(pat, PatFile)
    assert len(pat.moves) >= 1


@pytest.mark.qt
def test_file_loader_thread_error(tmp_path, qapp, qtbot):
    missing = tmp_path / "nope.pat"
    thread = FileLoaderThread(str(missing))
    results = []
    thread.finished_.connect(lambda pat, path, err: results.append((pat, path, err)))
    thread.run()
    pat, path, err = results[0]
    assert pat is None
    assert err is not None


@pytest.mark.qt
def test_file_scan_thread(tmp_path, qapp, qtbot):
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "a.pat").write_text("N1M02\n")
    (sub / "b.qli").write_text("EN\n")
    (sub / "c.svg").write_text("<svg/>")
    (sub / "skip.txt").write_text("nope")
    db = FileDatabase(str(tmp_path / "scan.db"))
    thread = FileScanThread(str(tmp_path), db)
    found = []
    thread.found.connect(found.append)
    totals = []
    thread.finished_.connect(totals.append)
    thread.run()
    assert totals[0] == 3
    assert db.count() == 3
    assert len(found) == 3


@pytest.mark.qt
def test_file_scan_stop(tmp_path, qapp, qtbot):
    for i in range(5):
        (tmp_path / f"f{i}.pat").write_text("N1M02\n")
    db = FileDatabase(str(tmp_path / "scan2.db"))
    thread = FileScanThread(str(tmp_path), db)
    thread.stop()
    totals = []
    thread.finished_.connect(totals.append)
    thread.run()
    assert totals[0] == 0
