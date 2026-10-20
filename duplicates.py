"""
Content-based duplicate file detection (same file type only).
Hashes real file bytes so two files are only ever considered duplicates
if their actual content matches -- not just their name.
"""

import os
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


def _file_sizes(paths) -> dict:
    """
    Size of each existing file, keyed by os.path.normcase(path). Reads each
    folder once with os.scandir, whose entries already carry the size on
    Windows, instead of one stat call per file.
    """
    by_dir: dict[str, set] = {}
    for path in paths:
        by_dir.setdefault(os.path.dirname(path), set()).add(
            os.path.normcase(os.path.basename(path)))
    sizes: dict[str, int] = {}
    for folder, names in by_dir.items():
        try:
            with os.scandir(folder or ".") as entries:
                for entry in entries:
                    name = os.path.normcase(entry.name)
                    if name in names and entry.is_file():
                        sizes[os.path.normcase(os.path.join(folder, entry.name))] = entry.stat().st_size
        except OSError:
            continue   # unreadable folder — its files are skipped
    return sizes


class DuplicateFinderThread(QThread):
    """
    Scans a list of (path, ext) pairs and groups files with byte-identical
    content into duplicate sets.  Only files sharing the same extension are
    ever compared to each other — a .pat and .qli are never considered
    duplicates even if (hypothetically) their bytes matched, since they are
    different file types.

    Only files that share both extension and size can be identical, so files
    with a unique size are never read; this runs automatically after every
    Add Folder, so most files are skipped.

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
        total = len(self._rows)
        done  = 0
        # Group by extension and size first — never compare across types.
        sizes = _file_sizes([row[0] for row in self._rows])
        if self._stopped:
            return
        by_size: dict[tuple[str, int], list[str]] = {}
        for path, _name, ext, _kw in self._rows:
            # Missing or unreadable file — skip, don't risk false merge.
            size = sizes.get(os.path.normcase(path))
            if size is not None:
                by_size.setdefault((ext.upper(), size), []).append(path)
            else:
                done += 1
                self.progress.emit(done, total)

        groups: list[list[str]] = []
        for paths in by_size.values():
            if len(paths) < 2:
                done += 1
                self.progress.emit(done, total)
                continue
            hash_to_paths: dict[str, list[str]] = {}
            for path in paths:
                if self._stopped:
                    return
                digest = _file_content_hash(path)
                done += 1
                self.progress.emit(done, total)
                if digest is None:
                    continue
                hash_to_paths.setdefault(digest, []).append(path)

            for path_list in hash_to_paths.values():
                if len(path_list) > 1:
                    path_list.sort(key=str.lower)
                    groups.append(path_list)

        self.finished_.emit(groups)
