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

datas = [
    (os.path.join(root, "web"), "web"),
    (os.path.join(root, "assets", "icons"), os.path.join("assets", "icons")),
]
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
    # OneLibrary. Imported lazily, so name the modules and collect their files.
    "mutagen",
    "mutagen.id3",
    "mutagen.flac",
    "mutagen.mp4",
    "pyrekordbox",
    "pyrekordbox.masterdb",
    "pyrekordbox.masterdb.database",
    "pyrekordbox.masterdb.models",
    "pyrekordbox.onelibrary",
    "pyrekordbox.onelibrary.database",
    "pyrekordbox.onelibrary.models",
    "pyrekordbox.utils",
    "sqlcipher3",
    "sqlcipher3.dbapi2",
    "sqlcipher3._sqlite3",
    "sqlite3",
    "_sqlite3",
    "prolink.pysqlcipher_dialect",
    "prolink.onelibrary_check",
]

# sqlcipher3 ships a binary extension. collect_all keeps the DLL and the
# pyrekordbox package data that a hidden import alone can miss.
from PyInstaller.utils.hooks import collect_all

_collected_bins = []
for _pkg in ("pyrekordbox", "sqlcipher3", "sqlalchemy", "numpy", "mutagen"):
    _datas, _bins, _hidden = collect_all(_pkg)
    datas += _datas
    _collected_bins += _bins
    hiddenimports += _hidden


def _ship_tree(module_name):
    """Copy a package onto disk under _internal, not only into the PYZ archive.

    JC's frozen build had no pyrekordbox, sqlcipher, or sqlite folder. Hidden
    imports live inside the archive and are easy to miss. These copies are
    real files next to the executable's _internal directory.
    """
    import importlib

    mod = importlib.import_module(module_name)
    root = os.path.dirname(os.path.abspath(mod.__file__))
    parent = os.path.dirname(root)
    shipped_datas = []
    shipped_bins = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests")]
        rel_dir = os.path.relpath(dirpath, parent)
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, name)
            lower = name.lower()
            if lower.endswith((".pyd", ".dll")) or ".so" in lower:
                shipped_bins.append((full, rel_dir))
            else:
                shipped_datas.append((full, rel_dir))
    return shipped_datas, shipped_bins


for _name in ("pyrekordbox", "sqlcipher3", "mutagen"):
    _extra_datas, _extra_bins = _ship_tree(_name)
    datas += _extra_datas
    _collected_bins += _extra_bins

import importlib as _importlib

_sqlite_mod = _importlib.import_module("_sqlite3")
_sqlite_file = os.path.abspath(getattr(_sqlite_mod, "__file__", "") or "")
if _sqlite_file and os.path.isfile(_sqlite_file):
    _collected_bins.append((_sqlite_file, "."))
_sqlite3_py = _importlib.import_module("sqlite3")
_sqlite3_root = os.path.dirname(os.path.abspath(_sqlite3_py.__file__))
if os.path.isdir(_sqlite3_root) and os.path.basename(_sqlite3_root) == "sqlite3":
    _sd, _sb = _ship_tree("sqlite3")
    datas += _sd
    _collected_bins += _sb

a = Analysis(
    [os.path.join(root, "desktop.py")],
    pathex=[root],
    binaries=_collected_bins,
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
