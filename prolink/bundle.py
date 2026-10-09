"""Locate files shipped next to the app, including a PyInstaller build."""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) executable."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> str:
    """Directory that contains ``web/`` and the rest of the shipped files.

    A source checkout uses the repository root. A frozen build uses the
    PyInstaller extraction directory, which is the ``_internal`` folder in a
    onedir layout. Settings and the library cache are not stored here.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return os.path.abspath(meipass)
        return os.path.dirname(os.path.abspath(sys.executable))
    # prolink/bundle.py -> repository root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
