"""ThumbnailGrid / KeywordAssignGrid / card widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QMouseEvent, QPixmap, QColor
from PySide6.QtWidgets import QListView

from database import FileDatabase
from thumbnail_widgets import (
    ThumbnailCard,
    KeywordAssignCard,
    ThumbnailGrid,
    KeywordAssignGrid,
    _visible_paths,
)
from file_item_model import FileItemModel
from constants import THUMB_SIZE


@pytest.mark.qt
def test_thumbnail_card_click_and_select(qapp, qtbot):
    card = ThumbnailCard("/p/a.pat", "a.pat", "PAT")
    qtbot.addWidget(card)
    clicked = []
    card.clicked.connect(clicked.append)
    card.set_selected(True)
    assert card._selected is True
    card.set_selected(False)
    pm = QPixmap(THUMB_SIZE, THUMB_SIZE)
    pm.fill(QColor("red"))
    card.set_pixmap(pm)
    # simulate left click
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    card.mousePressEvent(press)
    assert clicked == ["/p/a.pat"]


@pytest.mark.qt
def test_keyword_assign_card_toggle(qapp, qtbot):
    card = KeywordAssignCard("/p/b.qli", "b.qli", "QLI")
    qtbot.addWidget(card)
    events = []
    card.toggled.connect(lambda p, c: events.append((p, c)))
    assert card.is_checked() is False
    card.set_checked(True)
    assert card.is_checked() is True
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    card.mousePressEvent(press)
    assert card.is_checked() is False
    assert events  # emitted on click


@pytest.mark.qt
def test_thumbnail_grid_populate_select(qapp, qtbot, tmp_path, mini_pat):
    grid = ThumbnailGrid()
    qtbot.addWidget(grid)
    grid.resize(400, 400)
    rows = [(mini_pat, "mini.pat", "PAT", "")]
    grid.populate(rows)
    assert grid._model.rowCount() == 1
    grid.select(mini_pat)
    assert grid.selected_path() == mini_pat
    selected = []
    grid.file_selected.connect(selected.append)
    grid._on_clicked(grid._model.index(0, 0))
    assert selected == [mini_pat]
    grid._queue_render(mini_pat)
    assert mini_pat in grid._render_queue
    grid.stop_render(wait=False)
    assert grid._render_queue == set()
    grid.refresh_colors()
    grid.stop_render(wait=True)


@pytest.mark.qt
def test_visible_paths_empty(qapp, qtbot):
    view = QListView()
    qtbot.addWidget(view)
    model = FileItemModel()
    view.setModel(model)
    assert _visible_paths(view, model) == set()


@pytest.mark.qt
def test_keyword_assign_grid_populate_and_check(qapp, qtbot, tmp_path, mini_pat):
    db = FileDatabase(str(tmp_path / "kw.db"))
    db.add(mini_pat)
    grid = KeywordAssignGrid(db)
    qtbot.addWidget(grid)
    grid.resize(500, 500)
    rows = [(mini_pat, "mini.pat", "PAT", "")]
    grid.populate(rows)
    assert grid._model.rowCount() == 1
    # check all via model API used by grid
    grid._select_all()
    assert mini_pat in grid.checked_paths()
    grid._kw_entry.setText("floral, wreath")
    msgs = []
    grid.status_message.connect(msgs.append)
    grid._apply_keyword()
    assert db.get_keywords(mini_pat)
    grid._clear_selection()
    assert grid.checked_paths() == []
    grid.stop_render(wait=True)
