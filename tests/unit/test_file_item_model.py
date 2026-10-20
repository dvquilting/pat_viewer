"""FileItemModel unit tests (Qt model, no paint)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from file_item_model import FileItemModel, ThumbnailDelegate
from constants import THUMB_SIZE


@pytest.mark.qt
def test_model_row_count_and_roles(qapp):
    model = FileItemModel()
    assert model.rowCount() == 0
    rows = [
        ("/a/one.pat", "one.pat", "PAT", ""),
        ("/b/two.qli", "two.qli", "QLI", "kw"),
    ]
    model.load_data(rows)
    assert model.rowCount() == 2
    idx = model.index(0, 0)
    assert model.data(idx, FileItemModel.PathRole) == "/a/one.pat"
    assert model.data(idx, FileItemModel.NameRole) == "one.pat"
    assert model.data(idx, FileItemModel.ExtRole) == "PAT"
    assert model.data(idx, FileItemModel.CheckedRole) is False
    assert model.data(idx, Qt.DisplayRole) is None
    # invalid index
    assert model.data(model.index(-1, 0), FileItemModel.PathRole) is None


@pytest.mark.qt
def test_checked_set_data_and_bulk(qapp):
    model = FileItemModel()
    model.load_data([
        ("/a.pat", "a.pat", "PAT", ""),
        ("/b.pat", "b.pat", "PAT", ""),
    ])
    idx0 = model.index(0, 0)
    assert model.setData(idx0, True, FileItemModel.CheckedRole) is True
    assert model.data(idx0, FileItemModel.CheckedRole) is True
    assert "/a.pat" in model.get_checked_paths()
    # non-checked role rejected
    assert model.setData(idx0, True, Qt.EditRole) is False

    model.setData(idx0, False, FileItemModel.CheckedRole)
    assert "/a.pat" not in model.get_checked_paths()

    model.set_all_checked(True)
    assert model.get_checked_paths() == {"/a.pat", "/b.pat"}
    model.set_all_checked(False)
    assert model.get_checked_paths() == set()


@pytest.mark.qt
def test_load_data_keep_checked(qapp):
    model = FileItemModel()
    model.load_data([("/a.pat", "a.pat", "PAT", ""), ("/b.pat", "b.pat", "PAT", "")])
    model.setData(model.index(0, 0), True, FileItemModel.CheckedRole)
    model.setData(model.index(1, 0), True, FileItemModel.CheckedRole)
    # reload with keep_checked; b removed from data
    model.load_data([("/a.pat", "a.pat", "PAT", ""), ("/c.pat", "c.pat", "PAT", "")],
                    keep_checked=True)
    assert model.get_checked_paths() == {"/a.pat"}
    # without keep_checked clears
    model.load_data([("/a.pat", "a.pat", "PAT", "")], keep_checked=False)
    assert model.get_checked_paths() == set()


@pytest.mark.qt
def test_delegate_size_hint(qapp):
    d = ThumbnailDelegate()
    sz = d.sizeHint(None, None)
    assert sz.width() == THUMB_SIZE + 16
    assert sz.height() == THUMB_SIZE + 40


@pytest.mark.qt
def test_delegate_paint_assign_mode_checked(qapp, qtbot):
    """Paint path for assign-mode checked cell (covers checkbox branch)."""
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

    model = FileItemModel()
    model.load_data([("/z.qli", "z.qli", "QLI", "")])
    model.setData(model.index(0, 0), True, FileItemModel.CheckedRole)

    parent = QWidget()
    delegate = ThumbnailDelegate(parent, is_assign_mode=True)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, THUMB_SIZE + 16, THUMB_SIZE + 40)
    option.state = QStyle.State_Enabled | QStyle.State_Selected | QStyle.State_MouseOver
    option.widget = parent

    img = QImage(option.rect.size(), QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    # no crash is the assertion


@pytest.mark.qt
def test_delegate_paint_svg_and_selected(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

    model = FileItemModel()
    model.load_data([("/s.svg", "s.svg", "SVG", "")])
    parent = QWidget()
    delegate = ThumbnailDelegate(parent, is_assign_mode=False)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 140, 160)
    option.state = QStyle.State_Enabled | QStyle.State_Selected
    option.widget = parent
    img = QImage(140, 160, QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()


@pytest.mark.qt
def test_delegate_schedule_empty_path_noop(qapp):
    d = ThumbnailDelegate()
    d._schedule_render("")
    assert d._pending_renders == set()
