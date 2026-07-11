"""
Small shared constants used across the thumbnail rendering/caching and
list/grid display code. Kept in one place so file_item_model.py,
thumbnail_cache.py, and thumbnail_widgets.py all agree on the same values.
"""

import os

THUMB_SIZE = 120   # displayed thumbnail pixel size (square)
THUMB_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".pat_viewer_thumbs")

# Thumbnail render tuning
THUMB_MAX_WORKERS = 4          # parallel QThreadPool workers
THUMB_ARC_DEGREES = 8.0        # coarser arc sampling than the canvas (2°)
THUMB_PAT_CACHE_SIZE = 200     # parsed PatFile LRU entries
THUMB_DISK_FLUSH_EVERY = 25    # deferred shelve writes batched per thread


