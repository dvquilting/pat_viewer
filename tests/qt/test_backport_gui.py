"""Regression tests for GUI responsiveness changes ported from the C++ viewer."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from canvas import PatCanvas
from database import FileDatabase, normalize_path
from file_item_model import FileListModel
from models import Move, PatFile
from thumbnail_widgets import ThumbnailGrid, _visible_rows


def _box(path, size=10):
    path.write_text(f"N1G00X0Y0\nN2G01X{size}Y0\nN3G01X{size}Y{size}\nN4M02\n",
                    encoding="utf-8")
    return normalize_path(str(path))


@pytest.fixture
def main_win(tmp_path, qapp, qtbot, monkeypatch):
    from main_window import MainWindow
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("main_window.FileDatabase", lambda *a, **k: FileDatabase(db_path))
    win = MainWindow()
    qtbot.addWidget(win)
    qtbot.wait(50)
    return win


@pytest.mark.qt
def test_file_list_model_roles(qapp):
    model = FileListModel()
    model.load_data([("/a/one.qli", "one.qli", "QLI", ""), ("/b/two.pat", "two.pat", "PAT", "")])
    index = model.index(0, 0)
    assert model.data(index, Qt.DisplayRole) == "one.qli"
    assert model.data(index, Qt.ToolTipRole) == "/a/one.qli"
    assert model.data(index, Qt.ForegroundRole) is not None
    assert model.row_for_path("/b/two.pat") == 1
    assert model.update_keywords("/b/two.pat", "floral")
    assert model._rows[1][3] == "floral"


@pytest.mark.qt
def test_visible_rows_cover_only_the_viewport(qapp, qtbot):
    grid = ThumbnailGrid()
    qtbot.addWidget(grid)
    grid.resize(400, 300)
    grid.show()
    grid.populate([(f"/p/{i:04d}.pat", f"{i:04d}.pat", "PAT", "") for i in range(2000)])
    qtbot.wait(100)
    rows = _visible_rows(grid._view, grid._model)
    assert 0 < len(rows) < 100
    assert rows.start == 0
    grid.stop_render(wait=True)


@pytest.mark.qt
def test_canvas_wheel_and_resize(qapp, qtbot):
    pat = PatFile(moves=[Move(1, 0, 0.0, 0.0), Move(2, 1, 10.0, 0.0), Move(3, 1, 10.0, 8.0)])
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.show()
    c.load(pat)
    scale = c._scale
    horizontal = QWheelEvent(QPointF(200, 200), QPointF(200, 200), QPoint(0, 0), QPoint(120, 0),
                             Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    c.wheelEvent(horizontal)
    assert c._scale == scale              # horizontal scrolling does not zoom
    c.resize(600, 600)
    QApplication.processEvents()
    assert c._scale > scale               # refits until the user zooms
    fitted = c._scale
    c._zoom(2.0)
    c.resize(500, 500)
    QApplication.processEvents()
    assert c._scale == pytest.approx(fitted * 2.0)   # then keeps their view


@pytest.mark.qt
def test_canvas_repaint_uses_cached_image(qapp, qtbot):
    pat = PatFile(moves=[Move(1, 0, 0.0, 0.0)] +
                  [Move(i, 1, float(i % 50), float(i % 37)) for i in range(2, 3000)])
    c = PatCanvas()
    qtbot.addWidget(c)
    c.resize(400, 400)
    c.show()
    c.load(pat)
    c.repaint()
    first = c._cache
    c.repaint()
    assert c._cache is first   # unchanged view: no redraw


@pytest.mark.qt
def test_save_keywords_updates_in_place(main_win, tmp_path, monkeypatch):
    path = _box(tmp_path / "kw.pat")
    main_win._db.add(path)
    main_win._reload_list()
    main_win._current_path = path
    reloads = []
    monkeypatch.setattr(main_win, "_reload_list", lambda *a, **k: reloads.append(1))
    writes = []
    real_set = main_win._db.set_keywords
    monkeypatch.setattr(main_win._db, "set_keywords",
                        lambda p, k: (writes.append(k), real_set(p, k)))

    main_win._info_keywords.setText("floral, wreath")
    main_win._save_keywords()
    assert writes == ["floral, wreath"]
    assert main_win._list_model._rows[0][3] == "floral, wreath"
    main_win._save_keywords()                 # focus left again, nothing changed
    assert writes == ["floral, wreath"]
    assert reloads == []


@pytest.mark.qt
def test_purge_missing_runs_in_background(main_win, tmp_path, qtbot):
    keep = _box(tmp_path / "keep.pat")
    gone = _box(tmp_path / "gone.pat")
    main_win._db.add(keep)
    main_win._db.add(gone)
    main_win._current_path = gone
    os.remove(gone)
    main_win._purge_missing()
    qtbot.waitUntil(lambda: main_win._db.count() == 1, timeout=5000)
    qtbot.waitUntil(lambda: main_win._current_path is None, timeout=5000)


@pytest.mark.qt
def test_batch_convert_runs_in_background(main_win, tmp_path, qtbot):
    jobs = []
    for i in range(5):
        src = tmp_path / f"d{i}.qli"
        src.write_text(f"VP {i * 1000},0\nVP 1000,1000\nEN\n", encoding="utf-8")
        jobs.append((str(src), str(tmp_path / f"d{i}.pat")))
    main_win._start_batch_convert(jobs, 0, "qli_to_pat")
    qtbot.waitUntil(lambda: main_win._job_thread is None, timeout=10000)
    assert all(os.path.exists(out) for _src, out in jobs)
    assert main_win._db.count() == 5
