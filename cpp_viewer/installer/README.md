# Windows installer

`windows\build-installer.ps1` builds `qpv.exe`, creates a clean deployment
with Qt's `windeployqt`, and compiles a 64-bit Inno Setup installer.

Run from PowerShell:

```powershell
.\installer\windows\build-installer.ps1
```

The finished installer is written to `installer\output`. It installs per-user
by default under `%LOCALAPPDATA%\Programs\Quilt Pattern Viewer`, creates a
Start Menu shortcut, offers an optional desktop shortcut, and registers a
standard Windows uninstaller.

Use `-SkipBuild` to repackage an existing release build. The Qt, CMake, and
Inno Setup paths can be overridden with the script parameters when those
tools are installed somewhere else.
