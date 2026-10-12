"""Frameless floating Now Playing — renders the same overlay page as OBS (WebEngine)."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizeGrip, QVBoxLayout, QWidget

_log = logging.getLogger(__name__)

from gui.overlay_now import is_strip_style, normalize_now_style
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


def saved_window_origin(x: int, y: int) -> tuple[int, int]:
    """Restored top-left. ``(-1, -1)`` is the unset sentinel, not a coordinate."""
    if int(x) == -1 and int(y) == -1:
        return (80, 80)
    return (int(x), int(y))


class _FloatingDragHandle(QWidget):
    """Drag strip above the web view.

    QWebEngineView is its own window and paints over ordinary siblings. This
    strip stays stacked above that window. The bar is the same dark glass as
    the overlay, with a light grip, so it stays readable when the window is
    transparent over a light or dark desktop. Hover brightens it. Alt+drag
    on the page is handled in overlay.html.
    """

    def __init__(self, window: "FloatingNowPlayingWindow", parent=None) -> None:
        super().__init__(parent)
        self._window = window
        self.setObjectName("FloatingDragHandle")
        self.setFixedHeight(22)
        self.setMouseTracking(True)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Drag to move  ·  Alt+drag anywhere")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Stay above the WebEngine child window. A native window of our own
        # is what used to disappear under Chromium on Windows.
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        # Dark glass, not a white wash. A white bar at low alpha vanishes on
        # a bright desktop once the floating window itself is transparent.
        self.setStyleSheet(
            "#FloatingDragHandle{"
            "background:rgba(8,10,14,0.92);"
            "border-bottom:1px solid rgba(255,255,255,0.42);"
            "}"
            "#FloatingDragHandle:hover{"
            "background:rgba(46,54,66,0.98);"
            "border-bottom:1px solid rgba(255,255,255,0.88);"
            "}"
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_grip(painter, self.underMouse())
        painter.end()

    def _paint_grip(self, painter: QPainter, hot: bool) -> None:
        """Two-by-three grip. A dark halo keeps the dots visible on light content."""
        cols, rows = 2, 3
        step = 6
        radius = 2.15 if hot else 1.9
        span_w = (cols - 1) * step
        span_h = (rows - 1) * step
        ox = (self.width() - span_w) / 2
        oy = (self.height() - span_h) / 2 - 0.5
        halo = QColor(0, 0, 0, 230)
        fill = QColor(255, 255, 255, 255 if hot else 235)
        painter.setPen(Qt.NoPen)
        for row in range(rows):
            for col in range(cols):
                center = QPointF(ox + col * step, oy + row * step)
                painter.setBrush(halo)
                painter.drawEllipse(center, radius + 0.85, radius + 0.85)
                painter.setBrush(fill)
                painter.drawEllipse(center, radius, radius)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if not self._window._begin_system_drag("handle"):
                pos = event.globalPosition()
                self._window._begin_drag(int(pos.x()), int(pos.y()), origin="handle")
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._window._dragging and self._window._drag_offset is not None:
            pos = event.globalPosition()
            self._window._drag_to(int(pos.x()), int(pos.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._window._dragging and self._window._drag_offset is not None:
            self._window._end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)


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

    _CARD_MIN = (420, 480)
    _PANEL_MIN = (640, 260)
    # 500px cover + title, readouts, overview wave, 14px inset, and the drag handle.
    CARD_DEFAULT_SIZE = (720, 980)
    # Info row + spectrum + detail wave, with the wave kept inside the window.
    PANEL_DEFAULT_SIZE = (1040, 360)
    # Panel art is 168px, then the visualiser and the detail wave.
    PANEL_LARGE_MIN = (640, 400)
    PANEL_LARGE_DEFAULT = (1040, 460)

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

    def _begin_system_drag(self, origin: str) -> bool:
        """Ask the window manager to move the frameless window.

        Returns False when the platform refuses (common for a translucent
        frameless window on Windows). The caller then tracks the mouse itself.
        """
        wh = self.windowHandle()
        start = getattr(wh, "startSystemMove", None) if wh is not None else None
        if not callable(start):
            _log.info("floating now playing: startSystemMove unavailable (%s)", origin)
            return False
        try:
            ok = bool(start())
        except Exception:
            _log.info("floating now playing: startSystemMove raised (%s)", origin)
            return False
        if not ok:
            _log.info("floating now playing: startSystemMove declined (%s)", origin)
            return False
        _log.info("floating now playing: startSystemMove (%s)", origin)
        self._dragging = True
        self._drag_offset = None
        return True

    def _begin_drag(self, global_x: int, global_y: int, *, origin: str = "bridge") -> None:
        """Track a drag by moving the widget.

        The page calls this from the web channel after the mouse press has
        already left Qt, so startSystemMove cannot see the button and must
        not swallow the later dragMove calls.
        """
        _log.info("floating now playing: manual drag (%s)", origin)
        self._drag_offset = QPoint(int(global_x), int(global_y)) - self.frameGeometry().topLeft()
        self._dragging = True
        if origin != "bridge":
            self.grabMouse()

    def _drag_to(self, global_x: int, global_y: int) -> None:
        if self._drag_offset is None:
            return
        self.move(QPoint(int(global_x), int(global_y)) - self._drag_offset)
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
        if is_strip_style(self._style()):
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
        self.setObjectName("FloatingNowPlaying")
        if transparent:
            bg = "transparent"
        else:
            from gui.theme import COLORS

            bg = COLORS["bg"]
        # An unscoped background rule also paints child widgets, which clears
        # the drag strip when this window is transparent.
        self.setStyleSheet(f"#FloatingNowPlaying{{background:{bg};}}")

    def _apply_mode_visibility(self) -> None:
        style = self._style()
        if style == "panel":
            min_w, min_h = self.PANEL_LARGE_MIN
        elif style == "bar":
            min_w, min_h = self._PANEL_MIN
        else:
            min_w, min_h = self._CARD_MIN
        self.setMinimumSize(min_w, min_h)

    def _reload_overlay(self) -> None:
        from gui.overlay_url import build_floating_overlay_url

        port = int(getattr(self._backend, "port", 0) or 0)
        url = build_floating_overlay_url(port, self._settings_getter())
        if url == self._loaded_url:
            return
        self._loaded_url = url
        self._web.load(QUrl(url))

    def reload_live(self) -> None:
        """Load the overlay again even when the URL has not changed.

        A reconnect keeps the same port, so the cached URL would otherwise
        leave this window on a dead event stream.
        """
        self._loaded_url = ""
        self._reload_overlay()

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
        if is_strip_style(style):
            if style == "panel":
                min_w, min_h = self.PANEL_LARGE_MIN
                def_w, def_h = self.PANEL_LARGE_DEFAULT
            else:
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
        origin_x, origin_y = saved_window_origin(x, y)
        self.move(origin_x, origin_y)

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

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        handle = getattr(self, "_drag_handle", None)
        if handle is not None:
            handle.raise_()

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
