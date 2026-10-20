# Innova PAT / QLI Viewer

PySide6 desktop app for viewing `.pat`, `.qli`, and `.svg` embroidery files.

## Running tests

### Prerequisites

- Python 3.11+ (3.13 tested)
- Dependencies:

```bash
pip install PySide6 pytest pytest-qt pytest-cov
```

SVG import is built in; `svgpathtools` is no longer needed.

### Run all tests

From the project root:

```bash
pytest
```

Verbose output:

```bash
pytest -v
```

### Run a subset

Unit tests only (no Qt widgets):

```bash
pytest tests/unit/
```

Qt / widget tests:

```bash
pytest tests/qt/
```

Single file:

```bash
pytest tests/unit/test_duplicates.py
```

### Coverage

```bash
pytest --cov=. --cov-report=term-missing
```

### Test markers

Some tests are tagged with markers defined in `pytest.ini`:

| Marker | Description |
|--------|-------------|
| `qt`   | Requires a Qt event loop (most widget tests) |
| `svg`  | SVG conversion tests |
| `slow` | Integration or large-file tests |

Examples:

```bash
pytest -m qt
pytest -m "not slow"
```