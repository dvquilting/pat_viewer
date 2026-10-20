"""Content-hash duplicate detection."""

from __future__ import annotations

import pytest

from duplicates import DuplicateFinderThread, _file_content_hash


def test_hash_reads_content(tmp_path):
    a = tmp_path / "a.pat"
    a.write_bytes(b"hello world")
    h = _file_content_hash(str(a))
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex


def test_hash_identical_files_match(tmp_path):
    a = tmp_path / "a.pat"
    b = tmp_path / "b.pat"
    a.write_bytes(b"same-bytes")
    b.write_bytes(b"same-bytes")
    assert _file_content_hash(str(a)) == _file_content_hash(str(b))


def test_hash_different_content_differs(tmp_path):
    a = tmp_path / "a.pat"
    b = tmp_path / "b.pat"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    assert _file_content_hash(str(a)) != _file_content_hash(str(b))


def test_hash_missing_file_returns_none(tmp_path):
    assert _file_content_hash(str(tmp_path / "nope.pat")) is None


def test_hash_small_chunk_size(tmp_path):
    p = tmp_path / "big.pat"
    p.write_bytes(b"x" * 200)
    h1 = _file_content_hash(str(p), chunk_size=16)
    h2 = _file_content_hash(str(p), chunk_size=65536)
    assert h1 == h2


@pytest.mark.qt
def test_finder_groups_same_ext_only(tmp_path, qapp, qtbot):
    # Two identical .pat, one matching .qli content, one unique
    p1 = tmp_path / "one.pat"
    p2 = tmp_path / "two.pat"
    q1 = tmp_path / "one.qli"
    u1 = tmp_path / "unique.pat"
    payload = b"PATBODY"
    p1.write_bytes(payload)
    p2.write_bytes(payload)
    q1.write_bytes(payload)  # same bytes, different type — not a dup of pats
    u1.write_bytes(b"other")

    rows = [
        (str(p1), "one.pat", "PAT", ""),
        (str(p2), "two.pat", "PAT", ""),
        (str(q1), "one.qli", "QLI", ""),
        (str(u1), "unique.pat", "PAT", ""),
    ]
    thread = DuplicateFinderThread(rows)
    groups = []
    thread.finished_.connect(groups.append)
    # Call run() directly so coverage measures the body (QThread pool may not)
    thread.run()
    assert len(groups) == 1
    result = groups[0]
    assert len(result) == 1  # one duplicate set
    paths = result[0]
    assert set(paths) == {str(p1), str(p2)}
    # keeper is alphabetically first (case-insensitive)
    assert paths[0] == min(paths, key=str.lower)


@pytest.mark.qt
def test_finder_emits_progress(tmp_path, qapp, qtbot):
    a = tmp_path / "a.pat"
    b = tmp_path / "b.pat"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    rows = [(str(a), "a.pat", "PAT", ""), (str(b), "b.pat", "PAT", "")]
    thread = DuplicateFinderThread(rows)
    progress = []
    thread.progress.connect(lambda d, t: progress.append((d, t)))
    thread.run()
    assert progress
    assert progress[-1] == (2, 2)


@pytest.mark.qt
def test_finder_stop_aborts(tmp_path, qapp, qtbot):
    files = []
    for i in range(20):
        f = tmp_path / f"f{i:02d}.pat"
        f.write_bytes(bytes([i]) * 10)
        files.append(f)
    rows = [(str(f), f.name, "PAT", "") for f in files]
    thread = DuplicateFinderThread(rows)
    finished = []
    thread.finished_.connect(finished.append)
    thread._stopped = True  # stop before run processes
    thread.start()
    thread.wait(5000)
    # When stopped early in run, finished_ is not emitted
    assert finished == []
