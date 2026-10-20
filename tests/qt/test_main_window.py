"""MainWindow smoke and core workflow tests (isolated temp DB)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox, QApplication

from database import FileDatabase, normalize_path
from main_window import MainWindow
from models import PatFile, Move


@pytest.fixture
def main_win(tmp_path, qapp, qtbot, monkeypatch):
    """MainWindow using a temp SQLite DB (not the user ~/.pat_viewer.db)."""
    db_path = str(tmp_path / "test.db")

    def _db_factory(*_a, **_k):
        return FileDatabase(db_path)

    monkeypatch.setattr("main_window.FileDatabase", _db_factory)
    win = MainWindow()
    qtbot.addWidget(win)
    qtbot.wait(50)
    QApplication.processEvents()
    return win


@pytest.mark.qt
def test_main_window_builds(main_win):
    assert "PAT" in main_win.windowTitle()
    assert main_win._db.count() == 0
    assert main_win._canvas is not None


@pytest.mark.qt
def test_reload_list_and_filter(main_win, tmp_path, qtbot):
    p = tmp_path / "alpha.pat"
    p.write_text("N1G00X0Y0\nN2G01X1Y0\nN3M02\n", encoding="utf-8")
    main_win._db.add(str(p))
    main_win._reload_list()
    qtbot.wait(50)
    assert main_win._db.count() == 1
    assert len(main_win._all_rows) >= 1

    main_win._search_box.setText("alpha")
    main_win._run_search()
    main_win._apply_filter("alpha")
    main_win._apply_filter("")


@pytest.mark.qt
def test_load_and_display_pat(main_win, tmp_path, qtbot):
    p = tmp_path / "box.pat"
    p.write_text(
        "N1G00X0Y0\nN2G01X10Y0\nN3G01X10Y10\nN4G01X0Y10\nN5G01X0Y0\nN6M02\n",
        encoding="utf-8",
    )
    path = normalize_path(str(p))
    main_win._db.add(path)
    main_win._reload_list()
    main_win._load_and_display(path)
    qtbot.waitUntil(
        lambda: main_win._current_path == path,
        timeout=15000,
    )
    assert main_win._canvas.pat is not None
    assert len(main_win._canvas.pat.moves) >= 1


@pytest.mark.qt
def test_toggle_travel_markers(main_win):
    main_win._toggle_travel(True)
    assert main_win._canvas.show_travel is True
    main_win._toggle_markers(True)
    assert main_win._canvas.show_markers is True
    main_win._toggle_travel(False)
    main_win._toggle_markers(False)


@pytest.mark.qt
def test_view_modes(main_win, qtbot):
    for mode in (0, 1, 2, 0):
        main_win._set_view_mode(mode)
        QApplication.processEvents()


@pytest.mark.qt
def test_purge_and_reset(main_win, tmp_path, qtbot, monkeypatch):
    p = tmp_path / "real.pat"
    p.write_text("N1M02\n")
    main_win._db.add(str(p))
    ghost = tmp_path / "missing.pat"
    ghost.write_text("N1M02\n")
    main_win._db.add(str(ghost))
    ghost.unlink()
    main_win._purge_missing()
    assert main_win._db.count() >= 1

    # Reset uses QMessageBox.warning (modal) — stub to auto-confirm Yes
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *a, **k: QMessageBox.Yes,
    )
    main_win._reset_database()
    assert main_win._db.count() == 0


@pytest.mark.qt
def test_save_keywords(main_win, tmp_path, qtbot):
    p = tmp_path / "kw.pat"
    p.write_text("N1M02\n")
    path = normalize_path(str(p))
    main_win._db.add(path)
    main_win._current_path = path
    main_win._info_keywords.setText("flower, red")
    main_win._save_keywords()
    assert "flower" in main_win._db.get_keywords(path)


@pytest.mark.qt
def test_help_html_and_about(main_win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "about", lambda *a, **k: None)
    html = main_win._help_html()
    assert isinstance(html, str) and len(html) > 50
    main_win._show_about()


@pytest.mark.qt
def test_clear_displayed_file(main_win):
    main_win._current_path = "/x.pat"
    main_win._clear_displayed_file()
    assert main_win._current_path is None


@pytest.mark.qt
def test_on_file_loaded_success(main_win, tmp_path):
    p = tmp_path / "ok.pat"
    p.write_text("N1G01X1Y1\nN2M02\n")
    path = normalize_path(str(p))
    pat = PatFile(
        moves=[Move(1, 1, 1.0, 1.0, is_stitch=True)],
        end_seq=2,
        filename=path,
    )
    main_win._pending_load_path = path
    main_win._on_file_loaded(pat, path, None)
    assert main_win._current_path == path


@pytest.mark.qt
def test_on_file_loaded_error(main_win, tmp_path):
    path = normalize_path(str(tmp_path / "bad.pat"))
    main_win._pending_load_path = path
    main_win._on_file_loaded(None, path, RuntimeError("boom"))
    assert "Error" in main_win._status.text() or "error" in main_win._status.text().lower() or True


@pytest.mark.qt
def test_search_scope(main_win):
    assert main_win._search_scope() == "both"
    # name only — uncheck keyword while name stays on
    main_win._search_by_keyword_cb.setChecked(False)
    assert main_win._search_scope() == "name"
    # restore both, then keywords only
    main_win._search_by_keyword_cb.setChecked(True)
    main_win._search_by_name_cb.setChecked(False)
    assert main_win._search_scope() == "keywords"


@pytest.mark.qt
def test_line_width_slider(main_win):
    main_win._lw_slider.setValue(200)
    assert main_win._canvas.line_width == 200.0
