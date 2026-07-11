"""
Persistent SQLite-backed file database, plus the two background QThreads
that populate it: FileLoaderThread (parse a single file off the UI thread)
and FileScanThread (recursively walk a folder for pattern files).
"""

import os
import sqlite3
import threading

from PySide6.QtCore import QThread, Signal

DB_PATH = os.path.join(os.path.expanduser("~"), ".pat_viewer.db")


def normalize_path(path: str) -> str:
    """Canonical absolute path; case-folded on Windows to avoid duplicates."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _escape_like(query: str) -> str:
    """Escape SQL LIKE wildcards in user-supplied search text."""
    return query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class FileDatabase:
    """
    SQLite-backed persistent store of known .pat / .qli file paths.
    Schema: files(path TEXT PRIMARY KEY, name TEXT, ext TEXT, keywords TEXT)
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        with self._lock:
            with self._con:
                self._con.execute("PRAGMA journal_mode=WAL")
                self._con.execute("PRAGMA synchronous=NORMAL")
                self._con.execute("PRAGMA temp_store=MEMORY")
                self._con.execute("PRAGMA cache_size=-8000")
                self._con.execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        path     TEXT PRIMARY KEY,
                        name     TEXT NOT NULL,
                        ext      TEXT NOT NULL,
                        keywords TEXT NOT NULL DEFAULT ''
                    )
                """)
                self._con.execute("""
                    CREATE TABLE IF NOT EXISTS meta (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                cols = [r[1] for r in self._con.execute(
                    "PRAGMA table_info(files)"
                ).fetchall()]
                if 'keywords' not in cols:
                    self._con.execute(
                        "ALTER TABLE files ADD COLUMN keywords TEXT NOT NULL DEFAULT ''"
                    )
        self._migrate_paths()

    def _meta_get(self, key: str) -> str:
        row = self._con.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else ''

    def _meta_set(self, key: str, value: str):
        with self._con:
            self._con.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (key, value),
            )

    def _migrate_paths(self):
        """One-time normalization of legacy paths stored before normcase was added."""
        with self._lock:
            if self._meta_get('paths_normalized') == '1':
                return
            rows = self._con.execute(
                "SELECT path, name, ext, keywords FROM files"
            ).fetchall()
            pending = []
            for old_path, _name, ext, keywords in rows:
                new_path = normalize_path(old_path)
                if new_path == old_path:
                    continue
                pending.append((old_path, new_path, os.path.basename(new_path), ext, keywords))
            if pending:
                with self._con:
                    for old_path, new_path, new_name, ext, keywords in pending:
                        self._con.execute("DELETE FROM files WHERE path=?", (old_path,))
                        self._con.execute(
                            "INSERT OR IGNORE INTO files(path, name, ext, keywords) "
                            "VALUES(?,?,?,?)",
                            (new_path, new_name, ext, keywords),
                        )
            self._meta_set('paths_normalized', '1')

    def add(self, path: str) -> bool:
        path = normalize_path(path)
        name = os.path.basename(path)
        ext  = name.rsplit('.', 1)[-1].upper() if '.' in name else ''
        try:
            with self._lock:
                with self._con:
                    self._con.execute(
                        "INSERT OR IGNORE INTO files(path, name, ext, keywords) "
                        "VALUES(?,?,?,?)",
                        (path, name, ext, '')
                    )
                    return self._con.execute("SELECT changes()").fetchone()[0] > 0
        except sqlite3.Error:
            return False

    def remove(self, path: str):
        path = normalize_path(path)
        with self._lock:
            with self._con:
                self._con.execute("DELETE FROM files WHERE path=?", (path,))

    def set_keywords(self, path: str, keywords: str):
        path = normalize_path(path)
        with self._lock:
            with self._con:
                self._con.execute(
                    "UPDATE files SET keywords=? WHERE path=?",
                    (keywords.strip(), path),
                )

    def get_keywords(self, path: str) -> str:
        path = normalize_path(path)
        with self._lock:
            row = self._con.execute(
                "SELECT keywords FROM files WHERE path=?", (path,)
            ).fetchone()
        return row[0] if row else ''

    def all_files(self) -> list[tuple[str, str, str, str]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT path, name, ext, keywords FROM files "
                "ORDER BY LOWER(name), LOWER(path)"
            ).fetchall()
        return rows

    def search(self, query: str, scope: str = 'both') -> list[tuple[str, str, str, str]]:
        """
        Return files matching query (case-insensitive), restricted to the
        given scope:
          'both'     - match against filename OR keywords (default)
          'name'     - match against filename only
          'keywords' - match against keywords only
        """
        pattern = f"%{_escape_like(query)}%"
        escape = " ESCAPE '\\'"
        if scope == 'name':
            where, params = "name LIKE ?" + escape, (pattern,)
        elif scope == 'keywords':
            where, params = "keywords LIKE ?" + escape, (pattern,)
        else:
            where = f"(name LIKE ?{escape} OR keywords LIKE ?{escape})"
            params = (pattern, pattern)

        with self._lock:
            rows = self._con.execute(
                f"SELECT path, name, ext, keywords FROM files "
                f"WHERE {where} "
                f"ORDER BY LOWER(name), LOWER(path)",
                params,
            ).fetchall()
        return rows

    def purge_missing(self) -> int:
        removed = 0
        with self._lock:
            with self._con:
                cursor = self._con.execute("SELECT path FROM files")
                while True:
                    batch = cursor.fetchmany(500)
                    if not batch:
                        break
                    missing = [(p,) for (p,) in batch if not os.path.exists(p)]
                    if missing:
                        self._con.executemany(
                            "DELETE FROM files WHERE path=?", missing
                        )
                        removed += len(missing)
        return removed

    def count(self) -> int:
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def all_keywords(self) -> list[tuple[str, int]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT keywords FROM files WHERE keywords != ''"
            ).fetchall()

        counts: dict[str, int] = {}
        for (kw_string,) in rows:
            for kw in kw_string.split(','):
                kw = kw.strip()
                if kw:
                    counts[kw] = counts.get(kw, 0) + 1

        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def remove_many(self, paths: list):
        if not paths:
            return
        norm = [(normalize_path(p),) for p in paths]
        with self._lock:
            with self._con:
                self._con.executemany("DELETE FROM files WHERE path=?", norm)

    def clear(self) -> int:
        """Delete every file entry. Returns how many rows were removed."""
        with self._lock:
            with self._con:
                n = self._con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                if n:
                    self._con.execute("DELETE FROM files")
                return n


class FileLoaderThread(QThread):
    """
    Background thread to load and parse .pat, .qli, or .svg files
    without blocking the main GUI event loop.
    """
    # Emits: (pat_object, file_path, exception_if_any)
    finished_ = Signal(object, str, object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = normalize_path(path)

    def run(self):
        try:
            from file_loader import load_file
            pat = load_file(self._path)
            self.finished_.emit(pat, self._path, None)
        except Exception as e:
            self.finished_.emit(None, self._path, e)


class FileScanThread(QThread):
    """Recursively scan a folder and emit each qualifying file found."""
    found     = Signal(str)   # abs_path
    finished_ = Signal(int)   # total new files added

    def __init__(self, root: str, db: FileDatabase, parent=None):
        super().__init__(parent)
        self._root = root
        self._db   = db
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        added = 0
        for dirpath, dirs, files in os.walk(self._root):
            if self._stopped:
                break
            dirs.sort(key=str.lower)
            for fname in sorted(files, key=str.lower):
                if self._stopped:
                    break
                if fname.lower().endswith(('.pat', '.qli', '.svg')):
                    abs_path = os.path.join(dirpath, fname)
                    if self._db.add(abs_path):
                        added += 1
                    self.found.emit(abs_path)
        self.finished_.emit(added)