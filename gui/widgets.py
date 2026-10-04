"""Reusable widgets for the desktop shell."""

from __future__ import annotations

import struct
from typing import Any

from PySide6.QtCore import Qt, Signal, QSize, QPoint
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from gui.theme import COLORS, DECK_COLORS
from gui.settings import deck_elements_from
from prolink.proto import playhead_fraction, resolve_playhead_position
from prolink.session import ZOOM_BARS, bars_to_seconds

# Left identity column: room for artwork + MASTER / SYNC / ON AIR chips.
IDENT_WIDTH = 300


def mmss(ms: float) -> str:
    ms = max(0.0, float(ms or 0))
    total = int(ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def sane_duration_ms(deck_ms: float, meta_ms: float = 0) -> float:
    """Prefer a plausible track length when Absolute Position inflated duration."""
    deck = float(deck_ms or 0)
    meta = float(meta_ms or 0)
    limit = 12 * 3600 * 1000  # 12h

    def ok(d: float) -> bool:
        return 1000 < d < limit

    if ok(deck) and ok(meta):
        if deck > meta * 2:
            return meta
        if meta > deck * 2:
            return deck
        return max(deck, meta)
    if ok(meta):
        return meta
    if ok(deck):
        return deck
    return meta or deck or 0.0


# Beat Link WaveformFinder.WaveformStyle / ThreeBandLayer.
WAVE_STYLES = ("rgb", "3band", "blue")
BAND_LOW = (32, 83, 217)          # low frequencies
BAND_MID = (242, 170, 60)         # mids, when they stick out past the lows
BAND_OVERLAP = (169, 107, 39)     # where low and mid overlap
BAND_HIGH = (255, 255, 255)       # highs, drawn last
BLUE_SHADE = (0, 168, 232)        # COLOR_MAP[2], used when PWV3 is absent


def _cue_color(cue: dict) -> QColor:
    c = cue.get("color")
    if c and len(c) >= 3:
        return QColor(int(c[0]), int(c[1]), int(c[2]))
    return QColor("#ffd45e" if cue.get("hot") else "#ffffff")


# Phrase colours by rough section role (PSSI intro / verse / drop≈chorus).
_PHRASE_COLORS = {
    "intro": QColor(120, 180, 255),
    "verse": QColor(160, 220, 120),
    "bridge": QColor(200, 160, 255),
    "chorus": QColor(255, 120, 90),
    "drop": QColor(255, 120, 90),
    "up": QColor(255, 190, 80),
    "down": QColor(140, 160, 200),
    "outro": QColor(160, 160, 180),
}


def _phrase_color(phrase: dict) -> QColor:
    text = str(phrase.get("text") or "").lower()
    for key, color in _PHRASE_COLORS.items():
        if text.startswith(key):
            return color
    return QColor(200, 200, 210)


def phrase_time_ms(phrase: dict, meta: dict | None) -> float | None:
    """Map a PSSI phrase beat index → milliseconds via the beat grid."""
    beats = (meta or {}).get("beats") or []
    if not beats:
        return None
    try:
        beat = int(phrase.get("beat") or 0)
    except (TypeError, ValueError):
        return None
    if beat < 1 or beat > len(beats):
        return None
    return float(beats[beat - 1][0])


def _draw_phrase_marker(p: QPainter, x: int, y0: int, h: int, phrase: dict,
                        *, labeled: bool = True) -> None:
    color = _phrase_color(phrase)
    # Dashed-feel: short ticks top + bottom with a thin centre line.
    p.fillRect(x, y0, 1, h, QColor(color.red(), color.green(), color.blue(), 160))
    p.fillRect(x - 1, y0, 3, 3, color)
    p.fillRect(x - 1, y0 + h - 3, 3, 3, color)
    if not labeled:
        return
    label = str(phrase.get("text") or "")
    if not label:
        return
    font = QFont("DejaVu Sans Mono", 7)
    font.setBold(True)
    p.setFont(font)
    p.setPen(color)
    # Sit label near the bottom so it doesn't collide with hot-cue badges.
    p.drawText(x + 3, y0 + h - 4, label)


def _draw_cue_marker(p: QPainter, x: int, y0: int, h: int, cue: dict,
                     *, labeled: bool = True) -> None:
    """Hot-cue / memory-cue marker: vertical line + optional A–H badge."""
    if cue.get("type") == "loop":
        return
    color = _cue_color(cue)
    p.fillRect(x - 1, y0, 2, h, color)
    hot = int(cue.get("hot") or 0)
    if not labeled or hot <= 0:
        # Memory cue: small triangle at the top.
        p.fillRect(x - 3, y0, 6, 4, color)
        return
    # Hot cue A–H badge (same idea as the web panel).
    bw, bh = 12, 11
    p.fillRect(x - bw // 2, y0, bw, bh, color)
    p.setPen(QColor("#000000"))
    font = QFont("DejaVu Sans Mono", 8)
    font.setBold(True)
    p.setFont(font)
    p.drawText(x - bw // 2, y0, bw, bh, Qt.AlignCenter, chr(64 + hot))


def parse_waveform(data: bytes) -> tuple[dict | None, dict | None]:
    """Unpack PLWF detail + overview, plus optional PLWB frequency lanes."""
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

    def bands(offset: int):
        if offset + 24 > len(data):
            return None, offset
        magic, _ver, n, _cps, _dur, _flags = struct.unpack_from("<4sIIfII", data, offset)
        if magic != b"PLWB" or n < 0:
            return None, offset
        start = offset + 24
        end = start + n * 3
        if end > len(data):
            return None, offset
        return {
            "n": n,
            "low": data[start:start + n],
            "mid": data[start + n:start + 2 * n],
            "high": data[start + 2 * n:end],
        }, end

    detail, next_off = one(0)
    overview, next_off = one(next_off) if detail else (None, 0)
    detail_bands, next_off = bands(next_off)
    overview_bands, next_off = bands(next_off)
    detail_blue, next_off = _read_blue(data, next_off)
    overview_blue, _ = _read_blue(data, next_off)
    _attach_bands(detail, detail_bands)
    _attach_bands(overview, overview_bands)
    _attach_blue(detail, detail_blue)
    _attach_blue(overview, overview_blue)
    return detail, overview


def _read_blue(data: bytes, offset: int):
    """Read a PLBC block: blue heights then COLOR_MAP RGB."""
    if offset + 24 > len(data):
        return None, offset
    magic, _ver, n, _cps, _dur, _flags = struct.unpack_from("<4sIIfII", data, offset)
    if magic != b"PLBC" or n < 0:
        return None, offset
    start = offset + 24
    end_h = start + n
    end = end_h + n * 3
    if end > len(data):
        return None, offset
    return {"n": n, "h": data[start:end_h], "rgb": data[end_h:end]}, end


def _attach_bands(wave: dict | None, bands: dict | None) -> None:
    if not wave or not bands or bands["n"] != wave["n"]:
        return
    peak = 1
    for lane in (bands["low"], bands["mid"], bands["high"]):
        if lane:
            peak = max(peak, max(lane) if lane else 0)
    if peak < 2:
        return
    wave["low"] = bands["low"]
    wave["mid"] = bands["mid"]
    wave["high"] = bands["high"]
    wave["band_peak"] = peak


def _attach_blue(wave: dict | None, blue_block: dict | None) -> None:
    if not wave or not blue_block or blue_block["n"] != wave["n"]:
        return
    wave["blue_h"] = blue_block["h"]
    wave["blue_rgb"] = blue_block["rgb"]


def _fill_mirrored(p: QPainter, x: int, mid: float, px: float, color: QColor) -> None:
    if px < 0.4:
        return
    p.fillRect(x, int(mid - px), 1, max(1, int(px * 2)), color)


def _band_heights_at(wave: dict, src: int) -> tuple[int, int, int, int] | None:
    """Return (low, mid, high, peak) for 3BAND.

    Prefer real PWV7 lanes. When missing/near-zero, split the mono height so
    the view still reads as 3-band instead of muddy grey-brown.
    """
    lows = wave.get("low")
    if lows is not None and src < len(lows):
        low = lows[src]
        md = wave["mid"][src]
        hi = wave["high"][src]
        peak = int(wave.get("band_peak") or 1)
        if (low or md or hi) and peak >= 2:
            return low, md, hi, peak
    heights = wave.get("h")
    if heights is not None and src < len(heights):
        amp = int(heights[src])
        if amp:
            return amp, max(1, round(amp * 0.72)), max(1, round(amp * 0.28)), 31
    return None


def _paint_column(p: QPainter, x: int, mid: float, amp: float, style: str,
                  src: int, wave: dict, span: float) -> None:
    """One waveform column, mirrored around the centre line.

    3-Band follows ``WaveformDetailComponent``: the taller of low and mid is
    drawn in that band's colour, the overlap in brown, then highs in white.
    Heights are Beat Link's scaled pixel values, normalised to this view.
    When PWV7 is missing, RGB colour-waveform channels stand in for the bands.
    """
    if style == "3band":
        bands = _band_heights_at(wave, src)
        if bands is not None:
            low, md, hi, peak = bands
            low_px = low / peak * span
            mid_px = md / peak * span
            high_px = hi / peak * span
            if low_px > mid_px:
                _fill_mirrored(p, x, mid, low_px, QColor(*BAND_LOW))
                _fill_mirrored(p, x, mid, mid_px, QColor(*BAND_OVERLAP))
            else:
                if abs(low_px - mid_px) >= 0.4:
                    _fill_mirrored(p, x, mid, mid_px, QColor(*BAND_MID))
                _fill_mirrored(p, x, mid, low_px, QColor(*BAND_OVERLAP))
            _fill_mirrored(p, x, mid, high_px, QColor(*BAND_HIGH))
            return
    if amp < 0.5:
        return
    if style == "blue":
        blue_h = wave.get("blue_h")
        if blue_h and len(blue_h) == wave["n"]:
            amp = (blue_h[src] / 31.0) * span
        blue_rgb = wave.get("blue_rgb")
        if blue_rgb and len(blue_rgb) == wave["n"] * 3:
            color = QColor(blue_rgb[src * 3], blue_rgb[src * 3 + 1], blue_rgb[src * 3 + 2])
        else:
            color = QColor(*BLUE_SHADE)
    else:
        rgb = wave["rgb"]
        color = QColor(rgb[src * 3], rgb[src * 3 + 1], rgb[src * 3 + 2])
    _fill_mirrored(p, x, mid, amp, color)


class Sidebar(QFrame):
    navigated = Signal(str)
    hide_requested = Signal()

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)
        self._i18n = i18n
        self._buttons: dict[str, QPushButton] = {}
        self._active = "monitor"
        self._nav_keys = (
            "monitor", "devices", "health", "library", "session", "overlay", "settings", "about",
        )

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

        for key in self._nav_keys:
            btn = QPushButton(i18n.t(f"nav_{key}"))
            btn.setObjectName("NavButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda _=False, k=key: self._click(k))
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch(1)
        self.hide_btn = QPushButton(i18n.t("hide_sidebar"))
        self.hide_btn.setObjectName("Chip")
        self.hide_btn.setCursor(Qt.PointingHandCursor)
        self.hide_btn.setToolTip("Ctrl+B")
        self.hide_btn.clicked.connect(self.hide_requested.emit)
        layout.addWidget(self.hide_btn)
        self._set_active("monitor")

    def retranslate(self) -> None:
        for key, btn in self._buttons.items():
            btn.setText(self._i18n.t(f"nav_{key}"))
        self.hide_btn.setText(self._i18n.t("hide_sidebar"))

    def nav_keys(self) -> tuple[str, ...]:
        return self._nav_keys

    def set_active(self, key: str) -> None:
        self._set_active(key)

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
    """Overview strip + scrolling detail waveform with fixed playhead.

    Overview and detail are painted into QPixmap caches. On playhead motion the
    detail cache is scrolled and only the newly exposed edge is peak-picked;
    the overview cache is reused and only the playhead / unplayed shade redraw.
    """

    hover_ms_changed = Signal(float)   # ms under cursor (-1 when left)
    cue_ms_clicked = Signal(float)     # ms at click (display cue / readout)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._color = QColor(COLORS["accent"])
        self._detail = None
        self._overview = None
        self._meta: dict[str, Any] | None = None
        self._pos_ms = 0.0
        self._playing = False
        self._zoom_bars = 4
        self._bpm = 120.0
        self._offair = False
        self._style = "rgb"
        self._playhead_frac = 0.5
        self._loop_start_ms: float | None = None
        self._loop_end_ms: float | None = None
        self._loop_active = False
        self._overview_pm: QPixmap | None = None
        self._overview_key: tuple | None = None
        self._detail_pm: QPixmap | None = None
        self._detail_scratch: QPixmap | None = None
        self._detail_key: tuple | None = None
        self._detail_start: float = 0.0
        self._last_paint_pos: float = -1.0
        self._last_paint_playing: bool | None = None
        self._hover_x: int | None = None
        self._hover_ms: float | None = None
        self._overview_h = 36

    def _visible_seconds(self) -> float:
        return bars_to_seconds(self._zoom_bars, self._bpm)

    def _duration_ms(self) -> float:
        ov = self._overview
        return float((self._meta or {}).get("duration_ms") or (ov or {}).get("dur") or 0)

    def _ms_at(self, x: int, y: int) -> float | None:
        """Map widget coordinates to track time (ms)."""
        w = max(1, self.width())
        overview_h = self._overview_h
        dur = self._duration_ms()
        if y <= overview_h:
            if not dur:
                return None
            return max(0.0, min(dur, (x / w) * dur))
        d = self._detail
        if not d or not d["n"]:
            if dur:
                return max(0.0, min(dur, (x / w) * dur))
            return None
        visible_s = self._visible_seconds()
        anchor = w * self._playhead_frac
        ms = self._pos_ms + ((x - anchor) / w) * visible_s * 1000.0
        if dur:
            ms = max(0.0, min(dur, ms))
        return max(0.0, ms)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        x, y = int(event.position().x()), int(event.position().y())
        ms = self._ms_at(x, y)
        self._hover_x = x
        self._hover_ms = ms
        self.hover_ms_changed.emit(ms if ms is not None else -1.0)
        self.setToolTip(mmss(ms) if ms is not None else "")
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = None
        self._hover_ms = None
        self.hover_ms_changed.emit(-1.0)
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            ms = self._ms_at(int(event.position().x()), int(event.position().y()))
            if ms is not None:
                self.cue_ms_clicked.emit(ms)
        super().mousePressEvent(event)

    def set_deck_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_zoom(self, bars: int) -> None:
        bars = int(bars) if int(bars) in ZOOM_BARS else 4
        if bars == self._zoom_bars:
            return
        self._zoom_bars = bars
        self._invalidate_detail()
        self.update()

    def set_bpm(self, bpm: float) -> None:
        bpm = float(bpm or 0) or 120.0
        if abs(bpm - self._bpm) < 0.05:
            return
        self._bpm = bpm
        self._invalidate_detail()
        self.update()

    def set_style(self, style: str) -> None:
        style = style if style in WAVE_STYLES else "rgb"
        if style == self._style:
            return
        self._style = style
        self._invalidate_all()
        self.update()

    def set_playhead_position(self, position: str) -> None:
        """Needle anchor: 'centre' (middle) or 'left' (left quarter)."""
        frac = playhead_fraction(position)
        if frac == self._playhead_frac:
            return
        self._playhead_frac = frac
        self._invalidate_detail()
        self.update()

    def set_offair(self, off: bool) -> None:
        off = bool(off)
        if off == self._offair:
            return
        self._offair = off
        self._invalidate_all()
        self.update()

    def set_track(self, detail, overview, meta: dict | None) -> None:
        self._detail = detail
        self._overview = overview
        self._meta = meta
        self._invalidate_all()
        self.update()

    def set_position(self, pos_ms: float, playing: bool) -> None:
        # Skip a full QWidget repaint when the playhead has not moved a visible
        # amount and play/pause did not change (saves overview overlay work).
        moved = abs(pos_ms - self._last_paint_pos) >= 4.0  # ~¼ px at typical zoom
        play_changed = playing != self._last_paint_playing
        self._pos_ms = pos_ms
        self._playing = playing
        if not moved and not play_changed and self._detail_pm is not None:
            return
        self._last_paint_pos = pos_ms
        self._last_paint_playing = playing
        self.update()

    def set_loop_region(self, start_ms: float | None, end_ms: float | None,
                        active: bool = False) -> None:
        start = float(start_ms) if start_ms is not None else None
        end = float(end_ms) if end_ms is not None else None
        active = bool(active and start is not None and end is not None and end > start)
        if (start == self._loop_start_ms and end == self._loop_end_ms
                and active == self._loop_active):
            return
        self._loop_start_ms = start
        self._loop_end_ms = end
        self._loop_active = active
        self.update()

    def _invalidate_all(self) -> None:
        self._overview_pm = None
        self._overview_key = None
        self._invalidate_detail()

    def _invalidate_detail(self) -> None:
        self._detail_pm = None
        self._detail_scratch = None
        self._detail_key = None
        self._last_paint_pos = -1.0
        self._last_paint_playing = None

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        overview_h = max(28, min(48, int(h * 0.14)))
        self._overview_h = overview_h
        detail_h = max(1, h - overview_h - 1)

        p.fillRect(0, 0, w, overview_h, QColor("#0b0d10"))
        p.fillRect(0, overview_h + 1, w, detail_h, QColor("#080a0c"))
        p.setPen(QColor(COLORS["line"]))
        p.drawLine(0, overview_h, w, overview_h)

        self._blit_overview(p, 0, 0, w, overview_h)
        self._blit_detail(p, 0, overview_h + 1, w, detail_h)

        # Hover scrub line + time badge.
        if self._hover_x is not None and self._hover_ms is not None:
            hx = max(0, min(w - 1, self._hover_x))
            p.fillRect(hx, 0, 1, h, QColor(255, 255, 255, 140))
            badge = mmss(self._hover_ms)
            font = QFont("DejaVu Sans Mono", 9)
            font.setBold(True)
            p.setFont(font)
            tw = p.fontMetrics().horizontalAdvance(badge) + 8
            th = 14
            bx = max(0, min(w - tw, hx - tw // 2))
            by = 2 if self._hover_x is not None and self._hover_ms is not None else 2
            # Prefer detail region for the badge when hovering there.
            if self._hover_x is not None:
                # y from last leave/move isn't stored; put badge on overview edge.
                by = overview_h + 4 if overview_h + 4 + th < h else 2
            p.fillRect(bx, by, tw, th, QColor(8, 10, 12, 210))
            p.setPen(QColor("#f4f6fb"))
            p.drawText(bx, by, tw, th, Qt.AlignCenter, badge)
        p.end()

    def _blit_overview(self, p: QPainter, x0, y0, w, h) -> None:
        self._ensure_overview(w, h)
        if self._overview_pm is not None:
            alpha = 0.34 if self._offair else 1.0
            p.setOpacity(alpha)
            p.drawPixmap(x0, y0, self._overview_pm)
            p.setOpacity(1.0)

        ov = self._overview
        if not ov or not ov["n"]:
            return
        dur = (self._meta or {}).get("duration_ms") or ov.get("dur") or 1
        if self._loop_active and self._loop_start_ms is not None and self._loop_end_ms is not None:
            lx0 = int((self._loop_start_ms / max(1, dur)) * w)
            lx1 = int((self._loop_end_ms / max(1, dur)) * w)
            p.fillRect(x0 + lx0, y0, max(1, lx1 - lx0), h, QColor(46, 232, 154, 72))
            p.fillRect(x0 + lx0, y0, 2, h, QColor(46, 232, 154, 220))
            p.fillRect(x0 + max(lx0, lx1 - 2), y0, 2, h, QColor(46, 232, 154, 220))
        # Cue markers drawn live so they appear as soon as meta arrives.
        self._draw_overview_cues(p, x0, y0, w, h)
        self._draw_overview_phrases(p, x0, y0, w, h)
        px = max(0, min(w, int((self._pos_ms / max(1, dur)) * w)))
        if px < w:
            p.fillRect(x0 + px, y0, w - px, h, QColor(8, 10, 12, 150))
        p.fillRect(x0 + px - 1, y0, 2, h, self._color)

    def _ensure_overview(self, w, h) -> None:
        ov = self._overview
        key = (id(ov) if ov else 0, self._style, w, h, self._offair)
        if self._overview_pm is not None and self._overview_key == key:
            return
        pm = QPixmap(w, h)
        pm.fill(QColor("#0b0d10"))
        if ov and ov["n"]:
            qp = QPainter(pm)
            self._draw_overview_wave(qp, 0, 0, w, h)
            qp.end()
        self._overview_pm = pm
        self._overview_key = key

    def _draw_overview_cues(self, p: QPainter, x0, y0, w, h) -> None:
        ov = self._overview
        if not ov or not ov["n"]:
            return
        dur = (self._meta or {}).get("duration_ms") or ov.get("dur") or 1
        for cue in (self._meta or {}).get("cues") or []:
            cx = x0 + int((cue.get("t", 0) / max(1, dur)) * w)
            _draw_cue_marker(p, cx, y0, h, cue, labeled=True)

    def _draw_overview_phrases(self, p: QPainter, x0, y0, w, h) -> None:
        ov = self._overview
        if not ov or not ov["n"]:
            return
        dur = (self._meta or {}).get("duration_ms") or ov.get("dur") or 1
        for phrase in (self._meta or {}).get("phrases") or []:
            t = phrase_time_ms(phrase, self._meta)
            if t is None:
                continue
            cx = x0 + int((t / max(1, dur)) * w)
            _draw_phrase_marker(p, cx, y0, h, phrase, labeled=False)

    def _draw_overview_wave(self, p: QPainter, x0, y0, w, h) -> None:
        ov = self._overview
        if not ov or not ov["n"]:
            return
        n = ov["n"]
        heights = ov["h"]
        mid = y0 + h / 2
        span = (h / 2) * 0.94
        for x in range(w):
            i = min(n - 1, (x * n) // w)
            tall = (heights[i] / 31.0) * span
            _paint_column(p, x0 + x, mid, tall, self._style, i, ov, span)

    def _blit_detail(self, p: QPainter, x0, y0, w, h) -> None:
        self._ensure_detail(w, h)
        if self._detail_pm is not None:
            alpha = 0.34 if self._offair else 1.0
            p.setOpacity(alpha)
            p.drawPixmap(x0, y0, self._detail_pm)
            p.setOpacity(1.0)

        d = self._detail
        if d and d["n"] and self._loop_active and self._loop_start_ms is not None:
            cps = d["cps"] or 150.0
            visible = max(1.0, self._visible_seconds() * cps)
            px_per_col = w / visible
            current = (self._pos_ms / 1000.0) * cps
            start = self._window_start(current, visible)
            lx0 = int(((self._loop_start_ms / 1000.0) * cps - start) * px_per_col)
            lx1 = int(((self._loop_end_ms / 1000.0) * cps - start) * px_per_col)
            left = max(0, min(w, lx0))
            right = max(0, min(w, lx1))
            if right > left:
                p.fillRect(x0 + left, y0, right - left, h, QColor(46, 232, 154, 70))
                p.fillRect(x0 + left, y0, 2, h, QColor(46, 232, 154, 220))
                p.fillRect(x0 + max(left, right - 2), y0, 2, h, QColor(46, 232, 154, 220))

        # Cue markers on the scrolling detail strip (live, not only in the cache).
        if d and d["n"]:
            cps = d["cps"] or 150.0
            visible = max(1.0, self._visible_seconds() * cps)
            px_per_col = w / visible
            current = (self._pos_ms / 1000.0) * cps
            start = self._window_start(current, visible)
            for cue in (self._meta or {}).get("cues") or []:
                x = int(((cue.get("t", 0) / 1000.0) * cps - start) * px_per_col)
                if x < -20 or x > w + 20:
                    continue
                _draw_cue_marker(p, x0 + x, y0, h, cue, labeled=True)
            for phrase in (self._meta or {}).get("phrases") or []:
                t = phrase_time_ms(phrase, self._meta)
                if t is None:
                    continue
                x = int(((t / 1000.0) * cps - start) * px_per_col)
                if x < -40 or x > w + 40:
                    continue
                _draw_phrase_marker(p, x0 + x, y0, h, phrase, labeled=True)

        # Needle stays put; the wave scrolls under it. Centre is the middle,
        # Left is the left quarter (PLAYHEAD_LEFT_FRACTION).
        cx = x0 + int(round(w * self._playhead_frac))
        cx = max(x0, min(x0 + max(0, w - 2), cx))
        glow = QColor(self._color)
        glow.setAlpha(45 if self._playing else 20)
        p.fillRect(cx - 8, y0, 16, h, glow)
        p.fillRect(cx - 1, y0, 2, h, self._color)

    def _ensure_detail(self, w, h) -> None:
        d = self._detail
        if not d or not d["n"]:
            self._detail_pm = None
            self._detail_key = None
            return

        cps = d["cps"] or 150.0
        visible = max(1.0, self._visible_seconds() * cps)
        px_per_col = w / visible
        current = (self._pos_ms / 1000.0) * cps
        start = self._window_start(current, visible)
        key = (id(d), self._style, self._zoom_bars, round(self._bpm, 1),
               w, h, self._offair, self._playhead_frac)

        if (self._detail_pm is None or self._detail_key != key
                or self._detail_pm.width() != w or self._detail_pm.height() != h):
            self._rebuild_detail(w, h, start, key)
            return

        shift_f = (self._detail_start - start) * px_per_col
        shift = int(shift_f)  # toward zero — wait for a full pixel
        if shift == 0:
            return
        if abs(shift) >= w:
            self._rebuild_detail(w, h, start, key)
            return

        # Scroll within a recycled scratch pixmap — avoid allocating every frame.
        scratch = self._detail_scratch
        if scratch is None or scratch.width() != w or scratch.height() != h:
            scratch = QPixmap(w, h)
            self._detail_scratch = scratch
        scratch.fill(QColor("#080a0c"))
        qp = QPainter(scratch)
        if shift > 0:
            qp.drawPixmap(shift, 0, self._detail_pm, 0, 0, w - shift, h)
            self._draw_detail_range(qp, 0, 0, w, h, start, 0, shift)
        else:
            sh = -shift
            qp.drawPixmap(0, 0, self._detail_pm, sh, 0, w - sh, h)
            self._draw_detail_range(qp, 0, 0, w, h, start, w - sh, w)
        qp.end()
        # Swap buffers so the previous front becomes the next scratch.
        self._detail_scratch = self._detail_pm
        self._detail_pm = scratch
        self._detail_start -= shift / px_per_col

    def _rebuild_detail(self, w, h, start: float, key: tuple) -> None:
        pm = QPixmap(w, h)
        pm.fill(QColor("#080a0c"))
        qp = QPainter(pm)
        self._draw_detail_range(qp, 0, 0, w, h, start, 0, w)
        qp.end()
        self._detail_pm = pm
        self._detail_key = key
        self._detail_start = start

    def _window_start(self, current: float, visible: float) -> float:
        """First visible detail column so the needle stays on the playhead."""
        return current - visible * self._playhead_frac

    def _draw_detail_range(self, p: QPainter, x0, y0, w, h, start: float,
                           x_lo: int, x_hi: int) -> None:
        """Peak-pick detail columns for CSS pixels [x_lo, x_hi) at window start."""
        d = self._detail
        if not d or not d["n"] or x_hi <= x_lo:
            return
        n = d["n"]
        cps = d["cps"] or 150.0
        heights = d["h"]
        mid = y0 + h / 2
        visible = max(1.0, self._visible_seconds() * cps)
        px_per_col = w / visible
        span = (h / 2) * 0.98

        beats = (self._meta or {}).get("beats") or []
        if beats:
            t0 = ((start + x_lo / px_per_col) / cps) * 1000
            t1 = ((start + x_hi / px_per_col) / cps) * 1000
            for beat in beats:
                t, number = beat[0], beat[1]
                if t < t0 - 500:
                    continue
                if t > t1 + 500:
                    break
                x = int(((t / 1000.0) * cps - start) * px_per_col)
                if x < x_lo - 1 or x > x_hi:
                    continue
                pen = QPen(QColor(255, 255, 255, 50 if number == 1 else 18))
                p.setPen(pen)
                p.drawLine(x0 + x, y0, x0 + x, y0 + h)

        x_lo = max(0, x_lo)
        x_hi = min(w, x_hi)
        for px in range(x_lo, x_hi):
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
            amp = (tall / 31.0) * span
            _paint_column(p, x0 + px, mid, amp, self._style, src, d, span)

        # Cues are drawn live in _blit_detail so they stay sharp while scrolling.


class DeckCard(QFrame):
    zoom_override_changed = Signal(int, int)  # deck number, bars
    focused = Signal(int)                     # deck number

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
        self._wave_track_id = 0
        self._last_offair: bool | None = None
        self._last_tags = ""
        self._elements = deck_elements_from()
        self._zoom_bars = 4
        self._zoom_override = False
        self._playhead_mode = "auto"
        self._last_deck: dict | None = None
        self._cue_ms: float | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(f"background:{self._color}; border:none; border-radius:0;")
        root.addWidget(accent)

        body = QHBoxLayout()
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(12)
        root.addLayout(body, 1)
        self._body = body

        # identity
        ident = QVBoxLayout()
        ident.setSpacing(6)
        top = QHBoxLayout()
        self.art = QLabel("♪")
        self.art.setFixedSize(72, 72)
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
        row.addWidget(self.num)
        row.addStretch(1)
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
        # Tags on their own full-width row so MASTER / SYNC / ON AIR never clip.
        self.tags = QLabel("")
        self.tags.setObjectName("Mono")
        self.tags.setWordWrap(True)
        self.tags.setStyleSheet("font-size:10px;")
        ident.addWidget(self.tags)
        self.wrap_ident = QWidget()
        self.wrap_ident.setFixedWidth(IDENT_WIDTH)
        self.wrap_ident.setLayout(ident)
        body.addWidget(self.wrap_ident, 0)

        # waveform — takes all leftover horizontal (and vertical) space
        wave_col = QVBoxLayout()
        wave_col.setSpacing(2)
        wave_tools = QHBoxLayout()
        wave_tools.setSpacing(4)
        self.cue_readout = QLabel("")
        self.cue_readout.setObjectName("Mono")
        self.cue_readout.setStyleSheet(
            f"font-family:monospace; font-size:11px; color:{COLORS['dim']};"
        )
        wave_tools.addWidget(self.cue_readout)
        wave_tools.addStretch(1)
        self.zoom_btn = QPushButton(f"{self._zoom_bars}b")
        self.zoom_btn.setObjectName("Chip")
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_btn.setToolTip("Per-deck zoom (cycles 1/2/4/8/16 bars). Right-click clears override.")
        self.zoom_btn.clicked.connect(self._cycle_zoom)
        self.zoom_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zoom_btn.customContextMenuRequested.connect(lambda *_: self.clear_zoom_override())
        wave_tools.addWidget(self.zoom_btn)
        wave_col.addLayout(wave_tools)
        self.wave = WaveformView()
        self.wave.set_deck_color(self._color)
        self.wave.hover_ms_changed.connect(self._on_hover_ms)
        self.wave.cue_ms_clicked.connect(self._on_cue_ms)
        wave_col.addWidget(self.wave, 1)
        body.addLayout(wave_col, 1)

        # readouts
        read = QVBoxLayout()
        read.setSpacing(2)
        self.bpm = QLabel("— BPM")
        self.bpm.setStyleSheet(
            "font-family:monospace; font-size:28px; font-weight:700;"
        )
        self.pitch = QLabel("")
        self.pitch.setStyleSheet(f"font-family:monospace; font-size:12px; color:{COLORS['dim']};")
        self.times_wrap = QWidget()
        times = QHBoxLayout(self.times_wrap)
        times.setContentsMargins(0, 0, 0, 0)
        times.setSpacing(0)
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
        self.loop_badge = QLabel("")
        self.loop_badge.setObjectName("LoopBadge")
        self.loop_badge.setStyleSheet(
            "font-family:monospace; font-size:10px; font-weight:700; "
            "letter-spacing:1px; color:#2ee89a; "
            "border:1px solid rgba(46,232,154,140); border-radius:9px; "
            "padding:2px 7px 2px 5px; background:rgba(46,232,154,26);"
        )
        self.loop_badge.hide()
        self.key = QLabel("")
        self.key.setStyleSheet(
            f"font-family:monospace; font-size:12px; font-weight:700; color:{self._color};"
        )
        self.phase = QWidget()
        self.phase.setFixedHeight(10)
        phase_row = QHBoxLayout(self.phase)
        phase_row.setContentsMargins(0, 0, 0, 0)
        phase_row.setSpacing(3)
        self._phase_dots: list[QFrame] = []
        for _ in range(4):
            dot = QFrame()
            dot.setFixedSize(12, 4)
            dot.setStyleSheet(
                f"background:{COLORS['line']}; border:none; border-radius:1px;"
            )
            phase_row.addWidget(dot)
            self._phase_dots.append(dot)
        phase_row.addStretch(1)
        self._phase_beat = 0
        self.state_wrap = QWidget()
        state_row = QHBoxLayout(self.state_wrap)
        state_row.setContentsMargins(0, 0, 0, 0)
        state_row.setSpacing(6)
        state_row.addWidget(self.loop_badge, 0)
        state_row.addWidget(self.state, 1)
        state_row.addWidget(self.key)
        read.addWidget(self.bpm)
        read.addWidget(self.pitch)
        read.addSpacing(4)
        read.addWidget(self.times_wrap)
        read.addWidget(self.phase)
        read.addWidget(self.state_wrap)
        read.addStretch(1)
        self.wrap_read = QWidget()
        self.wrap_read.setFixedWidth(150)
        self.wrap_read.setLayout(read)
        body.addWidget(self.wrap_read, 0)

        self.setMinimumHeight(200)
        self.apply_elements(self._elements)

    def apply_elements(self, elements: dict[str, bool] | None) -> None:
        """Show or hide deck pieces; collapse empty columns so the waveform grows."""
        self._elements = deck_elements_from(elements)
        e = self._elements

        self.art.setVisible(e["deck_show_artwork"])
        self.title.setVisible(e["deck_show_title"])
        self.artist.setVisible(e["deck_show_artist"])
        self.meta_line.setVisible(e["deck_show_meta"])
        self.tags.setVisible(e["deck_show_tags"])
        self.wave.setVisible(e["deck_show_waveform"])
        self.zoom_btn.setVisible(e["deck_show_waveform"])
        self.cue_readout.setVisible(e["deck_show_waveform"])
        self.bpm.setVisible(e["deck_show_bpm"])
        self.pitch.setVisible(e["deck_show_tempo"])
        self.times_wrap.setVisible(e["deck_show_time"])
        self.key.setVisible(e["deck_show_key"])
        self.state.setVisible(e["deck_show_state"])
        self.phase.setVisible(e["deck_show_state"] or e["deck_show_bpm"])
        self.state_wrap.setVisible(e["deck_show_key"] or e["deck_show_state"])

        # Left column stays if any identity piece (or always the deck number) shows.
        left_bits = (
            e["deck_show_artwork"] or e["deck_show_title"] or e["deck_show_artist"]
            or e["deck_show_meta"] or e["deck_show_tags"]
        )
        self.wrap_ident.setVisible(True)  # deck number always present
        self.wrap_ident.setFixedWidth(IDENT_WIDTH if left_bits else 44)

        right_bits = (
            e["deck_show_bpm"] or e["deck_show_tempo"] or e["deck_show_time"]
            or e["deck_show_key"] or e["deck_show_state"]
        )
        self.wrap_read.setVisible(right_bits)

        # Base mins — MonitorPage raises these further so cards fill the panel.
        if e["deck_show_waveform"] and not left_bits and not right_bits:
            self.setMinimumHeight(160)
        elif e["deck_show_waveform"]:
            self.setMinimumHeight(140)
        else:
            self.setMinimumHeight(80)
        self.setMaximumHeight(16777215)

    def set_fill_height(self, height: int) -> None:
        """Stretch this card so its waveform uses the allotted panel slice."""
        floor = 80
        if self._elements.get("deck_show_waveform", True):
            floor = 140
        self.setMinimumHeight(max(floor, int(height)))
        # Cap max so equal stretch stays even when the host is viewport-sized.
        self.setMaximumHeight(max(floor, int(height)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.focused.emit(self.number)
        super().mousePressEvent(event)

    def set_zoom(self, bars: int, *, override: bool | None = None) -> None:
        bars = int(bars) if int(bars) in ZOOM_BARS else 4
        if override is not None:
            self._zoom_override = bool(override)
        self._zoom_bars = bars
        self.wave.set_zoom(bars)
        label = f"{bars}b"
        if self._zoom_override:
            label = f"{bars}b*"
        self.zoom_btn.setText(label)
        self.zoom_btn.setProperty("active", "true" if self._zoom_override else "false")
        self.zoom_btn.style().unpolish(self.zoom_btn)
        self.zoom_btn.style().polish(self.zoom_btn)

    def clear_zoom_override(self) -> None:
        if not self._zoom_override:
            return
        self._zoom_override = False
        self.zoom_override_changed.emit(self.number, -1)  # -1 = clear

    def _cycle_zoom(self) -> None:
        idx = ZOOM_BARS.index(self._zoom_bars) if self._zoom_bars in ZOOM_BARS else 2
        bars = ZOOM_BARS[(idx + 1) % len(ZOOM_BARS)]
        self.set_zoom(bars, override=True)
        self.zoom_override_changed.emit(self.number, bars)

    def _on_hover_ms(self, ms: float) -> None:
        if ms < 0:
            if self._cue_ms is not None:
                self.cue_readout.setText(f"cue {mmss(self._cue_ms)}")
            else:
                self.cue_readout.setText("")
            return
        self.cue_readout.setText(mmss(ms))

    def _on_cue_ms(self, ms: float) -> None:
        self._cue_ms = ms
        self.cue_readout.setText(f"cue {mmss(ms)}")
        self.cue_readout.setStyleSheet(
            f"font-family:monospace; font-size:11px; color:{self._color};"
        )

    def set_wave_style(self, style: str) -> None:
        self.wave.set_style(style)

    def set_playhead_mode(self, mode: str) -> None:
        """User override (auto / centre / left). Applies immediately."""
        self._playhead_mode = mode or "auto"
        self._apply_playhead()

    def _apply_playhead(self) -> None:
        deck = self._last_deck or {}
        position = resolve_playhead_position(
            self._playhead_mode,
            deck.get("waveform_position"),
            str(deck.get("name") or ""),
        )
        self.wave.set_playhead_position(position)

    def set_track_data(self, meta: dict | None, detail, overview, art: bytes | None) -> None:
        self._meta = meta
        self._detail = detail
        self._overview = overview
        if detail is not None or overview is not None:
            self._wave_track_id = (meta or {}).get("id") or self._track_id
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
                    72, 72, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.art.setPixmap(pix)
                self.art.setText("")
                self._art_id = (meta or {}).get("id", 0)

    def needs_waveform(self, track_id: int) -> bool:
        return bool(track_id) and (
            self._wave_track_id != track_id or self._detail is None
        )

    def advance_playhead(self, pos_ms: float, playing: bool, duration_ms: float = 0,
                         *, bar: int = 0, looping: bool = False) -> None:
        """Lightweight per-frame update: times + waveform position + phase."""
        meta_dur = float((self._meta or {}).get("duration_ms") or 0)
        dur = sane_duration_ms(duration_ms, meta_dur)
        if dur:
            pos_ms = min(pos_ms, dur)
        self.elapsed.setText(mmss(pos_ms))
        remain = max(0.0, dur - pos_ms) if dur else 0.0
        self.remaining.setText(("-" + mmss(remain)) if dur else "—")
        low = bool(dur and remain < 30000)
        self.remaining.setStyleSheet(
            "font-family:monospace; font-size:15px; font-weight:700; color:"
            + (COLORS["danger"] if low else COLORS["dim"]) + ";"
        )
        self._set_phase(bar)
        self._apply_loop_region(pos_ms, looping)
        self.wave.set_position(pos_ms, playing)

    def _set_phase(self, bar: int) -> None:
        bar = int(bar or 0)
        if bar == self._phase_beat:
            return
        self._phase_beat = bar
        for i, dot in enumerate(self._phase_dots):
            on = bar == i + 1
            if on:
                dot.setStyleSheet(
                    f"background:{self._color}; border:none; border-radius:1px;"
                )
            else:
                dot.setStyleSheet(
                    f"background:{COLORS['line']}; border:none; border-radius:1px;"
                )

    def _apply_loop_region(self, pos_ms: float, looping: bool) -> None:
        start = end = None
        cues = (self._meta or {}).get("cues") or []
        loops = [c for c in cues
                 if c.get("type") == "loop" and (c.get("end") or 0) > (c.get("t") or 0)]
        pick = None
        if loops:
            pick = next((c for c in loops if c["t"] <= pos_ms <= c["end"]), None)
            if pick is None and looping:
                pick = loops[0]
            if pick is not None:
                start, end = float(pick["t"]), float(pick["end"])
        self.wave.set_loop_region(start, end, active=bool(looping and start is not None))
        # Loop length badge (icon + beats) — same idea as the web panel.
        if looping and pick is not None:
            bpm = float(getattr(self, "_last_bpm", 0) or 0)
            if bpm <= 0:
                bpm = float((self._meta or {}).get("track_bpm") or 0)
            beats = 0
            if bpm > 0 and end is not None and start is not None:
                beats = max(0, round((end - start) / (60000.0 / bpm)))
            self.loop_badge.setText(f"↻ {beats}" if beats else "↻ LOOP")
            self.loop_badge.show()
        elif looping:
            self.loop_badge.setText("↻ LOOP")
            self.loop_badge.show()
        else:
            self.loop_badge.hide()
            self.loop_badge.setText("")

    def update_deck(self, deck: dict, pos_ms: float) -> None:
        tid = deck.get("track_id") or 0
        if tid != self._track_id:
            self._track_id = tid
            self._meta = None
            self._detail = None
            self._overview = None
            self._wave_track_id = 0
            self._art_id = 0
            self._last_offair = None
            self._last_tags = ""
            if not tid:
                self.title.setText("—")
                self.artist.setText("")
                self.meta_line.setText(self._i18n.t("no_track_loaded"))
                self.key.setText("")
                self.art.setPixmap(QPixmap())
                self.art.setText("♪")
                self.wave.set_track(None, None, None)

        empty = not tid
        offair = bool(tid and not deck.get("on_air"))
        if offair != self._last_offair:
            self._last_offair = offair
            self.setProperty("offair", "true" if offair else "false")
            self.style().unpolish(self)
            self.style().polish(self)
            self.wave.set_offair(offair)

        tags = []
        if deck.get("master"):
            tags.append(self._chip(self._i18n.t("master"), "#ffd45e"))
        if deck.get("sync"):
            tags.append(self._chip(self._i18n.t("sync"), COLORS["dim"]))
        if deck.get("on_air"):
            tags.append(self._chip(self._i18n.t("on_air"), COLORS["danger"]))
        elif tid:
            tags.append(self._chip(self._i18n.t("channel_closed"), COLORS["dim"]))
        # Loop length is shown as the icon badge next to state (not a tag chip).
        tags_html = " ".join(tags)
        if tags_html != self._last_tags:
            self._last_tags = tags_html
            self.tags.setText(tags_html)

        bpm = deck.get("bpm") or 0
        self._last_bpm = float(bpm or 0)
        self.wave.set_bpm(float(deck.get("track_bpm") or bpm or 120.0))
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

        self.state.setText(self._i18n.state(deck.get("state") or "unknown"))
        self._last_deck = deck
        self._apply_playhead()
        if empty and not self._meta:
            self.title.setText("—")
        elif tid and not self._meta:
            self.title.setText(self._i18n.t("loading"))

        self.advance_playhead(
            pos_ms,
            bool(deck.get("playing")),
            float(deck.get("duration_ms") or 0),
            bar=int(deck.get("bar") or 0),
            looping=(deck.get("state") == "looping"),
        )

    @staticmethod
    def _chip(text: str, color: str) -> str:
        return (
            f'<span style="font-family:monospace; font-size:9px; font-weight:700; '
            f'letter-spacing:1px; color:{color}; border:1px solid {color}; '
            f'padding:1px 5px; margin-right:4px;">{text}</span>'
        )
