"""Frameless floating Now Playing card or panel (live deck metadata)."""

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

from gui.overlay_now import normalize_now_pos, normalize_now_style
from gui.theme import COLORS
from gui.widgets import WaveformView, parse_waveform
from prolink.proto import resolve_playhead_position


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
    """Draggable, resizable always-on-top window fed from Backend state.

    Panel style: ``pos=bottom`` (default) shows track info above the full-width
    scrolling waveform; ``pos=top`` puts the waveform above the info row.
    """

    _CARD_MIN = (360, 420)
    _PANEL_MIN = (640, 160)
    _CARD_DEFAULT = (420, 520)
    _PANEL_DEFAULT = (960, 200)

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
        self._drag_target: QWidget | None = None
        self._last_state: dict | None = None
        self._received_at = 0.0
        self._track_key = ""
        self._track_id = 0
        self._wave_loaded_key = ""
        self._playhead_mode = "auto"

        self.setWindowTitle(i18n.t("floating_now_title"))
        self._apply_window_flags()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self._build_card_mode(outer)
        self._build_panel_mode(outer)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_geometry)

        self._apply_mode_visibility()
        self._apply_transparency()

    def _build_card_mode(self, outer: QVBoxLayout) -> None:
        self._card_wrap = QWidget()
        card_outer = QVBoxLayout(self._card_wrap)
        card_outer.setContentsMargins(0, 0, 0, 0)
        self._row = QHBoxLayout()
        self._row.setSpacing(0)
        card_outer.addLayout(self._row, 1)

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
        outer.addWidget(self._card_wrap, 1)

    def _build_panel_mode(self, outer: QVBoxLayout) -> None:
        self._panel_wrap = QFrame()
        self._panel_wrap.setObjectName("FloatingNowPanel")
        self._panel_wrap.setStyleSheet(
            f"QFrame#FloatingNowPanel {{"
            f" background: rgba(8,10,14,230);"
            f" border: 1px solid {COLORS['line']};"
            f" border-radius: 10px;"
            f"}}"
        )
        self._panel_outer = QVBoxLayout()
        self._panel_wrap.setLayout(self._panel_outer)
        self._panel_outer.setContentsMargins(12, 10, 12, 8)
        self._panel_outer.setSpacing(8)

        self._panel_info = QWidget()
        info_l = QHBoxLayout(self._panel_info)
        info_l.setContentsMargins(0, 0, 0, 0)
        info_l.setSpacing(12)

        self._panel_art = QLabel("♪")
        self._panel_art.setFixedSize(64, 64)
        self._panel_art.setAlignment(Qt.AlignCenter)
        self._panel_art.setStyleSheet(
            f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
            f"border-radius:8px; font-size:28px; color:{COLORS['dimmest']};"
        )
        info_l.addWidget(self._panel_art)

        copy = QVBoxLayout()
        copy.setSpacing(2)
        self._panel_tags = QLabel("")
        self._panel_tags.setStyleSheet("font-family:monospace;font-size:9px;color:#22d3ee;")
        copy.addWidget(self._panel_tags)
        self._panel_title = QLabel("—")
        self._panel_title.setStyleSheet("font-size:18px;font-weight:700;color:#f4f6fb;")
        self._panel_title.setWordWrap(True)
        copy.addWidget(self._panel_title)
        self._panel_artist = QLabel("")
        self._panel_artist.setStyleSheet(f"font-size:13px;color:{COLORS['dim']};")
        copy.addWidget(self._panel_artist)
        read = QHBoxLayout()
        self._panel_bpm = QLabel("— BPM")
        self._panel_bpm.setStyleSheet("font-family:monospace;font-size:15px;font-weight:700;")
        self._panel_key = QLabel("")
        self._panel_key.setStyleSheet("font-family:monospace;font-size:13px;font-weight:700;")
        self._panel_deck = QLabel("")
        self._panel_deck.setStyleSheet("font-family:monospace;font-size:10px;color:#22d3ee;")
        read.addWidget(self._panel_bpm)
        read.addWidget(self._panel_key)
        read.addStretch(1)
        read.addWidget(self._panel_deck)
        copy.addLayout(read)
        info_l.addLayout(copy, 1)

        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self._panel_wrap))
        self._panel_grip_host = QWidget()
        self._panel_grip_host.setLayout(grip_row)

        self._panel_wave = WaveformView()
        self._panel_wave.setMinimumHeight(72)

        self._panel_outer.addWidget(self._panel_info)
        self._panel_outer.addWidget(self._panel_wave, 1)
        self._panel_outer.addWidget(self._panel_grip_host)
        self._panel_wrap.installEventFilter(self)
        self._panel_info.installEventFilter(self)
        outer.addWidget(self._panel_wrap, 1)

    def eventFilter(self, obj, event):  # noqa: N802
        drag_targets = {self._card}
        if getattr(self, "_panel_wrap", None):
            drag_targets.add(self._panel_wrap)
        if getattr(self, "_panel_info", None):
            drag_targets.add(self._panel_info)
        if obj not in drag_targets:
            return super().eventFilter(obj, event)
        if event.type() == event.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.LeftButton:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                return True
        if event.type() == event.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                self._save_timer.start()
                return True
        if event.type() == event.Type.MouseButtonRelease:
            self._drag_offset = None
        return super().eventFilter(obj, event)

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
            self.setStyleSheet(f"background:{COLORS['bg']};")

    def _apply_card_side(self) -> None:
        settings = self._settings_getter()
        pos = normalize_now_pos("card", settings.get("overlay_now_pos"))
        right = pos == "right"
        self._left_spacer.setVisible(right)
        self._right_spacer.setVisible(not right)
        align = Qt.AlignRight if right else Qt.AlignLeft
        self._row.setAlignment(self._card, align)

    def _apply_panel_layout(self) -> None:
        settings = self._settings_getter()
        top = normalize_now_pos("panel", settings.get("overlay_now_pos")) == "top"
        while self._panel_outer.count():
            item = self._panel_outer.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
        if top:
            self._panel_outer.addWidget(self._panel_wave, 1)
            self._panel_outer.addWidget(self._panel_info)
        else:
            self._panel_outer.addWidget(self._panel_info)
            self._panel_outer.addWidget(self._panel_wave, 1)
        self._panel_outer.addWidget(self._panel_grip_host)

    def _apply_mode_visibility(self) -> None:
        panel = self._style() == "panel"
        self._card_wrap.setVisible(not panel)
        self._panel_wrap.setVisible(panel)
        min_w, min_h = self._PANEL_MIN if panel else self._CARD_MIN
        self.setMinimumSize(min_w, min_h)

    def _apply_wave_prefs(self) -> None:
        settings = self._settings_getter()
        style = str(settings.get("overlay_waveform_style") or "rgb").lower()
        if style not in ("rgb", "3band", "blue"):
            style = "rgb"
        self._panel_wave.set_style(style)
        self._panel_wave.set_show_phrases(bool(settings.get("show_phrases", True)))
        try:
            bars = int(settings.get("zoom_bars") or 4)
        except (TypeError, ValueError):
            bars = 4
        if bars not in (1, 2, 4, 8, 16):
            bars = 4
        self._panel_wave.set_zoom(bars)
        self._playhead_mode = str(settings.get("playhead_position") or "auto")

    def apply_settings(self) -> None:
        self._apply_window_flags()
        self._apply_transparency()
        self._apply_card_side()
        self._apply_panel_layout()
        self._apply_wave_prefs()
        self._apply_mode_visibility()

    def restore_geometry(self, settings: dict[str, Any]) -> None:
        style = normalize_now_style(settings.get("overlay_now_style"))
        if style == "panel":
            min_w, min_h = self._PANEL_MIN
            def_w, def_h = self._PANEL_DEFAULT
            keys = (
                "floating_now_panel_x",
                "floating_now_panel_y",
                "floating_now_panel_width",
                "floating_now_panel_height",
            )
        else:
            min_w, min_h = self._CARD_MIN
            def_w, def_h = self._CARD_DEFAULT
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
        self._persist_geometry()
        super().closeEvent(event)

    def _deck_tags(self, deck: dict) -> str:
        tags = []
        if deck.get("master"):
            tags.append("MASTER")
        if deck.get("sync"):
            tags.append("SYNC")
        if deck.get("on_air"):
            tags.append("ON AIR")
        return " · ".join(tags)

    def _load_waveform_if_needed(self, deck: dict, meta: dict | None) -> None:
        tid = int(deck.get("track_id") or 0)
        tkey = str(deck.get("track_key") or "")
        load_key = f"{tid}:{tkey}"
        if not tid or load_key == self._wave_loaded_key:
            return
        deck_no = int(deck.get("number") or 0)
        wave = self._backend.waveform(tid, deck=deck_no, track_key=tkey or None)
        detail, overview = parse_waveform(wave) if wave else (None, None)
        if meta and tkey and meta.get("track_key") != tkey:
            return
        self._panel_wave.set_track(detail, overview, meta)
        self._wave_loaded_key = load_key

    def apply_state(self, state: dict | None) -> None:
        if not state:
            return
        self._last_state = state
        self._received_at = time.time()
        settings = self._settings_getter()
        deck = pick_now_deck(state, use_mix=bool(settings.get("overlay_mix", True)))
        if not deck or not deck.get("track_id"):
            empty = self._i18n.t("no_track")
            self._title.setText(empty)
            self._artist.setText("")
            self._bpm.setText("— BPM")
            self._key.setText("")
            self._deck.setText("")
            self._tags.setText("")
            self._progress.setValue(0)
            self._art.setPixmap(QPixmap())
            self._art.setText("♪")
            self._panel_title.setText(empty)
            self._panel_artist.setText("")
            self._panel_bpm.setText("— BPM")
            self._panel_key.setText("")
            self._panel_deck.setText("")
            self._panel_tags.setText("")
            self._panel_art.setPixmap(QPixmap())
            self._panel_art.setText("♪")
            self._panel_wave.set_track(None, None, None)
            self._track_id = 0
            self._track_key = ""
            self._wave_loaded_key = ""
            return

        tid = int(deck.get("track_id") or 0)
        tkey = str(deck.get("track_key") or "")
        if tid != self._track_id or tkey != self._track_key:
            self._track_id = tid
            self._track_key = tkey
            self._wave_loaded_key = ""
            loading = self._i18n.t("loading")
            self._title.setText(loading)
            self._artist.setText("")
            self._art.setPixmap(QPixmap())
            self._art.setText("♪")
            self._panel_title.setText(loading)
            self._panel_artist.setText("")
            self._panel_art.setPixmap(QPixmap())
            self._panel_art.setText("♪")

        deck_no = int(deck.get("number") or 0)
        meta = self._backend.meta(tid, deck=deck_no, track_key=tkey or None)
        if meta and tkey and meta.get("track_key") != tkey:
            meta = None
        if meta:
            title = meta.get("title") or "—"
            artist = meta.get("artist") or ""
            key = meta.get("key") or ""
            self._title.setText(title)
            self._artist.setText(artist)
            self._key.setText(key)
            self._panel_title.setText(title)
            self._panel_artist.setText(artist)
            self._panel_key.setText(key)
            if meta.get("has_artwork"):
                art = self._backend.artwork(tid, deck=deck_no, track_key=tkey or None)
                if art:
                    img = QImage.fromData(art)
                    if not img.isNull():
                        for label, w, h in (
                            (self._art, 400, 200),
                            (self._panel_art, 64, 64),
                        ):
                            pix = QPixmap.fromImage(img).scaled(
                                QSize(w, h),
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation,
                            )
                            label.setPixmap(pix)
                            label.setText("")
            self._load_waveform_if_needed(deck, meta)
        elif deck.get("title"):
            title = str(deck.get("title") or "—")
            artist = str(deck.get("artist") or "")
            self._title.setText(title)
            self._artist.setText(artist)
            self._panel_title.setText(title)
            self._panel_artist.setText(artist)

        bpm = deck.get("bpm") or 0
        bpm_txt = f"{float(bpm):.2f} BPM" if bpm else "— BPM"
        self._bpm.setText(bpm_txt)
        self._panel_bpm.setText(bpm_txt)
        deck_txt = f"DECK {deck_no}" if deck_no else ""
        self._deck.setText(deck_txt)
        self._panel_deck.setText(deck_txt)
        tags = self._deck_tags(deck)
        self._tags.setText(tags)
        self._panel_tags.setText(tags)

        color = "#ffb020" if deck_no == 1 else "#22d3ee"
        if deck_no == 3:
            color = "#f472b6"
        elif deck_no == 4:
            color = "#4ade80"
        self._panel_wave.set_deck_color(color)
        pos_mode = resolve_playhead_position(
            self._playhead_mode,
            deck.get("waveform_position"),
            str(deck.get("name") or ""),
        )
        self._panel_wave.set_playhead_position(pos_mode)
        self._panel_wave.set_bpm(float(deck.get("track_bpm") or deck.get("bpm") or 120))

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
        looping = deck.get("state") == "looping"
        self._panel_wave.set_loop_region(None, None, looping)
        self._panel_wave.set_position(pos, bool(deck.get("playing")))
