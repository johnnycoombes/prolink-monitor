"""PySide6 desktop application entry."""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Ensure the repo root is importable when launched as a script.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from gui.main_window import MainWindow
    from gui.theme import STYLESHEET

    # Slightly sharper text on Linux/X11.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Engine Room")
    app.setOrganizationName("prolink-monitor")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    font = QFont("Segoe UI", 10)
    if not font.exactMatch():
        font = QFont("DejaVu Sans", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
