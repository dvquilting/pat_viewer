"""
The application's main window: menu bar, toolbar, file list / preview grid
/ keyword-assign grid (in a QStackedWidget), the pattern canvas, and the
file information panel. Ties together every other module in the app.
"""

import os
import sys
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QSizePolicy,
    QStatusBar, QFrame, QCheckBox, QSlider,
    QSplitter, QListView, QLineEdit,
    QTextEdit, QGroupBox, QStackedWidget, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QItemSelectionModel
from PySide6.QtGui import QColor, QAction, QKeySequence, QActionGroup

from models import PatFile
from database import (
    FileDatabase, FileLoaderThread, FileScanThread, PurgeMissingThread, normalize_path,
)
from canvas import PatCanvas
from file_item_model import FileListModel
from background import BatchConvertThread, JobDialog, convert_one_file

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Innova PAT / QLI Viewer")
        self.resize(1280, 800)
        self._db           = FileDatabase()
        self._scan_thread: Optional[FileScanThread] = None
        self._dup_thread = None
        self._loader_thread: Optional[FileLoaderThread] = None
        # Loaders still running after a newer request; kept referenced until done.
        self._stale_loaders: set = set()
        self._pending_load_path: Optional[str] = None
        self._purge_thread: Optional[PurgeMissingThread] = None
        # The one background job (folder listing or conversion) allowed at a time.
        self._job_thread = None
        self._job_dialog: Optional[JobDialog] = None
        self._dup_auto = False
        self._all_rows:    list[tuple[str,str,str,str]] = []
        self._current_path: Optional[str] = None
        self._thumb_grid = None
        self._kw_assign_grid = None
        self._thumb_placeholder = None
        self._kw_placeholder = None
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(250)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._run_search)
        self._build_ui()
        self._list_status.setText("Loading…")
        QTimer.singleShot(0, self._reload_list)
    # ----------------------------------------------------------------- UI build

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(4)

        # ---- Menu bar -----------------------------------------------
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        act_add_file = QAction("&Add File…", self)
        act_add_file.setShortcut(QKeySequence("Ctrl+O"))
        act_add_file.setStatusTip("Add one or more pattern files to the database")
        act_add_file.triggered.connect(self._add_file_dialog)
        file_menu.addAction(act_add_file)

        act_add_folder = QAction("Add &Folder…", self)
        act_add_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_add_folder.setStatusTip("Recursively scan a folder and add all pattern files found")
        act_add_folder.triggered.connect(self._add_folder_dialog)
        file_menu.addAction(act_add_folder)

        file_menu.addSeparator()

        act_remove = QAction("&Remove Selected", self)
        act_remove.setShortcut(QKeySequence("Del"))
        act_remove.setStatusTip("Remove the selected file from the database (does not delete the file on disk)")
        act_remove.triggered.connect(self._remove_selected)
        file_menu.addAction(act_remove)

        act_purge = QAction("&Purge Missing", self)
        act_purge.setStatusTip("Remove all database entries whose files no longer exist on disk")
        act_purge.triggered.connect(self._purge_missing)
        file_menu.addAction(act_purge)

        act_reset_db = QAction("Reset &Database…", self)
        act_reset_db.setStatusTip(
            "Remove all file entries from the database (files on disk are not deleted)"
        )
        act_reset_db.triggered.connect(self._reset_database)
        file_menu.addAction(act_reset_db)

        act_clear_cache = QAction("Clear &Thumbnail Cache", self)
        act_clear_cache.setStatusTip(
            "Delete all cached thumbnail images from disk — they will be re-rendered on next view"
        )
        act_clear_cache.triggered.connect(self._clear_thumbnail_cache)
        file_menu.addAction(act_clear_cache)

        act_merge_dupes = QAction("&Merge Duplicates…", self)
        act_merge_dupes.setStatusTip(
            "Find files with byte-identical content (same type only) and remove the extra copies"
        )
        act_merge_dupes.triggered.connect(lambda: self._find_and_merge_duplicates(auto=False))
        file_menu.addAction(act_merge_dupes)

        file_menu.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.setStatusTip("Exit the application")
        act_quit.triggered.connect(QApplication.instance().quit)
        file_menu.addAction(act_quit)

        # View menu
        view_menu = menubar.addMenu("&View")

        act_list = QAction("&List View", self)
        act_list.setShortcut(QKeySequence("Ctrl+L"))
        act_list.setStatusTip("Show files as a sorted list")
        act_list.setCheckable(True)
        act_list.setChecked(True)
        view_menu.addAction(act_list)

        act_preview = QAction("&Thumbnail Mode", self)
        act_preview.setShortcut(QKeySequence("Ctrl+P"))
        act_preview.setStatusTip("Show files as a thumbnail grid")
        act_preview.setCheckable(True)
        act_preview.setChecked(False)
        view_menu.addAction(act_preview)

        act_assign = QAction("&Assign Keywords", self)
        act_assign.setShortcut(QKeySequence("Ctrl+K"))
        act_assign.setStatusTip("Select multiple files and assign keywords to them in bulk")
        act_assign.setCheckable(True)
        act_assign.setChecked(False)
        view_menu.addAction(act_assign)

        view_group = QActionGroup(self)
        view_group.addAction(act_list)
        view_group.addAction(act_preview)
        view_group.addAction(act_assign)
        view_group.setExclusive(True)

        self._act_list    = act_list
        self._act_preview = act_preview
        self._act_assign  = act_assign
        act_list.triggered.connect(lambda: self._set_view_mode(0))
        act_preview.triggered.connect(lambda: self._set_view_mode(1))
        act_assign.triggered.connect(lambda: self._set_view_mode(2))

        view_menu.addSeparator()

        act_line_color = QAction("Line &Color…", self)
        act_line_color.setStatusTip("Choose the colour used to draw patterns and thumbnails")
        act_line_color.triggered.connect(self._choose_line_color)
        view_menu.addAction(act_line_color)

        act_show_keywords = QAction("Show &Keywords List…", self)
        act_show_keywords.setStatusTip(
            "Show every keyword currently in use, with how many files use each one"
        )
        act_show_keywords.triggered.connect(self._show_keywords_list)
        view_menu.addAction(act_show_keywords)

        # Convert menu
        convert_menu = menubar.addMenu("&Convert")

        act_convert_svg = QAction("Convert &SVG to PAT…", self)
        act_convert_svg.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_convert_svg.setStatusTip(
            "Convert an SVG vector file to an Innova PAT quilting pattern file"
        )
        act_convert_svg.triggered.connect(self._convert_svg_dialog)
        convert_menu.addAction(act_convert_svg)

        act_convert_svg_qli = QAction("Convert S&VG to QLI…", self)
        act_convert_svg_qli.setShortcut(QKeySequence("Ctrl+Shift+V"))
        act_convert_svg_qli.setStatusTip(
            "Convert an SVG vector file to a Statler Stitcher QLI quilting pattern file"
        )
        act_convert_svg_qli.triggered.connect(self._convert_svg_to_qli_dialog)
        convert_menu.addAction(act_convert_svg_qli)

        act_convert_qli = QAction("Convert &QLI to PAT…", self)
        act_convert_qli.setShortcut(QKeySequence("Ctrl+Shift+Q"))
        act_convert_qli.setStatusTip(
            "Convert a Statler Stitcher QLI file to an Innova PAT quilting pattern file"
        )
        act_convert_qli.triggered.connect(self._convert_qli_dialog)
        convert_menu.addAction(act_convert_qli)

        convert_menu.addSeparator()

        act_convert_folder = QAction("Convert &Folder…", self)
        act_convert_folder.setStatusTip(
            "Batch-convert files in a folder (QLI→PAT, SVG→PAT, or SVG→QLI; no subfolders)"
        )
        act_convert_folder.triggered.connect(
            lambda: self._convert_folder_dialog(recursive=False))
        convert_menu.addAction(act_convert_folder)

        act_convert_folder_tree = QAction("Convert Folder and &Subfolders…", self)
        act_convert_folder_tree.setStatusTip(
            "Batch-convert files in a folder and its subfolders "
            "(QLI→PAT, SVG→PAT, or SVG→QLI)"
        )
        act_convert_folder_tree.triggered.connect(
            lambda: self._convert_folder_dialog(recursive=True))
        convert_menu.addAction(act_convert_folder_tree)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        act_help = QAction("&Help", self)
        act_help.setShortcut(QKeySequence("F1"))
        act_help.setStatusTip("Show help information")
        act_help.triggered.connect(self._show_help)
        help_menu.addAction(act_help)

        help_menu.addSeparator()

        act_about = QAction("&About", self)
        act_about.setStatusTip("About this application")
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        # ---- Toolbar (canvas controls only) -------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._travel_cb = QCheckBox("Show travel moves")
        self._travel_cb.setChecked(False)
        self._travel_cb.toggled.connect(self._toggle_travel)
        toolbar.addWidget(self._travel_cb)

        self._markers_cb = QCheckBox("Show markers")
        self._markers_cb.setChecked(False)
        self._markers_cb.toggled.connect(self._toggle_markers)
        toolbar.addWidget(self._markers_cb)

        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("Line width:"))
        self._lw_slider = QSlider(Qt.Horizontal)
        self._lw_slider.setRange(10, 400)
        self._lw_slider.setValue(100)
        self._lw_slider.setFixedWidth(100)
        self._lw_slider.setToolTip(
            "Line thickness on screen (constant regardless of zoom level or file size)"
        )
        self._lw_slider.valueChanged.connect(
            lambda v: (setattr(self._canvas, 'line_width', float(v)),
                       self._canvas.update()))
        toolbar.addWidget(self._lw_slider)

        toolbar.addStretch()
        self._info_label = QLabel("")
        self._info_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(self._info_label)

        root_layout.addLayout(toolbar)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root_layout.addWidget(sep)

        # ---- Splitter: file list (left) + canvas (right) ------------
        splitter = QSplitter(Qt.Horizontal)

        # Left pane
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        # Search bar (shared between both modes)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search files…")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search_box)

        # Search scope checkboxes: filename and/or keyword
        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(2, 0, 2, 2)
        scope_row.setSpacing(10)
        scope_row.addWidget(QLabel("Search by:"))

        self._search_by_name_cb = QCheckBox("Filename")
        self._search_by_name_cb.setChecked(True)
        self._search_by_name_cb.toggled.connect(self._on_search_scope_changed)
        scope_row.addWidget(self._search_by_name_cb)

        self._search_by_keyword_cb = QCheckBox("Keyword")
        self._search_by_keyword_cb.setChecked(True)
        self._search_by_keyword_cb.toggled.connect(self._on_search_scope_changed)
        scope_row.addWidget(self._search_by_keyword_cb)

        scope_row.addStretch()
        left_layout.addLayout(scope_row)

        # Stacked widget: page 0 = list, page 1 = thumbnail grid
        self._stack = QStackedWidget()

        # Page 0: flat file list
        list_page = QWidget()
        list_page_layout = QVBoxLayout(list_page)
        list_page_layout.setContentsMargins(0, 0, 0, 0)
        list_page_layout.setSpacing(0)
        # A view on a list model: filtering resets the model instead of
        # rebuilding one QListWidgetItem per file on every keystroke.
        self._file_list = QListView()
        self._file_list.setMinimumWidth(220)
        self._file_list.setUniformItemSizes(True)
        self._list_model = FileListModel(self)
        self._file_list.setModel(self._list_model)
        self._file_list.selectionModel().currentChanged.connect(self._on_list_selection)
        list_page_layout.addWidget(self._file_list)
        self._stack.addWidget(list_page)

        # Pages 1–2: lightweight placeholders until Thumbnail / Assign mode is opened
        self._thumb_placeholder = QWidget()
        self._stack.addWidget(self._thumb_placeholder)
        self._kw_placeholder = QWidget()
        self._stack.addWidget(self._kw_placeholder)

        left_layout.addWidget(self._stack, stretch=1)

        self._list_status = QLabel("")
        self._list_status.setStyleSheet("font-size: 10px; color: #555; padding: 2px;")
        left_layout.addWidget(self._list_status)

        splitter.addWidget(left)

        # Right pane: vertical splitter with canvas (top) + info panel (bottom)
        right_splitter = QSplitter(Qt.Vertical)

        self._canvas = PatCanvas()
        right_splitter.addWidget(self._canvas)

        # Info panel
        info_panel = QGroupBox("File Information")
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(6, 6, 6, 6)
        info_layout.setSpacing(4)

        # File name row
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("<b>File:</b>"))
        self._info_name = QLabel("—")
        self._info_name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_row.addWidget(self._info_name, stretch=1)
        info_layout.addLayout(name_row)

        # Full path row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("<b>Path:</b>"))
        self._info_path = QLabel("—")
        self._info_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._info_path.setWordWrap(True)
        self._info_path.setStyleSheet("color: #444;")
        path_row.addWidget(self._info_path, stretch=1)
        info_layout.addLayout(path_row)

        # Dimensions row (pattern extent in X/Y, PAT units)
        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel("<b>Dimensions:</b>"))
        self._info_dimensions = QLabel("—")
        self._info_dimensions.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._info_dimensions.setStyleSheet("color: #444;")
        dim_row.addWidget(self._info_dimensions, stretch=1)
        info_layout.addLayout(dim_row)

        # Comments
        info_layout.addWidget(QLabel("<b>Comments:</b>"))
        self._info_comments = QTextEdit()
        self._info_comments.setReadOnly(True)
        self._info_comments.setPlaceholderText("No comments in this file.")
        self._info_comments.setFixedHeight(90)
        self._info_comments.setStyleSheet(
            "background: #f8f8f8; border: 1px solid #ccc; font-family: monospace;"
        )
        info_layout.addWidget(self._info_comments)

        # Keywords (user-editable, persisted to DB)
        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("<b>Keywords:</b>"))
        self._info_keywords = QLineEdit()
        self._info_keywords.setPlaceholderText("Enter keywords, separated by commas…")
        self._info_keywords.setToolTip(
            "Enter one or more keywords separated by commas (e.g. \"floral, wreath, border\").\n"
            "Keywords are saved automatically when you move away and are searchable.\n"
            "Duplicates are removed automatically."
        )
        self._info_keywords.editingFinished.connect(self._save_keywords)
        kw_row.addWidget(self._info_keywords, stretch=1)
        info_layout.addLayout(kw_row)

        right_splitter.addWidget(info_panel)
        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setSizes([640, 160])

        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 960])

        root_layout.addWidget(splitter, stretch=1)

        # ---- Status bar ---------------------------------------------
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status = QLabel("Use File > Add File or Add Folder to populate the database.")
        sb.addWidget(self._status, 1)
        sb.addPermanentWidget(QLabel(
            "Scroll=zoom  |  Drag=pan  |  F/Home=fit  |  +/-=zoom"
        ))

    def closeEvent(self, event):
        # Wait for thumbnail workers so no late signals touch destroyed widgets.
        if self._thumb_grid is not None:
            self._thumb_grid.stop_render(wait=True)
        if self._kw_assign_grid is not None:
            self._kw_assign_grid.stop_render(wait=True)

        for loader in [self._loader_thread, *self._stale_loaders]:
            if loader is not None and loader.isRunning():
                loader.wait()
        if self._job_thread is not None:
            if hasattr(self._job_thread, "stop"):
                self._job_thread.stop()
            self._job_thread.requestInterruption()
            self._job_thread.wait()
        if self._purge_thread is not None and self._purge_thread.isRunning():
            self._purge_thread.wait()

        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.stop()
            self._scan_thread.wait()
        if self._dup_thread and self._dup_thread.isRunning():
            self._dup_thread.stop()
            self._dup_thread.quit()
            self._dup_thread.wait()

        from thumbnail_cache import _thumb_cache
        _thumb_cache.close()
        super().closeEvent(event)

    # ----------------------------------------------------------------- lazy thumbnail grids

    def _ensure_thumb_grid(self):
        if self._thumb_grid is not None:
            return self._thumb_grid
        from thumbnail_widgets import ThumbnailGrid
        self._thumb_grid = ThumbnailGrid()
        self._thumb_grid.file_selected.connect(self._on_thumb_selected)
        self._thumb_grid.render_progress.connect(self._on_thumb_progress)
        if self._thumb_placeholder is not None:
            idx = self._stack.indexOf(self._thumb_placeholder)
            self._stack.removeWidget(self._thumb_placeholder)
            self._thumb_placeholder.deleteLater()
            self._thumb_placeholder = None
            self._stack.insertWidget(idx, self._thumb_grid)
        return self._thumb_grid

    def _ensure_kw_assign_grid(self):
        if self._kw_assign_grid is not None:
            return self._kw_assign_grid
        from thumbnail_widgets import KeywordAssignGrid
        self._kw_assign_grid = KeywordAssignGrid(self._db)
        self._kw_assign_grid.status_message.connect(
            lambda msg: self._status.setText(msg))
        if self._kw_placeholder is not None:
            idx = self._stack.indexOf(self._kw_placeholder)
            self._stack.removeWidget(self._kw_placeholder)
            self._kw_placeholder.deleteLater()
            self._kw_placeholder = None
            self._stack.insertWidget(idx, self._kw_assign_grid)
        return self._kw_assign_grid

    # ----------------------------------------------------------------- list management

    def _reload_list(self, preserve_selection: str = ""):
        """Reload _all_rows from DB and refresh the displayed list."""
        self._all_rows = self._db.all_files()
        self._apply_filter(self._search_box.text(), preserve_selection)

    def _search_scope(self) -> str:
        """
        Translate the Filename/Keyword checkboxes into a FileDatabase.search()
        scope string. If the user unchecks both (leaving nothing to search),
        treat that the same as both checked rather than matching nothing.
        """
        by_name = self._search_by_name_cb.isChecked()
        by_kw   = self._search_by_keyword_cb.isChecked()
        if by_name and by_kw:
            return 'both'
        if by_name:
            return 'name'
        if by_kw:
            return 'keywords'
        return 'both'   # neither checked — fall back to searching everything

    def _on_search_scope_changed(self, _checked: bool):
        # Prevent both checkboxes from being unchecked at once — at least
        # one scope must remain active so the search bar still does something.
        if not self._search_by_name_cb.isChecked() and not self._search_by_keyword_cb.isChecked():
            sender = self.sender()
            # Re-check whichever box the user just unchecked
            sender.blockSignals(True)
            sender.setChecked(True)
            sender.blockSignals(False)
            return
        self._apply_filter(self._search_box.text())

    def _apply_filter(self, query: str, preserve_selection: str = ""):
        """Filter the visible list (and grid) by query string."""
        if query.strip():
            rows = self._db.search(query.strip(), scope=self._search_scope())
        else:
            rows = self._all_rows

        # ---- Update flat list ----
        self._list_model.load_data(rows)
        current = preserve_selection or self._current_path or ""
        row = self._list_model.row_for_path(current) if current else -1
        if row >= 0:
            # Restore the current row (so arrow keys continue from it)
            # without reopening the file.
            selection = self._file_list.selectionModel()
            selection.blockSignals(True)
            selection.setCurrentIndex(self._list_model.index(row, 0),
                                      QItemSelectionModel.ClearAndSelect)
            selection.blockSignals(False)
            if preserve_selection:
                self._file_list.scrollTo(self._list_model.index(row, 0))

        # ---- Update thumbnail grids only when that view is active ----
        if self._stack.currentIndex() == 1 and self._thumb_grid is not None:
            self._thumb_grid.populate(rows)
            if preserve_selection:
                self._thumb_grid.select(preserve_selection)
        elif self._stack.currentIndex() == 2 and self._kw_assign_grid is not None:
            self._kw_assign_grid.populate(rows)

        shown = len(rows)
        total = shown if not query.strip() else self._db.count()
        if query.strip():
            self._list_status.setText(
                f"{shown} match{'es' if shown != 1 else ''} / {total} total"
            )
        else:
            self._list_status.setText(
                f"{total} file{'s' if total != 1 else ''} in database"
            )

    def _on_search_changed(self, text: str):
        self._search_timer.start()

    def _run_search(self):
        self._apply_filter(self._search_box.text())

    def _on_list_selection(self, current, _previous):
        if current is None or not current.isValid():
            return
        path = current.data(FileListModel.PathRole)
        if path:
            self._load_and_display(path)

    def _on_thumb_selected(self, path: str):
        self._load_and_display(path)

    def _on_thumb_progress(self, done: int, total: int, finished: bool):
        """Show a 'generating thumbnails' indicator while the render thread works."""
        if finished:
            # Restore the normal file-count text once rendering completes
            total_files = self._db.count()
            self._list_status.setText(
                f"{total_files} file{'s' if total_files != 1 else ''} in database"
            )
            self._status.setText("Thumbnails ready.")
        else:
            self._list_status.setText(f"⏳ Generating thumbnails… {done}/{total}")
            self._status.setText(f"Generating thumbnails… {done}/{total}")

    def _set_view_mode(self, mode: int):
        """
        Switch between view modes:
          0 = List view
          1 = Thumbnail mode (thumbnail grid)
          2 = Assign Keywords (multi-select thumbnail grid)
        """
        query = self._search_box.text()
        rows  = self._db.search(query.strip(), scope=self._search_scope()) if query.strip() else self._all_rows

        if mode == 0:
            if self._thumb_grid is not None:
                self._thumb_grid.stop_render()
            if self._kw_assign_grid is not None:
                self._kw_assign_grid.stop_render()
            self._stack.setCurrentIndex(0)
        elif mode == 1:
            if self._kw_assign_grid is not None:
                self._kw_assign_grid.stop_render()
            grid = self._ensure_thumb_grid()
            self._stack.setCurrentIndex(1)
            grid.populate(rows)
            if self._current_path:
                grid.select(self._current_path)
        elif mode == 2:
            if self._thumb_grid is not None:
                self._thumb_grid.stop_render()
            grid = self._ensure_kw_assign_grid()
            self._stack.setCurrentIndex(2)
            grid.populate(rows)

        # Keep menu actions in sync
        self._act_list.setChecked(mode == 0)
        self._act_preview.setChecked(mode == 1)
        self._act_assign.setChecked(mode == 2)

    def _show_help(self):
        """Show a scrollable help dialog covering all current features."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout as _QVBox, QTextBrowser, QPushButton as _QPushButton,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Help — Innova PAT / QLI Viewer")
        dlg.resize(640, 720)
        layout = _QVBox(dlg)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._help_html())
        layout.addWidget(browser)

        close_btn = _QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)

        dlg.exec()

    def _help_html(self) -> str:
        """Full help content as HTML for the Help dialog."""
        return """
