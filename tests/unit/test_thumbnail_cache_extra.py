"""Additional ThumbnailCache coverage beyond thread-safety guards."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QColor

from thumbnail_cache import (
    ThumbnailCache,
    _blank_image,
    _blank_thumbnail,
    _default_render_scale,
    _on_gui_thread,
    _render_one_thumbnail,
    _thumb_color_tag,
    set_thumbnail_color,
    image_to_pixmap,
)


@pytest.mark.qt
def test_default_render_scale_with_app(qapp):
    assert _default_render_scale() in (1, 2)


@pytest.mark.qt
def test_on_gui_thread(qapp):
    assert _on_gui_thread() is True


@pytest.mark.qt
def test_blank_thumbnail_is_pixmap(qapp):
    pm = _blank_thumbnail(24)
    assert not pm.isNull()
    assert pm.width() == 24


@pytest.mark.qt
def test_image_to_pixmap_null(qapp):
    pm = image_to_pixmap(QImage())
    assert not pm.isNull()


@pytest.mark.qt
def test_thumb_color_tag_and_set(qapp):
    import thumbnail_cache as tc

    old = QColor(tc._thumb_stitch_color)
    try:
        set_thumbnail_color(QColor(10, 20, 30))
        assert _thumb_color_tag() == "0a141e"
    finally:
        set_thumbnail_color(old)


@pytest.mark.qt
def test_cache_miss_and_eviction(tmp_path, qapp):
    cache = ThumbnailCache(db_path=str(tmp_path / "t"), max_size=2)
    try:
        assert cache.get_memory_only("/nope.pat") is None
        assert cache.get("/nope.pat") is None

        for name in ("a.pat", "b.pat", "c.pat"):
            p = tmp_path / name
            p.write_text("N1M02\n")
            cache.put(str(p), _blank_image(16), defer_disk=True)

        assert cache.count() <= 2
        assert cache.get_memory_only(str(tmp_path / "c.pat")) is not None
    finally:
        cache.close()


@pytest.mark.qt
def test_cache_disk_roundtrip_flush(tmp_path, qapp, mini_pat):
    cache = ThumbnailCache(db_path=str(tmp_path / "disk"), max_size=64)
    try:
        img = _render_one_thumbnail(mini_pat, size=32, render_scale=1)
        cache.put(mini_pat, img, defer_disk=True)
        cache.flush_pending_disk()
        cache._mem.clear()
        cache._gui.clear()
        got = cache.get(mini_pat)
        assert got is not None
        assert isinstance(got, QImage)
        assert not got.isNull()
        assert cache.disk_count() >= 1
    finally:
        cache.close()


@pytest.mark.qt
def test_cache_invalidate_and_clear(tmp_path, qapp, mini_pat):
    cache = ThumbnailCache(db_path=str(tmp_path / "inv"), max_size=64)
    try:
        img = _render_one_thumbnail(mini_pat, size=32, render_scale=1)
        cache.put(mini_pat, img, defer_disk=False)
        cache.ensure_gui_pixmap(mini_pat, img)
        cache.invalidate(mini_pat)
        assert cache.get_memory_only(mini_pat) is None
        cache.put(mini_pat, img, defer_disk=True)
        cache.clear()
        assert cache.count() == 0
    finally:
        cache.close()


@pytest.mark.qt
def test_render_missing_file_returns_blank(qapp, tmp_path):
    img = _render_one_thumbnail(str(tmp_path / "missing.pat"), size=40, render_scale=1)
    assert isinstance(img, QImage)
    assert img.width() == 40


@pytest.mark.qt
def test_put_null_image_ignored(tmp_path, qapp):
    cache = ThumbnailCache(db_path=str(tmp_path / "n"), max_size=8)
    try:
        p = tmp_path / "x.pat"
        p.write_text("N1M02\n")
        cache.put(str(p), QImage(), defer_disk=True)
        assert cache.count() == 0
    finally:
        cache.close()
