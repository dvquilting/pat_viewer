"""
Guards against the scroll-time segfault class: QPixmap used from worker threads.

What these tests prove
----------------------
1. The renderer returns QImage, never QPixmap.
2. Cache put/get on a background thread never constructs QPixmap.
3. GUI helpers refuse (or only run) on the application thread.
4. The coordinator delivers results that are QImage, and only the main
   thread materialises QPixmap for display.
5. ThumbnailDelegate does not emit render_requested re-entrantly during paint.

These cannot prove the absence of every native crash, but they lock in the
thread-ownership contract that Qt requires and that the original code broke.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PySide6.QtCore import QRect, QSize, QThread, Qt, QModelIndex
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QStyle, QStyleOptionViewItem, QListView, QWidget,
)

from constants import THUMB_SIZE
from file_item_model import FileItemModel, ThumbnailDelegate
from thumbnail_cache import (
    ThumbnailCache,
    ThumbnailRenderCoordinator,
    _render_one_thumbnail,
    _blank_image,
    _require_gui_thread,
    image_to_pixmap,
    _thumb_cache,
)


# ---------------------------------------------------------------------------
# Pure render / type contract
# ---------------------------------------------------------------------------

@pytest.mark.qt
def test_render_one_returns_qimage_not_pixmap(mini_pat, qapp, pixmap_guard):
    img = _render_one_thumbnail(mini_pat, size=64, render_scale=1)
    assert isinstance(img, QImage)
    assert not img.isNull()
    assert img.width() == 64
    assert img.height() == 64
    # QPixmap is a different type — ensure we did not wrap it.
    assert not isinstance(img, QPixmap)


@pytest.mark.qt
def test_render_one_safe_on_worker_thread(mini_pat, qapp, pixmap_guard):
    """Worker-thread render must succeed and must not touch QPixmap.fromImage."""
    result = {}
    error = {}

    def worker():
        try:
            result["img"] = _render_one_thumbnail(mini_pat, size=48, render_scale=1)
        except Exception as exc:
            error["exc"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive()
    assert "exc" not in error, error.get("exc")
    assert isinstance(result["img"], QImage)
    assert not result["img"].isNull()


@pytest.mark.qt
def test_blank_image_is_qimage():
    img = _blank_image(32)
    assert isinstance(img, QImage)
    assert img.width() == 32


# ---------------------------------------------------------------------------
# Cache: QImage off-thread, QPixmap on GUI only
# ---------------------------------------------------------------------------

@pytest.mark.qt
def test_cache_stores_qimage(isolated_cache, mini_pat, qapp, pixmap_guard):
    img = _render_one_thumbnail(mini_pat, size=THUMB_SIZE, render_scale=1)
    isolated_cache.put(mini_pat, img, defer_disk=True)

    got = isolated_cache.get_memory_only(mini_pat)
    assert isinstance(got, QImage)
    assert got.width() == img.width()


@pytest.mark.qt
def test_cache_put_get_from_worker_thread(isolated_cache, mini_pat, qapp, pixmap_guard):
    """Simulates the coordinator worker path against an isolated cache."""
    errors = []

    def worker():
        try:
            img = _render_one_thumbnail(mini_pat, size=THUMB_SIZE, render_scale=1)
            isolated_cache.put(mini_pat, img, defer_disk=True)
            # Disk round-trip also must stay on QImage.
            again = isolated_cache.get(mini_pat)
            assert isinstance(again, QImage)
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive()
    assert errors == [], errors
    assert isolated_cache.get_memory_only(mini_pat) is not None


@pytest.mark.qt
def test_get_pixmap_for_display_requires_gui_thread(isolated_cache, mini_pat, qapp):
    img = _render_one_thumbnail(mini_pat, size=THUMB_SIZE, render_scale=1)
    isolated_cache.put(mini_pat, img, defer_disk=True)

    # Main thread: OK
    pm = isolated_cache.get_pixmap_for_display(mini_pat)
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()

    # Worker thread: must raise
    raised = {}

    def worker():
        try:
            isolated_cache.get_pixmap_for_display(mini_pat)
            raised["ok"] = True
        except RuntimeError as exc:
            raised["err"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    assert "err" in raised
    assert "GUI thread" in raised["err"]


@pytest.mark.qt
def test_image_to_pixmap_requires_gui_thread(qapp):
    img = _blank_image(16)
    pm = image_to_pixmap(img)
    assert isinstance(pm, QPixmap)

    raised = {}

    def worker():
        try:
            image_to_pixmap(img)
            raised["ok"] = True
        except RuntimeError as exc:
            raised["err"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    assert "err" in raised


@pytest.mark.qt
def test_require_gui_thread_helper(qapp):
    _require_gui_thread("test")  # main: ok
    err = {}

    def worker():
        try:
            _require_gui_thread("test")
        except RuntimeError as exc:
            err["e"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5)
    assert "e" in err


# ---------------------------------------------------------------------------
# Coordinator: private pool, QImage results, shutdown waits
# ---------------------------------------------------------------------------

@pytest.mark.qt
def test_coordinator_emits_qimage_and_warms_gui_cache(
    mini_pat, qapp, qtbot, tmp_path, pixmap_guard, monkeypatch
):
    """End-to-end: enqueue → worker → main-thread pixmap ready for paint."""
    from thumbnail_cache import ThumbnailRenderCoordinator
    import thumbnail_cache as tc

    # Point the module-global cache at a temp path for isolation.
    test_cache = ThumbnailCache(db_path=str(tmp_path / "coord_thumbs"), max_size=64)
    monkeypatch.setattr(tc, "_thumb_cache", test_cache)

    coord = ThumbnailRenderCoordinator(max_workers=2)
    received = []

    def on_rendered(path, image):
        received.append((path, image))

    coord.rendered.connect(on_rendered)
    coord.enqueue([mini_pat], visible_paths={mini_pat})

    qtbot.waitUntil(lambda: len(received) >= 1, timeout=15000)

    path, image = received[0]
    assert path == mini_pat
    assert isinstance(image, QImage)
    assert not image.isNull()

    # Display pixmap must now be available on the GUI thread.
    pm = test_cache.get_pixmap_for_display(mini_pat)
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()

    coord.shutdown()
    test_cache.close()


@pytest.mark.qt
def test_coordinator_uses_private_pool_not_global(qapp):
    global_pool = __import__("PySide6.QtCore", fromlist=["QThreadPool"]).QThreadPool.globalInstance()
    before = global_pool.maxThreadCount()

    coord = ThumbnailRenderCoordinator(max_workers=2)
    # Our pool is private; changing it must not alter the global default.
    assert coord._pool is not global_pool
    assert global_pool.maxThreadCount() == before
    coord.shutdown()


@pytest.mark.qt
def test_coordinator_shutdown_is_idempotent(mini_pat, qapp, qtbot, tmp_path, monkeypatch):
    import thumbnail_cache as tc

    test_cache = ThumbnailCache(db_path=str(tmp_path / "shut_thumbs"), max_size=64)
    monkeypatch.setattr(tc, "_thumb_cache", test_cache)

    coord = ThumbnailRenderCoordinator(max_workers=2)
    # Start work then shut down immediately — must not hang or crash.
    paths = [mini_pat] * 1
    coord.enqueue(paths, visible_paths=set(paths))
    coord.shutdown()
    coord.shutdown()  # second call safe
    test_cache.close()


# ---------------------------------------------------------------------------
# Delegate: no re-entrant emit during paint
# ---------------------------------------------------------------------------

@pytest.mark.qt
def test_delegate_does_not_emit_during_paint(qapp, qtbot):
    """render_requested must fire after paint returns (0-ms timer), not inside it."""
    model = FileItemModel()
    model.load_data([("/no/such/file.pat", "file.pat", "PAT", "")])

    parent = QWidget()
    delegate = ThumbnailDelegate(parent)
    emissions = []
    in_paint = {"flag": False}
    emitted_during_paint = []

    def on_req(path):
        emissions.append(path)
        if in_paint["flag"]:
            emitted_during_paint.append(path)

    delegate.render_requested.connect(on_req)

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, THUMB_SIZE + 16, THUMB_SIZE + 40)
    option.state = QStyle.State_Enabled
    option.widget = parent

    index = model.index(0, 0)
    img = QImage(200, 200, QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)

    in_paint["flag"] = True
    delegate.paint(painter, option, index)
    in_paint["flag"] = False
    painter.end()

    # Synchronous emissions during paint must be zero.
    assert emitted_during_paint == []
    assert emissions == []  # timer not yet fired

    # After the event loop runs the 0-ms timer, the request is delivered.
    qtbot.waitUntil(lambda: len(emissions) >= 1, timeout=2000)
    assert emissions == ["/no/such/file.pat"]


@pytest.mark.qt
def test_delegate_clear_pending_cancels_flush(qapp, qtbot):
    model = FileItemModel()
    model.load_data([("/x.pat", "x.pat", "PAT", "")])
    parent = QWidget()
    delegate = ThumbnailDelegate(parent)
    emissions = []
    delegate.render_requested.connect(emissions.append)

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 100, 100)
    option.state = QStyle.State_Enabled
    option.widget = parent

    img = QImage(100, 100, QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()

    # Cancel before the timer fires.
    delegate.clear_pending_renders()
    qtbot.wait(50)
    assert emissions == []


# ---------------------------------------------------------------------------
# Static / structural regression checks (no Qt event loop load required)
# ---------------------------------------------------------------------------

def test_render_source_has_no_qpixmap_fromimage():
    """
    Source-level guard: _render_one_thumbnail must not call QPixmap.fromImage.

    Complements the runtime pixmap_guard for reviewers who only skim diffs.
    """
    import ast
    import inspect
    import textwrap
    import thumbnail_cache as tc

    src = textwrap.dedent(inspect.getsource(tc._render_one_thumbnail))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # QPixmap.fromImage(...) or QPixmap(...)
        if isinstance(node, ast.Attribute) and node.attr == "fromImage":
            pytest.fail("_render_one_thumbnail must not call *.fromImage")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "QPixmap":
                pytest.fail("_render_one_thumbnail must not construct QPixmap")
            if isinstance(func, ast.Attribute) and func.attr == "fromImage":
                pytest.fail("_render_one_thumbnail must not call fromImage")


def test_thumbnail_task_run_source_has_no_qpixmap():
    import ast
    import inspect
    import textwrap
    import thumbnail_cache as tc

    src = textwrap.dedent(inspect.getsource(tc._ThumbnailTask.run))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "QPixmap":
            pytest.fail("_ThumbnailTask.run must not reference QPixmap")
        if isinstance(node, ast.Attribute) and node.attr == "fromImage":
            pytest.fail("_ThumbnailTask.run must not call fromImage")