<h2>Innova PAT / QLI Viewer — Help</h2>
<p>View and organize <b>Innova PAT</b>, <b>Statler Stitcher QLI</b>, and
<b>SVG</b> quilting patterns. Files are stored in a persistent SQLite database
(<code>~/.pat_viewer.db</code>) with optional keywords for searching. Thumbnail
images are cached under <code>~/.pat_viewer_thumbs</code>.</p>

<h3>Supported formats</h3>
<ul>
  <li><b>PAT</b> — native Innova pattern format (G00 travel, G01 linear stitch,
      G02/G03 arcs)</li>
  <li><b>QLI</b> — Statler Stitcher files (converted to PAT for display)</li>
  <li><b>SVG</b> — vector graphics (converted to stitch paths for display)</li>
</ul>
<p>In the file list, names are colour-coded: <span style="color:#0064b4">blue</span>
for QLI, <span style="color:#a03c00">brown</span> for SVG,
<span style="color:#14823c">green</span> for PAT.</p>

<h3>File menu</h3>
<ul>
  <li><b>Add File…</b> (Ctrl+O) — add one or more .pat / .qli / .svg files to the
      database. Files already present are skipped.</li>
  <li><b>Add Folder…</b> (Ctrl+Shift+O) — recursively scan a folder for pattern
      files and add them. After a scan, identical duplicates are detected
      automatically when new files were added.</li>
  <li><b>Remove Selected</b> (Del) — remove the current selection from the
      database only (files on disk are never deleted).
      <ul>
        <li><i>List View</i> — the highlighted list item</li>
        <li><i>Thumbnail Mode</i> — the selected thumbnail, or the file open on
            the canvas</li>
        <li><i>Assign Keywords</i> — all checked thumbnails</li>
      </ul>
  </li>
  <li><b>Purge Missing</b> — remove database entries whose files no longer exist
      on disk.</li>
  <li><b>Reset Database…</b> — after confirmation, remove <i>all</i> file entries
      from the database. Disk files are not deleted. This cannot be undone.</li>
  <li><b>Clear Thumbnail Cache</b> — delete cached thumbnail images; they are
      re-rendered the next time Thumbnail mode is opened.</li>
  <li><b>Merge Duplicates…</b> — find files with byte-identical content
      (compared only within the same extension) and keep one database entry per
      set. Duplicate files on disk are not deleted.</li>
  <li><b>Quit</b> (Ctrl+Q) — exit the application.</li>
