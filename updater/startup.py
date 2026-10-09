"""Check for an update after the main window is on screen.

Imported from ``gui.app`` only. A failure here must not stop the app: the
link to the players matters more than an update check.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from updater.apply import decide_action
from updater.check import UpdateCheck, download_and_apply, log_update, perform_check
from updater.config import DISPLAY_NAME, releases_page_url

log = logging.getLogger(__name__)


class _Bridge(QObject):
    """Hop from the network thread back to the window thread."""

    result_ready = Signal(object)


def schedule_update_check(window: QWidget) -> None:
    """Start a check soon, unless Settings turns it off."""
    settings = getattr(window, "settings", {}) or {}
    if not bool(settings.get("check_for_updates", True)):
        log_update("update check skipped (turned off in settings)")
        return
    QTimer.singleShot(1500, lambda: _start(window))


def _start(window: QWidget) -> None:
    bridge = _Bridge(window)
    bridge.result_ready.connect(lambda result: _on_result(window, result))

    def work() -> None:
        try:
            result = perform_check()
        except Exception as exc:
            log_update(f"update check failed: {exc}")
            return
        bridge.result_ready.emit(result)

    threading.Thread(target=work, name="prolink-update-check", daemon=True).start()


def _on_result(window: QWidget, result: UpdateCheck) -> None:
    log_update(
        f"update check: {result.status}"
        + (f" {result.available}" if result.available else "")
        + (f" ({result.message})" if result.message and result.status != "available" else "")
    )
    if result.status != "available" or not result.available:
        return
    frozen = bool(getattr(sys, "frozen", False))
    confirmed = _confirm(window, result, frozen=frozen)
    action = decide_action(result.status, confirmed=confirmed, frozen=frozen)
    if action == "show_download":
        webbrowser.open(releases_page_url())
        return
    if action != "apply":
        return
    try:
        download_and_apply(result, exe=sys.executable)
    except Exception as exc:
        log_update(f"update install failed: {exc}")
        QMessageBox.warning(
            window,
            DISPLAY_NAME,
            "The update could not be installed.\n\n"
            f"{exc}\n\n"
            "Prolink Listener is still the version you already have. "
            "Details were written to the update.log file in your .prolink-monitor folder.",
        )


def _confirm(window: QWidget, result: UpdateCheck, *, frozen: bool) -> bool:
    if frozen:
        text = (
            f"Version {result.available} is available.\n"
            f"This copy is {result.current}.\n\n"
            "Install it now? Prolink Listener will close and open again. "
            "Your settings stay in the .prolink-monitor folder and the "
            "library cache stays in .prolink-cache."
        )
        answer = QMessageBox.question(
            window,
            "Update Prolink Listener",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
    text = (
        f"Version {result.available} is available.\n"
        f"This copy is {result.current}.\n\n"
        "This one is running from source, so it will not replace your files. "
        "The Releases page has the Windows build."
    )
    answer = QMessageBox.question(
        window,
        "Update Prolink Listener",
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    # Yes means "open the releases page", which decide_action treats as confirmed
    # and then chooses show_download because this process is not frozen.
    return answer == QMessageBox.StandardButton.Yes
