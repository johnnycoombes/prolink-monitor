"""Desktop GUI launcher (PySide6).

    python desktop.py
    python -m gui
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
