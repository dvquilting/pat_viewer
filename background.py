"""
Background work for long-running batch operations, so the window keeps
repainting and can be cancelled:

  JobDialog                - progress window with a Cancel button
  CollectConvertibleThread - find the files a folder conversion will touch
  BatchConvertThread       - convert a list of files
"""

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)


def convert_one_file(src_path: str, out_path: str, mode: str) -> None:
    """Run a single conversion ('qli_to_pat', 'svg_to_pat' or 'svg_to_qli')."""
    if mode == 'qli_to_pat':
        from qli_converter import QliConverter
        from pat_writer import _write_pat_file
        _write_pat_file(QliConverter().convert(src_path), out_path)
    elif mode == 'svg_to_pat':
        from svg_converter import svg_to_pat_file
        svg_to_pat_file(src_path, out_path)
    elif mode == 'svg_to_qli':
        from svg_converter import svg_to_qli_file
        svg_to_qli_file(src_path, out_path)
    else:
        raise ValueError(f"Unknown conversion mode: {mode}")


def collect_convertible_files(folder: str, recursive: bool, source_exts: tuple) -> list[str]:
    """Sorted paths of matching source files (optionally recursive). Raises OSError."""
    paths: list[str] = []
    if recursive:
        for dirpath, dirs, files in os.walk(folder):
            dirs.sort(key=str.lower)
            for fname in sorted(files, key=str.lower):
                if fname.lower().endswith(source_exts):
                    paths.append(os.path.join(dirpath, fname))
    else:
        for fname in sorted(os.listdir(folder), key=str.lower):
            full = os.path.join(folder, fname)
            if os.path.isfile(full) and fname.lower().endswith(source_exts):
                paths.append(full)
    return paths


class JobDialog(QDialog):
    """
    Window-modal progress window for a background job.

    Unlike QProgressDialog, whose setValue() runs a nested event loop when
    the dialog is modal, updating this never processes events, so a job's
    completion handler cannot run (and close the dialog) in the middle of a
    progress update. Escape, the close button and Cancel all request
    cancellation; the owner closes the dialog when the job ends.
    """

    def __init__(self, title: str, text: str, on_cancel, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self._on_cancel = on_cancel
        self._cancelling = False
        layout = QVBoxLayout(self)
        self._label = QLabel(text)
        self._label.setMinimumWidth(360)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # busy until the first progress report
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._request_cancel)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._cancel)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(row)

    def set_progress(self, done: int, total: int, text: str):
        if self._cancelling:
            return
        self._bar.setRange(0, total)
        self._bar.setValue(done)
        self._label.setText(text)

    def finish(self):
        """Close for good (called by the owner when the job has ended)."""
        self._on_cancel = None
        self.hide()
        self.deleteLater()

    def reject(self):
        self._request_cancel()

    def closeEvent(self, event):
        self._request_cancel()
        event.ignore()

    def _request_cancel(self):
        if self._cancelling or self._on_cancel is None:
            return
        self._cancelling = True
        self._cancel.setEnabled(False)
        self._label.setText("Cancelling…")
        self._on_cancel()


class CollectConvertibleThread(QThread):
    """List a folder's source files and count outputs that already exist."""
    finished_ = Signal(list, int, str)   # [(src, out)], existing outputs, error text

    def __init__(self, folder: str, recursive: bool, source_exts: tuple,
                 out_ext: str, parent=None):
        super().__init__(parent)
        self._args = (folder, recursive, source_exts)
        self._out_ext = out_ext

    def run(self):
        try:
            sources = collect_convertible_files(*self._args)
        except OSError as e:
            self.finished_.emit([], 0, str(e))
            return
        jobs = [(src, os.path.splitext(src)[0] + self._out_ext) for src in sources]
        existing = sum(1 for _src, out in jobs if os.path.exists(out))
        self.finished_.emit(jobs, existing, "")


class BatchConvertThread(QThread):
    """Convert (source, output) pairs; existing outputs are overwritten or skipped."""
    progress  = Signal(int, int, str)          # done, total, current file name
    finished_ = Signal(list, int, list, bool)  # converted outputs, skipped, errors, cancelled

    def __init__(self, jobs: list, mode: str, overwrite: bool, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._mode = mode
        self._overwrite = overwrite
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        converted: list[str] = []
        errors: list[str] = []
        skipped = 0
        total = len(self._jobs)
        for i, (src, out) in enumerate(self._jobs):
            if self._stopped:
                break
            name = os.path.basename(src)
            self.progress.emit(i, total, name)
            if not self._overwrite and os.path.exists(out):
                skipped += 1
                continue
            try:
                convert_one_file(src, out, self._mode)
                converted.append(out)
            except Exception as e:
                errors.append(f"{name}: {e}")
        # Files converted before a cancel exist on disk, so they are reported.
        self.finished_.emit(converted, skipped, errors, self._stopped)
