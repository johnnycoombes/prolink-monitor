"""Frameless floating Now Playing card (live deck metadata)."""

from __future__ import annotations

import time
from typing import Any, Callable

from PySide6.QtCore import Qt, QPoint, QSize, QTimer
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from gui.theme import COLORS


def pick_now_deck(state: dict | None, *, use_mix: bool = True) -> dict | None:
    """Same audience-deck selection as the OBS overlay (SmartTiming when enabled)."""
    if not state:
        return None
    decks = state.get("decks") or []
    if use_mix:
        np = state.get("now_playing")
        if np and np.get("number"):
            live = next((d for d in decks if d.get("number") == np.get("number")), None)
            deck = {**(np or {}), **(live or {})} if live else dict(np)
            if deck.get("track_id") or deck.get("playing"):
                return deck
    for d in decks:
        if d.get("track_id") or d.get("playing"):
            return d
    return decks[0] if decks else None


class FloatingNowPlayingWindow(QWidget):
    """Draggable, resizable always-on-top card fed from Backend state."""

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
        self._last_state: dict | None = None
        self._received_at = 0.0
        self._track_key = ""
        self._track_id = 0

        self.setWindowTitle(i18n.t("floating_now_title"))
        self.setMinimumSize(360, 420)
        self._apply_window_flags()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self._row = QHBoxLayout()
        self._row.setSpacing(0)
        outer.addLayout(self._row, 1)

        self._card = QFrame()
        self._card.setObjectName("FloatingNowCard")
        self._card.setStyleSheet(
            f"QFrame#FloatingNowCard {{"
            f" background: rgba(8,10,14,230);"
            f" border: 1px solid {COLORS['line']};"
            f" border-radius: 14px;"
            f"}}"
        )
        card_l = QVBoxLayout(self._card)
        card_l.setContentsMargins(20, 16, 20, 14)
        card_l.setSpacing(8)

        self._tags = QLabel("")
        self._tags.setStyleSheet("font-family:monospace;font-size:9px;color:#22d3ee;")
        self._tags.setWordWrap(True)
        card_l.addWidget(self._tags)

        self._art = QLabel("♪")
        self._art.setAlignment(Qt.AlignCenter)
        self._art.setFixedHeight(200)
        self._art.setStyleSheet(
            f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
            f"border-radius:12px; font-size:48px; color:{COLORS['dimmest']};"
        )
        card_l.addWidget(self._art)

        self._title = QLabel("—")
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-size:22px;font-weight:700;color:#f4f6fb;")
        card_l.addWidget(self._title)

        self._artist = QLabel("")
        self._artist.setStyleSheet(f"font-size:14px;color:{COLORS['dim']};")
        self._artist.setWordWrap(True)
        card_l.addWidget(self._artist)

        read = QHBoxLayout()
        self._bpm = QLabel("— BPM")
        self._bpm.setStyleSheet("font-family:monospace;font-size:18px;font-weight:700;")
        self._key = QLabel("")
        self._key.setStyleSheet("font-family:monospace;font-size:14px;font-weight:700;")
        self._deck = QLabel("")
        self._deck.setStyleSheet("font-family:monospace;font-size:11px;color:#22d3ee;")
        read.addWidget(self._bpm)
        read.addWidget(self._key)
        read.addStretch(1)
        read.addWidget(self._deck)
        card_l.addLayout(read)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(5)
        self._progress.setStyleSheet(
            "QProgressBar{background:rgba(255,255,255,0.1);border:none;border-radius:3px;}"
            "QProgressBar::chunk{background:#22d3ee;border-radius:3px;}"
        )
        card_l.addWidget(self._progress)

        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self._card))
        card_l.addLayout(grip_row)

        self._left_spacer = QWidget()
        self._right_spacer = QWidget()
        self._row.addWidget(self._left_spacer, 1)
        self._row.addWidget(self._card, 0)
        self._row.addWidget(self._right_spacer, 1)

        self._card.installEventFilter(self)
        self._apply_card_side()
        self._apply_transparency()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_geometry)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._card:
            if event.type() == event.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.LeftButton:
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return True
            if event.type() == event.Type.MouseMove and isinstance(event, QMouseEvent):
                if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    self._save_timer.start()
                    return True
            if event.type() == event.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(obj, event)

    def _apply_window_flags(self) -> None:
        settings = self._settings_getter()
        flags = Qt.FramelessWindowHint | Qt.Window
        if bool(settings.get("floating_now_topmost", True)):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        # Re-show after flag change
        if self.isVisible():
            self.show()

    def _apply_transparency(self) -> None:
        settings = self._settings_getter()
        transparent = bool(settings.get("floating_now_transparent", True))
        self.setAttribute(Qt.WA_TranslucentBackground, transparent)
        if transparent:
            self.setStyleSheet("background:transparent;")
        else:
            self.setStyleSheet(f"background:{COLORS['bg']};")

    def _apply_card_side(self) -> None:
        settings = self._settings_getter()
        right = str(settings.get("overlay_now_pos") or "left").lower() == "right"
        self._left_spacer.setVisible(right)
        self._right_spacer.setVisible(not right)
        align = Qt.AlignRight if right else Qt.AlignLeft
        self._row.setAlignment(self._card, align)

    def apply_settings(self) -> None:
        self._apply_window_flags()
        self._apply_transparency()
        self._apply_card_side()

    def restore_geometry(self, settings: dict[str, Any]) -> None:
        try:
            w = int(settings.get("floating_now_width") or 0)
            h = int(settings.get("floating_now_height") or 0)
            x = int(settings.get("floating_now_x") or -1)
            y = int(settings.get("floating_now_y") or -1)
        except (TypeError, ValueError):
            w = h = 0
            x = y = -1
        if w >= 360 and h >= 420:
            self.resize(w, h)
        else:
            self.resize(420, 520)
        if x >= 0 and y >= 0:
            self.move(x, y)
        else:
            self.move(80, 80)

    def _persist_geometry(self) -> None:
        from gui.settings import load_settings, save_settings

        data = load_settings()
        geo = self.geometry()
        data["floating_now_x"] = int(geo.x())
        data["floating_now_y"] = int(geo.y())
        data["floating_now_width"] = int(geo.width())
        data["floating_now_height"] = int(geo.height())
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
        self._persist_geometry()
        super().closeEvent(event)

    def apply_state(self, state: dict | None) -> None:
        if not state:
            return
        self._last_state = state
        self._received_at = time.time()
        settings = self._settings_getter()
        deck = pick_now_deck(state, use_mix=bool(settings.get("overlay_mix", True)))
        if not deck or not deck.get("track_id"):
            self._title.setText(self._i18n.t("no_track"))
            self._artist.setText("")
            self._bpm.setText("— BPM")
            self._key.setText("")
            self._deck.setText("")
            self._tags.setText("")
            self._progress.setValue(0)
            self._art.setPixmap(QPixmap())
            self._art.setText("♪")
            self._track_id = 0
            self._track_key = ""
            return

        tid = int(deck.get("track_id") or 0)
        tkey = str(deck.get("track_key") or "")
        if tid != self._track_id or tkey != self._track_key:
            self._track_id = tid
            self._track_key = tkey
            self._title.setText(self._i18n.t("loading"))
            self._artist.setText("")
            self._art.setPixmap(QPixmap())
            self._art.setText("♪")

        deck_no = int(deck.get("number") or 0)
        meta = self._backend.meta(tid, deck=deck_no, track_key=tkey or None)
        if meta and tkey and meta.get("track_key") != tkey:
            meta = None
        if meta:
            self._title.setText(meta.get("title") or "—")
            self._artist.setText(meta.get("artist") or "")
            self._key.setText(meta.get("key") or "")
            if meta.get("has_artwork"):
                art = self._backend.artwork(tid, deck=deck_no, track_key=tkey or None)
                if art:
                    img = QImage.fromData(art)
                    if not img.isNull():
                        pix = QPixmap.fromImage(img).scaled(
                            QSize(400, 200),
                            Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation,
                        )
                        self._art.setPixmap(pix)
                        self._art.setText("")
        elif deck.get("title"):
            self._title.setText(str(deck.get("title") or "—"))
            self._artist.setText(str(deck.get("artist") or ""))

        bpm = deck.get("bpm") or 0
        self._bpm.setText(f"{float(bpm):.2f} BPM" if bpm else "— BPM")
        self._deck.setText(f"DECK {deck_no}" if deck_no else "")

        tags = []
        if deck.get("master"):
            tags.append("MASTER")
        if deck.get("sync"):
            tags.append("SYNC")
        if deck.get("on_air"):
            tags.append("ON AIR")
        self._tags.setText(" · ".join(tags))

        self._update_progress(deck)

    def tick_paint(self) -> None:
        if not self._last_state:
            return
        settings = self._settings_getter()
        deck = pick_now_deck(self._last_state, use_mix=bool(settings.get("overlay_mix", True)))
        if deck:
            self._update_progress(deck, extrapolate=True)

    def _update_progress(self, deck: dict, *, extrapolate: bool = False) -> None:
        dur = float(deck.get("duration_ms") or 0)
        pos = float(deck.get("position_ms") or 0)
        if extrapolate and deck.get("playing"):
            dt = time.time() - self._received_at
            pos += dt * 1000.0 * float(deck.get("speed") or 1.0)
        if dur > 0:
            pos = min(pos, dur)
            self._progress.setValue(int(max(0, min(1000, (pos / dur) * 1000))))
        else:
            self._progress.setValue(0)
