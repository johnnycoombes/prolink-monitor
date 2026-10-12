"""PySide6 desktop application entry."""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Ensure the repo root is importable when launched as a script.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    if "--check-onelibrary" in sys.argv:
        from prolink.onelibrary_check import main as check_main

        args = [a for a in sys.argv[1:] if a != "--check-onelibrary"]
        return check_main(args)

    from prolink.single_instance import claim_or_handoff, launch_command

    # A second launch hands off before Qt starts, including when the first
    # window is hidden in the tray.
    command = launch_command(sys.argv)
    hub = claim_or_handoff(command)
    if hub is None:
        return 0

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from gui.main_window import MainWindow
    from gui.theme import STYLESHEET

    # Slightly sharper text on Linux/X11.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Prolink Listener")
    app.setOrganizationName("prolink-monitor")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    font = QFont("Segoe UI", 10)
    if not font.exactMatch():
        font = QFont("DejaVu Sans", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    def _drain_instance() -> None:
        for cmd in hub.poll():
            window.handle_instance_command(cmd)

    timer = QTimer(window)
    timer.timeout.connect(_drain_instance)
    timer.start(200)
    from prolink.shutdown import arm_process_exit, request_process_exit

    arm_process_exit()
    app.aboutToQuit.connect(hub.close)
    app.aboutToQuit.connect(request_process_exit)
    _drain_instance()

    try:
        from updater.startup import schedule_update_check

        schedule_update_check(window)
    except Exception:
        # A broken update check must not stop the link to the players.
        pass
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
