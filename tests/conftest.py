"""Shared fixtures for thumbnail / Qt tests."""

from __future__ import annotations

import os
import textwrap

import pytest
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication (pytest-qt also provides one; this is a fallback)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mini_pat(tmp_path):
    """Write a tiny .pat file that renders to a non-empty stitch path."""
    path = tmp_path / "mini.pat"
    path.write_text(textwrap.dedent("""\
        N1G00X0Y0
        N2G01X100Y0
        N3G01X100Y80
        N4G01X0Y80
        N5G01X0Y0
        N6M02
    """), encoding="utf-8")
    return str(path)


@pytest.fixture
def isolated_cache(tmp_path, qapp):
    """Fresh ThumbnailCache backed by a temp shelve path (not the user cache)."""
    from thumbnail_cache import ThumbnailCache

    cache = ThumbnailCache(db_path=str(tmp_path / "thumbs"), max_size=64)
    yield cache
    cache.close()


@pytest.fixture
def pixmap_guard(monkeypatch, qapp):
    """
    Fail the test if QPixmap.fromImage is called off the GUI thread.

    Use this around worker-side render/cache code to catch regressions that
    re-introduce cross-thread QPixmap construction (the scroll segfault class).
    """
    from PySide6.QtCore import QThread

    real_from_image = QPixmap.fromImage.__func__ if hasattr(QPixmap.fromImage, "__func__") else QPixmap.fromImage
    # PySide static methods: keep a bound-safe wrapper via the type.
    _original = QPixmap.fromImage

    def _guarded_from_image(image, *args, **kwargs):
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            raise AssertionError(
                "QPixmap.fromImage called off the GUI thread — "
                "workers must return QImage only"
            )
        return _original(image, *args, **kwargs)

    monkeypatch.setattr(QPixmap, "fromImage", staticmethod(_guarded_from_image))
    return _guarded_from_image
