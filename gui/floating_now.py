"""Frameless floating Now Playing — renders the same overlay page as OBS (WebEngine)."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizeGrip, QVBoxLayout, QWidget

_log = logging.getLogger(__name__)

from gui.overlay_now import normalize_now_style
from prolink.audience_deck import merge_live_deck

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE = True
except ImportError:  # pragma: no cover - optional PySide6-Addons
    QWebChannel = None  # type: ignore[misc, assignment]
    QWebEngineView = None  # type: ignore[misc, assignment]
    QWebEngineSettings = None  # type: ignore[misc, assignment]
    _HAS_WEBENGINE = False


def pick_now_deck(state: dict | None, *, use_mix: bool = False) -> dict | None:
    """Live audience deck (master / on-air), matching OBS overlay ``audience_deck``."""
    if not state:
        return None
    decks = state.get("decks") or []
    aud = state.get("audience_deck")
    if aud and aud.get("number"):
        merged = merge_live_deck(aud, decks)
        if merged and (merged.get("track_id") or merged.get("playing")):
            return merged
    if use_mix:
        np = state.get("now_playing")
        if np and np.get("number"):
            live = next((d for d in decks if d.get("number") == np.get("number")), None)
            deck = {**(np or {}), **(live or {})} if live else dict(np)
            if deck.get("track_id") or deck.get("playing"):
                return deck
    for d in decks:
        if d.get("track_id") and d.get("playing"):
            return d
    for d in decks:
        if d.get("track_id") or d.get("playing"):
            return d
    return decks[0] if decks else None


def webengine_available() -> bool:
    return _HAS_WEBENGINE


class _FloatingDragHandle(QWidget):
    """Native drag strip — uses OS window move (works when WebEngine swallows events)."""

    def __init__(self, window: "FloatingNowPlayingWindow", parent=None) -> None:
        super().__init__(parent)
        self._window = window
        self.setObjectName("FloatingDragHandle")
        self.setFixedHeight(10)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Drag to move")
        self.setStyleSheet(
            "#FloatingDragHandle{background:rgba(255,255,255,0.06);}"
            "#FloatingDragHandle:hover{background:rgba(255,255,255,0.18);}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._window._begin_system_drag("handle")
            event.accept()
            return
        super().mousePressEvent(event)


class _FloatingDragBridge(QObject):
    """JS → Qt drag bridge (Chromium eats mouse events before Qt event filters)."""

    def __init__(self, window: "FloatingNowPlayingWindow") -> None:
        super().__init__()
        self._window = window

    @Slot(int, int)
    def dragStart(self, global_x: int, global_y: int) -> None:
        self._window._begin_drag(global_x, global_y, origin="bridge")

    @Slot(int, int)
    def dragMove(self, global_x: int, global_y: int) -> None:
        self._window._drag_to(global_x, global_y)

    @Slot()
    def dragEnd(self) -> None:
        self._window._end_drag()


class FloatingNowPlayingWindow(QWidget):
    """Draggable, resizable always-on-top window showing ``web/overlay.html`` (now playing)."""

    _CARD_MIN = (380, 360)
    _PANEL_MIN = (640, 220)
    # Card hugs a 500px cover plus title, readouts and the overview wave.
    CARD_DEFAULT_SIZE = (600, 860)
    PANEL_DEFAULT_SIZE = (960, 280)

    def __init__(
        self,
        backend,
        i18n,
        settings_getter: Callable[[], dict[str, Any]],
        parent=None,
    ):
        super().__init__(parent)
        self._backend = backend
        self._i18n = i18n
        self._settings_getter = settings_getter
        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._loaded_url = ""
        self._drag_bridge: _FloatingDragBridge | None = None

        if not _HAS_WEBENGINE:
            raise RuntimeError(
                "PySide6 WebEngine is required for the floating Now Playing window. "
                "Install PySide6-Addons (see requirements.txt)."
            )

        self.setWindowTitle(i18n.t("floating_now_title"))
        self._apply_window_flags()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._drag_handle = _FloatingDragHandle(self)
        outer.addWidget(self._drag_handle, 0)

        self._web = QWebEngineView(self)
        self._web.setContextMenuPolicy(Qt.NoContextMenu)
        self._web.setAttribute(Qt.WA_TranslucentBackground, True)
        page = self._web.page()
        page.setBackgroundColor(Qt.transparent)
        wsettings = self._web.settings()
        wsettings.setAttribute(QWebEngineSettings.ShowScrollBars, False)
        wsettings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        wsettings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)

        self._drag_bridge = _FloatingDragBridge(self)
        channel = QWebChannel(page)
        channel.registerObject("floatingDrag", self._drag_bridge)
        page.setWebChannel(channel)
        page.loadFinished.connect(self._on_load_finished)

        outer.addWidget(self._web, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 4)
        grip_row.addStretch(1)
        self._grip = QSizeGrip(self)
        grip_row.addWidget(self._grip, 0, Qt.AlignBottom | Qt.AlignRight)
        outer.addLayout(grip_row)

        self._grip.installEventFilter(self)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_geometry)

        self._apply_transparency()
        self._apply_mode_visibility()

    def _begin_system_drag(self, origin: str) -> None:
        """Ask the window manager to move the frameless window (Qt 5.15+ / 6)."""
        wh = self.windowHandle()
        if wh is not None and hasattr(wh, "startSystemMove"):
            _log.info("floating now playing: startSystemMove (%s)", origin)
            wh.startSystemMove()
            self._dragging = True
            return
        _log.warning("floating now playing: startSystemMove unavailable (%s)", origin)

    def _begin_drag(self, global_x: int, global_y: int, *, origin: str = "bridge") -> None:
        wh = self.windowHandle()
        if wh is not None and hasattr(wh, "startSystemMove"):
            _log.info("floating now playing: startSystemMove (%s)", origin)
            wh.startSystemMove()
            self._dragging = True
            return
        _log.info("floating now playing: manual grabMouse drag (%s)", origin)
        self._drag_offset = QPoint(global_x, global_y) - self.frameGeometry().topLeft()
        self._dragging = True
        self.grabMouse()

    def _drag_to(self, global_x: int, global_y: int) -> None:
        if self._drag_offset is None:
            return
        if self._drag_offset is None:
            return
        self.move(QPoint(global_x, global_y) - self._drag_offset)
        self._save_timer.start()

    def _end_drag(self) -> None:
        self._dragging = False
        self._drag_offset = None
        if self.mouseGrabber() is self:
            self.releaseMouse()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._grip:
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._dragging:
            self._end_drag()
        super().mouseReleaseEvent(event)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return
        # overlay.html installs drag when float=1; retry once qwebchannel.js is ready.
        self._web.page().runJavaScript(
            "if (typeof installFloatingDrag === 'function') installFloatingDrag();"
        )

    def _style(self) -> str:
        return normalize_now_style(self._settings_getter().get("overlay_now_style"))

    def _geometry_keys(self) -> tuple[str, str, str, str]:
        if self._style() == "panel":
            return (
                "floating_now_panel_x",
                "floating_now_panel_y",
                "floating_now_panel_width",
                "floating_now_panel_height",
            )
        return (
            "floating_now_x",
            "floating_now_y",
            "floating_now_width",
            "floating_now_height",
        )

    @staticmethod
    def _default_card_size() -> tuple[int, int]:
        """Default size hugs the tall card (large art + meta + wave)."""
        content_w, content_h = FloatingNowPlayingWindow.CARD_DEFAULT_SIZE
        screen = QApplication.primaryScreen()
        if screen is None:
            return content_w, content_h
        geo = screen.availableGeometry()
        w = max(480, min(content_w, int(geo.width() * 0.46)))
        h = max(720, min(content_h, int(geo.height() * 0.9)))
        return w, h

    @staticmethod
    def _default_panel_size() -> tuple[int, int]:
        content_w, content_h = FloatingNowPlayingWindow.PANEL_DEFAULT_SIZE
        screen = QApplication.primaryScreen()
        if screen is None:
            return content_w, content_h
        geo = screen.availableGeometry()
        return (
            max(640, min(content_w, int(geo.width() * 0.62))),
            max(250, min(content_h, int(geo.height() * 0.32))),
        )

    def _apply_window_flags(self) -> None:
        settings = self._settings_getter()
        flags = Qt.FramelessWindowHint | Qt.Window
        if bool(settings.get("floating_now_topmost", True)):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if self.isVisible():
            self.show()

    def _apply_transparency(self) -> None:
        settings = self._settings_getter()
        transparent = bool(settings.get("floating_now_transparent", True))
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        if transparent:
            self.setStyleSheet("background:transparent;")
        else:
            from gui.theme import COLORS

            self.setStyleSheet(f"background:{COLORS['bg']};")

    def _apply_mode_visibility(self) -> None:
        panel = self._style() == "panel"
        min_w, min_h = self._PANEL_MIN if panel else self._CARD_MIN
        self.setMinimumSize(min_w, min_h)

    def _reload_overlay(self) -> None:
        from gui.overlay_url import build_floating_overlay_url

        port = int(getattr(self._backend, "port", 0) or 0)
        url = build_floating_overlay_url(port, self._settings_getter())
        if url == self._loaded_url:
            return
        self._loaded_url = url
        self._web.load(QUrl(url))

    def apply_settings(self) -> None:
        self._apply_window_flags()
        self._apply_transparency()
        self._apply_mode_visibility()
        self._reload_overlay()

    def load_url(self, url: str) -> None:
        """Load a specific overlay URL (used by screenshot tooling)."""
        self._loaded_url = url
        if "float=1" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}float=1"
        self._web.load(QUrl(url))

    def restore_geometry(self, settings: dict[str, Any]) -> None:
        style = normalize_now_style(settings.get("overlay_now_style"))
        if style == "panel":
            min_w, min_h = self._PANEL_MIN
            def_w, def_h = self._default_panel_size()
            keys = (
                "floating_now_panel_x",
                "floating_now_panel_y",
                "floating_now_panel_width",
                "floating_now_panel_height",
            )
        else:
            min_w, min_h = self._CARD_MIN
            def_w, def_h = self._default_card_size()
            keys = ("floating_now_x", "floating_now_y", "floating_now_width", "floating_now_height")
        try:
            w = int(settings.get(keys[2]) or 0)
            h = int(settings.get(keys[3]) or 0)
            x = int(settings.get(keys[0]) or -1)
            y = int(settings.get(keys[1]) or -1)
        except (TypeError, ValueError):
            w = h = 0
            x = y = -1
        if w >= min_w and h >= min_h:
            self.resize(w, h)
        else:
            self.resize(def_w, def_h)
        if x >= 0 and y >= 0:
            self.move(x, y)
        else:
            self.move(80, 80)

    def _persist_geometry(self) -> None:
        from gui.settings import load_settings, save_settings

        data = load_settings()
        geo = self.geometry()
        xk, yk, wk, hk = self._geometry_keys()
        data[xk] = int(geo.x())
        data[yk] = int(geo.y())
        data[wk] = int(geo.width())
        data[hk] = int(geo.height())
        try:
            save_settings(data)
        except OSError:
            pass

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._save_timer.start()

    def moveEvent(self, event):  # noqa: N802
        super().moveEvent(event)
        self._save_timer.start()

    def closeEvent(self, event):  # noqa: N802
        self._end_drag()
        self._persist_geometry()
        super().closeEvent(event)

    def apply_state(self, state: dict | None) -> None:
        """No-op: overlay page consumes SSE directly from the embedded HTTP server."""

    def tick_paint(self) -> None:
        """No-op: overlay paint loop runs inside the web view."""