</ul>

<h3>View menu</h3>
<ul>
  <li><b>List View</b> (Ctrl+L) — sorted file list; click a row to open it on the
      canvas.</li>
  <li><b>Thumbnail Mode</b> (Ctrl+P) — scrollable grid of pattern previews.
      Thumbnails render in the background as you scroll; click one to open it.</li>
  <li><b>Assign Keywords</b> (Ctrl+K) — multi-select thumbnail grid. Click
      thumbnails (or their checkboxes) to select, enter one or more
      comma-separated keywords, then <b>Apply to Selected</b>.
      Use <b>Select All</b> / <b>Clear Selection</b> as needed.</li>
  <li><b>Line Color…</b> — colour used for stitch lines on the canvas and in
      thumbnails. Changing colour re-renders the visible thumbnail grid.</li>
  <li><b>Show Keywords List…</b> — every keyword in use with file counts.
      Double-click a keyword to put it in the search box and filter the library.</li>
</ul>

<h3>Convert menu</h3>
<p>Conversions write an output file next to the source (or a path you choose for
single-file convert) and add it to the database.</p>
<ul>
  <li><b>Convert SVG to PAT…</b> (Ctrl+Shift+S) — pick one SVG, choose the output
      PAT path, convert and open it.</li>
  <li><b>Convert SVG to QLI…</b> (Ctrl+Shift+V) — pick one SVG, choose the output
      QLI path (Statler Stitcher format), convert and open it.</li>
  <li><b>Convert QLI to PAT…</b> (Ctrl+Shift+Q) — pick one or more QLI files.
      For multiple files, each PAT is written beside the source; existing PATs
      prompt for overwrite (Yes / No / Yes to All).</li>
  <li><b>Convert Folder…</b> — batch-convert files in one folder (not subfolders).
      You choose the conversion: QLI→PAT, SVG→PAT, or SVG→QLI.</li>
  <li><b>Convert Folder and Subfolders…</b> — same, including nested folders.
      Overwrite prompts include Cancel to stop early. Failures are summarized
      at the end.</li>
