"""Main window: sidebar navigation + stacked pages + connection controls."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from gui.backend import Backend
from gui.i18n import I18n
from gui.pages import AboutPage, DevicesPage, LibraryPage, MonitorPage, SettingsPage
from gui.settings import load_settings, save_settings
from gui.theme import COLORS
from gui.widgets import Sidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.i18n = I18n()
        self.backend = Backend(self)

        self.setWindowTitle(self.i18n.t("app_title"))
        self.resize(
            int(self.settings.get("window_width", 1360)),
            int(self.settings.get("window_height", 860)),
        )
        self.setMinimumSize(960, 640)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar(self.i18n)
        self.sidebar.navigated.connect(self._navigate)
        outer.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        outer.addLayout(right, 1)

        # top bar
        top = QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(56)
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(20, 0, 16, 0)
        self.status_led = QLabel("●")
        self.status_led.setStyleSheet(f"color:{COLORS['dimmest']}; font-size:12px;")
        self.status_text = QLabel(self.i18n.t("idle"))
        self.status_text.setObjectName("Mono")
        self.status_text.setStyleSheet(
            f"font-family:monospace; font-size:11px; color:{COLORS['dim']};"
        )
        top_l.addWidget(self.status_led)
        top_l.addWidget(self.status_text, 1)

        self.btn_connect = QPushButton(self.i18n.t("connect"))
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_disconnect = QPushButton(self.i18n.t("disconnect"))
        self.btn_disconnect.setCursor(Qt.PointingHandCursor)
        self.btn_web = QPushButton(self.i18n.t("open_web"))
        self.btn_web.setCursor(Qt.PointingHandCursor)
        self.btn_connect.clicked.connect(self.connect_backend)
        self.btn_disconnect.clicked.connect(self.disconnect_backend)
        self.btn_web.clicked.connect(self.backend.open_web_panel)
        top_l.addWidget(self.btn_web)
        top_l.addWidget(self.btn_disconnect)
        top_l.addWidget(self.btn_connect)
        right.addWidget(top)

        self.stack = QStackedWidget()
        self.page_monitor = MonitorPage(self.i18n, self.backend)
        self.page_devices = DevicesPage(self.i18n)
        self.page_library = LibraryPage(self.i18n)
        self.page_settings = SettingsPage(self.i18n)
        self.page_about = AboutPage(self.i18n)
        self._pages = {
            "monitor": self.page_monitor,
            "devices": self.page_devices,
            "library": self.page_library,
            "settings": self.page_settings,
            "about": self.page_about,
        }
        for page in self._pages.values():
            self.stack.addWidget(page)
        right.addWidget(self.stack, 1)

        # status bar strip
        bottom = QFrame()
        bottom.setObjectName("StatusBar")
        bottom.setFixedHeight(28)
        bot_l = QHBoxLayout(bottom)
        bot_l.setContentsMargins(16, 0, 16, 0)
        self.footer = QLabel("")
        self.footer.setStyleSheet(
            f"font-family:monospace; font-size:10px; color:{COLORS['dimmest']};"
        )
        bot_l.addWidget(self.footer)
        right.addWidget(bottom)

        self.page_settings.load_settings(self.settings)
        self.page_settings.saved.connect(self._on_settings_saved)
        self.page_monitor.apply_prefs(self.settings)

        self.backend.state_changed.connect(self._on_state)
        self.backend.status_changed.connect(self._on_status)
        self.backend.track_ready.connect(lambda _tid: None)

        QShortcut(QKeySequence("+"), self, activated=lambda: self._nudge_zoom(-1))
        QShortcut(QKeySequence("="), self, activated=lambda: self._nudge_zoom(-1))
        QShortcut(QKeySequence("-"), self, activated=lambda: self._nudge_zoom(1))

        self._navigate("monitor")
        self._on_status("idle", "")

        if self.settings.get("auto_connect", True):
            self.connect_backend()

    # -- navigation ---------------------------------------------------------
    def _navigate(self, key: str) -> None:
        if key == "settings":
            self.page_settings.load_settings(
                {**self.settings, **self.page_monitor.prefs_snapshot()})
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)

    # -- backend ------------------------------------------------------------
    def connect_backend(self) -> None:
        # keep latest display prefs from monitor chips
        self.settings.update(self.page_monitor.prefs_snapshot())
        self.backend.start(self.settings)

    def disconnect_backend(self) -> None:
        self.backend.stop()

    def _on_status(self, status: str, detail: str) -> None:
        colors = {
            "idle": COLORS["dimmest"],
            "connecting": COLORS["warn"],
            "connected": COLORS["ok"],
            "error": COLORS["danger"],
        }
        self.status_led.setStyleSheet(
            f"color:{colors.get(status, COLORS['dimmest'])}; font-size:12px;"
        )
        label = self.i18n.t(status if status in (
            "idle", "connecting", "connected", "error") else "idle")
        text = f"{label}" + (f"  ·  {detail}" if detail else "")
        self.status_text.setText(text)
        busy = status in ("connecting", "connected")
        self.btn_connect.setEnabled(status != "connecting")
        self.btn_disconnect.setEnabled(busy)
        self.btn_web.setEnabled(status == "connected" and bool(
            self.settings.get("start_web_server", True)))

    def _on_state(self, state: dict) -> None:
        self.page_monitor.update_state(state)
        self.page_devices.update_state(state)
        self.page_library.update_state(state)
        packets = state.get("packets") or 0
        devices = len(state.get("devices") or [])
        mode = state.get("mode") or state.get("mode_kind") or "—"
        self.footer.setText(
            f"{mode}   ·   {devices} {self.i18n.t('nav_devices').lower()}   ·   "
            f"{packets} {self.i18n.t('packets')}"
        )

    def _on_settings_saved(self, data: dict, reconnect: bool) -> None:
        # preserve window size
        data["window_width"] = self.width()
        data["window_height"] = self.height()
        merged = {**self.settings, **data}
        save_settings(merged)
        self.settings = load_settings()
        self.page_monitor.apply_prefs(self.settings)
        self.page_settings.load_settings(self.settings)
        if reconnect:
            self.connect_backend()
        else:
            QMessageBox.information(self, self.i18n.t("settings_title"),
                                    self.i18n.t("saved"))

    def _nudge_zoom(self, direction: int) -> None:
        levels = [4, 8, 16, 32]
        cur = self.page_monitor._zoom
        try:
            i = levels.index(cur)
        except ValueError:
            i = 1
        nxt = levels[max(0, min(len(levels) - 1, i + direction))]
        self.page_monitor.set_zoom(nxt)

    def closeEvent(self, event):  # noqa: N802
        self.settings["window_width"] = self.width()
        self.settings["window_height"] = self.height()
        self.settings.update(self.page_monitor.prefs_snapshot())
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.backend.stop()
        super().closeEvent(event)
