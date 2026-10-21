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

## Where the viewer keeps its data

- The pattern library and keywords are in `library.json` in the app data
  folder (on Windows, `%APPDATA%\dvquilting\Quilt Pattern Viewer`).
- The line colour is a QSettings value under `dvquilting/QuiltPatternViewer`.
- Thumbnails are cached as PNG files in the cache folder (on Windows,
  `%LOCALAPPDATA%\dvquilting\Quilt Pattern Viewer\cache\thumbnails`).
- In builds with licensing on, the trial start and any license key are in
  `license.dat` (on Windows, `%APPDATA%\dvquilting\QuiltPatternViewer`),
  shared by the viewer and `qpv-cli`.
- Earlier versions used the organisation name "OpenAI" for all of these, and
  before that kept the library in QSettings (the registry on Windows). That
  data is moved to the locations above the first time a newer version starts.

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

macOS builds the same way as Linux, with Qt's `lib/cmake` folder in
`CMAKE_PREFIX_PATH`; `qpv` is built as an app bundle, `qpv.app`.

### Platforms built in CI

`.github/workflows/cpp.yml` builds everything, GUI included, and runs `ctest`
on Ubuntu (GCC), macOS (Apple Clang) and Windows (Qt's MinGW) with Qt 6.8.
On Ubuntu it also builds and tests once with licensing on, using a signing
key made in the job.

Number parsing uses `std::from_chars` where the standard library has it for
floating point, and otherwise (Apple's libc++, for one) a fallback that also
ignores the C locale; see `include/qpv/parse_double.hpp`.

## Packaging

Only Windows has an installer. A macOS `qpv.app` that runs on other Macs
needs Qt bundled into it with `macdeployqt`, and to be sold it must also be
codesigned with a Developer ID certificate and notarized by Apple; none of
that is set up yet. On Linux, `cmake --install` installs `qpv` and `qpv-cli`
against the system's Qt.

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
qpv-cli license [install "<name or email>" <key> | remove]   (licensed builds)
```

The output format is selected from the destination extension. PAT and QLI are
written with CRLF line endings for machine-software compatibility.

## Licensing

Licensing is off by default, and then nothing below applies. When it is on:

- each Windows user gets a 30-day trial from the first run of either program;
- after the trial, opening and viewing patterns, search, Merge Duplicates and
  the other library actions stay free, while conversions (in the viewer and
  `qpv-cli convert`/`batch`), Thumbnail Mode, Assign Keywords and keyword
  editing need a license key; `qpv-cli info` stays free;
- a license key is tied to the customer's name or email and never expires.
  It is entered with **Help → Enter License Key…** or
  `qpv-cli license install "<name or email>" <key>`.

Keys are EdDSA signatures over Curve25519 (Monocypher 4.0.2, in
`third_party/monocypher`; Monocypher's default EdDSA hashes with BLAKE2b
rather than Ed25519's SHA-512, at the same strength). The programs hold only
the public key, so a key cannot be forged from them. The trial is a
convenience, not a lock: deleting `license.dat` starts it again, and an
edited trial date ends it.

### Turning it on

1. Build the key generator (it does not need the public key yet) and make
   your signing key pair once:

   ```powershell
   cmake --build build --target qpv-keygen
   .\build\qpv-keygen.exe init C:\Keys\quilt.qpvkey --header include\qpv\license_public_key.hpp
   ```

   `quilt.qpvkey` is the private key. Keep it outside the repository and
   back it up: anyone with it can make keys, and if it is lost, no more keys
   can be made that the programs already sold accept. (`*.qpvkey` files are
   ignored by git as a safety net.) The header it writes holds only the
   public key and is safe to commit.

2. Set `QPV_LICENSING` to 1 in `include/qpv/license_config.hpp`, or configure
   with `cmake -B build -DQPV_LICENSING=ON`. The same file sets the trial
   length (`QPV_TRIAL_DAYS`) and an optional purchase link
   (`QPV_PURCHASE_URL`) shown in the license messages. A build with licensing
   on and the placeholder public key stops with an error.

3. Rebuild and run `installer\windows\build-installer.ps1` as usual; it
   builds with whatever the `build` folder is configured for.

### Issuing keys

```powershell
qpv-keygen issue C:\Keys\quilt.qpvkey "Jane Doe <jane@example.com>" --log C:\Keys\issued.csv
```

Send the customer both printed lines, `Licensed to:` and `License key:`. The
name must be entered as issued, though case and spacing do not matter.
`--log` appends each key to a CSV file for your records, and `--date` sets
the issue date. `qpv-keygen verify <private-key> <name> <key>` checks a key a
customer sends back; `qpv-keygen header <private-key>` prints the public key
header again.

`qpv-keygen` is never installed or included in the installer. To try a
licensed build without replacing the committed header, point CMake at another
one with `-DQPV_LICENSE_PUBLIC_KEY_HEADER=<path>`.

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
