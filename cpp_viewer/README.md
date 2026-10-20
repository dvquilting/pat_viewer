# Quilt Pattern Viewer — C++ port

This directory contains a portable, high-performance C++20 implementation of
the Python Innova PAT / QLI Viewer.

It has:

- a dependency-free core for PAT, QLI, and SVG loading;
- PAT and QLI writing, including SVG → PAT/QLI and QLI → PAT conversion;
- adaptive Bézier flattening and native circular-arc preservation;
- a native Qt 6 desktop viewer with file/folder loading, search, zoom, pan,
  travel-path display, markers, compact file details, and conversion;
- persistent comma-separated keywords with filename/keyword search scopes,
  bulk keyword assignment, and a keyword usage browser;
- list, cached thumbnail, and multi-select keyword-assignment views, with
  viewport-prioritized thumbnails rendered on an adaptive worker pool;
- missing-file purge, database reset, thumbnail-cache clearing, and
  same-format byte-identical duplicate merging;
- single-file, folder, and recursive-tree conversion commands and a complete
  in-app Help/About menu matching the Python application;
- a command-line converter that builds even when Qt is unavailable.

## Build

Windows (Visual Studio 2022):

```powershell
cmake -S . -B build -G "Visual Studio 17 2022"
cmake --build build --config Release
ctest --test-dir build -C Release
```

Linux:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build
```

The GUI is built as `qpv` when Qt 6.4+ Widgets development files are
installed. The converter `qpv-cli` and tests require only a C++20 compiler.
To explicitly omit the GUI use `-DQPV_BUILD_GUI=OFF`.

## Windows installer

With Inno Setup 6 and Qt's `windeployqt` installed, build a self-contained
64-bit installer with:

```powershell
.\installer\windows\build-installer.ps1
```

The installer is written to `installer\output` and contains `qpv.exe`, its
compiler runtime, the required Qt DLLs, and Qt plug-ins. See
`installer\README.md` for path overrides and packaging details.

## Command line

```text
qpv-cli info pattern.pat
qpv-cli convert design.qli design.pat
qpv-cli convert design.svg design.pat
qpv-cli convert design.svg design.qli
qpv-cli batch qli-to-pat folder [--recursive] [--overwrite]
qpv-cli batch svg-to-pat folder [--recursive] [--overwrite]
qpv-cli batch svg-to-qli folder [--recursive] [--overwrite]
```

The output format is selected from the destination extension. PAT and QLI are
written with CRLF line endings for machine-software compatibility.

## SVG support

The built-in SVG reader accepts paths using `M/L/H/V/Z`, cubic and quadratic
Béziers (`C/S/Q/T`), and elliptical arcs (`A`), including relative commands.
Circular arcs remain PAT G02/G03 blocks; elliptical arcs and Béziers are
adaptively flattened. Decorative coloured paths and axis-aligned frame paths
are omitted to match the Python application. SVG dimensions in px, pt, mm, cm,
or inches are honored; otherwise the largest pattern dimension is normalized
to 12 PAT units.

The reader intentionally focuses on SVG path geometry. CSS styles, `<use>`
expansion, and arbitrary nested transform composition are not expanded.
Negative-Y group transforms are detected for quilting SVG orientation, as in
the Python implementation.

## Performance notes

Thumbnail and keyword grids render only visible items plus a small scrolling
prefetch margin. Queued work is cancelled when the view or filter changes,
duplicate requests are coalesced, and search input is debounced for 250 ms.
The canvas uses a 1.0-pixel cosmetic pen by default (matching the Python
viewer); the slider covers 0.1–4.0 screen pixels without changing under zoom.