</ul>

<h3>Search</h3>
<p>Use the search box above the file list to filter the library. Scope
checkboxes control what is matched:</p>
<ul>
  <li><b>Filename</b> — match the file name</li>
  <li><b>Keyword</b> — match user keywords</li>
</ul>
<p>At least one scope must stay enabled. Search is case-insensitive and updates
as you type (short debounce). Clearing the box shows the full library again.</p>

<h3>Canvas and display</h3>
<p>The right pane shows the loaded pattern. Controls:</p>
<ul>
  <li><b>Scroll wheel</b> — zoom in / out (toward cursor)</li>
  <li><b>Click and drag</b> — pan</li>
  <li><b>F</b> or <b>Home</b> — fit the pattern to the window</li>
  <li><b>+</b> / <b>−</b> — zoom in / out</li>
  <li><b>Show travel moves</b> — draw G00 travel paths</li>
  <li><b>Show markers</b> — mark stitch endpoints</li>
  <li><b>Line width</b> slider — on-screen line thickness (constant across zoom
      and pattern size)</li>
</ul>

<h3>File Information panel</h3>
<ul>
  <li><b>File</b> / <b>Path</b> — name and full path of the open file</li>
  <li><b>Dimensions</b> — pattern extent in PAT units</li>
  <li><b>Comments</b> — comments embedded in the pattern file (read-only)</li>
  <li><b>Keywords</b> — comma-separated tags you define (e.g.
      <code>floral, wreath, border</code>). Saved when you leave the field
      (Tab / click away). Duplicates are removed automatically. Keywords are
      searchable and can be assigned in bulk via Assign Keywords mode.</li>
</ul>

<h3>Keyboard shortcuts</h3>
<table cellpadding="4" cellspacing="0" border="0">
  <tr><td><b>Ctrl+O</b></td><td>Add File…</td></tr>
  <tr><td><b>Ctrl+Shift+O</b></td><td>Add Folder…</td></tr>
  <tr><td><b>Del</b></td><td>Remove Selected</td></tr>
  <tr><td><b>Ctrl+Q</b></td><td>Quit</td></tr>
  <tr><td><b>Ctrl+L</b></td><td>List View</td></tr>
  <tr><td><b>Ctrl+P</b></td><td>Thumbnail Mode</td></tr>
  <tr><td><b>Ctrl+K</b></td><td>Assign Keywords</td></tr>
  <tr><td><b>Ctrl+Shift+S</b></td><td>Convert SVG to PAT…</td></tr>
  <tr><td><b>Ctrl+Shift+V</b></td><td>Convert SVG to QLI…</td></tr>
  <tr><td><b>Ctrl+Shift+Q</b></td><td>Convert QLI to PAT…</td></tr>
  <tr><td><b>F1</b></td><td>This help</td></tr>
  <tr><td><b>F</b> / <b>Home</b></td><td>Fit pattern (canvas focused)</td></tr>
  <tr><td><b>+</b> / <b>−</b></td><td>Zoom (canvas focused)</td></tr>
</table>

