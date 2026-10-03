"""Stacked pages for the desktop shell."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.theme import COLORS
from gui.widgets import DeckCard, parse_waveform
from gui.settings import deck_elements_from

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
        self._zoom = 8
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
        for s in (4, 8, 16, 32):
            b = QPushButton(f"{s}s")
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=s: self.set_zoom(v))
            tools.addWidget(b)
            self._zoom_btns[s] = b

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

        self.set_zoom(8)
        self.set_max_decks(4)
        self.set_wave_style("rgb")

    def apply_prefs(self, settings: dict) -> None:
        self.set_zoom(int(settings.get("zoom_seconds", 8)))
        self.set_max_decks(int(settings.get("max_decks", 4)))
        self.set_wave_style(str(settings.get("waveform_style") or "rgb"))
        self._show_empty = bool(settings.get("show_empty_decks", True))
        self.set_elements(settings)

    def set_elements(self, settings: dict | None) -> None:
        self._elements = deck_elements_from(settings)
        for card in self._cards.values():
            card.apply_elements(self._elements)
        self._relayout_deck_heights()

    def set_zoom(self, seconds: int) -> None:
        self._zoom = seconds
        for s, b in self._zoom_btns.items():
            b.setProperty("active", "true" if s == seconds else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        for card in self._cards.values():
            card.set_zoom(seconds)

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
            "zoom_seconds": self._zoom,
            "max_decks": self._max_decks,
            "waveform_style": self._wave_style,
            **self._elements,
        }

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
                card.set_zoom(self._zoom)
                card.set_wave_style(self._wave_style)
                card.apply_elements(self._elements)
                self._cards[n] = card
                self.decks_layout.addWidget(card, 1)

            card = self._cards[n]
            pos = float(d.get("position_ms") or 0)
            if d.get("playing"):
                pos += dt * 1000.0 * float(d.get("speed") or 1.0)
            if d.get("duration_ms"):
                pos = min(pos, float(d["duration_ms"]))

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
            pos = float(d.get("position_ms") or 0)
            if d.get("playing"):
                pos += dt * 1000.0 * float(d.get("speed") or 1.0)
            dur = float(d.get("duration_ms") or 0)
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
    def __init__(self, i18n, parent=None):
        super().__init__(i18n, "library_title", "library_sub", parent)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.cards: dict[str, QLabel] = {}
        wrap = QFrame()
        wrap.setObjectName("Card")
        wrap_l = QVBoxLayout(wrap)
        wrap_l.setContentsMargins(18, 18, 18, 18)
        wrap_l.addLayout(self.grid)
        self.host_label = QLabel("")
        self.host_label.setObjectName("Dim")
        wrap_l.addWidget(self.host_label)
        self.onelibrary_label = QLabel("")
        self.onelibrary_label.setObjectName("Dim")
        wrap_l.addWidget(self.onelibrary_label)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color:{COLORS['danger']};")
        wrap_l.addWidget(self.error_label)
        self.layout_root.addWidget(wrap)
        self.layout_root.addStretch(1)
        for i, key in enumerate(("tracks", "artists", "albums")):
            title = QLabel(i18n.t(key).upper())
            title.setObjectName("SectionTitle")
            value = QLabel("—")
            value.setStyleSheet("font-family:monospace; font-size:28px; font-weight:700;")
            self.grid.addWidget(title, 0, i)
            self.grid.addWidget(value, 1, i)
            self.cards[key] = value

    def retranslate(self) -> None:
        super().retranslate()
        # section labels are recreated via keys on next update; title/sub handled

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
        err = state.get("library_error") or ""
        self.error_label.setText(err if not lib else "")


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
        self.preview = QCheckBox(i18n.t("overlay_preview"))

        form.addRow(i18n.t("overlay_layout"), self.layout_box)
        form.addRow(i18n.t("overlay_corner"), self.corner_box)
        form.addRow(i18n.t("overlay_decks"), self.decks_box)
        form.addRow("", self.playing_only)
        form.addRow("", self.use_mix)
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

        for w in (self.layout_box, self.corner_box, self.decks_box):
            w.currentIndexChanged.connect(self._on_change)
        self.playing_only.toggled.connect(self._on_change)
        self.use_mix.toggled.connect(self._on_change)
        self.preview.toggled.connect(self._on_change)
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
        self.preview.setChecked(False)
        self._refresh_url()

    def collect(self) -> dict:
        return {
            "overlay_layout": self.layout_box.currentData(),
            "overlay_corner": self.corner_box.currentData(),
            "overlay_decks": self.decks_box.currentData(),
            "overlay_playing_only": self.playing_only.isChecked(),
            "overlay_mix": self.use_mix.isChecked(),
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
        ]
        if not self.playing_only.isChecked():
            qs.append("playing=0")
        if not self.use_mix.isChecked():
            qs.append("mix=0")
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
        self.iface = QLineEdit()
        self.tshark = QLineEdit()
        self.cache = QLineEdit()
        conn.addRow(i18n.t("mode"), self.mode)
        conn.addRow(i18n.t("host"), self.host)
        conn.addRow(i18n.t("device_number"), self.number)
        conn.addRow(i18n.t("device_name"), self.name)
        conn.addRow(i18n.t("port"), self.port)
        conn.addRow(i18n.t("iface"), self.iface)
        conn.addRow(i18n.t("tshark"), self.tshark)
        conn.addRow(i18n.t("cache"), self.cache)

        disp = section("section_display")
        self.max_decks = QComboBox()
        for n in (2, 4):
            self.max_decks.addItem(str(n), n)
        self.zoom = QComboBox()
        for s in (4, 8, 16, 32):
            self.zoom.addItem(f"{s}s", s)
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
        ov.addRow(i18n.t("overlay_layout"), self.overlay_layout)
        ov.addRow(i18n.t("overlay_corner"), self.overlay_corner)
        ov.addRow(i18n.t("overlay_decks"), self.overlay_decks)
        ov.addRow("", self.overlay_playing)
        ov.addRow("", self.overlay_mix)

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
        self.iface.setText(data.get("iface") or "")
        self.tshark.setText(data.get("tshark") or "")
        self.cache.setText(data.get("cache") or "")
        idx = self.max_decks.findData(normalize_max_decks(data.get("max_decks", 4)))
        self.max_decks.setCurrentIndex(max(0, idx))
        idx = self.zoom.findData(int(data.get("zoom_seconds", 8)))
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
            "iface": self.iface.text().strip(),
            "tshark": self.tshark.text().strip(),
            "cache": self.cache.text().strip(),
            "max_decks": self.max_decks.currentData(),
            "zoom_seconds": self.zoom.currentData(),
            "waveform_style": self.wave_style.currentData(),
            "show_empty_decks": self.show_empty.isChecked(),
            "poll_hz": self.poll_hz.value(),
            "overlay_layout": self.overlay_layout.currentData(),
            "overlay_corner": self.overlay_corner.currentData(),
            "overlay_decks": self.overlay_decks.currentData(),
            "overlay_playing_only": self.overlay_playing.isChecked(),
            "overlay_mix": self.overlay_mix.isChecked(),
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
