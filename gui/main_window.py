"""Main window: sidebar navigation + stacked pages + connection controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from gui.backend import Backend
from gui.i18n import I18n
from gui.pages import (
    AboutPage, DevicesPage, HealthPage, LibraryPage, MonitorPage, OverlayPage,
    SessionPage, SettingsPage,
)
from gui.settings import load_settings, save_settings
from gui.theme import COLORS
from gui.floating_now import FloatingNowPlayingWindow
from gui.widgets import Sidebar
from prolink.session import ZOOM_BARS, DEFAULT_ZOOM_BARS


def _tray_icon() -> QIcon:
    """Simple branded tray glyph (no external asset required)."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(COLORS["panel"]))
    p.drawRoundedRect(2, 2, size - 4, size - 4, 14, 14)
    p.setBrush(QColor(COLORS["accent"]))
    p.drawEllipse(16, 16, 32, 32)
    p.setBrush(QColor(COLORS["bg"]))
    p.drawEllipse(24, 24, 16, 16)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.i18n = I18n()
        self.backend = Backend(self)
        self._sidebar_visible = bool(self.settings.get("sidebar_visible", True))
        self._force_quit = False
        self._tray: QSystemTrayIcon | None = None
        self._floating_now: FloatingNowPlayingWindow | None = None
        self._last_state: dict | None = None

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
        self.sidebar.hide_requested.connect(lambda: self.set_sidebar_visible(False))
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
        top_l.setContentsMargins(12, 0, 16, 0)

        self.btn_sidebar = QPushButton("☰")
        self.btn_sidebar.setObjectName("Chip")
        self.btn_sidebar.setFixedWidth(36)
        self.btn_sidebar.setCursor(Qt.PointingHandCursor)
        self.btn_sidebar.setToolTip(f"{self.i18n.t('toggle_sidebar')}  (Ctrl+B)")
        self.btn_sidebar.clicked.connect(self.toggle_sidebar)
        top_l.addWidget(self.btn_sidebar)

        self.nav_button = QToolButton()
        self.nav_button.setObjectName("Chip")
        self.nav_button.setText(self.i18n.t("nav_menu"))
        self.nav_button.setCursor(Qt.PointingHandCursor)
        self.nav_button.setPopupMode(QToolButton.InstantPopup)
        self._nav_menu = QMenu(self.nav_button)
        self.nav_button.setMenu(self._nav_menu)
        self._nav_actions: dict[str, QAction] = {}
        for key in self.sidebar.nav_keys():
            act = QAction(self.i18n.t(f"nav_{key}"), self)
            act.triggered.connect(lambda _=False, k=key: self._navigate(k))
            self._nav_menu.addAction(act)
            self._nav_actions[key] = act
        top_l.addWidget(self.nav_button)

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
        self.page_health = HealthPage(self.i18n)
        self.page_library = LibraryPage(self.i18n, self.backend)
        self.page_session = SessionPage(self.i18n, self.backend)
        self.page_overlay = OverlayPage(self.i18n, self.backend)
        self.page_settings = SettingsPage(self.i18n)
        self.page_about = AboutPage(self.i18n)
        self._pages = {
            "monitor": self.page_monitor,
            "devices": self.page_devices,
            "health": self.page_health,
            "library": self.page_library,
            "session": self.page_session,
            "overlay": self.page_overlay,
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
        self.page_overlay.load_prefs(self.settings, port=int(self.settings.get("port", 8777)))
        self.page_overlay.prefs_changed.connect(self._on_overlay_prefs)
        self.page_overlay.set_floating_now_handler(self.toggle_floating_now)
        self.page_settings.settings_floating_btn.clicked.connect(self.toggle_floating_now)
        self.page_overlay.set_web_ready(False)

        self.backend.state_changed.connect(self._on_state)
        self.backend.paint_tick.connect(self._on_paint_tick)
        self.backend.status_changed.connect(self._on_status)
        self.backend.track_ready.connect(self._on_track_ready)
        self.backend.port_changed.connect(self._on_port_changed)

        self._devices_sig: tuple | None = None
        self._library_sig: tuple | None = None
        self._footer_sig: tuple | None = None

        self._install_hotkeys()
        self._setup_tray()

        self.set_sidebar_visible(self._sidebar_visible, persist=False)
        self._navigate("monitor")
        self._on_status("idle", "")

        if self.settings.get("auto_connect", True):
            self.connect_backend()

    # -- hotkeys ------------------------------------------------------------
    def _install_hotkeys(self) -> None:
        def bind(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)

        bind("+", lambda: self._nudge_zoom(-1))
        bind("=", lambda: self._nudge_zoom(-1))
        bind("-", lambda: self._nudge_zoom(1))
        bind("Ctrl+=", lambda: self._nudge_zoom(-1))
        bind("Ctrl+-", lambda: self._nudge_zoom(1))
        bind("Ctrl+B", self.toggle_sidebar)
        bind("Ctrl+R", self._toggle_record)
        bind("Ctrl+O", self._open_overlay_hotkey)
        bind("Ctrl+Shift+N", self.toggle_floating_now)

    def _toggle_record(self) -> None:
        mon = self.backend.monitor
        recording = bool(mon is not None and mon.session.recording)
        if recording:
            self.backend.stop_session()
        else:
            self.backend.start_session()

    def toggle_floating_now(self) -> None:
        if self.backend.status != "connected":
            QMessageBox.information(
                self, self.i18n.t("floating_now_title"), self.i18n.t("connecting"))
            return
        if self._floating_now is not None and self._floating_now.isVisible():
            self._floating_now.close()
            self._floating_now = None
            return
        win = FloatingNowPlayingWindow(
            self.backend, self.i18n, lambda: self.settings, self)
        win.setAttribute(Qt.WA_DeleteOnClose)
        win.destroyed.connect(lambda: setattr(self, "_floating_now", None))
        self._floating_now = win
        win.restore_geometry(self.settings)
        win.apply_settings()
        if self._last_state:
            win.apply_state(self._last_state)
        win.show()

    def _open_overlay_hotkey(self) -> None:
        if self.backend.status != "connected":
            return
        if not self.settings.get("start_web_server", True):
            return
        self._navigate("overlay")
        self.page_overlay._open_overlay()

    # -- system tray --------------------------------------------------------
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

        self._tray = QSystemTrayIcon(_tray_icon(), self)
        self._tray.setToolTip(self.i18n.t("tray_tooltip"))
        menu = QMenu()
        act_show = QAction(self.i18n.t("tray_show"), self)
        act_show.triggered.connect(self.restore_from_tray)
        act_record = QAction(f"{self.i18n.t('session_record')}  ({self.i18n.t('hotkey_record')})", self)
        act_record.triggered.connect(self._toggle_record)
        act_overlay = QAction(f"{self.i18n.t('open_overlay')}  ({self.i18n.t('hotkey_overlay')})", self)
        act_overlay.triggered.connect(self._open_overlay_hotkey)
        act_floating = QAction(
            f"{self.i18n.t('tray_floating_now')}  ({self.i18n.t('hotkey_floating_now')})", self)
        act_floating.triggered.connect(self.toggle_floating_now)
        act_connect = QAction(self.i18n.t("connect"), self)
        act_connect.triggered.connect(self.connect_backend)
        act_disconnect = QAction(self.i18n.t("disconnect"), self)
        act_disconnect.triggered.connect(self.disconnect_backend)
        act_quit = QAction(self.i18n.t("tray_quit"), self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_record)
        menu.addAction(act_overlay)
        menu.addAction(act_floating)
        menu.addSeparator()
        menu.addAction(act_connect)
        menu.addAction(act_disconnect)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray_act_record = act_record
        self._tray_act_overlay = act_overlay
        self._tray_act_floating = act_floating
        self._tray_act_connect = act_connect
        self._tray_act_disconnect = act_disconnect
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_from_tray()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        if self._floating_now is not None:
            self._floating_now.close()
            self._floating_now = None
        self._force_quit = True
        self.close()

    def _tray_enabled(self) -> bool:
        return self._tray is not None and self._tray.isVisible()

    def _connected(self) -> bool:
        return self.backend.status == "connected"

    def _should_minimize_to_tray(self) -> bool:
        return (
            self._tray_enabled()
            and self._connected()
            and bool(self.settings.get("minimize_to_tray", True))
        )

    def _should_close_to_tray(self) -> bool:
        return (
            not self._force_quit
            and self._tray_enabled()
            and self._connected()
            and bool(self.settings.get("close_to_tray", True))
        )

    def _hide_to_tray(self) -> None:
        self.hide()
        if self._tray is not None:
            try:
                self._tray.showMessage(
                    self.i18n.t("app_title"),
                    self.i18n.t("tray_minimized"),
                    QSystemTrayIcon.Information,
                    2500,
                )
            except Exception:
                pass

    # -- sidebar ------------------------------------------------------------
    def toggle_sidebar(self) -> None:
        self.set_sidebar_visible(not self._sidebar_visible)

    def set_sidebar_visible(self, visible: bool, persist: bool = True) -> None:
        self._sidebar_visible = bool(visible)
        self.sidebar.setVisible(self._sidebar_visible)
        # Nav menu stands in while the side panel is hidden.
        self.nav_button.setVisible(not self._sidebar_visible)
        self.btn_sidebar.setToolTip(
            f"{self.i18n.t('show_sidebar' if not self._sidebar_visible else 'hide_sidebar')}"
            f"  (Ctrl+B)"
        )
        self.btn_sidebar.setProperty("active", "false" if self._sidebar_visible else "true")
        self.btn_sidebar.style().unpolish(self.btn_sidebar)
        self.btn_sidebar.style().polish(self.btn_sidebar)
        if persist:
            self.settings["sidebar_visible"] = self._sidebar_visible
            try:
                save_settings(self.settings)
            except OSError:
                pass
            # Keep Settings checkbox in sync if that page is loaded.
            if hasattr(self.page_settings, "show_sidebar"):
                self.page_settings.show_sidebar.setChecked(self._sidebar_visible)

    # -- navigation ---------------------------------------------------------
    def _navigate(self, key: str) -> None:
        self.sidebar.set_active(key)
        if key == "settings":
            self.page_settings.load_settings(
                {**self.settings, **self.page_monitor.prefs_snapshot(),
                 **self.page_overlay.collect(),
                 "sidebar_visible": self._sidebar_visible})
        elif key == "overlay":
            self.page_overlay.load_prefs(
                self.settings, port=self.backend.port)
            ready = (self.backend.status == "connected"
                     and bool(self.settings.get("start_web_server", True)))
            self.page_overlay.set_web_ready(ready)
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
        web_ready = status == "connected" and bool(
            self.settings.get("start_web_server", True))
        self.btn_web.setEnabled(web_ready)
        self.page_overlay.set_port(self.backend.port)
        self.page_overlay.set_web_ready(web_ready)
        if self._tray is not None:
            tip = self.i18n.t("tray_tooltip")
            if status == "connected":
                tip = f"{tip} · {self.i18n.t('connected')}"
            elif status == "error":
                tip = f"{tip} · {self.i18n.t('error')}"
            self._tray.setToolTip(tip)
            if hasattr(self, "_tray_act_connect"):
                self._tray_act_connect.setEnabled(status != "connecting")
                self._tray_act_disconnect.setEnabled(busy)
                self._tray_act_overlay.setEnabled(web_ready)
                if hasattr(self, "_tray_act_floating"):
                    self._tray_act_floating.setEnabled(status == "connected")

    def _on_state(self, state: dict) -> None:
        self._last_state = state
        if self._floating_now is not None and self._floating_now.isVisible():
            self._floating_now.apply_state(state)
        self.page_monitor.update_state(state)
        self.page_health.update_state(state)

        devices = state.get("devices") or []
        devices_sig = tuple(
            (d.get("number"), d.get("name"), d.get("kind"), d.get("ip"))
            for d in devices
        )
        if devices_sig != self._devices_sig:
            self._devices_sig = devices_sig
            self.page_devices.update_state(state)

        lib = state.get("library")
        ol = state.get("onelibrary")
        library_sig = (
            None if lib is None else tuple(sorted((str(k), str(v)) for k, v in lib.items())),
            None if ol is None else (
                ol.get("present"), ol.get("readable"), ol.get("tracks"),
                ol.get("playlists"), ol.get("history"), ol.get("detail"), ol.get("error"),
            ),
            state.get("library_error"),
            state.get("host") or "",
        )
        if library_sig != self._library_sig:
            self._library_sig = library_sig
            self.page_library.update_state(state)

        self.page_session.update_state(state)

        packets = state.get("packets") or 0
        mode = state.get("mode") or state.get("mode_kind") or "—"
        footer_sig = (mode, len(devices), packets)
        if footer_sig != self._footer_sig:
            self._footer_sig = footer_sig
            self.footer.setText(
                f"{mode}   ·   {len(devices)} {self.i18n.t('nav_devices').lower()}   ·   "
                f"{packets} {self.i18n.t('packets')}"
            )

    def _on_paint_tick(self) -> None:
        self.page_monitor.advance_playheads()
        if self._floating_now is not None and self._floating_now.isVisible():
            self._floating_now.tick_paint()

    def _on_track_ready(self, _tid: int) -> None:
        # Next state tick will attach meta/waveform; nudge playheads now.
        self.page_monitor.advance_playheads()

    def _on_settings_saved(self, data: dict, reconnect: bool) -> None:
        # preserve window size
        data["window_width"] = self.width()
        data["window_height"] = self.height()
        merged = {**self.settings, **data}
        save_settings(merged)
        self.settings = load_settings()
        self.page_monitor.apply_prefs(self.settings)
        self.page_settings.load_settings(self.settings)
        self.page_overlay.load_prefs(self.settings, port=int(self.settings.get("port", 8777)))
        self.set_sidebar_visible(bool(self.settings.get("sidebar_visible", True)), persist=False)
        if self.backend.monitor is not None:
            self.backend.monitor.show_phrases = bool(
                self.settings.get("show_phrases", True))
        if self._floating_now is not None:
            self._floating_now.apply_settings()
        if reconnect:
            self.connect_backend()
        else:
            QMessageBox.information(self, self.i18n.t("settings_title"),
                                    self.i18n.t("saved"))

    def _on_overlay_prefs(self, data: dict) -> None:
        self.settings.update(data)
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.page_settings.load_settings(self.settings)

    def _on_port_changed(self, port: int) -> None:
        """HTTP bind fell back to another port — keep UI / saved settings in sync."""
        self.settings["port"] = int(port)
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.page_settings.load_settings(self.settings)
        self.page_overlay.set_port(int(port))
        self.page_overlay.set_web_ready(True)

    def _nudge_zoom(self, direction: int) -> None:
        levels = list(ZOOM_BARS)
        cur = self.page_monitor._zoom
        try:
            i = levels.index(cur)
        except ValueError:
            i = levels.index(DEFAULT_ZOOM_BARS)
        nxt = levels[max(0, min(len(levels) - 1, i + direction))]
        self.page_monitor.set_zoom(nxt)

    def changeEvent(self, event):  # noqa: N802
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            if self._should_minimize_to_tray():
                # Defer hide so Qt finishes the minimize transition cleanly.
                QTimer.singleShot(0, self._hide_to_tray)
                event.accept()
                return
        super().changeEvent(event)

    def closeEvent(self, event):  # noqa: N802
        if self._should_close_to_tray():
            event.ignore()
            self._hide_to_tray()
            return
        self.settings["window_width"] = self.width()
        self.settings["window_height"] = self.height()
        self.settings["sidebar_visible"] = self._sidebar_visible
        self.settings.update(self.page_monitor.prefs_snapshot())
        self.settings.update(self.page_overlay.collect())
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.backend.stop()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None and self._force_quit:
            app.quit()