<h3>Tips</h3>
<ul>
  <li>Database changes (add / remove / keywords) never delete pattern files on
      disk — only the library index is updated.</li>
  <li>After Add Folder or Add File, the app may offer to merge content
      duplicates automatically.</li>
  <li>Thumbnail generation runs in the background; scrolling large libraries is
      normal while placeholders fill in.</li>
  <li>Splitter handles between list, canvas, and info panel can be dragged to
      resize panes.</li>
</ul>
"""

    def _show_about(self):
        QMessageBox.about(self, "About Innova PAT / QLI Viewer", (
            "<h3>Innova PAT / QLI Viewer</h3>"
            "<p>A viewer for Innova PAT and Statler Stitcher QLI quilting pattern files.</p>"
            "<p>Supports G00/G01 linear moves and G02/G03 circular arc moves. "
            "QLI files are converted to PAT format internally before display.</p>"
            "<p>Pattern files are stored in a persistent SQLite database with "
            "user-defined keywords for easy searching.</p>"
        ))

    def _convert_qli_dialog(self):
        """Prompt for one or more QLI files, convert each to PAT, save, and display the last."""
        qli_paths, _ = QFileDialog.getOpenFileNames(
            self, "Select QLI File(s) to Convert", "",
            "QLI Files (*.qli *.QLI);;All Files (*)"
        )
        if not qli_paths:
            return

        if len(qli_paths) == 1:
            default_out = os.path.splitext(qli_paths[0])[0] + ".pat"
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Save PAT File As", default_out,
                "PAT Files (*.pat);;All Files (*)"
            )
            if not out_path:
                return
            try:
                convert_one_file(qli_paths[0], out_path, 'qli_to_pat')
            except Exception as e:
                QMessageBox.critical(
                    self, "Conversion Error",
                    f"Failed to convert '{os.path.basename(qli_paths[0])}':\n\n{e}"
                )
                return
            self._db.add(out_path)
            self._reload_list()
            self._load_and_display(out_path)
            self._status.setText("Converted 1 QLI file to PAT and added to database.")
            return

        # Several files: each PAT is written beside its source, in the background.
        jobs = [(p, os.path.splitext(p)[0] + ".pat") for p in qli_paths]
        existing = sum(1 for _src, out in jobs if os.path.exists(out))
        self._start_batch_convert(jobs, existing, 'qli_to_pat')

    # ----------------------------------------------------------------- SVG conversion

    def _convert_svg_dialog(self):
        """Prompt for an SVG file, convert it to PAT, save, and display it."""
        svg_path, _ = QFileDialog.getOpenFileName(
            self, "Select SVG File to Convert", "",
            "SVG Files (*.svg *.SVG);;All Files (*)"
        )
        if not svg_path:
            return

        # Suggest output path alongside the SVG
        default_out = os.path.splitext(svg_path)[0] + ".pat"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save PAT File As", default_out,
            "PAT Files (*.pat);;All Files (*)"
        )
        if not out_path:
            return

        try:
            from svg_converter import svg_to_pat_file
            svg_to_pat_file(svg_path, out_path)
        except ImportError as e:
            QMessageBox.critical(self, "Missing Dependency", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Conversion Error",
                                 f"Failed to convert SVG:\n\n{e}")
            return

        # Add to database and display
        self._db.add(out_path)
        self._reload_list()
        self._load_and_display(out_path)
        self._status.setText(
            f"Converted '{os.path.basename(svg_path)}' → "
            f"'{os.path.basename(out_path)}' and added to database."
        )

    def _convert_svg_to_qli_dialog(self):
        """Prompt for an SVG file, convert it to QLI, save, and display it."""
        svg_path, _ = QFileDialog.getOpenFileName(
            self, "Select SVG File to Convert", "",
            "SVG Files (*.svg *.SVG);;All Files (*)"
        )
        if not svg_path:
            return

        default_out = os.path.splitext(svg_path)[0] + ".qli"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save QLI File As", default_out,
            "QLI Files (*.qli);;All Files (*)"
        )
        if not out_path:
            return
        if not out_path.lower().endswith('.qli'):
            out_path += '.qli'

        try:
            from svg_converter import svg_to_qli_file
            svg_to_qli_file(svg_path, out_path)
        except ImportError as e:
            QMessageBox.critical(self, "Missing Dependency", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Conversion Error",
                                 f"Failed to convert SVG to QLI:\n\n{e}")
            return

        self._db.add(out_path)
        self._reload_list()
        self._load_and_display(out_path)
        self._status.setText(
            f"Converted '{os.path.basename(svg_path)}' → "
            f"'{os.path.basename(out_path)}' and added to database."
        )

    # ----------------------------------------------------------------- folder conversion

    # mode_id → (menu/radio label, source extensions, output extension, source name)
    _FOLDER_CONVERT_MODES = {
        'qli_to_pat': ('QLI → PAT', ('.qli',), '.pat', 'QLI'),
        'svg_to_pat': ('SVG → PAT', ('.svg',), '.pat', 'SVG'),
        'svg_to_qli': ('SVG → QLI', ('.svg',), '.qli', 'SVG'),
    }

    def _choose_folder_convert_mode(self, recursive: bool) -> Optional[str]:
        """
        Ask which batch conversion to run. Returns a mode key from
        _FOLDER_CONVERT_MODES, or None if the user cancels.
        """
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout as _QVBox, QHBoxLayout as _QHBox,
            QRadioButton, QButtonGroup, QPushButton as _QPushButton,
            QLabel as _QLabel,
        )

        title = (
            "Convert Folder and Subfolders"
            if recursive else
            "Convert Folder"
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(360)
        layout = _QVBox(dlg)
        layout.setSpacing(8)

        scope = (
            "in the chosen folder and all subfolders"
            if recursive else
            "in the chosen folder (not subfolders)"
        )
        layout.addWidget(_QLabel(
            f"<b>Which conversion?</b><br>"
            f"Matching files {scope} will be converted. "
            f"Output files are written next to each source."
        ))

        group = QButtonGroup(dlg)
        radios: dict[str, QRadioButton] = {}
        for i, (mode_id, (label, _exts, out_ext, src_name)) in enumerate(
                self._FOLDER_CONVERT_MODES.items()):
            rb = QRadioButton(f"{label}  (.{src_name.lower()} → {out_ext})")
            if i == 0:
                rb.setChecked(True)
            group.addButton(rb)
            radios[mode_id] = rb
            layout.addWidget(rb)

        btn_row = _QHBox()
        btn_row.addStretch()
        cancel_btn = _QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = _QPushButton("Choose Folder…")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return None
        for mode_id, rb in radios.items():
            if rb.isChecked():
                return mode_id
        return None

    def _convert_folder_dialog(self, recursive: bool = False):
        """Choose conversion type, pick a folder, then batch-convert."""
        mode = self._choose_folder_convert_mode(recursive)
        if not mode:
            return

        label, _exts, out_ext, src_name = self._FOLDER_CONVERT_MODES[mode]
        title = (
            f"Convert Folder and Subfolders — {label}"
            if recursive else
            f"Convert Folder — {label}"
        )
        folder = QFileDialog.getExistingDirectory(
            self, title, "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not folder:
            return
        self._convert_folder(folder, recursive=recursive, mode=mode)

    def _collect_convertible_files(
        self, folder: str, recursive: bool, source_exts: tuple
    ) -> list[str]:
        """Return sorted paths of matching source files (optionally recursive)."""
        from background import collect_convertible_files
        try:
            return collect_convertible_files(folder, recursive, source_exts)
        except OSError as e:
            QMessageBox.critical(self, "Folder Error", f"Could not read folder:\n\n{e}")
            return []

    def _convert_one_file(self, src_path: str, out_path: str, mode: str) -> None:
        """Run a single conversion for the given folder-batch mode. Raises on failure."""
        convert_one_file(src_path, out_path, mode)

    def _convert_folder(self, folder: str, recursive: bool = False,
                        mode: str = 'qli_to_pat'):
        """
        Convert matching source files under folder using the chosen mode.
        Listing the folder, checking for existing outputs and converting all
        run in the background.
        """
        if mode not in self._FOLDER_CONVERT_MODES:
            raise ValueError(f"Unknown conversion mode: {mode}")
        if self._job_busy():
            return

        label, source_exts, out_ext, src_name = self._FOLDER_CONVERT_MODES[mode]
        from background import CollectConvertibleThread
        thread = CollectConvertibleThread(folder, recursive, source_exts, out_ext, self)
        dialog = JobDialog(f"Convert Folder — {label}", f"Looking for {src_name} files…",
                           thread.requestInterruption, self)

        def collected(jobs, existing, error):
            self._finish_job(thread, dialog)
            if thread.isInterruptionRequested():
                self._status.setText("Folder conversion cancelled.")
            elif error:
                QMessageBox.critical(self, "Folder Error", f"Could not read folder:\n\n{error}")
            elif not jobs:
                scope = "folder and subfolders" if recursive else "folder"
                QMessageBox.information(self, "No Files Found",
                                        f"No {src_name} files found in this {scope}.")
            else:
                self._start_batch_convert(jobs, existing, mode)

        thread.finished_.connect(collected)
        self._start_job(thread, dialog)

    def _start_batch_convert(self, jobs: list, existing: int, mode: str):
        """Convert (source, output) pairs in the background, asking once about overwrites."""
        if self._job_busy():
            return
        label, _exts, out_ext, _src = self._FOLDER_CONVERT_MODES[mode]
        overwrite = False
        if existing:
            # One question for the whole batch instead of one per existing file.
            box = QMessageBox(
                QMessageBox.Question, f"Overwrite {out_ext.lstrip('.').upper()} Files?",
                f"{existing} of the {len(jobs)} output file"
                f"{'s' if len(jobs) != 1 else ''} already exist.",
                QMessageBox.NoButton, self)
            overwrite_btn = box.addButton("Overwrite", QMessageBox.AcceptRole)
            skip_btn = box.addButton("Skip Existing", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(skip_btn)
            box.exec()
            if box.clickedButton() not in (overwrite_btn, skip_btn):
                self._status.setText("Conversion cancelled.")
                return
            overwrite = box.clickedButton() is overwrite_btn

        thread = BatchConvertThread(jobs, mode, overwrite, self)
        dialog = JobDialog(label, f"Converting {len(jobs)} files…", thread.stop, self)
        thread.progress.connect(
            lambda done, total, name: dialog.set_progress(
                done, total, f"{label}: {done + 1} of {total}\n{name}"))

        def finished(converted, skipped, errors, cancelled):
            self._finish_job(thread, dialog)
            self._on_batch_convert_done(label, len(jobs), converted, skipped, errors, cancelled)

        thread.finished_.connect(finished)
        self._start_job(thread, dialog)

    def _on_batch_convert_done(self, label: str, total: int, converted: list,
                               skipped: int, errors: list, cancelled: bool):
        if converted:
            # Files converted before a cancel exist on disk, so they are kept.
            self._db.add_many(converted)
            self._reload_list()
            self._load_and_display(converted[-1])

        parts = [
            f"{label}: converted {len(converted)} of {total} "
            f"file{'s' if total != 1 else ''}"
        ]
        if skipped:
            parts.append(f"{skipped} skipped")
        if errors:
            parts.append(f"{len(errors)} failed")
        if cancelled:
            parts.append("cancelled")
        self._status.setText("; ".join(parts) + ".")

        if errors:
            # Cap the dialog so a huge folder doesn't produce an unusable box.
            shown = errors[:20]
            more = len(errors) - len(shown)
            body = "\n".join(shown)
            if more > 0:
                body += f"\n… and {more} more."
            QMessageBox.warning(
                self,
                "Some Conversions Failed",
                f"The following file{'s' if len(errors) != 1 else ''} "
                f"could not be converted:\n\n{body}",
            )

    # ----------------------------------------------------------------- background jobs

    def _job_busy(self) -> bool:
        if self._job_thread is not None:
            self._status.setText("Please wait for the current task to finish.")
            return True
        return False

    def _start_job(self, thread, dialog: JobDialog):
        """Run one background job; its window appears only if it takes a while."""
        self._job_thread = thread
        self._job_dialog = dialog
        QTimer.singleShot(400, dialog, lambda: dialog.show() if self._job_dialog is dialog else None)
        thread.start()

    def _finish_job(self, thread, dialog: JobDialog):
        thread.wait()
        if self._job_thread is thread:
            self._job_thread = None
            self._job_dialog = None
        dialog.finish()

    # ----------------------------------------------------------------- add file

    def _add_file_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Pattern Files", "",
            "Quilting Pattern Files (*.pat *.PAT *.qli *.QLI *.svg *.SVG);;"
            "PAT Files (*.pat *.PAT);;"
            "QLI Files (*.qli *.QLI);;"
            "SVG Files (*.svg *.SVG);;"
            "All Files (*)"
        )
        if not paths:
            return
        added = 0
        for path in paths:
            if self._db.add(path):
                added += 1
        self._reload_list()
        self._status.setText(
            f"Added {added} new file{'s' if added != 1 else ''} "
            f"({len(paths) - added} already in database)."
        )
        # Automatically check for duplicates among the newly-added files
        if added > 0:
            self._find_and_merge_duplicates(auto=True)

    # ----------------------------------------------------------------- add folder

    def _add_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Add Folder", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._scan_folder(folder)

    def _scan_folder(self, folder: str):
        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.stop()
            self._scan_thread.wait()

        self._status.setText(f"Scanning {folder} …")
        self._scan_thread = FileScanThread(folder, self._db, self)
        self._scan_thread.progress.connect(
            lambda found: self._status.setText(
                f"Scanning {folder} … {found} pattern file{'s' if found != 1 else ''} found"))
        self._scan_thread.finished_.connect(self._on_scan_done)
        self._scan_thread.start()

    def _on_scan_done(self, added: int):
        self._reload_list()
        total = self._db.count()
        self._status.setText(
            f"Scan complete — {added} new file{'s' if added != 1 else ''} added. "
            f"{total} total in database."
        )
        # Automatically check for duplicates among the newly-scanned files
        if added > 0:
            self._find_and_merge_duplicates(auto=True)

    # ----------------------------------------------------------------- remove / purge

    def _selected_paths_for_remove(self) -> list[str]:
        """
        Paths the Remove Selected action should act on.

        Uses the active left-pane view's selection, then falls back to the
        file currently shown on the canvas. Previously only the list widget
        was consulted, so Remove did nothing in Thumbnail mode.
        """
        mode = self._stack.currentIndex()

        if mode == 0:
            index = self._file_list.currentIndex()
            if index.isValid():
                path = index.data(FileListModel.PathRole)
                if path:
                    return [path]

        elif mode == 1 and self._thumb_grid is not None:
            path = self._thumb_grid.selected_path()
            if path:
                return [path]

        elif mode == 2 and self._kw_assign_grid is not None:
            # Assign mode is multi-select via checkboxes
            checked = self._kw_assign_grid.checked_paths()
            if checked:
                return checked

        if self._current_path:
            return [self._current_path]
        return []

    def _clear_displayed_file(self):
        """Clear canvas + info panel after the open file is removed."""
        self._pending_load_path = None
        self._current_path = None
        self._canvas.load(PatFile())
        self._info_label.setText("")
        self._info_name.setText("—")
        self._info_path.setText("—")
        self._info_dimensions.setText("—")
        self._info_comments.setPlainText("")
        self._info_keywords.blockSignals(True)
        self._info_keywords.clear()
        self._info_keywords.blockSignals(False)
        self.setWindowTitle("Innova PAT / QLI Viewer")

    def _remove_selected(self):
        paths = self._selected_paths_for_remove()
        if not paths:
            self._status.setText("No file selected.")
            return

        removed_current = False
        current = normalize_path(self._current_path) if self._current_path else None
        for path in paths:
            self._db.remove(path)
            if current and normalize_path(path) == current:
                removed_current = True

        if removed_current:
            self._clear_displayed_file()

        self._reload_list()
        if len(paths) == 1:
            name = os.path.basename(paths[0])
            self._status.setText(f"Removed '{name}' from database.")
        else:
            self._status.setText(
                f"Removed {len(paths)} file{'s' if len(paths) != 1 else ''} "
                f"from database."
            )

    def _purge_missing(self):
        """Check every file in the background (slow on network drives)."""
        if self._purge_thread is not None and self._purge_thread.isRunning():
            self._status.setText("Purge Missing is already running…")
            return
        self._status.setText("Checking for missing files…")
        self._purge_thread = PurgeMissingThread(self._db, self)
        self._purge_thread.finished_.connect(self._on_purge_done)
        self._purge_thread.start()

    def _on_purge_done(self, removed: int):
        # The open file may have been one of the missing ones.
        if self._current_path and not os.path.exists(self._current_path):
            self._clear_displayed_file()
        self._reload_list()
        self._status.setText(
            f"Purged {removed} missing file{'s' if removed != 1 else ''} from database."
        )

    def _reset_database(self):
        """Empty the file database after an explicit Yes confirmation."""
        n = self._db.count()
        if n == 0:
            QMessageBox.information(
                self,
                "Reset Database",
                "The database is already empty.",
            )
            return

        reply = QMessageBox.warning(
            self,
            "Reset Database",
            f"Remove all {n} file entr{'ies' if n != 1 else 'y'} from the database?\n\n"
            "Files on disk will not be deleted.\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self._status.setText("Database reset cancelled.")
            return

        # Stop background work that depends on the current DB contents.
        if self._purge_thread is not None and self._purge_thread.isRunning():
            self._purge_thread.wait()
        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.stop()
            self._scan_thread.wait()
        if self._dup_thread and self._dup_thread.isRunning():
            self._dup_thread.stop()
            self._dup_thread.quit()
            self._dup_thread.wait()
        if self._thumb_grid is not None:
            self._thumb_grid.stop_render()
        if self._kw_assign_grid is not None:
            self._kw_assign_grid.stop_render()

        removed = self._db.clear()

        # Clear UI selection / canvas so nothing references removed paths.
        self._pending_load_path = None
        self._current_path = None
        self._search_box.blockSignals(True)
        self._search_box.clear()
        self._search_box.blockSignals(False)
        self._canvas.load(PatFile())
        self._info_label.setText("")
        self._info_name.setText("—")
        self._info_path.setText("—")
        self._info_dimensions.setText("—")
        self._info_comments.setPlainText("")
        self._info_keywords.blockSignals(True)
        self._info_keywords.clear()
        self._info_keywords.blockSignals(False)

        self._reload_list()
        self._status.setText(
            f"Database reset — removed {removed} file"
            f"{'s' if removed != 1 else ''}."
        )

    def _clear_thumbnail_cache(self):
        from thumbnail_cache import _thumb_cache
        count = max(_thumb_cache.count(), _thumb_cache.disk_count())
        _thumb_cache.clear()
        self._status.setText(
            f"Thumbnail cache cleared ({count} entr{'ies' if count != 1 else 'y'} removed). "
            "Thumbnails will be re-rendered next time Thumbnail mode is opened."
        )

    # ----------------------------------------------------------------- duplicate merging

    def _find_and_merge_duplicates(self, auto: bool = False):
        """
        Scan the database for files with byte-identical content (compared
        only within the same extension) and merge them, keeping one path
        per duplicate set.

        auto=True is used for the automatic post-scan/post-add check: it
        runs silently in the background and only pops up the results
        dialog if duplicates were actually found, so it never interrupts
        the user when there's nothing to report.
        auto=False (menu-triggered) always shows a status message even
        when no duplicates are found, since the user explicitly asked.
        """
        if self._dup_thread and self._dup_thread.isRunning():
            if not auto:
                self._status.setText("Duplicate scan already in progress…")
            return

        rows = self._db.all_files()
        if len(rows) < 2:
            if not auto:
                self._status.setText("Not enough files in the database to check for duplicates.")
            return

        self._dup_auto = auto
        if not auto:
            self._status.setText(f"Scanning {len(rows)} files for duplicates…")

        from duplicates import DuplicateFinderThread
        self._dup_thread = DuplicateFinderThread(rows, self)
        self._dup_thread.progress.connect(self._on_dup_progress)
        self._dup_thread.finished_.connect(self._on_dup_finished)
        self._dup_thread.start()

    def _on_dup_progress(self, done: int, total: int):
        if not self._dup_auto:
            self._status.setText(f"Scanning for duplicates… {done}/{total}")

    def _on_dup_finished(self, groups: list):
        auto = self._dup_auto

        if not groups:
            if not auto:
                self._status.setText("No duplicate files found.")
            return

        # Merge: for each group, keep the first (alphabetically-first) path
        # and remove the rest from the database.
        removed_paths = []
        for group in groups:
            keeper = group[0]
            for dupe in group[1:]:
                removed_paths.append((dupe, keeper))

        self._db.remove_many([p for p, _ in removed_paths])
        self._reload_list()

        self._status.setText(
            f"Merged {len(groups)} duplicate set{'s' if len(groups) != 1 else ''} "
            f"({len(removed_paths)} file{'s' if len(removed_paths) != 1 else ''} removed)."
        )
        self._show_duplicate_merge_dialog(groups, removed_paths)

    def _show_duplicate_merge_dialog(self, groups: list, removed_paths: list):
        """Display a dialog listing every duplicate set that was merged."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout as _QVBox, QTextEdit as _QTextEdit, \
            QPushButton as _QPushButton, QLabel as _QLabel

        dlg = QDialog(self)
        dlg.setWindowTitle("Duplicate Files Merged")
        dlg.resize(700, 450)
        layout = _QVBox(dlg)

        n_sets   = len(groups)
        n_merged = len(removed_paths)
        header = _QLabel(
            f"<b>{n_sets} duplicate set{'s' if n_sets != 1 else ''} found "
            f"({n_merged} duplicate file{'s' if n_merged != 1 else ''} removed from the database):</b>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        text = _QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("font-family: monospace; font-size: 11px;")

        lines = []
        for i, group in enumerate(groups, 1):
            keeper = group[0]
            dupes  = group[1:]
            ext    = keeper.rsplit('.', 1)[-1].upper() if '.' in keeper else '?'
            lines.append(f"Set {i} ({ext}):")
            lines.append(f"  KEPT:    {keeper}")
            for d in dupes:
                lines.append(f"  REMOVED: {d}")
            lines.append("")
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)

        note = _QLabel(
            "Note: only the database entries were removed — the duplicate files "
            "themselves are untouched on disk."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(note)

        close_btn = _QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)

        dlg.exec()

    # ----------------------------------------------------------------- display

    def _load_and_display(self, path: str):
        """Initiates the background loading of a file."""
        path = normalize_path(path)
        self._pending_load_path = path

        if self._loader_thread is not None:
            try:
                self._loader_thread.finished_.disconnect(self._on_file_loaded)
            except RuntimeError:
                pass
            if self._loader_thread.isRunning():
                # Don't wait for it: quick keyboard navigation through large
                # files used to block here. It finishes on its own and its
                # result is ignored; keep it referenced until then.
                stale = self._loader_thread
                self._stale_loaders.add(stale)
                stale.finished.connect(lambda: self._stale_loaders.discard(stale))

        name = os.path.basename(path)
        self._status.setText(f"Loading '{name}'...")
        self._info_name.setText("Loading...")

        self._loader_thread = FileLoaderThread(path, self)
        self._loader_thread.finished_.connect(self._on_file_loaded)
        self._loader_thread.start()

    def _on_file_loaded(self, pat: Optional[PatFile], path: str, error: Optional[Exception]):
        """Slot called when the FileLoaderThread finishes."""
        if path != self._pending_load_path:
            return

        if error is not None:
            self._status.setText(f"Error reading '{os.path.basename(path)}': {error}")
            self._info_name.setText("Error")
            return

        if pat is None:
            return # Safety catch

        # The loader thread prepares the drawing data off the GUI thread.
        self._canvas.load(pat, getattr(pat, "canvas_runs", None))
        self._current_path = path   # track for keyword save

        name     = os.path.basename(path)
        n_travel = sum(1 for m in pat.moves if m.gcode == 0)
        n_arc    = sum(1 for m in pat.moves if m.gcode in (2, 3))
        n_stitch = sum(1 for m in pat.moves if m.gcode in (1, 2, 3))
        n_linear = n_stitch - n_arc
        self._info_label.setText(
            f"{name}  |  {len(pat.moves)} blocks  |  "
            f"stitch={n_stitch} (linear={n_linear}, arc={n_arc})  "
            f"travel={n_travel}  comments={len(pat.comments)}"
        )
        b = self._canvas._bounds
        if not b.isEmpty():
            self._status.setText(
                f"{name}  —  "
                f"X {b.left():.4f}–{b.right():.4f}  "
                f"Y {b.top():.4f}–{b.bottom():.4f}  "
                f"({b.width():.4f} × {b.height():.4f} PAT units)"
            )

        # Update info panel
        self._info_name.setText(name)
        self._info_path.setText(path)
        if not b.isEmpty():
            self._info_dimensions.setText(
                f"{b.width():.4f} × {b.height():.4f}"
            )
        else:
            self._info_dimensions.setText("—")
        if pat.comments:
            self._info_comments.setPlainText("\n".join(pat.comments))
        else:
            self._info_comments.setPlainText("")
            
        # Load saved keywords from DB (block signals to avoid spurious save)
        self._info_keywords.blockSignals(True)
        self._info_keywords.setText(self._db.get_keywords(path))
        self._info_keywords.blockSignals(False)

        comments = " | ".join(pat.comments[:3])
        self.setWindowTitle(
            f"Innova PAT / QLI Viewer — {name}"
            + (f" — {comments}" if comments else "")
        )

    def _save_keywords(self):
        """Persist the keywords field to the database for the current file."""
        if not self._current_path:
            return

        raw = self._info_keywords.text()
        # Normalise: split on commas only (so multi-word keywords like
        # "floral wreath" keep their internal space), strip each token,
        # drop empties, deduplicate while preserving order of first
        # appearance.
        seen: set  = set()
        cleaned: list = []
        for kw in raw.split(','):
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                cleaned.append(kw)

        normalised = ', '.join(cleaned)

        # Write back the normalised form so the field looks tidy too
        if self._info_keywords.text() != normalised:
            self._info_keywords.blockSignals(True)
            self._info_keywords.setText(normalised)
            self._info_keywords.blockSignals(False)

        # editingFinished also fires whenever focus merely leaves the field.
        path = self._current_path
        if normalised == self._db.get_keywords(path):
            return
        self._db.set_keywords(path, normalised)

        # Update the rows in place rather than reloading and rebuilding every
        # view; only a keyword search can change which files match.
        self._all_rows = [(p, n, e, normalised if p == path else k)
                          for p, n, e, k in self._all_rows]
        query = self._search_box.text().strip()
        if query and self._search_scope() in ('both', 'keywords'):
            self._apply_filter(self._search_box.text(), preserve_selection=path)
        else:
            for grid in (self._thumb_grid, self._kw_assign_grid):
                if grid is not None:
                    grid._model.update_keywords(path, normalised)
            self._list_model.update_keywords(path, normalised)
        self._status.setText(
            f"Keywords saved for '{os.path.basename(path)}'."
        )

    # ----------------------------------------------------------------- toggles

    def _toggle_travel(self, checked: bool):
        self._canvas.show_travel = checked
        self._canvas._build_paths()
        self._canvas.update()

    def _toggle_markers(self, checked: bool):
        self._canvas.show_markers = checked
        self._canvas.update()

    # ----------------------------------------------------------------- line color

    def _choose_line_color(self):
        from PySide6.QtWidgets import QColorDialog
        current = self._canvas.stitch_color
        color = QColorDialog.getColor(current, self, "Choose Line Color")
        if not color.isValid():
            return

        # Update the live canvas immediately
        self._canvas.stitch_color = color
        self._canvas.update()

        # Update thumbnail rendering for all future/cached lookups.
        # Cache keys include the colour, so old (different-coloured) entries
        # are simply left behind rather than overwritten — no explicit
        # invalidation needed, and switching back to a previous colour will
        # still hit the cache for files rendered with that colour before.
        from thumbnail_cache import set_thumbnail_color
        set_thumbnail_color(color)

        # Re-render the currently visible grid (if any) so thumbnails update
        # without requiring the user to leave and re-enter Thumbnail mode.
        if self._stack.currentIndex() == 1 and self._thumb_grid is not None:
            self._thumb_grid.refresh_colors()
        elif self._stack.currentIndex() == 2 and self._kw_assign_grid is not None:
            self._kw_assign_grid.refresh_colors()

        self._status.setText(
            f"Line colour changed to rgb({color.red()}, {color.green()}, {color.blue()})."
        )

    # ----------------------------------------------------------------- keywords list

    def _show_keywords_list(self):
        """Show a dialog listing every keyword in use, with a per-keyword file count."""
        keywords = self._db.all_keywords()

        from PySide6.QtWidgets import QDialog, QVBoxLayout as _QVBox, \
            QListWidget as _QListWidget, QListWidgetItem as _QListWidgetItem, \
            QPushButton as _QPushButton, QLabel as _QLabel

        dlg = QDialog(self)
        dlg.setWindowTitle("Keywords In Use")
        dlg.resize(420, 500)
        layout = _QVBox(dlg)

        n = len(keywords)
        header = _QLabel(
            f"<b>{n} keyword{'s' if n != 1 else ''} in use.</b>"
            if n else "<b>No keywords have been assigned to any files yet.</b>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        if n:
            hint = _QLabel("Double-click a keyword to filter the file list by it.")
            hint.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(hint)

            kw_list = _QListWidget()
            for kw, count in keywords:
                item = _QListWidgetItem(f"{kw}   ({count} file{'s' if count != 1 else ''})")
                item.setData(Qt.UserRole, kw)
                kw_list.addItem(item)

            def _filter_by_keyword(item):
                kw = item.data(Qt.UserRole)
                self._search_box.setText(kw)
                dlg.accept()

            kw_list.itemDoubleClicked.connect(_filter_by_keyword)
            layout.addWidget(kw_list)

        close_btn = _QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)

        dlg.exec()
