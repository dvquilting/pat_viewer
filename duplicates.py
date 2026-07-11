"""
Content-based duplicate file detection (same file type only).
Hashes real file bytes so two files are only ever considered duplicates
if their actual content matches -- not just their name.
"""

from typing import Optional

from PySide6.QtCore import QThread, Signal

def _file_content_hash(path: str, chunk_size: int = 65536) -> Optional[str]:
    """
    Compute a SHA-256 hash of a file's actual byte content.
    Returns None if the file can't be read.

    Hashing real content (not just filename) is essential here: two files
    can share a name but live in different folders with different content,
    and conversely two differently-named files can be byte-for-byte
    identical copies. Only a content hash tells the two cases apart.
    """
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return None


class DuplicateFinderThread(QThread):
    """
    Scans a list of (path, ext) pairs and groups files with byte-identical
    content into duplicate sets.  Only files sharing the same extension are
    ever compared to each other — a .pat and .qli are never considered
    duplicates even if (hypothetically) their bytes matched, since they are
    different file types.

    Emits finished_(groups) where groups is a list of lists of paths; each
    inner list is one set of duplicates (2 or more paths with identical
    content and the same extension), sorted so the alphabetically-first
    path is first (used as the "keeper").
    """
    progress  = Signal(int, int)          # (done, total)
    finished_ = Signal(list)              # list[list[str]]

    def __init__(self, rows: list, parent=None):
        """rows: list of (path, name, ext, keywords) as returned by FileDatabase."""
        super().__init__(parent)
        self._rows    = rows
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        # Group candidate paths by extension first — never compare across types.
        by_ext: dict[str, list[str]] = {}
        for path, _name, ext, _kw in self._rows:
            by_ext.setdefault(ext.upper(), []).append(path)

        total = len(self._rows)
        done  = 0
        # Hash → list of paths with that hash (within the current extension group)
        groups: list[list[str]] = []

        for ext, paths in by_ext.items():
            hash_to_paths: dict[str, list[str]] = {}
            for path in paths:
                if self._stopped:
                    return
                digest = _file_content_hash(path)
                done += 1
                self.progress.emit(done, total)
                if digest is None:
                    continue   # unreadable file — skip, don't risk false merge
                hash_to_paths.setdefault(digest, []).append(path)

            for path_list in hash_to_paths.values():
                if len(path_list) > 1:
                    path_list.sort(key=str.lower)
                    groups.append(path_list)

        self.finished_.emit(groups)
