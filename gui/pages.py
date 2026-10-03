"""Stacked pages for the desktop shell."""

from __future__ import annotations

import os
import time
from typing import Any

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.theme import COLORS
from gui.widgets import DeckCard, parse_waveform
from gui.settings import deck_elements_from
from prolink.session import ZOOM_BARS, DEFAULT_ZOOM_BARS

# Visual stack order for Monitor cards (Pioneer-style 4-deck layout).
DECK_LAYOUT = {
    2: (1, 2),
    4: (3, 1, 2, 4),
}


def normalize_max_decks(n: int) -> int:
    """Only 2- and 4-deck layouts are supported."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 4
    return 4 if n >= 4 else 2


class Page(QFrame):
    def __init__(self, i18n, title_key: str, sub_key: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self._i18n = i18n
        self._title_key = title_key
        self._sub_key = sub_key

        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(24, 20, 24, 20)
        self.layout_root.setSpacing(16)

        head = QVBoxLayout()
        head.setSpacing(4)
        self.title = QLabel(i18n.t(title_key))
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel(i18n.t(sub_key))
        self.subtitle.setObjectName("PageSubtitle")
        head.addWidget(self.title)
        head.addWidget(self.subtitle)
        self.layout_root.addLayout(head)

    def retranslate(self) -> None:
        self.title.setText(self._i18n.t(self._title_key))
        self.subtitle.setText(self._i18n.t(self._sub_key))


class MonitorPage(Page):
    def __init__(self, i18n, backend, parent=None):
        super().__init__(i18n, "monitor_title", "monitor_sub", parent)
        self.backend = backend
        self._cards: dict[int, DeckCard] = {}
        self._zoom = DEFAULT_ZOOM_BARS
        self._zoom_overrides: dict[int, int] = {}
        self._focus_deck: int | None = None
        self._max_decks = 4
        self._wave_style = "rgb"
        self._show_empty = True
        self._elements = deck_elements_from()
        self._last_state: dict[str, Any] | None = None
        self._received_at = 0.0

        self.layout_root.setContentsMargins(20, 14, 20, 12)
        self.layout_root.setSpacing(10)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        zoom_lbl = QLabel(i18n.t("zoom"))
        zoom_lbl.setObjectName("SectionTitle")
        tools.addWidget(zoom_lbl)
        self._zoom_btns: dict[int, QPushButton] = {}
        for bars in ZOOM_BARS:
            b = QPushButton(f"{bars}")
            b.setObjectName("Chip")
            b.setToolTip(i18n.t("zoom_bars_tip").format(bars=bars, beats=bars * 4))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=bars: self.set_zoom(v))
            tools.addWidget(b)
            self._zoom_btns[bars] = b
        self.reset_zoom_btn = QPushButton(i18n.t("zoom_reset"))
        self.reset_zoom_btn.setObjectName("Chip")
        self.reset_zoom_btn.setCursor(Qt.PointingHandCursor)
        self.reset_zoom_btn.setToolTip(i18n.t("zoom_reset_tip"))
        self.reset_zoom_btn.clicked.connect(self.clear_zoom_overrides)
        tools.addWidget(self.reset_zoom_btn)

        tools.addSpacing(16)
        decks_lbl = QLabel(i18n.t("max_decks"))
        decks_lbl.setObjectName("SectionTitle")
        tools.addWidget(decks_lbl)
        self._deck_btns: dict[int, QPushButton] = {}
        for n in (2, 4):
            b = QPushButton(str(n))
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=n: self.set_max_decks(v))
            tools.addWidget(b)
            self._deck_btns[n] = b

        tools.addSpacing(16)
        wave_lbl = QLabel(i18n.t("wave"))
        wave_lbl.setObjectName("SectionTitle")
        tools.addWidget(wave_lbl)
        self._style_btns: dict[str, QPushButton] = {}
        for key, label in (("rgb", "RGB"), ("3band", "3BAND"), ("blue", "BLUE")):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=key: self.set_wave_style(v))
            tools.addWidget(b)
            self._style_btns[key] = b

        tools.addStretch(1)
        self.record_btn = QPushButton(i18n.t("session_record"))
        self.record_btn.setObjectName("Chip")
        self.record_btn.setCursor(Qt.PointingHandCursor)
        self.record_btn.setToolTip(i18n.t("session_record_tip"))
        self.record_btn.clicked.connect(self._toggle_record)
        tools.addWidget(self.record_btn)
        self.layout_root.addLayout(tools)

        self.waiting = QLabel(i18n.t("waiting"))
        self.waiting.setAlignment(Qt.AlignCenter)
        self.waiting.setStyleSheet(
            f"color:{COLORS['dim']}; font-family:monospace; font-size:13px; padding:40px;"
        )
        self.layout_root.addWidget(self.waiting)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.decks_host = QWidget()
        self.decks_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.decks_layout = QVBoxLayout(self.decks_host)
        self.decks_layout.setContentsMargins(0, 0, 0, 0)
        self.decks_layout.setSpacing(8)
        self.scroll.setWidget(self.decks_host)
        self.layout_root.addWidget(self.scroll, 1)
        self.scroll.viewport().installEventFilter(self)

        self.set_zoom(DEFAULT_ZOOM_BARS)
        self.set_max_decks(4)
        self.set_wave_style("rgb")

    def apply_prefs(self, settings: dict) -> None:
        self.set_zoom(int(settings.get("zoom_bars", DEFAULT_ZOOM_BARS)))
        raw = settings.get("zoom_bars_by_deck") or {}
        if isinstance(raw, dict):
            self._zoom_overrides = {
                int(k): int(v) for k, v in raw.items()
                if int(v) in ZOOM_BARS
            }
        self._apply_card_zooms()
        self.set_max_decks(int(settings.get("max_decks", 4)))
        self.set_wave_style(str(settings.get("waveform_style") or "rgb"))
        self._show_empty = bool(settings.get("show_empty_decks", True))
        self.set_elements(settings)

    def set_elements(self, settings: dict | None) -> None:
        self._elements = deck_elements_from(settings)
        for card in self._cards.values():
            card.apply_elements(self._elements)
        self._relayout_deck_heights()

    def set_zoom(self, bars: int) -> None:
        bars = int(bars) if int(bars) in ZOOM_BARS else DEFAULT_ZOOM_BARS
        # Toolbar zoom: if a deck is focused, override that deck only;
        # otherwise set the global default for decks without an override.
        if self._focus_deck is not None and self._focus_deck in self._cards:
            self._zoom_overrides[self._focus_deck] = bars
            self._cards[self._focus_deck].set_zoom(bars, override=True)
        else:
            self._zoom = bars
            for n, card in self._cards.items():
                if n not in self._zoom_overrides:
                    card.set_zoom(bars, override=False)
        for s, b in self._zoom_btns.items():
            b.setProperty("active", "true" if s == bars else "false")
            b.style().unpolish(b)
            b.style().polish(b)

    def clear_zoom_overrides(self) -> None:
        self._zoom_overrides.clear()
        self._apply_card_zooms()

    def _apply_card_zooms(self) -> None:
        for n, card in self._cards.items():
            if n in self._zoom_overrides:
                card.set_zoom(self._zoom_overrides[n], override=True)
            else:
                card.set_zoom(self._zoom, override=False)

    def _on_deck_focus(self, number: int) -> None:
        self._focus_deck = int(number)

    def _on_deck_zoom_override(self, number: int, bars: int) -> None:
        if bars < 0:
            self._zoom_overrides.pop(number, None)
            if number in self._cards:
                self._cards[number].set_zoom(self._zoom, override=False)
            return
        self._zoom_overrides[number] = bars

    def set_max_decks(self, n: int) -> None:
        self._max_decks = normalize_max_decks(n)
        for k, b in self._deck_btns.items():
            b.setProperty("active", "true" if k == self._max_decks else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        if self._last_state:
            self.update_state(self._last_state)
        else:
            self._relayout_deck_heights()

    def set_wave_style(self, style: str) -> None:
        self._wave_style = style if style in ("rgb", "3band", "blue") else "rgb"
        for key, b in self._style_btns.items():
            b.setProperty("active", "true" if key == self._wave_style else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        for card in self._cards.values():
            card.set_wave_style(self._wave_style)

    def prefs_snapshot(self) -> dict:
        return {
            "zoom_bars": self._zoom,
            "zoom_bars_by_deck": {str(k): v for k, v in self._zoom_overrides.items()},
            "max_decks": self._max_decks,
            "waveform_style": self._wave_style,
            **self._elements,
        }

    def _toggle_record(self) -> None:
        session = (self._last_state or {}).get("session") or {}
        if session.get("recording"):
            self.backend.stop_session()
        else:
            self.backend.start_session()

    def _sync_record_btn(self, state: dict) -> None:
        session = state.get("session") or {}
        recording = bool(session.get("recording"))
        if recording:
            n = len(session.get("tracks") or [])
            elapsed = session.get("elapsed") or "00:00:00"
            label = self._i18n.t("session_paused") if session.get("paused") else self._i18n.t("session_stop")
            self.record_btn.setText(f"{label} · {elapsed} · {n}")
            self.record_btn.setProperty("active", "true")
        else:
            self.record_btn.setText(self._i18n.t("session_record"))
            self.record_btn.setProperty("active", "false")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._relayout_deck_heights()
        return super().eventFilter(obj, event)

    def _relayout_deck_heights(self) -> None:
        """Split the Monitor viewport evenly so waveforms fill the panel."""
        order = DECK_LAYOUT.get(self._max_decks, (1, 2))
        cards = [self._cards[n] for n in order if n in self._cards]
        n = len(cards)
        vh = max(0, self.scroll.viewport().height())
        if n == 0:
            self.decks_host.setMinimumHeight(0)
            return
        spacing = self.decks_layout.spacing() * (n - 1)
        # Always divide the visible panel so cards fill it (no dead space below).
        # Soft floor keeps a usable waveform when the window is very short.
        floor = 140 if self._elements.get("deck_show_waveform", True) else 80
        natural = ((vh - spacing) // n) if vh else floor
        per = max(floor, natural)
        for card in cards:
            card.set_fill_height(per)
        total = per * n + spacing
        # Match the viewport when we fit; grow (scroll) only if the floor forces it.
        self.decks_host.setMinimumHeight(max(vh, total))
        self.decks_host.setMaximumHeight(16777215)

    def update_state(self, state: dict) -> None:
        self._last_state = state
        self._received_at = time.time()
        self._sync_record_btn(state)
        decks = list(state.get("decks") or [])
        visible = self._visible(decks)
        alive = {d["number"] for d in visible}

        for number in list(self._cards):
            if number not in alive:
                card = self._cards.pop(number)
                self.decks_layout.removeWidget(card)
                card.deleteLater()

        if not visible:
            self.waiting.show()
            self._relayout_deck_heights()
            devices = state.get("devices") or []
            if state.get("backend_status") == "idle":
                self.waiting.setText(self._i18n.t("idle"))
            elif state.get("backend_status") == "connecting":
                self.waiting.setText(self._i18n.t("connecting"))
            elif state.get("backend_status") == "error":
                self.waiting.setText(
                    f"{self._i18n.t('error')}: {state.get('backend_detail') or ''}"
                )
            elif devices:
                self.waiting.setText(self._i18n.t("seen_no_status"))
            else:
                self.waiting.setText(self._i18n.t("searching"))
            return

        self.waiting.hide()
        dt = time.time() - self._received_at
        # rebuild order
        for d in visible:
            n = d["number"]
            if n not in self._cards:
                card = DeckCard(n, self._i18n)
                if n in self._zoom_overrides:
                    card.set_zoom(self._zoom_overrides[n], override=True)
                else:
                    card.set_zoom(self._zoom, override=False)
                card.set_wave_style(self._wave_style)
                card.apply_elements(self._elements)
                card.focused.connect(self._on_deck_focus)
                card.zoom_override_changed.connect(self._on_deck_zoom_override)
                self._cards[n] = card
                self.decks_layout.addWidget(card, 1)

            card = self._cards[n]
            meta_dur = float((card._meta or {}).get("duration_ms") or 0)
            from gui.widgets import sane_duration_ms
            dur = sane_duration_ms(float(d.get("duration_ms") or 0), meta_dur)
            pos = float(d.get("position_ms") or 0)
            if d.get("playing"):
                pos += dt * 1000.0 * float(d.get("speed") or 1.0)
            if dur:
                pos = min(pos, dur)

            tid = d.get("track_id") or 0
            if tid:
                meta = self.backend.meta(tid)
                if meta:
                    if card.needs_waveform(tid):
                        detail = overview = None
                        wave = None
                        if self._elements.get("deck_show_waveform", True):
                            wave = self.backend.waveform(tid)
                        if wave:
                            detail, overview = parse_waveform(wave)
                        art = None
                        if (self._elements.get("deck_show_artwork", True)
                                and meta.get("has_artwork")):
                            art = self.backend.artwork(tid)
                        # Only attach when we have a wave, or when titles are still empty.
                        if wave or card._meta is None:
                            card.set_track_data(meta, detail, overview, art)
                    elif card._meta is None:
                        art = None
                        if (self._elements.get("deck_show_artwork", True)
                                and meta.get("has_artwork")):
                            art = self.backend.artwork(tid)
                        card.set_track_data(meta, card._detail, card._overview, art)
            card.update_deck(d, pos)

        # Keep layout order (2-deck: 1-2, 4-deck: 3-1-2-4).
        for i, d in enumerate(visible):
            card = self._cards[d["number"]]
            self.decks_layout.insertWidget(i, card, 1)
        self._relayout_deck_heights()

    def advance_playheads(self) -> None:
        """Extrapolate playheads between state polls (~60 Hz paint path)."""
        state = self._last_state
        if not state or not self._received_at:
            return
        decks = self._visible(state.get("decks") or [])
        if not decks:
            return
        dt = time.time() - self._received_at
        for d in decks:
            card = self._cards.get(d["number"])
            if card is None:
                continue
            from gui.widgets import sane_duration_ms
            meta_dur = float((card._meta or {}).get("duration_ms") or 0)
            dur = sane_duration_ms(float(d.get("duration_ms") or 0), meta_dur)
            pos = float(d.get("position_ms") or 0)
            if d.get("playing"):
                pos += dt * 1000.0 * float(d.get("speed") or 1.0)
            if dur:
                pos = min(pos, dur)
            card.advance_playhead(
                pos,
                bool(d.get("playing")),
                dur,
                bar=int(d.get("bar") or 0),
                looping=(d.get("state") == "looping"),
            )

    def _visible(self, decks: list[dict]) -> list[dict]:
        """Pick decks for the current layout and return them in display order."""
        by_num = {int(d.get("number") or 0): d for d in decks}
        order = DECK_LAYOUT.get(self._max_decks, (1, 2))
        out: list[dict] = []
        for n in order:
            d = by_num.get(n)
            if d is None:
                continue
            if not self._show_empty and not d.get("track_id"):
                continue
            out.append(d)
        return out


class DevicesPage(Page):
    def __init__(self, i18n, parent=None):
        super().__init__(i18n, "devices_title", "devices_sub", parent)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            i18n.t("col_number"), i18n.t("col_name"),
            i18n.t("col_kind"), i18n.t("col_ip"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.layout_root.addWidget(self.table, 1)
        self.empty = QLabel(i18n.t("no_devices"))
        self.empty.setObjectName("Dim")
        self.layout_root.addWidget(self.empty)

    def retranslate(self) -> None:
        super().retranslate()
        self.table.setHorizontalHeaderLabels([
            self._i18n.t("col_number"), self._i18n.t("col_name"),
            self._i18n.t("col_kind"), self._i18n.t("col_ip"),
        ])
        self.empty.setText(self._i18n.t("no_devices"))

    def update_state(self, state: dict) -> None:
        devices = state.get("devices") or []
        self.empty.setVisible(not devices)
        self.table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            vals = [str(d.get("number", "")), d.get("name", ""),
                    d.get("kind", ""), d.get("ip", "")]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                self.table.setItem(row, col, item)


class LibraryPage(Page):
    """Browse export.pdb tracks, OneLibrary playlists/history, and on-deck art."""

    def __init__(self, i18n, backend=None, parent=None):
        super().__init__(i18n, "library_title", "library_sub", parent)
        self.backend = backend
        self._query = ""
        self._source = "all"  # all | loaded | playlist:<id>|<host>
        self._last_host_key = ""
        self._art_ids: list[int] = []

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.cards: dict[str, QLabel] = {}
        for key in ("tracks", "artists", "albums"):
            box = QFrame()
            box.setObjectName("Card")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(14, 10, 14, 10)
            title = QLabel(i18n.t(key).upper())
            title.setObjectName("SectionTitle")
            value = QLabel("—")
            value.setStyleSheet("font-family:monospace; font-size:22px; font-weight:700;")
            lay.addWidget(title)
            lay.addWidget(value)
            stats.addWidget(box, 1)
            self.cards[key] = value
        self.layout_root.addLayout(stats)

        self.host_label = QLabel("")
        self.host_label.setObjectName("Dim")
        self.onelibrary_label = QLabel("")
        self.onelibrary_label.setObjectName("Dim")
        self.multi_label = QLabel("")
        self.multi_label.setStyleSheet(f"color:{COLORS['accent']}; font-size:12px;")
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color:{COLORS['danger']};")
        for w in (self.host_label, self.onelibrary_label, self.multi_label, self.error_label):
            self.layout_root.addWidget(w)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.t("library_search"))
        self.search.textChanged.connect(self._on_search)
        self.refresh_btn = QPushButton(i18n.t("library_all"))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._show_all)
        self.loaded_btn = QPushButton(i18n.t("library_loaded"))
        self.loaded_btn.setCursor(Qt.PointingHandCursor)
        self.loaded_btn.clicked.connect(self._show_loaded)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.refresh_btn)
        tools.addWidget(self.loaded_btn)
        self.layout_root.addLayout(tools)

        split = QSplitter(Qt.Horizontal)
        left = QFrame()
        left.setObjectName("Card")
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        pl_lbl = QLabel(i18n.t("library_playlists"))
        pl_lbl.setObjectName("SectionTitle")
        left_l.addWidget(pl_lbl)
        self.playlist_list = QListWidget()
        self.playlist_list.itemClicked.connect(self._on_playlist)
        left_l.addWidget(self.playlist_list, 1)
        hist_lbl = QLabel(i18n.t("library_history"))
        hist_lbl.setObjectName("SectionTitle")
        left_l.addWidget(hist_lbl)
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self._on_history)
        left_l.addWidget(self.history_list, 1)
        split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)
        self.art_scroll = QScrollArea()
        self.art_scroll.setWidgetResizable(True)
        self.art_scroll.setFixedHeight(96)
        self.art_scroll.setFrameShape(QFrame.NoFrame)
        self.art_host = QWidget()
        self.art_row = QHBoxLayout(self.art_host)
        self.art_row.setContentsMargins(0, 0, 0, 0)
        self.art_row.setSpacing(8)
        self.art_row.addStretch(1)
        self.art_scroll.setWidget(self.art_host)
        right_l.addWidget(self.art_scroll)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            i18n.t("library_col_title"),
            i18n.t("library_col_artist"),
            i18n.t("library_col_album"),
            i18n.t("library_col_bpm"),
            i18n.t("library_col_key"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        right_l.addWidget(self.table, 1)
        self.empty = QLabel(i18n.t("library_empty"))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(
            f"color:{COLORS['dim']}; font-family:monospace; font-size:13px; padding:24px;"
        )
        right_l.addWidget(self.empty)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        self.layout_root.addWidget(split, 1)

        self._status_timer_n = 0

    def set_backend(self, backend) -> None:
        self.backend = backend

    def retranslate(self) -> None:
        super().retranslate()
        self.search.setPlaceholderText(self._i18n.t("library_search"))
        self.refresh_btn.setText(self._i18n.t("library_all"))
        self.loaded_btn.setText(self._i18n.t("library_loaded"))
        self.empty.setText(self._i18n.t("library_empty"))
        self.table.setHorizontalHeaderLabels([
            self._i18n.t("library_col_title"),
            self._i18n.t("library_col_artist"),
            self._i18n.t("library_col_album"),
            self._i18n.t("library_col_bpm"),
            self._i18n.t("library_col_key"),
        ])

    def _on_search(self, text: str) -> None:
        self._query = text.strip()
        if self._source.startswith("playlist:"):
            return
        self._reload_tracks()

    def _show_all(self) -> None:
        self._source = "all"
        self._reload_tracks()

    def _show_loaded(self) -> None:
        self._source = "loaded"
        self._reload_tracks()

    def _on_playlist(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        self._source = f"playlist:{data.get('id')}|{data.get('host') or ''}"
        self._reload_tracks()

    def _on_history(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        self._source = f"playlist:{data.get('id')}|{data.get('host') or ''}"
        self._reload_tracks()

    def _reload_tracks(self) -> None:
        if self.backend is None:
            self._fill_table([])
            return
        tracks: list[dict] = []
        if self._source == "loaded":
            tracks = list(self.backend.loaded_tracks() or [])
            q = self._query.lower()
            if q:
                tracks = [
                    t for t in tracks
                    if q in f"{t.get('title','')} {t.get('artist','')} {t.get('album','')}".lower()
                ]
        elif self._source.startswith("playlist:"):
            _, rest = self._source.split(":", 1)
            pid_s, _, host = rest.partition("|")
            try:
                pid = int(pid_s)
            except ValueError:
                pid = 0
            data = self.backend.browse_playlist_tracks(pid, host or None)
            tracks = list((data or {}).get("tracks") or [])
        else:
            data = self.backend.browse_tracks(self._query, limit=200, offset=0)
            tracks = list((data or {}).get("tracks") or [])
            if (data or {}).get("multi_player"):
                self.multi_label.setText(self._i18n.t("library_multi"))
            else:
                self.multi_label.setText("")
        self._fill_table(tracks)
        self._refresh_art_grid(tracks[:24])

    def _fill_table(self, tracks: list[dict]) -> None:
        self.empty.setVisible(not tracks)
        self.table.setVisible(bool(tracks))
        self.table.setRowCount(len(tracks))
        for row, t in enumerate(tracks):
            bpm = t.get("bpm") or 0
            vals = [
                t.get("title") or "—",
                t.get("artist") or "",
                t.get("album") or "",
                f"{float(bpm):.1f}" if bpm else "—",
                t.get("key") or "",
            ]
            for col, val in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(val)))

    def _refresh_art_grid(self, tracks: list[dict]) -> None:
        while self.art_row.count():
            item = self.art_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._art_ids = []
        for t in tracks:
            tid = int(t.get("id") or 0)
            if not tid or not t.get("has_artwork"):
                continue
            self._art_ids.append(tid)
            cell = QLabel("♪")
            cell.setFixedSize(72, 72)
            cell.setAlignment(Qt.AlignCenter)
            cell.setStyleSheet(
                f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
                f"border-radius:6px; color:{COLORS['dimmest']};"
            )
            cell.setToolTip(f"{t.get('title') or ''} — {t.get('artist') or ''}")
            if self.backend is not None:
                art = self.backend.artwork(tid)
                if art:
                    from PySide6.QtGui import QImage, QPixmap
                    img = QImage.fromData(art)
                    if not img.isNull():
                        pix = QPixmap.fromImage(img).scaled(
                            72, 72, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        cell.setPixmap(pix)
                        cell.setText("")
            self.art_row.addWidget(cell)
        self.art_row.addStretch(1)
        self.art_scroll.setVisible(bool(self._art_ids))

    def _reload_sidebars(self) -> None:
        if self.backend is None:
            return
        data = self.backend.browse_playlists() or {}
        self.playlist_list.clear()
        for p in data.get("playlists") or []:
            if p.get("folder"):
                continue
            item = QListWidgetItem(p.get("name") or f"#{p.get('id')}")
            item.setData(Qt.UserRole, p)
            self.playlist_list.addItem(item)
        self.history_list.clear()
        for h in data.get("history") or []:
            item = QListWidgetItem(h.get("name") or f"#{h.get('id')}")
            item.setData(Qt.UserRole, h)
            self.history_list.addItem(item)

    def update_state(self, state: dict) -> None:
        lib = state.get("library") or {}
        for key, label in self.cards.items():
            label.setText(str(lib.get(key, "—")) if lib else "—")
        host = state.get("host") or ""
        self.host_label.setText(host)
        ol = state.get("onelibrary")
        if ol and ol.get("present"):
            if ol.get("readable"):
                self.onelibrary_label.setText(
                    f"OneLibrary · {ol.get('detail') or 'readable'}")
            else:
                detail = ol.get("detail") or ol.get("error") or "present"
                self.onelibrary_label.setText(f"OneLibrary · {detail}")
        else:
            self.onelibrary_label.setText("")
        err = state.get("library_error")
        self.error_label.setText(err or "")

        # Refresh browse data when media appears / changes (throttled).
        host_key = f"{host}|{bool(lib)}|{bool(ol and ol.get('readable'))}"
        self._status_timer_n += 1
        if host_key != self._last_host_key or self._status_timer_n % 40 == 1:
            self._last_host_key = host_key
            self._reload_sidebars()
            self._reload_tracks()


class SessionPage(Page):
    """Realtime chronological playlist of tracks played during a recorded session."""

    def __init__(self, i18n, backend, parent=None):
        super().__init__(i18n, "session_title", "session_sub", parent)
        self.backend = backend
        self._last_count = -1
        self._export_note = ""

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.record_btn = QPushButton(i18n.t("session_record"))
        self.record_btn.setObjectName("Primary")
        self.record_btn.setCursor(Qt.PointingHandCursor)
        self.record_btn.clicked.connect(self._toggle_record)
        self.stop_btn = QPushButton(i18n.t("session_stop"))
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self._stop)
        self.clear_btn = QPushButton(i18n.t("session_clear"))
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear)
        self.copy_btn = QPushButton(i18n.t("session_copy"))
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_playlist)
        tools.addWidget(self.record_btn)
        tools.addWidget(self.stop_btn)
        tools.addWidget(self.clear_btn)
        tools.addWidget(self.copy_btn)
        tools.addStretch(1)
        self.status = QLabel(i18n.t("session_idle"))
        self.status.setObjectName("Mono")
        self.status.setStyleSheet(
            f"font-family:monospace; font-size:12px; color:{COLORS['dim']};"
        )
        tools.addWidget(self.status)
        self.layout_root.addLayout(tools)

        exports = QHBoxLayout()
        exports.setSpacing(8)
        self.export_csv_btn = QPushButton(i18n.t("session_export_csv"))
        self.export_json_btn = QPushButton(i18n.t("session_export_json"))
        self.export_m3u_btn = QPushButton(i18n.t("session_export_m3u"))
        self.export_all_btn = QPushButton(i18n.t("session_export_all"))
        self.export_all_btn.setObjectName("Primary")
        for btn, fmt in (
            (self.export_csv_btn, "csv"),
            (self.export_json_btn, "json"),
            (self.export_m3u_btn, "m3u"),
        ):
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, f=fmt: self._export_one(f))
            exports.addWidget(btn)
        self.export_all_btn.setCursor(Qt.PointingHandCursor)
        self.export_all_btn.clicked.connect(self._export_all)
        exports.addWidget(self.export_all_btn)
        exports.addStretch(1)
        self.layout_root.addLayout(exports)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            i18n.t("session_col_time"),
            i18n.t("session_col_deck"),
            i18n.t("session_col_title"),
            i18n.t("session_col_artist"),
            i18n.t("session_col_bpm"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.layout_root.addWidget(self.table, 1)

        self.empty = QLabel(i18n.t("session_empty"))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(
            f"color:{COLORS['dim']}; font-family:monospace; font-size:13px; padding:24px;"
        )
        self.layout_root.addWidget(self.empty)

    def retranslate(self) -> None:
        super().retranslate()
        self.table.setHorizontalHeaderLabels([
            self._i18n.t("session_col_time"),
            self._i18n.t("session_col_deck"),
            self._i18n.t("session_col_title"),
            self._i18n.t("session_col_artist"),
            self._i18n.t("session_col_bpm"),
        ])
        self.clear_btn.setText(self._i18n.t("session_clear"))
        self.stop_btn.setText(self._i18n.t("session_stop"))
        self.copy_btn.setText(self._i18n.t("session_copy"))
        self.export_csv_btn.setText(self._i18n.t("session_export_csv"))
        self.export_json_btn.setText(self._i18n.t("session_export_json"))
        self.export_m3u_btn.setText(self._i18n.t("session_export_m3u"))
        self.export_all_btn.setText(self._i18n.t("session_export_all"))
        self.empty.setText(self._i18n.t("session_empty"))

    def _toggle_record(self) -> None:
        mon = self.backend.monitor
        recording = bool(mon is not None and mon.session.recording)
        if recording:
            self._stop()
        else:
            self._export_note = ""
            self.backend.start_session()

    def _stop(self) -> None:
        paths = self.backend.stop_session()
        if paths:
            folder = os.path.dirname(paths[0]) if paths else ""
            self._export_note = f"{self._i18n.t('session_exported')} {folder}"
        else:
            self._export_note = ""

    def _clear(self) -> None:
        self._export_note = ""
        self.backend.clear_session()

    def _copy_playlist(self) -> None:
        from PySide6.QtWidgets import QApplication
        lines = []
        for row in range(self.table.rowCount()):
            vals = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                vals.append(item.text() if item else "")
            lines.append("\t".join(vals))
        QApplication.clipboard().setText("\n".join(lines))

    def _export_one(self, fmt: str) -> None:
        body = self.backend.export_session(fmt)
        if not body:
            return
        filters = {
            "csv": "CSV (*.csv)",
            "json": "JSON (*.json)",
            "m3u": "M3U playlist (*.m3u)",
        }
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._i18n.t(f"session_export_{fmt}"),
            f"session.{fmt}",
            filters.get(fmt, "All files (*)"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            self._export_note = f"{self._i18n.t('session_exported')} {path}"
        except OSError:
            self._export_note = ""

    def _export_all(self) -> None:
        paths = self.backend.save_session_exports()
        if paths:
            folder = os.path.dirname(paths[0])
            self._export_note = f"{self._i18n.t('session_exported')} {folder}"

    def update_state(self, state: dict) -> None:
        session = state.get("session") or {}
        recording = bool(session.get("recording"))
        paused = bool(session.get("paused"))
        tracks = session.get("tracks") or []
        elapsed = session.get("elapsed") or "00:00:00"
        if recording:
            label = (
                self._i18n.t("session_paused") if paused
                else self._i18n.t("session_recording")
            )
            self.record_btn.setText(self._i18n.t("session_recording"))
            self.status.setText(
                f"{label} · {elapsed} · {len(tracks)} {self._i18n.t('tracks')}"
            )
            color = COLORS["dim"] if paused else COLORS["danger"]
            self.status.setStyleSheet(
                f"font-family:monospace; font-size:12px; color:{color};"
            )
        else:
            self.record_btn.setText(self._i18n.t("session_record"))
            if self._export_note:
                self.status.setText(self._export_note)
            elif tracks:
                self.status.setText(
                    f"{self._i18n.t('session_stopped')} · {elapsed} · "
                    f"{len(tracks)} {self._i18n.t('tracks')}"
                )
            else:
                self.status.setText(self._i18n.t("session_idle"))
            self.status.setStyleSheet(
                f"font-family:monospace; font-size:12px; color:{COLORS['dim']};"
            )

        self.empty.setVisible(not tracks)
        self.table.setVisible(bool(tracks))
        self._last_count = len(tracks)
        self.table.setRowCount(len(tracks))
        for row, t in enumerate(tracks):
            bpm = t.get("bpm") or 0
            vals = [
                t.get("timestamp") or "00:00:00",
                str(t.get("deck") or ""),
                t.get("title") or "—",
                t.get("artist") or "",
                f"{bpm:.1f}" if bpm else "—",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col == 0:
                    item.setForeground(Qt.GlobalColor.cyan)
                self.table.setItem(row, col, item)


class OverlayPage(Page):
    """Build and copy an OBS Browser Source URL for web/overlay.html."""

    prefs_changed = Signal(dict)

    def __init__(self, i18n, backend, parent=None):
        super().__init__(i18n, "overlay_title", "overlay_sub", parent)
        self.backend = backend
        self._port = 8777

        howto = QLabel(i18n.t("overlay_howto"))
        howto.setObjectName("Dim")
        howto.setWordWrap(True)
        howto.setStyleSheet(f"color:{COLORS['dim']}; line-height:1.45; font-size:13px;")
        self.layout_root.addWidget(howto)

        card = QFrame()
        card.setObjectName("Card")
        form = QFormLayout(card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft)

        self.layout_box = QComboBox()
        self.layout_box.addItem(i18n.t("overlay_layout_now"), "nowplaying")
        self.layout_box.addItem(i18n.t("overlay_layout_dual"), "dual")
        self.layout_box.addItem(i18n.t("overlay_layout_min"), "minimal")
        self.layout_box.addItem(i18n.t("overlay_layout_setlist"), "setlist")
        self.corner_box = QComboBox()
        for key, label in (
            ("bl", "overlay_corner_bl"),
            ("br", "overlay_corner_br"),
            ("tl", "overlay_corner_tl"),
            ("tr", "overlay_corner_tr"),
            ("center", "overlay_corner_center"),
        ):
            self.corner_box.addItem(i18n.t(label), key)
        self.decks_box = QComboBox()
        for n in (1, 2, 3, 4):
            self.decks_box.addItem(str(n), n)
        self.playing_only = QCheckBox(i18n.t("overlay_playing"))
        self.use_mix = QCheckBox(i18n.t("overlay_mix"))
        self.use_mix.setChecked(True)
        self.show_tags = QCheckBox(i18n.t("overlay_show_tags"))
        self.show_tags.setChecked(True)
        self.show_bpm = QCheckBox(i18n.t("overlay_show_bpm"))
        self.show_bpm.setChecked(True)
        self.show_next = QCheckBox(i18n.t("overlay_show_next"))
        self.show_next.setChecked(True)
        self.wave_box = QComboBox()
        self.wave_box.addItem(i18n.t("wave_rgb"), "rgb")
        self.wave_box.addItem(i18n.t("wave_3band"), "3band")
        self.wave_box.addItem(i18n.t("wave_blue"), "blue")
        self.scale_box = QComboBox()
        for label, val in (("100%", "1"), ("75%", "0.75"), ("125%", "1.25"),
                           ("150%", "1.5"), ("200%", "2")):
            self.scale_box.addItem(label, val)
        self.accent_edit = QLineEdit()
        self.accent_edit.setPlaceholderText("#22d3ee")
        self.preview = QCheckBox(i18n.t("overlay_preview"))

        form.addRow(i18n.t("overlay_layout"), self.layout_box)
        form.addRow(i18n.t("overlay_corner"), self.corner_box)
        form.addRow(i18n.t("overlay_decks"), self.decks_box)
        form.addRow(i18n.t("overlay_wave"), self.wave_box)
        form.addRow(i18n.t("overlay_scale"), self.scale_box)
        form.addRow(i18n.t("overlay_accent"), self.accent_edit)
        form.addRow("", self.playing_only)
        form.addRow("", self.use_mix)
        form.addRow("", self.show_tags)
        form.addRow("", self.show_bpm)
        form.addRow("", self.show_next)
        form.addRow("", self.preview)
        self.layout_root.addWidget(card)

        url_box = QFrame()
        url_box.setObjectName("Card")
        url_l = QVBoxLayout(url_box)
        url_l.setContentsMargins(18, 16, 18, 16)
        url_l.setSpacing(10)
        url_lbl = QLabel(i18n.t("overlay_url"))
        url_lbl.setObjectName("SectionTitle")
        url_l.addWidget(url_lbl)
        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        self.url_edit.setObjectName("Mono")
        url_l.addWidget(self.url_edit)
        self.hint = QLabel("")
        self.hint.setObjectName("Dim")
        self.hint.setWordWrap(True)
        url_l.addWidget(self.hint)

        actions = QHBoxLayout()
        self.copy_btn = QPushButton(i18n.t("copy_url"))
        self.copy_btn.setObjectName("Primary")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn = QPushButton(i18n.t("open_overlay"))
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_url)
        self.open_btn.clicked.connect(self._open_overlay)
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        url_l.addLayout(actions)
        self.layout_root.addWidget(url_box)
        self.layout_root.addStretch(1)

        for w in (self.layout_box, self.corner_box, self.decks_box, self.wave_box,
                  self.scale_box):
            w.currentIndexChanged.connect(self._on_change)
        self.playing_only.toggled.connect(self._on_change)
        self.use_mix.toggled.connect(self._on_change)
        self.show_tags.toggled.connect(self._on_change)
        self.show_bpm.toggled.connect(self._on_change)
        self.show_next.toggled.connect(self._on_change)
        self.preview.toggled.connect(self._on_change)
        self.accent_edit.textChanged.connect(self._on_change)
        self.layout_box.currentIndexChanged.connect(self._maybe_bump_decks)

    def load_prefs(self, data: dict, port: int | None = None) -> None:
        if port is not None:
            self._port = int(port)
        idx = self.layout_box.findData(data.get("overlay_layout", "nowplaying"))
        self.layout_box.setCurrentIndex(max(0, idx))
        idx = self.corner_box.findData(data.get("overlay_corner", "bl"))
        self.corner_box.setCurrentIndex(max(0, idx))
        decks = int(data.get("overlay_decks", 1))
        if self.layout_box.currentData() == "dual" and decks < 2:
            decks = 2
        idx = self.decks_box.findData(decks)
        self.decks_box.setCurrentIndex(max(0, idx))
        self.playing_only.setChecked(bool(data.get("overlay_playing_only", True)))
        self.use_mix.setChecked(bool(data.get("overlay_mix", True)))
        self.show_tags.setChecked(bool(data.get("overlay_show_tags", True)))
        self.show_bpm.setChecked(bool(data.get("overlay_show_bpm", True)))
        self.show_next.setChecked(bool(data.get("overlay_show_next", True)))
        idx = self.wave_box.findData(data.get("overlay_waveform_style", "rgb"))
        self.wave_box.setCurrentIndex(max(0, idx))
        scale = str(data.get("overlay_scale") or "1")
        idx = self.scale_box.findData(scale)
        self.scale_box.setCurrentIndex(max(0, idx))
        self.accent_edit.setText(str(data.get("overlay_accent") or ""))
        self.preview.setChecked(False)
        self._refresh_url()

    def collect(self) -> dict:
        return {
            "overlay_layout": self.layout_box.currentData(),
            "overlay_corner": self.corner_box.currentData(),
            "overlay_decks": self.decks_box.currentData(),
            "overlay_playing_only": self.playing_only.isChecked(),
            "overlay_mix": self.use_mix.isChecked(),
            "overlay_waveform_style": self.wave_box.currentData(),
            "overlay_scale": self.scale_box.currentData(),
            "overlay_accent": self.accent_edit.text().strip(),
            "overlay_show_tags": self.show_tags.isChecked(),
            "overlay_show_bpm": self.show_bpm.isChecked(),
            "overlay_show_next": self.show_next.isChecked(),
        }

    def set_port(self, port: int) -> None:
        self._port = int(port)
        self._refresh_url()

    def set_web_ready(self, ready: bool) -> None:
        self.copy_btn.setEnabled(True)
        self.open_btn.setEnabled(ready)
        if ready:
            self.hint.setText("")
        else:
            self.hint.setText(self._i18n.t("overlay_need_web"))

    def build_url(self, preview: bool | None = None) -> str:
        qs = [
            f"layout={self.layout_box.currentData()}",
            f"corner={self.corner_box.currentData()}",
            f"decks={self.decks_box.currentData()}",
            f"wave={self.wave_box.currentData()}",
        ]
        scale = self.scale_box.currentData() or "1"
        if scale and scale != "1":
            qs.append(f"scale={scale}")
        accent = (self.accent_edit.text() or "").strip().lstrip("#")
        if accent and len(accent) == 6:
            qs.append(f"accent={accent}")
        if not self.playing_only.isChecked():
            qs.append("playing=0")
        if not self.use_mix.isChecked():
            qs.append("mix=0")
        if not self.show_tags.isChecked():
            qs.append("tags=0")
        if not self.show_bpm.isChecked():
            qs.append("bpm=0")
        if not self.show_next.isChecked():
            qs.append("next=0")
        use_preview = self.preview.isChecked() if preview is None else preview
        if use_preview:
            qs.append("preview=1")
        return f"http://127.0.0.1:{self._port}/overlay?{'&'.join(qs)}"

    def _maybe_bump_decks(self) -> None:
        if self.layout_box.currentData() == "dual" and int(self.decks_box.currentData() or 1) < 2:
            idx = self.decks_box.findData(2)
            if idx >= 0:
                self.decks_box.setCurrentIndex(idx)

    def _on_change(self, *_args) -> None:
        self._refresh_url()
        self.prefs_changed.emit(self.collect())

    def _refresh_url(self) -> None:
        self.url_edit.setText(self.build_url())

    def _copy_url(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.build_url(preview=False))
        self.hint.setText(self._i18n.t("copied"))

    def _open_overlay(self) -> None:
        self.backend.open_overlay(self.build_url())


class SettingsPage(Page):
    saved = Signal(dict, bool)  # settings, reconnect

    def __init__(self, i18n, parent=None):
        super().__init__(i18n, "settings_title", "settings_sub", parent)
        self._i18n = i18n

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        form_wrap = QVBoxLayout(body)
        form_wrap.setSpacing(18)

        def section(title_key: str) -> QFormLayout:
            box = QFrame()
            box.setObjectName("Card")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(18, 16, 18, 16)
            lbl = QLabel(i18n.t(title_key))
            lbl.setObjectName("SectionTitle")
            lay.addWidget(lbl)
            form = QFormLayout()
            form.setSpacing(10)
            form.setLabelAlignment(Qt.AlignLeft)
            lay.addLayout(form)
            form_wrap.addWidget(box)
            return form

        conn = section("section_connection")
        self.mode = QComboBox()
        self.mode.addItem(i18n.t("mode_auto"), "auto")
        self.mode.addItem(i18n.t("mode_vcdj"), "vcdj")
        self.mode.addItem(i18n.t("mode_sniffer"), "sniffer")
        self.host = QLineEdit()
        self.host.setPlaceholderText(i18n.t("host_hint"))
        self.number = QSpinBox()
        self.number.setRange(1, 6)
        self.name = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.http_bind = QCheckBox(i18n.t("http_bind"))
        self.http_bind.setToolTip(i18n.t("http_bind_tip"))
        self.http_bind.setChecked(True)
        self.iface = QLineEdit()
        self.tshark = QLineEdit()
        self.cache = QLineEdit()
        conn.addRow(i18n.t("mode"), self.mode)
        conn.addRow(i18n.t("host"), self.host)
        conn.addRow(i18n.t("device_number"), self.number)
        conn.addRow(i18n.t("device_name"), self.name)
        conn.addRow(i18n.t("port"), self.port)
        conn.addRow("", self.http_bind)
        conn.addRow(i18n.t("iface"), self.iface)
        conn.addRow(i18n.t("tshark"), self.tshark)
        conn.addRow(i18n.t("cache"), self.cache)

        disp = section("section_display")
        self.max_decks = QComboBox()
        for n in (2, 4):
            self.max_decks.addItem(str(n), n)
        self.zoom = QComboBox()
        for bars in ZOOM_BARS:
            self.zoom.addItem(
                i18n.t("zoom_bars_option").format(bars=bars, beats=bars * 4),
                bars,
            )
        self.wave_style = QComboBox()
        self.wave_style.addItem(i18n.t("wave_rgb"), "rgb")
        self.wave_style.addItem(i18n.t("wave_3band"), "3band")
        self.wave_style.addItem(i18n.t("wave_blue"), "blue")
        self.show_empty = QCheckBox(i18n.t("show_empty"))
        self.poll_hz = QSpinBox()
        self.poll_hz.setRange(5, 60)
        disp.addRow(i18n.t("max_decks"), self.max_decks)
        disp.addRow(i18n.t("zoom"), self.zoom)
        disp.addRow(i18n.t("wave"), self.wave_style)
        disp.addRow(i18n.t("poll_hz"), self.poll_hz)
        disp.addRow("", self.show_empty)

        deck_el = section("section_deck_elements")
        hint = QLabel(i18n.t("deck_el_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Dim")
        hint.setStyleSheet(f"color:{COLORS['dim']}; font-size:12px;")
        deck_el.addRow(hint)
        self.deck_checks: dict[str, QCheckBox] = {}
        for key, label_key in (
            ("deck_show_artwork", "deck_el_artwork"),
            ("deck_show_title", "deck_el_title"),
            ("deck_show_artist", "deck_el_artist"),
            ("deck_show_meta", "deck_el_meta"),
            ("deck_show_tags", "deck_el_tags"),
            ("deck_show_waveform", "deck_el_waveform"),
            ("deck_show_bpm", "deck_el_bpm"),
            ("deck_show_tempo", "deck_el_tempo"),
            ("deck_show_time", "deck_el_time"),
            ("deck_show_key", "deck_el_key"),
            ("deck_show_state", "deck_el_state"),
        ):
            cb = QCheckBox(i18n.t(label_key))
            self.deck_checks[key] = cb
            deck_el.addRow(cb)

        ov = section("section_overlay")
        self.overlay_layout = QComboBox()
        self.overlay_layout.addItem(i18n.t("overlay_layout_now"), "nowplaying")
        self.overlay_layout.addItem(i18n.t("overlay_layout_dual"), "dual")
        self.overlay_layout.addItem(i18n.t("overlay_layout_min"), "minimal")
        self.overlay_layout.addItem(i18n.t("overlay_layout_setlist"), "setlist")
        self.overlay_corner = QComboBox()
        for key, label in (
            ("bl", "overlay_corner_bl"),
            ("br", "overlay_corner_br"),
            ("tl", "overlay_corner_tl"),
            ("tr", "overlay_corner_tr"),
            ("center", "overlay_corner_center"),
        ):
            self.overlay_corner.addItem(i18n.t(label), key)
        self.overlay_decks = QComboBox()
        for n in (1, 2, 3, 4):
            self.overlay_decks.addItem(str(n), n)
        self.overlay_playing = QCheckBox(i18n.t("overlay_playing"))
        self.overlay_mix = QCheckBox(i18n.t("overlay_mix"))
        self.overlay_wave = QComboBox()
        self.overlay_wave.addItem(i18n.t("wave_rgb"), "rgb")
        self.overlay_wave.addItem(i18n.t("wave_3band"), "3band")
        self.overlay_wave.addItem(i18n.t("wave_blue"), "blue")
        ov.addRow(i18n.t("overlay_layout"), self.overlay_layout)
        ov.addRow(i18n.t("overlay_corner"), self.overlay_corner)
        ov.addRow(i18n.t("overlay_decks"), self.overlay_decks)
        ov.addRow(i18n.t("overlay_wave"), self.overlay_wave)
        ov.addRow("", self.overlay_playing)
        ov.addRow("", self.overlay_mix)

        sess = section("section_session")
        self.session_autosave = QCheckBox(i18n.t("session_autosave"))
        self.session_autosave_dir = QLineEdit()
        self.session_autosave_dir.setPlaceholderText("~/.prolink-monitor/sessions")
        sess.addRow(self.session_autosave)
        sess.addRow(i18n.t("session_autosave_dir"), self.session_autosave_dir)

        beh = section("section_behaviour")
        self.auto_connect = QCheckBox(i18n.t("auto_connect"))
        self.start_web = QCheckBox(i18n.t("start_web"))
        self.show_sidebar = QCheckBox(i18n.t("show_sidebar"))
        beh.addRow(self.auto_connect)
        beh.addRow(self.start_web)
        beh.addRow(self.show_sidebar)

        form_wrap.addStretch(1)
        scroll.setWidget(body)
        self.layout_root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        self.save_btn = QPushButton(i18n.t("save"))
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn = QPushButton(i18n.t("apply_reconnect"))
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(lambda: self._emit(False))
        self.apply_btn.clicked.connect(lambda: self._emit(True))
        actions.addWidget(self.save_btn)
        actions.addWidget(self.apply_btn)
        actions.addStretch(1)
        self.layout_root.addLayout(actions)

        self._labels = {
            "mode": conn.labelForField(self.mode),
            "host": conn.labelForField(self.host),
        }

    def load_settings(self, data: dict) -> None:
        idx = self.mode.findData(data.get("mode", "auto"))
        self.mode.setCurrentIndex(max(0, idx))
        self.host.setText(data.get("host") or "")
        self.number.setValue(int(data.get("number", 5)))
        self.name.setText(data.get("name") or "monitor")
        self.port.setValue(int(data.get("port", 8777)))
        self.http_bind.setChecked(str(data.get("http_bind") or "0.0.0.0") != "127.0.0.1")
        self.iface.setText(data.get("iface") or "")
        self.tshark.setText(data.get("tshark") or "")
        self.cache.setText(data.get("cache") or "")
        idx = self.max_decks.findData(normalize_max_decks(data.get("max_decks", 4)))
        self.max_decks.setCurrentIndex(max(0, idx))
        idx = self.zoom.findData(int(data.get("zoom_bars", DEFAULT_ZOOM_BARS)))
        self.zoom.setCurrentIndex(max(0, idx))
        idx = self.wave_style.findData(data.get("waveform_style", "rgb"))
        self.wave_style.setCurrentIndex(max(0, idx))
        self.show_empty.setChecked(bool(data.get("show_empty_decks", True)))
        self.poll_hz.setValue(int(data.get("poll_hz", 60)))
        for key, cb in self.deck_checks.items():
            cb.setChecked(bool(data.get(key, True)))
        idx = self.overlay_layout.findData(data.get("overlay_layout", "nowplaying"))
        self.overlay_layout.setCurrentIndex(max(0, idx))
        idx = self.overlay_corner.findData(data.get("overlay_corner", "bl"))
        self.overlay_corner.setCurrentIndex(max(0, idx))
        idx = self.overlay_decks.findData(int(data.get("overlay_decks", 1)))
        self.overlay_decks.setCurrentIndex(max(0, idx))
        self.overlay_playing.setChecked(bool(data.get("overlay_playing_only", True)))
        self.overlay_mix.setChecked(bool(data.get("overlay_mix", True)))
        idx = self.overlay_wave.findData(data.get("overlay_waveform_style", "rgb"))
        self.overlay_wave.setCurrentIndex(max(0, idx))
        self.session_autosave.setChecked(bool(data.get("session_autosave", False)))
        self.session_autosave_dir.setText(data.get("session_autosave_dir") or "")
        self.auto_connect.setChecked(bool(data.get("auto_connect", True)))
        self.start_web.setChecked(bool(data.get("start_web_server", True)))
        self.show_sidebar.setChecked(bool(data.get("sidebar_visible", True)))

    def collect(self) -> dict:
        data = {
            "mode": self.mode.currentData(),
            "host": self.host.text().strip(),
            "number": self.number.value(),
            "name": self.name.text().strip() or "monitor",
            "port": self.port.value(),
            "http_bind": "0.0.0.0" if self.http_bind.isChecked() else "127.0.0.1",
            "iface": self.iface.text().strip(),
            "tshark": self.tshark.text().strip(),
            "cache": self.cache.text().strip(),
            "max_decks": self.max_decks.currentData(),
            "zoom_bars": self.zoom.currentData(),
            "waveform_style": self.wave_style.currentData(),
            "show_empty_decks": self.show_empty.isChecked(),
            "poll_hz": self.poll_hz.value(),
            "overlay_layout": self.overlay_layout.currentData(),
            "overlay_corner": self.overlay_corner.currentData(),
            "overlay_decks": self.overlay_decks.currentData(),
            "overlay_playing_only": self.overlay_playing.isChecked(),
            "overlay_mix": self.overlay_mix.isChecked(),
            "overlay_waveform_style": self.overlay_wave.currentData(),
            "session_autosave": self.session_autosave.isChecked(),
            "session_autosave_dir": self.session_autosave_dir.text().strip(),
            "auto_connect": self.auto_connect.isChecked(),
            "start_web_server": self.start_web.isChecked(),
            "sidebar_visible": self.show_sidebar.isChecked(),
        }
        for key, cb in self.deck_checks.items():
            data[key] = cb.isChecked()
        return data

    def _emit(self, reconnect: bool) -> None:
        self.saved.emit(self.collect(), reconnect)


class AboutPage(Page):
    def __init__(self, i18n, parent=None):
        super().__init__(i18n, "about_title", "about_sub", parent)
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        self.body = QLabel(i18n.t("about_body"))
        self.body.setWordWrap(True)
        self.body.setObjectName("Dim")
        self.body.setStyleSheet(f"color:{COLORS['dim']}; line-height:1.5; font-size:13px;")
        lay.addWidget(self.body)
        self.layout_root.addWidget(card)
        self.layout_root.addStretch(1)

    def retranslate(self) -> None:
        super().retranslate()
        self.body.setText(self._i18n.t("about_body"))
