"""
Two-layer persistent LRU thumbnail cache (in-memory dict + on-disk shelve,
one shelve connection per thread), the module-level current thumbnail
colour, the thumbnail renderer, and ThumbnailRenderCoordinator — a
QThreadPool-backed parallel renderer used by the Thumbnail Mode and Assign Keywords
grids.

Thread-safety contract
----------------------
* Worker threads may create and store only ``QImage`` (and PNG bytes).
* ``QPixmap`` is created and used only on the GUI thread via
  ``get_pixmap_for_display`` / ``ensure_gui_pixmap``.
* This avoids native segfaults from cross-thread QPixmap use (common when
  scrolling a thumbnail grid under load).
"""

import os
import threading

from PySide6.QtCore import (
    QObject, QRunnable, QThread, QThreadPool, Qt, Signal,
    QByteArray, QBuffer, QIODevice,
)
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QTransform, QPixmap, QImage
from PySide6.QtWidgets import QApplication

from constants import (
    THUMB_SIZE, THUMB_CACHE_PATH, THUMB_MAX_WORKERS,
    THUMB_ARC_DEGREES, THUMB_PAT_CACHE_SIZE, THUMB_DISK_FLUSH_EVERY,
)
from file_loader import load_file
from geometry import arc_to_path


def _default_render_scale() -> int:
    """2× on HiDPI displays, 1× on standard — cuts paint cost ~4× on the latter."""
    app = QApplication.instance()
    if app is None:
        return 2
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else app.devicePixelRatio()
    return 2 if ratio > 1.25 else 1


def _on_gui_thread() -> bool:
    """True if no QApplication yet, or we are on the application thread."""
    app = QApplication.instance()
    if app is None:
        return True
    return QThread.currentThread() is app.thread()


def _require_gui_thread(op: str = "QPixmap operation") -> None:
    if not _on_gui_thread():
        raise RuntimeError(f"{op} must only be used on the GUI thread")


def image_to_pixmap(image: QImage) -> QPixmap:
    """Convert a QImage to QPixmap. Must be called on the GUI thread."""
    _require_gui_thread("image_to_pixmap")
    if image is None or image.isNull():
        return _blank_thumbnail(THUMB_SIZE)
    pm = QPixmap.fromImage(image)
    dpr = image.devicePixelRatio()
    if dpr > 0:
        pm.setDevicePixelRatio(dpr)
    return pm


