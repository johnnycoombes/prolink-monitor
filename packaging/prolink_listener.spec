# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build of Prolink Listener.

PyInstaller, not pyside6-deploy: Qt WebEngine needs QtWebEngineProcess.exe,
the .pak resource files, and icudtl.dat next to the Qt libraries. PyInstaller's
PySide6 hooks collect those. pyside6-deploy often skips them unless Visual
Studio's dumpbin is installed, and that has been unreliable for WebEngine.

onedir, not onefile: QtWebEngineProcess has to be a real executable on disk.
A onefile archive would unpack it on every launch and still break the relative
paths WebEngine expects.

Settings stay in ~/.prolink-monitor and the USB cache stays in ~/.prolink-cache.
Nothing in this spec redirects those folders.
"""

import os

spec_dir = os.path.dirname(os.path.abspath(SPEC))
root = os.path.dirname(spec_dir)

datas = [(os.path.join(root, "web"), "web")]
root_json = os.path.join(root, "packaging", "tuf", "root.json")
if os.path.isfile(root_json):
    # Public trust anchor only. Private keys are not in the repository.
    datas.append((root_json, "tuf"))

# Qt WebEngine is imported lazily inside gui/floating_now.py. Name it here so
# the PySide6 hook still collects QtWebEngineProcess and its resources.
# tufup is imported only when an update check runs.
hiddenimports = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "PIL",
    "PIL.Image",
    "updater",
    "updater.apply",
    "updater.check",
    "updater.config",
    "updater.startup",
    "updater.versioning",
    # The client only. Importing the top-level tufup package also loads the
    # repository signer, which does not belong in the app.
    "tufup.client",
    "tufup.common",
    "tufup.utils",
    "tufup.utils.platform_specific",
    "tuf.ngclient",
    "requests",
    "packaging",
    "packaging.version",
]

a = Analysis(
    [os.path.join(root, "desktop.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProlinkListener",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ProlinkListener",
)
