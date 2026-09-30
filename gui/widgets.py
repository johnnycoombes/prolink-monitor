"""Reusable widgets for the desktop shell."""

from __future__ import annotations

import struct
from typing import Any

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from gui.theme import COLORS, DECK_COLORS


def mmss(ms: float) -> str:
    ms = max(0.0, float(ms or 0))
    total = int(ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_waveform(data: bytes) -> tuple[dict | None, dict | None]:
    """Unpack the PLWF detail + overview blocks produced by app._pack_wave."""
    if not data or len(data) < 24:
        return None, None

    def one(offset: int):
        if offset + 24 > len(data):
            return None, offset
        magic, ver, n, cps, dur, _flags = struct.unpack_from("<4sIIfII", data, offset)
        if magic != b"PLWF" or ver != 1 or n < 0:
            return None, offset
        start = offset + 24
        end_h = start + n
        end_rgb = end_h + n * 3
        if end_rgb > len(data):
            return None, offset
        return {
            "n": n,
            "cps": cps,
            "dur": dur,
            "h": data[start:end_h],
            "rgb": data[end_h:end_rgb],
        }, end_rgb

    detail, next_off = one(0)
    overview, _ = one(next_off) if detail else (None, 0)
    return detail, overview


class Sidebar(QFrame):
    navigated = Signal(str)

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)
        self._i18n = i18n
        self._buttons: dict[str, QPushButton] = {}
        self._active = "monitor"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(6)

        brand = QLabel(i18n.t("brand"))
        brand.setObjectName("Brand")
        sub = QLabel(i18n.t("brand_sub"))
        sub.setObjectName("BrandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(18)

        for key in ("monitor", "devices", "library", "settings", "about"):
            btn = QPushButton(i18n.t(f"nav_{key}"))
            btn.setObjectName("NavButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda _=False, k=key: self._click(k))
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch(1)
        self._set_active("monitor")

    def retranslate(self) -> None:
        for key, btn in self._buttons.items():
            btn.setText(self._i18n.t(f"nav_{key}"))

    def _click(self, key: str) -> None:
        self._set_active(key)
        self.navigated.emit(key)

    def _set_active(self, key: str) -> None:
        self._active = key
        for name, btn in self._buttons.items():
            btn.setProperty("active", "true" if name == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)


class WaveformView(QWidget):
    """Overview strip + scrolling detail waveform with fixed playhead."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._color = QColor(COLORS["accent"])
        self._detail = None
        self._overview = None
        self._meta: dict[str, Any] | None = None
        self._pos_ms = 0.0
        self._playing = False
        self._zoom = 8
        self._offair = False

    def set_deck_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_zoom(self, seconds: int) -> None:
        self._zoom = seconds
        self.update()

    def set_offair(self, off: bool) -> None:
        self._offair = off
        self.update()

    def set_track(self, detail, overview, meta: dict | None) -> None:
        self._detail = detail
        self._overview = overview
        self._meta = meta
        self.update()

    def set_position(self, pos_ms: float, playing: bool) -> None:
        self._pos_ms = pos_ms
        self._playing = playing
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        overview_h = max(28, int(h * 0.28))
        detail_h = h - overview_h - 1

        bg = QColor("#0b0d10")
        p.fillRect(0, 0, w, overview_h, bg)
        p.fillRect(0, overview_h + 1, w, detail_h, QColor("#080a0c"))
        p.setPen(QColor(COLORS["line"]))
        p.drawLine(0, overview_h, w, overview_h)

        alpha = 0.34 if self._offair else 1.0
        p.setOpacity(alpha)
        self._paint_overview(p, 0, 0, w, overview_h)
        self._paint_detail(p, 0, overview_h + 1, w, detail_h)
        p.setOpacity(1.0)
        p.end()

    def _paint_overview(self, p: QPainter, x0, y0, w, h) -> None:
        ov = self._overview
        if not ov or not ov["n"]:
            return
        n = ov["n"]
        heights = ov["h"]
        rgb = ov["rgb"]
        mid = y0 + h / 2
        # cache-ish: draw columns directly
        for x in range(w):
            i = min(n - 1, (x * n) // w)
            tall = (heights[i] / 31.0) * (h / 2) * 0.94
            color = QColor(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])
            color.setAlpha(140)
            p.fillRect(x0 + x, int(mid - tall), 1, max(1, int(tall * 2)), color)

        dur = (self._meta or {}).get("duration_ms") or ov.get("dur") or 1
        px = max(0, min(w, int((self._pos_ms / max(1, dur)) * w)))
        # played portion full brightness
        for x in range(px):
            i = min(n - 1, (x * n) // w)
            tall = (heights[i] / 31.0) * (h / 2) * 0.94
            color = QColor(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])
            p.fillRect(x0 + x, int(mid - tall), 1, max(1, int(tall * 2)), color)

        for cue in (self._meta or {}).get("cues") or []:
            cx = int((cue.get("t", 0) / max(1, dur)) * w)
            c = cue.get("color")
            color = QColor(*c) if c else QColor("#ffd45e" if cue.get("hot") else "#ffffff")
            p.fillRect(x0 + cx, y0, 1, h, color)

        p.fillRect(x0 + px - 1, y0, 2, h, self._color)

    def _paint_detail(self, p: QPainter, x0, y0, w, h) -> None:
        d = self._detail
        if not d or not d["n"]:
            return
        n = d["n"]
        cps = d["cps"] or 150.0
        heights = d["h"]
        rgb = d["rgb"]
        mid = y0 + h / 2
        visible = max(1.0, self._zoom * cps)
        px_per_col = w / visible
        current = (self._pos_ms / 1000.0) * cps
        start = current - visible / 2

        # beat grid
        beats = (self._meta or {}).get("beats") or []
        if beats:
            t0 = (start / cps) * 1000
            t1 = ((start + visible) / cps) * 1000
            for beat in beats:
                t, number = beat[0], beat[1]
                if t < t0 - 500:
                    continue
                if t > t1 + 500:
                    break
                x = int(((t / 1000.0) * cps - start) * px_per_col)
                pen = QPen(QColor(255, 255, 255, 50 if number == 1 else 18))
                p.setPen(pen)
                p.drawLine(x0 + x, y0, x0 + x, y0 + h)

        for px in range(w):
            lo = int(start + px / px_per_col)
            hi = int(start + (px + 1) / px_per_col + 0.999)
            if hi <= lo:
                hi = lo + 1
            if hi <= 0 or lo >= n:
                continue
            lo = max(0, lo)
            hi = min(n, hi)
            tall = 0
            src = lo
            for i in range(lo, hi):
                if heights[i] > tall:
                    tall = heights[i]
                    src = i
            a = (tall / 31.0) * (h / 2) * 0.92
            color = QColor(rgb[src * 3], rgb[src * 3 + 1], rgb[src * 3 + 2])
            p.fillRect(x0 + px, int(mid - a), 1, max(1, int(a * 2)), color)

        for cue in (self._meta or {}).get("cues") or []:
            x = int(((cue.get("t", 0) / 1000.0) * cps - start) * px_per_col)
            if x < -20 or x > w + 20:
                continue
            c = cue.get("color")
            color = QColor(*c) if c else QColor("#ffd45e" if cue.get("hot") else "#ffffff")
            p.fillRect(x0 + x - 1, y0, 2, h, color)

        # fixed playhead
        cx = x0 + w // 2
        glow = QColor(self._color)
        glow.setAlpha(45 if self._playing else 20)
        p.fillRect(cx - 8, y0, 16, h, glow)
        p.fillRect(cx - 1, y0, 2, h, self._color)


class DeckCard(QFrame):
    def __init__(self, number: int, i18n, parent=None):
        super().__init__(parent)
        self.setObjectName("DeckCard")
        self.number = number
        self._i18n = i18n
        self._color = DECK_COLORS.get(number, COLORS["accent"])
        self._track_id = 0
        self._meta = None
        self._detail = None
        self._overview = None
        self._art_id = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(f"background:{self._color}; border:none; border-radius:0;")
        root.addWidget(accent)

        body = QHBoxLayout()
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(14)
        root.addLayout(body, 1)

        # identity
        ident = QVBoxLayout()
        ident.setSpacing(6)
        top = QHBoxLayout()
        self.art = QLabel("♪")
        self.art.setFixedSize(64, 64)
        self.art.setAlignment(Qt.AlignCenter)
        self.art.setStyleSheet(
            f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
            f"border-radius:4px; color:{COLORS['dimmest']}; font-size:22px;"
        )
        top.addWidget(self.art)

        who = QVBoxLayout()
        who.setSpacing(3)
        row = QHBoxLayout()
        self.num = QLabel(str(number))
        self.num.setStyleSheet(
            f"font-family:monospace; font-size:18px; font-weight:700; color:{self._color};"
        )
        self.tags = QLabel("")
        self.tags.setObjectName("Mono")
        row.addWidget(self.num)
        row.addWidget(self.tags, 1)
        who.addLayout(row)
        self.title = QLabel("—")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size:14px; font-weight:600;")
        self.artist = QLabel("")
        self.artist.setObjectName("Dim")
        self.artist.setStyleSheet(f"color:{COLORS['dim']}; font-size:12px;")
        self.meta_line = QLabel("")
        self.meta_line.setStyleSheet(
            f"font-family:monospace; font-size:10px; color:{COLORS['dimmest']};"
        )
        who.addWidget(self.title)
        who.addWidget(self.artist)
        who.addWidget(self.meta_line)
        who.addStretch(1)
        top.addLayout(who, 1)
        ident.addLayout(top)
        wrap_ident = QWidget()
        wrap_ident.setFixedWidth(260)
        wrap_ident.setLayout(ident)
        body.addWidget(wrap_ident)

        # waveform
        self.wave = WaveformView()
        self.wave.set_deck_color(self._color)
        body.addWidget(self.wave, 1)

        # readouts
        read = QVBoxLayout()
        read.setSpacing(2)
        self.bpm = QLabel("— BPM")
        self.bpm.setStyleSheet(
            "font-family:monospace; font-size:28px; font-weight:700;"
        )
        self.pitch = QLabel("")
        self.pitch.setStyleSheet(f"font-family:monospace; font-size:12px; color:{COLORS['dim']};")
        times = QHBoxLayout()
        self.elapsed = QLabel("00:00")
        self.elapsed.setStyleSheet("font-family:monospace; font-size:15px; font-weight:700;")
        self.remaining = QLabel("-00:00")
        self.remaining.setStyleSheet(
            f"font-family:monospace; font-size:15px; font-weight:700; color:{COLORS['dim']};"
        )
        times.addWidget(self.elapsed)
        times.addSpacing(12)
        times.addWidget(self.remaining)
        times.addStretch(1)
        self.state = QLabel("")
        self.state.setStyleSheet(
            f"font-family:monospace; font-size:10px; color:{COLORS['dim']};"
        )
        self.key = QLabel("")
        self.key.setStyleSheet(
            f"font-family:monospace; font-size:12px; font-weight:700; color:{self._color};"
        )
        state_row = QHBoxLayout()
        state_row.addWidget(self.state, 1)
        state_row.addWidget(self.key)
        read.addWidget(self.bpm)
        read.addWidget(self.pitch)
        read.addSpacing(4)
        read.addLayout(times)
        read.addLayout(state_row)
        read.addStretch(1)
        wrap_read = QWidget()
        wrap_read.setFixedWidth(170)
        wrap_read.setLayout(read)
        body.addWidget(wrap_read)

        self.setMinimumHeight(150)

    def set_zoom(self, seconds: int) -> None:
        self.wave.set_zoom(seconds)

    def set_track_data(self, meta: dict | None, detail, overview, art: bytes | None) -> None:
        self._meta = meta
        self._detail = detail
        self._overview = overview
        self.wave.set_track(detail, overview, meta)
        if meta:
            self.title.setText(meta.get("title") or "—")
            self.artist.setText(meta.get("artist") or "")
            bits = [meta.get("album"), meta.get("genre"), meta.get("label"),
                    str(meta.get("year") or "") or None]
            self.meta_line.setText(" · ".join(b for b in bits if b))
            self.key.setText(meta.get("key") or "")
        if art and self._art_id != (meta or {}).get("id"):
            img = QImage.fromData(art)
            if not img.isNull():
                pix = QPixmap.fromImage(img).scaled(
                    64, 64, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.art.setPixmap(pix)
                self.art.setText("")
                self._art_id = (meta or {}).get("id", 0)

    def update_deck(self, deck: dict, pos_ms: float) -> None:
        tid = deck.get("track_id") or 0
        if tid != self._track_id:
            self._track_id = tid
            self._meta = None
            self._detail = None
            self._overview = None
            self._art_id = 0
            if not tid:
                self.title.setText("—")
                self.artist.setText("")
                self.meta_line.setText(self._i18n.t("no_track_loaded"))
                self.key.setText("")
                self.art.setPixmap(QPixmap())
                self.art.setText("♪")
                self.wave.set_track(None, None, None)

        empty = not tid
        self.setProperty("offair", "true" if (tid and not deck.get("on_air")) else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.wave.set_offair(bool(tid and not deck.get("on_air")))

        tags = []
        if deck.get("master"):
            tags.append(self._chip(self._i18n.t("master"), "#ffd45e"))
        if deck.get("sync"):
            tags.append(self._chip(self._i18n.t("sync"), COLORS["dim"]))
        if deck.get("on_air"):
            tags.append(self._chip(self._i18n.t("on_air"), COLORS["danger"]))
        elif tid:
            tags.append(self._chip(self._i18n.t("channel_closed"), COLORS["dim"]))
        self.tags.setText(" ".join(tags))

        bpm = deck.get("bpm") or 0
        self.bpm.setText(f"{bpm:.2f} BPM" if bpm else "— BPM")
        pitch = float(deck.get("pitch") or 0)
        sign = "+" if pitch >= 0 else ""
        self.pitch.setText(f"{sign}{pitch:.2f} %" if tid else "")
        if pitch > 0.01:
            self.pitch.setStyleSheet(
                f"font-family:monospace; font-size:12px; color:#ff9f45;"
            )
        elif pitch < -0.01:
            self.pitch.setStyleSheet(
                f"font-family:monospace; font-size:12px; color:#5eb0ff;"
            )
        else:
            self.pitch.setStyleSheet(
                f"font-family:monospace; font-size:12px; color:{COLORS['dim']};"
            )

        self.elapsed.setText(mmss(pos_ms))
        remain = (deck.get("duration_ms") or 0) - pos_ms
        self.remaining.setText("-" + mmss(remain))
        low = bool(deck.get("duration_ms") and remain < 30000)
        self.remaining.setStyleSheet(
            "font-family:monospace; font-size:15px; font-weight:700; color:"
            + (COLORS["danger"] if low else COLORS["dim"]) + ";"
        )
        self.state.setText(self._i18n.state(deck.get("state") or "unknown"))
        if empty and not self._meta:
            self.title.setText("—")
        elif tid and not self._meta:
            self.title.setText(self._i18n.t("loading"))

        self.wave.set_position(pos_ms, bool(deck.get("playing")))

    @staticmethod
    def _chip(text: str, color: str) -> str:
        return (
            f'<span style="font-family:monospace; font-size:9px; font-weight:700; '
            f'letter-spacing:1px; color:{color}; border:1px solid {color}; '
            f'padding:1px 5px; margin-right:4px;">{text}</span>'
        )