class ThumbnailCache:
    """
    Two-layer persistent LRU thumbnail cache.

    Layer 1 — in-memory dict of QImage, keyed by "<path>|<mtime>|<color>".
              Safe to read/write from worker threads under ``_lock``.
    Layer 1b — GUI-thread-only dict of QPixmap for painting (never touched
               from workers).
    Layer 2 — shelve on disk; one connection per thread via threading.local.

    Disk writes are deferred by default (flush_pending_disk / close) so worker
    threads are not blocked on PNG encode + shelve I/O during batch renders.
    """

    FLUSH_EVERY = THUMB_DISK_FLUSH_EVERY

    def __init__(self, db_path: str = THUMB_CACHE_PATH, max_size: int = 4000):
        from collections import OrderedDict

        self._db_path = db_path
        self._max     = max_size
        self._lock    = threading.Lock()
        self._mem: "OrderedDict[str, QImage]" = OrderedDict()
        # Main-thread-only paint cache — never lock-shared with workers.
        self._gui: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._local   = threading.local()
        self._disk_generation = 0
        self._pending_disk: dict[str, bytes] = {}   # cache_key → png bytes
        self._disk_lock = threading.Lock()

    def _key(self, path: str) -> str:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        return f"{path}|{mtime}|{_thumb_color_tag()}"

    def _close_thread_db(self):
        db = getattr(self._local, 'db', None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        self._local.db = None
        self._local.dirty_count = 0

    def _thread_db(self):
        gen = self._disk_generation
        if (getattr(self._local, 'db', None) is not None
                and getattr(self._local, 'db_gen', -1) == gen):
            return self._local.db
        self._close_thread_db()
        import shelve as _shelve
        try:
            db = _shelve.open(self._db_path, flag='c')
        except Exception:
            db = None
        self._local.db = db
        self._local.db_gen = gen
        self._local.dirty_count = 0
        return db

    def get_memory_only(self, path: str):
        """Fast in-memory lookup (QImage). Safe from any thread."""
        key = self._key(path)
        with self._lock:
            img = self._mem.get(key)
            if img is not None:
                self._mem.move_to_end(key)
                return img
        return None

    def get(self, path: str):
        """Return a QImage if cached and up to date, else None.

        May be called from worker threads (loads disk → QImage only).
        """
        img = self.get_memory_only(path)
        if img is not None:
            return img

        key = self._key(path)
        db = self._thread_db()
        if db is None:
            return None
        try:
            png_bytes = db.get(key)
        except Exception:
            self._close_thread_db()
            return None
        if png_bytes is None:
            return None

        image = QImage()
        if not image.loadFromData(png_bytes, 'PNG') or image.isNull():
            return None

        if THUMB_SIZE:
            ratio = image.width() / THUMB_SIZE
            if ratio > 0:
                image.setDevicePixelRatio(ratio)

        with self._lock:
            self._mem[key] = image
            self._mem.move_to_end(key)
            self._evict_if_needed()
        return image

    def put(self, path: str, image: QImage, *, defer_disk: bool = True):
        """Store a QImage in memory; shelve write is deferred unless defer_disk=False.

        Safe from worker threads. Does not create or touch QPixmap.
        """
        if image is None or image.isNull():
            return
        key = self._key(path)

        with self._lock:
            self._mem[key] = image
            self._mem.move_to_end(key)
            self._evict_if_needed()

        # Drop any stale GUI pixmap for this key. Safe: we only delete a key
        # that may exist; actual QPixmap construction still happens on GUI.
        # If called from a worker, skip — main thread will rebuild on next paint
        # from the new QImage (same key → same pixels after re-render).
        if _on_gui_thread():
            self._gui.pop(key, None)

        if defer_disk:
            png_bytes = _image_to_png_bytes(image)
            if png_bytes:
                with self._disk_lock:
                    self._pending_disk[key] = png_bytes
            return

        self._write_disk_entry(key, image)

    def ensure_gui_pixmap(self, path: str, image: QImage = None):
        """Return a QPixmap for painting. Must be called on the GUI thread."""
        _require_gui_thread("ensure_gui_pixmap")
        key = self._key(path)
        pm = self._gui.get(key)
        if pm is not None:
            self._gui.move_to_end(key)
            return pm

        if image is None:
            image = self.get_memory_only(path)
        if image is None or image.isNull():
            return None

        pm = image_to_pixmap(image)
        self._gui[key] = pm
        self._gui.move_to_end(key)
        while len(self._gui) > self._max:
            self._gui.popitem(last=False)
        return pm

    def get_pixmap_for_display(self, path: str):
        """Main-thread paint helper: QPixmap from cache, or None if missing."""
        return self.ensure_gui_pixmap(path)

    def _write_disk_entry(self, key: str, image: QImage):
        db = self._thread_db()
        if db is None:
            return
        png_bytes = _image_to_png_bytes(image)
        if not png_bytes:
            return
        try:
            db[key] = png_bytes
            self._local.dirty_count = getattr(self._local, 'dirty_count', 0) + 1
            if self._local.dirty_count >= self.FLUSH_EVERY:
                db.sync()
                self._local.dirty_count = 0
        except Exception:
            self._close_thread_db()

    def flush_pending_disk(self):
        """Write all deferred PNG entries to shelve (call when render pool is idle)."""
        with self._disk_lock:
            if not self._pending_disk:
                return
            pending = dict(self._pending_disk)
            self._pending_disk.clear()

        db = self._thread_db()
        if db is None:
            return
        try:
            for key, png_bytes in pending.items():
                db[key] = png_bytes
            db.sync()
            self._local.dirty_count = 0
        except Exception:
            self._close_thread_db()

    def _evict_if_needed(self):
        while len(self._mem) > self._max:
            self._mem.popitem(last=False)

    def invalidate(self, path: str):
        with self._lock:
            mem_stale = [k for k in self._mem if k.split('|')[0] == path]
            for k in mem_stale:
                del self._mem[k]
        if _on_gui_thread():
            gui_stale = [k for k in self._gui if k.split('|')[0] == path]
            for k in gui_stale:
                del self._gui[k]
        with self._disk_lock:
            disk_stale = [k for k in self._pending_disk if k.split('|')[0] == path]
            for k in disk_stale:
                del self._pending_disk[k]
        db = self._thread_db()
        if db is not None:
            try:
                db_stale = [k for k in db.keys() if k.split('|')[0] == path]
                for k in db_stale:
                    del db[k]
            except Exception:
                pass

    def clear(self):
        import shelve as _shelve
        import glob as _glob

        with self._lock:
            self._mem.clear()
            self._disk_generation += 1
        if _on_gui_thread():
            self._gui.clear()
        with self._disk_lock:
            self._pending_disk.clear()

        self._close_thread_db()
        for stale in _glob.glob(self._db_path + '*'):
            try:
                os.remove(stale)
            except OSError:
                pass
        try:
            with _shelve.open(self._db_path, flag='n'):
                pass
        except Exception:
            pass
        self._thread_db()

    def flush(self):
        self.flush_pending_disk()
        db = getattr(self._local, 'db', None)
        if db is not None and getattr(self._local, 'dirty_count', 0):
            try:
                db.sync()
                self._local.dirty_count = 0
            except Exception:
                pass

    def close(self):
        self.flush_pending_disk()
        db = getattr(self._local, 'db', None)
        if db is not None:
            try:
                db.sync()
                db.close()
            except Exception:
                pass
            self._local.db = None

    def count(self) -> int:
        with self._lock:
            return len(self._mem)

    def disk_count(self) -> int:
        db = self._thread_db()
        if db is None:
            return 0
        try:
            return len(db)
        except Exception:
            return 0


def _image_to_png_bytes(image: QImage) -> bytes:
    """Encode QImage to PNG bytes (safe on worker threads)."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, 'PNG')
    buf.close()
    return bytes(ba.data())


_thumb_cache = ThumbnailCache()

# Parsed PatFile LRU — avoids re-parsing on every thumbnail in a batch.
_pat_cache_lock = threading.Lock()
_pat_cache: "dict[str, object]" = {}
_pat_cache_order: list[str] = []


def _load_pat_cached(path: str):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return load_file(path)
    key = f"{path}|{mtime}"
    with _pat_cache_lock:
        if key in _pat_cache:
            return _pat_cache[key]
    pat = load_file(path)
    with _pat_cache_lock:
        if key not in _pat_cache:
            _pat_cache[key] = pat
            _pat_cache_order.append(key)
            while len(_pat_cache_order) > THUMB_PAT_CACHE_SIZE:
                old = _pat_cache_order.pop(0)
                _pat_cache.pop(old, None)
    return pat


def _blank_image(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor(220, 220, 220))
    return img


def _blank_thumbnail(size: int) -> QPixmap:
    """Placeholder pixmap for QLabel widgets. Call on the GUI thread only."""
    _require_gui_thread("_blank_thumbnail")
    return QPixmap.fromImage(_blank_image(size))


_thumb_stitch_color = QColor(0, 0, 0)


def _thumb_color_tag() -> str:
    c = _thumb_stitch_color
    return f"{c.red():02x}{c.green():02x}{c.blue():02x}"


def set_thumbnail_color(color: QColor):
    global _thumb_stitch_color
    _thumb_stitch_color = QColor(color)


def _build_stitch_path(pat, arc_degrees: float = THUMB_ARC_DEGREES) -> QPainterPath:
    stitch = QPainterPath()
    cur_x = cur_y = 0.0
    for mv in pat.moves:
        tx, ty = mv.x, mv.y
        if mv.gcode == 1:
            stitch.moveTo(cur_x, cur_y)
            stitch.lineTo(tx, ty)
        elif (mv.gcode in (2, 3)
              and mv.i is not None and mv.j is not None):
            stitch.addPath(arc_to_path(
                cur_x, cur_y, tx, ty, mv.i, mv.j, mv.gcode,
                degrees_per_segment=arc_degrees,
            ))
        cur_x, cur_y = tx, ty
    return stitch


def _render_one_thumbnail(path: str, size: int, color: QColor = None,
                          render_scale: int = None) -> QImage:
    """
    Load a file and render to QImage.  Safe to call from worker threads.
    Never creates a QPixmap.
    """
    if color is None:
        color = _thumb_stitch_color
    if render_scale is None:
        render_scale = _default_render_scale()
    try:
        pat = _load_pat_cached(path)
    except Exception:
        return _blank_image(size)

    stitch = _build_stitch_path(pat)
    bounds = stitch.boundingRect()
    if bounds.isEmpty() or bounds.width() == 0 or bounds.height() == 0:
        return _blank_image(size)

    render_size = size * render_scale
    margin      = 4 * render_scale
    draw_size   = render_size - 2 * margin
    scale       = min(draw_size / bounds.width(), draw_size / bounds.height())
    bcx         = bounds.center().x()
    bcy         = bounds.center().y()
    ox          = render_size / 2.0 - bcx * scale
    oy          = render_size / 2.0 + bcy * scale

    img = QImage(render_size, render_size, QImage.Format_RGB32)
    img.fill(QColor(255, 255, 255))

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    t = QTransform()
    t.translate(ox, oy)
    t.scale(scale, -scale)
    painter.setTransform(t)
    pen = QPen(color, 0.5 * render_scale)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.drawPath(stitch)
    painter.end()

    img.setDevicePixelRatio(render_scale)
    return img


# ------------------------------------------------------------------ parallel render pool

class _ThumbnailWorkerSignals(QObject):
    """Bridge task completion from QRunnable workers back to the main thread."""
    task_done = Signal(str, object)   # path, QImage


class _ThumbnailTask(QRunnable):
    def __init__(self, path: str, render_scale: int, signals: _ThumbnailWorkerSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._render_scale = render_scale
        self._signals = signals

    def run(self):
        cached = _thumb_cache.get(self._path)
        if cached is not None:
            self._signals.task_done.emit(self._path, cached)
            return
        image = _render_one_thumbnail(
            self._path, THUMB_SIZE, render_scale=self._render_scale,
        )
        _thumb_cache.put(self._path, image, defer_disk=True)
        self._signals.task_done.emit(self._path, image)


class ThumbnailRenderCoordinator(QObject):
    """
    Long-lived parallel thumbnail renderer backed by a private QThreadPool.

    - Multiple workers render concurrently (default: THUMB_MAX_WORKERS).
    - Workers produce QImage only; QPixmap conversion happens on the GUI thread.
    - New paths are merged into the queue without cancelling in-flight work.
    - Visible paths are prioritised within each enqueue() call.
    - Disk cache writes are flushed when the pool goes idle.
    - shutdown() waits for in-flight tasks (use on window close).
    """
    rendered  = Signal(str, object)   # path, QImage
    progress  = Signal(int, int)      # done, total
    finished_ = Signal()

    def __init__(self, max_workers: int = THUMB_MAX_WORKERS, parent=None):
        super().__init__(parent)
        self._max_workers = max(1, max_workers)
        # Private pool so we never throttle the process-global pool, and so
        # waitForDone() only waits for our thumbnail tasks.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(self._max_workers)
        self._signals = _ThumbnailWorkerSignals(self)
        self._signals.task_done.connect(self._on_task_done, Qt.QueuedConnection)

        self._pending: list[str] = []
        self._pending_set: set[str] = set()
        self._in_flight: set[str] = set()
        self._stopped = False
        self._shutting_down = False
        self._signals_connected = True
        self._done = 0
        self._render_scale = _default_render_scale()

    def enqueue(self, paths: list, visible_paths: set = None):
        """Add paths to the render queue; already-cached items emit immediately."""
        if self._shutting_down:
            return
        if self._stopped:
            self._stopped = False

        visible = set(visible_paths or [])
        vis   = [p for p in paths if p in visible]
        invis = [p for p in paths if p not in visible]

        for path in vis + invis:
            if path in self._pending_set or path in self._in_flight:
                continue
            img = _thumb_cache.get_memory_only(path)
            if img is not None:
                # Warm GUI pixmap cache on the main thread, then notify views.
                _thumb_cache.ensure_gui_pixmap(path, img)
                self.rendered.emit(path, img)
                continue
            self._pending.append(path)
            self._pending_set.add(path)

        self._pump()

    def _pump(self):
        if self._shutting_down:
            return
        while (not self._stopped
               and len(self._in_flight) < self._max_workers
               and self._pending):
            path = self._pending.pop(0)
            self._pending_set.discard(path)
            self._in_flight.add(path)
            task = _ThumbnailTask(path, self._render_scale, self._signals)
            self._pool.start(task)
        self._emit_progress()
        if not self._in_flight and not self._pending and not self._stopped:
            _thumb_cache.flush_pending_disk()
            self.finished_.emit()
            self._done = 0

    def _on_task_done(self, path: str, image):
        self._in_flight.discard(path)
        if self._shutting_down or self._stopped:
            self._pump()
            return
        # Convert to QPixmap on the GUI thread (QueuedConnection delivers here).
        if image is not None:
            _thumb_cache.ensure_gui_pixmap(path, image)
        self.rendered.emit(path, image)
        self._done += 1
        self._emit_progress()
        self._pump()

    def _emit_progress(self):
        total = self._done + len(self._pending) + len(self._in_flight)
        if total > 0:
            self.progress.emit(self._done, total)

    def stop(self):
        """Cancel queued (not yet started) tasks; in-flight tasks may finish."""
        self._stopped = True
        self._pending.clear()
        self._pending_set.clear()
        self._done = 0
        self._pool.clear()

    def shutdown(self):
        """
        Stop all work and wait for in-flight workers to finish.
        Call from the GUI thread before destroying the owner widget.
        Safe to call more than once.
        """
        self._shutting_down = True
        self._stopped = True
        self._pending.clear()
        self._pending_set.clear()
        self._done = 0
        self._pool.clear()
        self._pool.waitForDone()
        self._in_flight.clear()
        if self._signals_connected:
            self._signals_connected = False
            try:
                self._signals.task_done.disconnect(self._on_task_done)
            except (RuntimeError, TypeError):
                pass

    def refresh_render_scale(self):
        """Re-read display DPI (e.g. after moving window to another monitor)."""
        self._render_scale = _default_render_scale()
