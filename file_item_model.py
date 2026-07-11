"""
Qt model/delegate pair used by both the Preview and Assign Keywords grids:
FileItemModel holds the row data (path/name/ext/checked-state) and
ThumbnailDelegate paints each cell (thumbnail image, filename, optional
checkbox overlay) directly with QPainter for performance at scale.
"""

from PySide6.QtCore import QAbstractListModel, Qt, QModelIndex, Signal, QSize, QRect, QTimer
from PySide6.QtWidgets import QStyledItemDelegate, QApplication, QStyle, QStyleOptionButton
from PySide6.QtGui import QPainter, QColor, QPen, QFontMetrics

from constants import THUMB_SIZE
from thumbnail_cache import _thumb_cache

class FileItemModel(QAbstractListModel):
    """Data model holding the list of files for the QListViews."""
    PathRole = Qt.UserRole + 1
    NameRole = Qt.UserRole + 2
    ExtRole = Qt.UserRole + 3
    CheckedRole = Qt.UserRole + 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._checked_paths = set()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        path, name, ext, _kw = self._rows[index.row()]
        
        if role == self.PathRole: return path
        if role == self.NameRole: return name
        if role == self.ExtRole: return ext
        if role == self.CheckedRole: return path in self._checked_paths
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == self.CheckedRole:
            path = self._rows[index.row()][0]
            if value:
                self._checked_paths.add(path)
            else:
                self._checked_paths.discard(path)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def load_data(self, rows, keep_checked=False):
        self.beginResetModel()
        self._rows = rows
        if not keep_checked:
            self._checked_paths.clear()
        else:
            # Retain only checked paths that still exist in the new data
            valid_paths = {r[0] for r in rows}
            self._checked_paths = self._checked_paths.intersection(valid_paths)
        self.endResetModel()

    def get_checked_paths(self):
        return self._checked_paths
        
    def set_all_checked(self, checked: bool):
        self.beginResetModel()
        if checked:
            self._checked_paths = {r[0] for r in self._rows}
        else:
            self._checked_paths.clear()
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    """Paints the thumbnail, filename, and optional checkbox directly using QPainter."""
    
    # Signal emitted when a thumbnail is needed but missing from cache.
    # Emitted *after* paint returns (coalesced via a 0-ms timer) to avoid
    # re-entrancy during QListView painting.
    render_requested = Signal(str)

    def __init__(self, parent=None, is_assign_mode=False):
        super().__init__(parent)
        self.is_assign_mode = is_assign_mode
        self.thumb_size = THUMB_SIZE
        self.margin = 8
        self._pending_renders: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush_render_requests)

    def sizeHint(self, option, index):
        return QSize(self.thumb_size + (self.margin * 2), self.thumb_size + 40)

    def clear_pending_renders(self):
        """Drop queued paint-time render requests (e.g. on model reload)."""
        self._flush_timer.stop()
        self._pending_renders.clear()

    def _schedule_render(self, path: str):
        if not path:
            return
        self._pending_renders.add(path)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_render_requests(self):
        paths = list(self._pending_renders)
        self._pending_renders.clear()
        for path in paths:
            self.render_requested.emit(path)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        # Smooth (bilinear) scaling for drawPixmap below -- without this the
        # oversampled thumbnail bitmap is scaled with fast/nearest-neighbour
        # filtering, which looks blocky instead of crisp.
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        path = index.data(FileItemModel.PathRole)
        name = index.data(FileItemModel.NameRole)
        ext = index.data(FileItemModel.ExtRole)

        # 1. Draw Background (Selection / Hover)
        is_selected = option.state & QStyle.State_Selected
        is_hovered = option.state & QStyle.State_MouseOver

        if self.is_assign_mode and index.data(FileItemModel.CheckedRole):
            painter.fillRect(option.rect, QColor("#cce0ff"))
            painter.setPen(QPen(QColor("#3380cc"), 2))
            painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 4, 4)
        elif is_selected and not self.is_assign_mode:
            painter.fillRect(option.rect, QColor("#cce0ff"))
            painter.setPen(Qt.NoPen)
        elif is_hovered:
            painter.fillRect(option.rect, QColor("#eef4ff"))
            painter.setPen(Qt.NoPen)

        # 2. Draw Image — QPixmap only from the GUI-thread display cache
        img_rect = QRect(option.rect.x() + self.margin, option.rect.y() + self.margin, 
                         self.thumb_size, self.thumb_size)
        
        pixmap = _thumb_cache.get_pixmap_for_display(path) if path else None
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(img_rect, pixmap)
        else:
            # Draw placeholder; request render *after* paint (timer), not here.
            painter.fillRect(img_rect, QColor(220, 220, 220))
            self._schedule_render(path)

        # Image border
        painter.setPen(QPen(QColor("#ccc"), 1))
        painter.drawRect(img_rect)

        # 3. Draw Checkbox (If in Assign Mode)
        if self.is_assign_mode:
            cb_rect = QRect(img_rect.x() + 4, img_rect.y() + 4, 20, 20)
            painter.fillRect(cb_rect, QColor(255, 255, 255, 200)) # Semi-trans background
            
            opt = QStyleOptionButton()
            opt.rect = cb_rect
            opt.state = QStyle.State_Enabled | (QStyle.State_On if index.data(FileItemModel.CheckedRole) else QStyle.State_Off)
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, painter, option.widget)

        # 4. Draw Filename text
        text_rect = QRect(option.rect.x() + 4, img_rect.bottom() + 4, option.rect.width() - 8, 30)
        
        if ext == 'QLI': text_color = QColor('#005aaa')
        elif ext == 'SVG': text_color = QColor('#a03c00')
        else: text_color = QColor('#1a7a3a')
        
        painter.setPen(text_color)
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        
        # Elide text if it's too long
        metrics = QFontMetrics(font)
        elided_name = metrics.elidedText(name or "", Qt.ElideRight, text_rect.width() * 2)
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, elided_name)

        painter.restore()
