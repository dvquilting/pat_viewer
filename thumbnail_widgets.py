"""
Thumbnail grid widgets: the plain single-select Thumbnail Mode grid (ThumbnailGrid)
and the multi-select Assign Keywords grid (KeywordAssignGrid), plus the
older per-widget ThumbnailCard/KeywordAssignCard classes retained for
compatibility (the grids now use FileItemModel + ThumbnailDelegate + a
single QListView instead of one widget per file, for performance).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QLineEdit,
    QPushButton, QFrame, QListView,
)
from PySide6.QtCore import Qt, Signal, QTimer

from constants import THUMB_SIZE
from thumbnail_cache import _blank_thumbnail, ThumbnailRenderCoordinator
from file_item_model import FileItemModel, ThumbnailDelegate


def _visible_paths(view: QListView, model: FileItemModel) -> set:
    """Return paths whose thumbnail cells intersect the list viewport."""
    paths = set()
    viewport_rect = view.viewport().rect()
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        if viewport_rect.intersects(view.visualRect(index)):
            path = model.data(index, FileItemModel.PathRole)
            if path:
                paths.add(path)
    return paths


class ThumbnailCard(QWidget):
    clicked = Signal(str)

    def __init__(self, path: str, name: str, ext: str, parent=None):
        super().__init__(parent)
        self._path     = path
        self._selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignHCenter)

        self._img_label = QLabel()
        self._img_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("border: 1px solid #ccc; background: #fff;")
        self._img_label.setPixmap(_blank_thumbnail(THUMB_SIZE))
        layout.addWidget(self._img_label, alignment=Qt.AlignHCenter)

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setFixedWidth(THUMB_SIZE + 8)
        name_label.setStyleSheet(
            f"font-size: 10px; color: {'#005aaa' if ext == 'QLI' else '#a03c00' if ext == 'SVG' else '#1a7a3a'};"
        )
        layout.addWidget(name_label, alignment=Qt.AlignHCenter)

        self.setFixedWidth(THUMB_SIZE + 16)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()

    def set_pixmap(self, pixmap):
        self._img_label.setPixmap(pixmap)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet("ThumbnailCard { background: #cce0ff; border-radius: 4px; }")
        else:
            self.setStyleSheet(
                "ThumbnailCard { background: transparent; border-radius: 4px; }"
                "ThumbnailCard:hover { background: #eef4ff; }"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._path)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Thumbnail grid with viewport-aware rendering
# ---------------------------------------------------------------------------

class ThumbnailGrid(QWidget):
    file_selected  = Signal(str)
    render_progress = Signal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QListView()
        self._view.setViewMode(QListView.IconMode)
        self._view.setResizeMode(QListView.Adjust)
        self._view.setSpacing(6)
        self._view.setUniformItemSizes(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setSelectionMode(QListView.SingleSelection)

        self._model = FileItemModel()
        self._view.setModel(self._model)

        self._delegate = ThumbnailDelegate(self)
        self._view.setItemDelegate(self._delegate)

        layout.addWidget(self._view)

        # Connections
        self._view.clicked.connect(self._on_clicked)
        self._delegate.render_requested.connect(self._queue_render)
        
        # Render queuing (Debouncing)
        self._render_queue = set()
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(150) # Wait 150ms after scroll/paint stops before rendering
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._process_render_queue)
        self._render_coordinator = None

    def _ensure_coordinator(self):
        if self._render_coordinator is None:
            coord = ThumbnailRenderCoordinator(parent=self)
            coord.rendered.connect(self._on_rendered)
            coord.progress.connect(
                lambda done, total: self.render_progress.emit(done, total, False))
            coord.finished_.connect(
                lambda: self.render_progress.emit(0, 0, True))
            self._render_coordinator = coord

    def _update_row_for_path(self, path: str):
        for row in range(self._model.rowCount()):
            idx = self._model.index(row, 0)
            if self._model.data(idx, FileItemModel.PathRole) == path:
                self._view.viewport().update(self._view.visualRect(idx))
                return

    def populate(self, rows: list):
        self.stop_render()
        self._render_queue.clear()
        self._delegate.clear_pending_renders()
        self._model.load_data(rows)

    def select(self, path: str):
        for row in range(self._model.rowCount()):
            idx = self._model.index(row, 0)
            if self._model.data(idx, FileItemModel.PathRole) == path:
                self._view.setCurrentIndex(idx)
                break

    def selected_path(self) -> str | None:
        """Return the path of the currently selected thumbnail, or None."""
        idx = self._view.currentIndex()
        if not idx.isValid():
            return None
        return self._model.data(idx, FileItemModel.PathRole)

    def _on_clicked(self, index):
        path = self._model.data(index, FileItemModel.PathRole)
        self.file_selected.emit(path)

    def _queue_render(self, path: str):
        self._render_queue.add(path)
        self._render_timer.start()

    def _process_render_queue(self):
        if not self._render_queue: return
        
        paths_to_render = list(self._render_queue)
        self._render_queue.clear()
        self._start_render(paths_to_render)

    def _start_render(self, paths: list):
        self._ensure_coordinator()
        visible = _visible_paths(self._view, self._model)
        self._render_coordinator.enqueue(paths, visible_paths=visible)

    def _on_rendered(self, path: str, _image):
        self._update_row_for_path(path)

    def stop_render(self, wait: bool = False):
        self._render_timer.stop()
        self._render_queue.clear()
        self._delegate.clear_pending_renders()
        if self._render_coordinator is not None:
            if wait:
                self._render_coordinator.shutdown()
                self._render_coordinator = None
            else:
                self._render_coordinator.stop()

    def refresh_colors(self):
        if self._render_coordinator is not None:
            self._render_coordinator.refresh_render_scale()
        paths = [r[0] for r in self._model._rows]
        if paths:
            self._start_render(paths)

class KeywordAssignCard(QWidget):
    """
    Thumbnail card for keyword-assign mode.
    Shows a checkbox overlay for multi-select; clicking toggles selection
    rather than navigating to the file.
    """
    toggled = Signal(str, bool)   # (path, selected)

    def __init__(self, path: str, name: str, ext: str, parent=None):
        super().__init__(parent)
        self._path     = path
        self._checked  = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignHCenter)

        # Image label with checkbox overlay
        img_container = QWidget()
        img_container.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        img_container.setStyleSheet("border: 1px solid #ccc; background: #fff;")

        self._img_label = QLabel(img_container)
        self._img_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setPixmap(_blank_thumbnail(THUMB_SIZE))

        self._check = QCheckBox(img_container)
        self._check.setGeometry(4, 4, 20, 20)
        self._check.setStyleSheet("QCheckBox { background: rgba(255,255,255,200); }")
        self._check.toggled.connect(self._on_check)

        layout.addWidget(img_container, alignment=Qt.AlignHCenter)

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setFixedWidth(THUMB_SIZE + 8)
        name_label.setStyleSheet(
            f"font-size: 10px; color: {'#005aaa' if ext == 'QLI' else '#a03c00' if ext == 'SVG' else '#1a7a3a'};"
        )
        layout.addWidget(name_label, alignment=Qt.AlignHCenter)

        self.setFixedWidth(THUMB_SIZE + 16)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style()

    def set_pixmap(self, pixmap):
        self._img_label.setPixmap(pixmap)

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool):
        self._check.blockSignals(True)
        self._check.setChecked(checked)
        self._checked = checked
        self._check.blockSignals(False)
        self._update_style()

    def _on_check(self, checked: bool):
        self._checked = checked
        self._update_style()
        self.toggled.emit(self._path, checked)

    def _update_style(self):
        if self._checked:
            self.setStyleSheet(
                "KeywordAssignCard { background: #cce0ff; border-radius: 4px; border: 2px solid #3380cc; }"
            )
        else:
            self.setStyleSheet(
                "KeywordAssignCard { background: transparent; border-radius: 4px; }"
                "KeywordAssignCard:hover { background: #eef4ff; }"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.set_checked(not self._checked)
            self.toggled.emit(self._path, self._checked)
        super().mousePressEvent(event)


class KeywordAssignGrid(QWidget):
    status_message = Signal(str)

    def __init__(self, db: "FileDatabase", parent=None):
        super().__init__(parent)
        self._db = db
        
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ListView Setup
        self._view = QListView()
        self._view.setViewMode(QListView.IconMode)
        self._view.setResizeMode(QListView.Adjust)
        self._view.setSpacing(6)
        self._view.setUniformItemSizes(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setSelectionMode(QListView.NoSelection) # Selection handled by CheckedRole

        self._model = FileItemModel()
        self._view.setModel(self._model)

        self._delegate = ThumbnailDelegate(self, is_assign_mode=True)
        self._view.setItemDelegate(self._delegate)
        outer.addWidget(self._view, stretch=1)

        # Bottom assignment bar
        bar = QFrame()
        bar.setFrameShape(QFrame.HLine)
        bar.setFrameShadow(QFrame.Sunken)
        outer.addWidget(bar)

        assign_bar = QWidget()
        assign_bar.setStyleSheet("background: #f0f4ff;")
        bar_outer_layout = QVBoxLayout(assign_bar)
        bar_outer_layout.setContentsMargins(8, 6, 8, 6)
        bar_outer_layout.setSpacing(6)

        # Row 1: selection count + action buttons
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._sel_label = QLabel("0 files selected")
        self._sel_label.setStyleSheet("font-weight: bold; color: #333;")
        controls_row.addWidget(self._sel_label)

        controls_row.addStretch()

        select_all_btn = QPushButton("Select All")
        select_all_btn.setFixedHeight(28)
        select_all_btn.clicked.connect(self._select_all)
        controls_row.addWidget(select_all_btn)

        clear_btn = QPushButton("Clear Selection")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._clear_selection)
        controls_row.addWidget(clear_btn)

        bar_outer_layout.addLayout(controls_row)

        # Row 2: keyword entry on its own full-width row
        kw_row = QHBoxLayout()
        kw_row.setSpacing(8)

        kw_row.addWidget(QLabel("Keyword:"))
        self._kw_entry = QLineEdit()
        self._kw_entry.setPlaceholderText("Enter keywords to assign (comma-separated)…")
        self._kw_entry.setFixedHeight(28)
        self._kw_entry.returnPressed.connect(self._apply_keyword)
        kw_row.addWidget(self._kw_entry, stretch=1)

        apply_btn = QPushButton("Apply to Selected")
        apply_btn.setFixedHeight(28)
        apply_btn.clicked.connect(self._apply_keyword)
        kw_row.addWidget(apply_btn)

        bar_outer_layout.addLayout(kw_row)

        outer.addWidget(assign_bar)

        # Connections
        self._view.clicked.connect(self._on_clicked)
        self._model.dataChanged.connect(self._update_sel_label)
        self._delegate.render_requested.connect(self._queue_render)
        
        # Render queuing (Debouncing)
        self._render_queue = set()
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(150)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._process_render_queue)
        self._render_coordinator = None

    def _ensure_coordinator(self):
        if self._render_coordinator is None:
            coord = ThumbnailRenderCoordinator(parent=self)
            coord.rendered.connect(self._on_rendered)
            coord.progress.connect(
                lambda done, total: self.status_message.emit(
                    f"Generating thumbnails… {done}/{total}"))
            coord.finished_.connect(
                lambda: self.status_message.emit("Thumbnails ready."))
            self._render_coordinator = coord

    def _update_row_for_path(self, path: str):
        for row in range(self._model.rowCount()):
            idx = self._model.index(row, 0)
            if self._model.data(idx, FileItemModel.PathRole) == path:
                self._view.viewport().update(self._view.visualRect(idx))
                return

    def populate(self, rows: list):
        self.stop_render()
        self._render_queue.clear()
        self._delegate.clear_pending_renders()
        self._model.load_data(rows, keep_checked=True)
        self._update_sel_label()

    def _on_clicked(self, index):
        # Toggle checkbox state on item click
        current = self._model.data(index, FileItemModel.CheckedRole)
        self._model.setData(index, not current, FileItemModel.CheckedRole)

    def _queue_render(self, path: str):
        self._render_queue.add(path)
        self._render_timer.start()

    def _process_render_queue(self):
        if not self._render_queue: return
        paths_to_render = list(self._render_queue)
        self._render_queue.clear()
        self._start_render(paths_to_render)

    def _start_render(self, paths: list):
        self._ensure_coordinator()
        visible = _visible_paths(self._view, self._model)
        self._render_coordinator.enqueue(paths, visible_paths=visible)

    def _on_rendered(self, path: str, _image):
        self._update_row_for_path(path)

    def stop_render(self, wait: bool = False):
        self._render_timer.stop()
        self._render_queue.clear()
        self._delegate.clear_pending_renders()
        if self._render_coordinator is not None:
            if wait:
                self._render_coordinator.shutdown()
                self._render_coordinator = None
            else:
                self._render_coordinator.stop()

    def refresh_colors(self):
        if self._render_coordinator is not None:
            self._render_coordinator.refresh_render_scale()
        paths = [r[0] for r in self._model._rows]
        if paths:
            self._start_render(paths)

    def checked_paths(self) -> list:
        """Return paths currently checked for keyword assignment."""
        return list(self._model.get_checked_paths())

    def _select_all(self):
        self._model.set_all_checked(True)
        self._update_sel_label()

    def _clear_selection(self):
        self._model.set_all_checked(False)
        self._update_sel_label()

    def _update_sel_label(self, *args):
        n = len(self._model.get_checked_paths())
        self._sel_label.setText(f"{n} file{'s' if n != 1 else ''} selected")

    def _apply_keyword(self):
        raw      = self._kw_entry.text().strip()
        selected = self._model.get_checked_paths()

        if not raw:
            self.status_message.emit("Please enter a keyword before applying.")
            return
        if not selected:
            self.status_message.emit("No files selected — tick some thumbnails first.")
            return

        # Parse one or more comma-separated keywords from the entry field
        # (split on commas only, so multi-word keywords like "floral wreath"
        # keep their internal space)
        new_keywords = [kw.strip() for kw in raw.split(',') if kw.strip()]
        # Deduplicate while preserving order
        seen: set = set()
        new_keywords = [kw for kw in new_keywords if not (kw in seen or seen.add(kw))]

        if not new_keywords:
            self.status_message.emit("Please enter at least one keyword.")
            return

        updated = 0
        for path in selected:
            existing     = self._db.get_keywords(path)
            existing_set = {k.strip() for k in existing.split(',') if k.strip()}
            to_add       = [kw for kw in new_keywords if kw not in existing_set]
            if to_add:
                merged = existing + (', ' if existing else '') + ', '.join(to_add)
                self._db.set_keywords(path, merged.strip(', '))
                updated += 1

        kw_label = ', '.join(f"'{kw}'" for kw in new_keywords)
        n = len(selected)
        self.status_message.emit(
            f"{kw_label} assigned to {updated} file{'s' if updated != 1 else ''} "
            f"({n - updated} already had {'them' if len(new_keywords) > 1 else 'it'})."
        )
        self._kw_entry.clear()
