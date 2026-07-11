#!/usr/bin/env python3
"""
Innova PAT / QLI Viewer -- entry point.

Run with:  python main.py [file_or_folder]

This is a multi-file split of what was originally a single pat_viewer.py
script. All the .py files in this folder must stay together (they import
each other by plain module name, e.g. `from database import FileDatabase`)
for this to run -- copy or move the whole folder as a unit.
"""

import sys
import os

from PySide6.QtWidgets import QApplication

from database import normalize_path
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Innova PAT / QLI Viewer")
    win = MainWindow()
    win.show()

    # Command-line: pre-load a file or folder
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isdir(arg):
            win._scan_folder(arg)
        elif os.path.isfile(arg):
            path = normalize_path(arg)
            win._db.add(path)
            win._reload_list()
            win._load_and_display(path)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
