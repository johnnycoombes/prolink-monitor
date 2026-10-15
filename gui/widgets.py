"""Reusable widgets for the desktop shell."""

from __future__ import annotations

import struct
from typing import Any

from PySide6.QtCore import Qt, Signal, QSize, QPoint
from PySide6.QtGui import (
    QColor, QFontInfo, QFontMetrics, QImage, QPainter, QPen, QPixmap, QFont, QMouseEvent,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from prolink.countdown import current_phrase, format_countdown, next_mark, urgency_color
from prolink.fields import beat_phrase_text, color_label, display_text, display_year, rating_stars

from gui import icons
from prolink.vocal import VOCAL_STRIP_H
from gui.deck_fit import (
    ARTIST_MIN, BPM_MIN, DETAIL_MIN, META_MIN, NUM_MIN, PITCH_MIN, READOUT_DROP,
    TIME_MIN, TITLE_MIN, art_side, choose_optional, effective_dpr, elide_lines,
    row_scale, snap_line, title_line_count,
)
from gui.deck_layout import (
    ACCENT_PX, ARTIST_PX, ART_PX, ART_RADIUS, BODY_GAP, BODY_MARGINS, BPM_PX,
    DETAIL_PX, IDENT_COLLAPSED, IDENT_WIDTH, PHRASE_H, PHASE_H, PHASE_W, PITCH_PX,
    READOUT_PANEL_MAX, READOUT_PANEL_MIN, TIME_PX, TITLE_PX, NUM_PX, KEY_PX, overview_height,
)
from gui.theme import COLORS, DECK_COLORS
from gui.settings import deck_elements_from
from prolink.playhead_smooth import sync_detail_origin
from prolink.proto import playhead_fraction, resolve_playhead_position
from prolink.session import ZOOM_BARS, bars_to_seconds
from prolink.wavezoom import bars_for_seconds

def _draw_vocal_strip(p: QPainter, x: int, y: int, w: int, h: int,
                      spans: list, dur: float, pos_ms: float) -> None:
    """Thin lane of vocal regions. Same idea as the phrase strip, shorter."""
    if dur <= 0:
        return
    for span in spans:
        try:
            t0 = float(span.get("t") or 0)
            t1 = float(span.get("end") or t0)
        except (TypeError, ValueError, AttributeError):
            continue
        x0 = int((t0 / dur) * w)
        x1 = int((t1 / dur) * w)
        p.fillRect(x + x0, y + 1, max(1, x1 - x0), max(1, h - 2), QColor("#f472b6"))
    px = max(0, min(w, int((pos_ms / max(1, dur)) * w)))
    if px < w:
        p.fillRect(x + px, y, w - px, h, QColor(8, 10, 12, 120))


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


# 3-band palettes come from web/wavepalette.json (one definition for Qt and
# web). "beatlink" uses the reference overlap method; "cdj3000" fills each
# band combination (mask) in its own colour, as OneLibraryCompanion does.
from prolink import wavestyle as _ws
from prolink.wavezoom import clamp_seconds, wheel_zoom, x_for_ms

WAVE_STYLES = _ws.WAVE_STYLES
_PALETTE_CACHE: dict[str, dict] = {}


def wave_palette(name: str | None) -> dict:
    """QColor palette for ``name``: keys low, mid, high, low_mid, ..., plus mode."""
    key = _ws.normalize_palette(name)
    pal = _PALETTE_CACHE.get(key)
    if pal is None:
        raw = _ws.palette(key)
        pal = {"mode": raw["mode"]}
        for k in _ws.MASK_KEYS:
            pal[k] = QColor(*_ws.hex_rgb(raw[k]))
        _PALETTE_CACHE[key] = pal
    return pal


_BL = _ws.palette("beatlink")
BAND_LOW = _ws.hex_rgb(_BL["low"])          # low frequencies
BAND_MID = _ws.hex_rgb(_BL["mid"])          # mids, when they stick out past the lows
BAND_OVERLAP = _ws.hex_rgb(_BL["low_mid"])  # where low and mid overlap
BAND_HIGH = _ws.hex_rgb(_BL["high"])        # highs, drawn last
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


def _phrase_segments(meta: dict | None, duration_ms: float) -> list[tuple[float, float, dict]]:
    """Return [(start_ms, end_ms, phrase), ...] covering the track timeline."""
    phrases = list((meta or {}).get("phrases") or [])
    if not phrases or duration_ms <= 0:
        return []
    starts: list[tuple[float, dict]] = []
    for phrase in phrases:
        t = phrase_time_ms(phrase, meta)
        if t is None:
            continue
        starts.append((max(0.0, float(t)), phrase))
    if not starts:
        return []
    starts.sort(key=lambda row: row[0])
    out: list[tuple[float, float, dict]] = []
    for i, (start, phrase) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else duration_ms
        if end <= start:
            continue
        out.append((start, min(duration_ms, end), phrase))
    return out


def _draw_phrase_strip(p: QPainter, x0: int, y0: int, w: int, h: int,
                       meta: dict | None, duration_ms: float) -> None:
    """Coloured phrase band (Rekordbox-style), drawn beneath the overview."""
    segments = _phrase_segments(meta, duration_ms)
    if not segments or w <= 0 or h <= 0:
        p.fillRect(x0, y0, w, h, QColor("#0a0c0f"))
        return
    dur = max(1.0, duration_ms)
    font = QFont("DejaVu Sans Mono", 7)
    font.setBold(True)
    p.setFont(font)
    for start, end, phrase in segments:
        x1 = x0 + int((start / dur) * w)
        x2 = x0 + int((end / dur) * w)
        if x2 <= x1:
            x2 = x1 + 1
        color = _phrase_color(phrase)
        fill = QColor(color.red(), color.green(), color.blue(), 200)
        p.fillRect(x1, y0, x2 - x1, h, fill)
        label = str(phrase.get("text") or "")
        if label and x2 - x1 > 28:
            p.setPen(QColor(8, 10, 12, 220))
            p.drawText(x1 + 4, y0, x2 - x1 - 6, h, Qt.AlignVCenter, label)
            p.setPen(QColor(244, 246, 251))
            p.drawText(x1 + 3, y0 - 1, x2 - x1 - 6, h, Qt.AlignVCenter, label)


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
            "flags": _flags,
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
    # PWV6 overview: contributions stacked bottom-up, already peak-normalised.
    wave["band_stacked"] = bool(int(bands.get("flags") or 0) & 1)
    if wave["band_stacked"]:
        wave["band_peak"] = 255


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


def paint_band_column(p: QPainter, x: int, mid: float, low_px: float, mid_px: float,
                      high_px: float, pal: dict) -> None:
    """Mirrored 3-band column in palette ``pal``.

    ``overlap`` mode (Beat Link reference): the taller of low and mid in its
    colour, the overlap in brown, then the highs. ``mask`` mode
    (OneLibraryCompanion): each band combination is filled to the height all
    its members reach, in the order low, mid, high, low+mid, low+high,
    mid+high, all, so every overlap gets its own colour.
    """
    if pal["mode"] == "mask":
        hs = (low_px, mid_px, high_px)
        for key, bits in zip(_ws.MASK_KEYS, _ws.MASK_BITS):
            px = min(hs[i] for i in range(3) if bits & (1 << i))
            _fill_mirrored(p, x, mid, px, pal[key])
        return
    if low_px > mid_px:
        _fill_mirrored(p, x, mid, low_px, pal["low"])
        _fill_mirrored(p, x, mid, mid_px, pal["low_mid"])
    else:
        if abs(low_px - mid_px) >= 0.4:
            _fill_mirrored(p, x, mid, mid_px, pal["mid"])
        _fill_mirrored(p, x, mid, low_px, pal["low_mid"])
    _fill_mirrored(p, x, mid, high_px, pal["high"])


def paint_stacked_column(p: QPainter, x: int, bottom: float, height: float,
                         low: int, md: int, hi: int, pal: dict) -> None:
    """PWV6 overview column, stacked bottom-up: total in high, low+mid in mid, low."""
    for value, key in ((low + md + hi, "high"), (low + md, "mid"), (low, "low")):
        px = value / 255.0 * height
        if px >= 0.4:
            p.fillRect(x, int(bottom - px), 1, max(1, int(round(px))), pal[key])


def stacked_pick(wave: dict, a: int, b: int) -> int:
    """Max-pool source index in [a, b) by stacked total."""
    low, md, hi = wave["low"], wave["mid"], wave["high"]
    best, top = a, -1
    for i in range(a, max(a + 1, b)):
        t = low[i] + md[i] + hi[i]
        if t > top:
            best, top = i, t
    return best


def _paint_column(p: QPainter, x: int, mid: float, amp: float, style: str,
                  src: int, wave: dict, span: float, palette: str | None = None) -> None:
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
            paint_band_column(p, x, mid, low / peak * span, md / peak * span,
                              hi / peak * span, wave_palette(palette))
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
    zoom_seconds_changed = Signal(float)  # wheel zoom in seconds, -1 = reset

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
        self._palette = _ws.DEFAULT_PALETTE
        self._zoom_seconds: float | None = None   # wheel zoom; None = bars setting
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
        self._phrase_h = 0
        self._vocal_h = 0
        self._show_phrases = True
        self._show_vocals = True

    def set_show_phrases(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._show_phrases:
            return
        self._show_phrases = enabled
        self.update()

    def set_show_vocals(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._show_vocals:
            return
        self._show_vocals = enabled
        self.update()

    def _visible_seconds(self) -> float:
        if self._zoom_seconds is not None:
            return self._zoom_seconds
        return bars_to_seconds(self._zoom_bars, self._bpm)

    def detail_x(self, t_ms: float, width: int | None = None) -> float:
        """X of track time on the scrolling strip (loops, cues, beats share it)."""
        return x_for_ms(t_ms, self._pos_ms, self._visible_seconds(),
                        width if width is not None else max(1, self.width()),
                        self._playhead_frac)

    def set_zoom_seconds(self, seconds: float | None) -> None:
        value = None if seconds is None or seconds < 0 else clamp_seconds(seconds)
        if value == self._zoom_seconds:
            return
        self._zoom_seconds = value
        self._invalidate_detail()
        self.update()

    def _over_detail(self, y: float) -> bool:
        return y > self._overview_h + self._phrase_h + self._vocal_h

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Wheel over the scrolling strip zooms ~2-30 s, playhead stays put."""
        delta = event.angleDelta().y()
        if not delta or not self._over_detail(event.position().y()):
            return super().wheelEvent(event)
        seconds = wheel_zoom(self._visible_seconds(), delta)
        self.set_zoom_seconds(seconds)
        self.zoom_seconds_changed.emit(seconds)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._over_detail(event.position().y()):
            self.set_zoom_seconds(None)
            self.zoom_seconds_changed.emit(-1.0)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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

    def set_palette(self, name: str) -> None:
        name = _ws.normalize_palette(name)
        if name == self._palette:
            return
        self._palette = name
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
        overview_h = overview_height(h)
        self._overview_h = overview_h
        dur = self._duration_ms()
        phrase_segments = _phrase_segments(self._meta, dur) if self._show_phrases else []
        phrase_h = PHRASE_H if phrase_segments else 0
        self._phrase_h = phrase_h
        vocals = (self._meta or {}).get("vocals") or []
        vocal_h = VOCAL_STRIP_H if (self._show_vocals and vocals and dur) else 0
        self._vocal_h = vocal_h
        gap = 1
        y_after_phrase = overview_h + gap + phrase_h + (gap if phrase_h else 0)
        y_detail = y_after_phrase + vocal_h + (gap if vocal_h else 0)
        detail_h = max(1, h - y_detail)

        p.fillRect(0, 0, w, overview_h, QColor("#0b0d10"))
        if phrase_h:
            p.fillRect(0, overview_h + gap, w, phrase_h, QColor("#0a0c0f"))
        if vocal_h:
            p.fillRect(0, y_after_phrase, w, vocal_h, QColor("#141018"))
        p.fillRect(0, y_detail, w, detail_h, QColor("#080a0c"))
        p.setPen(QColor(COLORS["line"]))
        p.drawLine(0, overview_h, w, overview_h)
        if phrase_h:
            p.drawLine(0, overview_h + gap + phrase_h, w, overview_h + gap + phrase_h)
        if vocal_h:
            p.drawLine(0, y_after_phrase + vocal_h, w, y_after_phrase + vocal_h)

        self._blit_overview(p, 0, 0, w, overview_h)
        if phrase_h:
            _draw_phrase_strip(p, 0, overview_h + gap, w, phrase_h, self._meta, dur)
            px = max(0, min(w, int((self._pos_ms / max(1, dur)) * w)))
            if px < w:
                p.fillRect(px, overview_h + gap, w - px, phrase_h, QColor(8, 10, 12, 120))
        if vocal_h:
            _draw_vocal_strip(p, 0, y_after_phrase, w, vocal_h, vocals, dur, self._pos_ms)
        self._blit_detail(p, 0, y_detail, w, detail_h)

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
        px = max(0, min(w, int((self._pos_ms / max(1, dur)) * w)))
        if px < w:
            p.fillRect(x0 + px, y0, w - px, h, QColor(8, 10, 12, 150))
        p.fillRect(x0 + px - 1, y0, 2, h, self._color)

    def _ensure_overview(self, w, h) -> None:
        ov = self._overview
        key = (id(ov) if ov else 0, self._style, self._palette, w, h, self._offair)
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

    def _draw_overview_wave(self, p: QPainter, x0, y0, w, h) -> None:
        ov = self._overview
        if not ov or not ov["n"]:
            return
        n = ov["n"]
        heights = ov["h"]
        mid = y0 + h / 2
        span = (h / 2) * 0.94
        if self._style == "3band" and ov.get("band_stacked"):
            pal = wave_palette(self._palette)
            bottom = y0 + h - 1
            for x in range(w):
                i = stacked_pick(ov, min(n - 1, (x * n) // w), min(n, ((x + 1) * n) // w))
                paint_stacked_column(p, x0 + x, bottom, h - 2, ov["low"][i],
                                     ov["mid"][i], ov["high"][i], pal)
            return
        for x in range(w):
            # Max-pool every source column under this pixel.
            a = min(n - 1, (x * n) // w)
            b = max(a + 1, min(n, ((x + 1) * n) // w))
            i = max(range(a, b), key=heights.__getitem__)
            tall = (heights[i] / 31.0) * span
            _paint_column(p, x0 + x, mid, tall, self._style, i, ov, span, self._palette)

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
        key = (id(d), self._style, self._palette, self._zoom_bars,
               self._zoom_seconds, round(self._bpm, 1),
               w, h, self._offair, self._playhead_frac)

        if (self._detail_pm is None or self._detail_key != key
                or self._detail_pm.width() != w or self._detail_pm.height() != h):
            self._rebuild_detail(w, h, start, key)
            return

        # Same origin rule as the overlay: the bitmap shift and the stored
        # column stay in one time base, so the loop highlight cannot walk.
        new_start, shift, full = sync_detail_origin(
            self._detail_start, start, px_per_col, w, 1.0)
        if full:
            self._rebuild_detail(w, h, start, key)
            return
        if shift == 0:
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
        self._detail_start = new_start

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
            _paint_column(p, x0 + px, mid, amp, self._style, src, d, span, self._palette)

        # Cues are drawn live in _blit_detail so they stay sharp while scrolling.


class PlayedBar(QWidget):
    """Thin played / remaining bar. Empty until a duration is known."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._frac = 0.0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_fraction(self, frac: float) -> None:
        self._frac = max(0.0, min(1.0, float(frac or 0)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["line"]))
        painter.drawRoundedRect(self.rect(), 2, 2)
        width = int(self.width() * self._frac)
        if width > 0:
            painter.setBrush(self._color)
            painter.drawRoundedRect(0, 0, width, self.height(), 2, 2)
        painter.end()


class DeckCard(QFrame):
    zoom_override_changed = Signal(int, int)  # deck number, bars
    zoom_seconds_changed = Signal(int, float)  # deck number, seconds (-1 = reset)
    focused = Signal(int)                     # deck number

    def __init__(self, number: int, i18n, parent=None):
        super().__init__(parent)
        self.setObjectName("DeckCard")
        self.number = number
        self._i18n = i18n
        self._color = DECK_COLORS.get(number, COLORS["accent"])
        self._track_id = 0
        self._track_key = ""
        self._meta = None
        self._detail = None
        self._overview = None
        self._art_id = 0
        self._art_key = ""
        self._art_miss_key = ""
        self._wave_track_id = 0
        self._wave_track_key = ""
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
        accent.setFixedWidth(ACCENT_PX)
        accent.setStyleSheet(f"background:{self._color}; border:none; border-radius:0;")
        root.addWidget(accent)

        body = QHBoxLayout()
        body.setContentsMargins(*BODY_MARGINS)
        body.setSpacing(BODY_GAP)
        root.addLayout(body, 1)
        self._body = body

        # identity — title and the track details. Tags live in the readout column.
        ident = QVBoxLayout()
        ident.setContentsMargins(0, 0, 0, 0)
        ident.setSpacing(4)
        self._ident_layout = ident
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.art = QLabel()
        self.art.setFixedSize(ART_PX, ART_PX)
        self._set_art_placeholder()
        self.art.setAlignment(Qt.AlignCenter)
        self.art.setStyleSheet(
            f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
            f"border-radius:{ART_RADIUS}px; color:{COLORS['dimmest']}; font-size:22px;"
        )
        top.addWidget(self.art, 0, Qt.AlignTop)

        who = QVBoxLayout()
        who.setContentsMargins(0, 0, 0, 0)
        who.setSpacing(1)
        self._who = who
        self.num = QLabel(str(number))
        self.title = QLabel("")
        self.title.setWordWrap(False)
        self.artist = QLabel("")
        self.artist.setObjectName("Dim")
        self.album = QLabel("")
        self.album.setObjectName("DeckMeta")
        self.label_line = QLabel("")
        self.label_line.setObjectName("DeckMeta")
        self.genre = QLabel("")
        self.genre.setObjectName("DeckMeta")
        self.genre.setWordWrap(False)
        self._genre_raw = ""
        facts = QHBoxLayout()
        facts.setSpacing(8)
        facts.setContentsMargins(0, 0, 0, 0)
        self.year = QLabel("")
        self.year.setObjectName("DeckMeta")
        self.key = QLabel("")
        self.rating = QLabel("")
        self.rating.setTextFormat(Qt.RichText)
        self.rating.setObjectName("DeckMeta")
        self.color_line = QLabel("")
        self.color_line.setObjectName("DeckExtra")
        self.color_line.setTextFormat(Qt.RichText)
        for widget in (self.year, self.key, self.rating, self.color_line):
            facts.addWidget(widget)
        facts.addStretch(1)
        self._facts = QWidget()
        self._facts.setLayout(facts)
        self.comment = QLabel("")
        self.comment.setObjectName("DeckExtra")
        self.comment.setWordWrap(False)
        for widget in (
            self.num, self.title, self.artist, self.album, self.label_line,
            self.genre, self._facts, self.comment,
        ):
            who.addWidget(widget)
        who.addStretch(1)
        top.addLayout(who, 1)
        ident.addLayout(top)
        self.played = PlayedBar(self._color)
        ident.addWidget(self.played)
        self.wrap_ident = QWidget()
        self.wrap_ident.setFixedWidth(IDENT_WIDTH)
        self.wrap_ident.setLayout(ident)
        body.addWidget(self.wrap_ident, 0)

        # waveform — takes all leftover horizontal (and vertical) space
        wave_col = QVBoxLayout()
        wave_col.setContentsMargins(0, 0, 0, 0)
        wave_col.setSpacing(2)
        wave_tools = QHBoxLayout()
        wave_tools.setContentsMargins(0, 0, 0, 0)
        wave_tools.setSpacing(4)
        self.cue_readout = QLabel("")
        self.cue_readout.setObjectName("Mono")
        self.cue_readout.setStyleSheet(
            f"font-family:monospace; font-size:11px; color:{COLORS['dim']};"
        )
        wave_tools.addWidget(self.cue_readout)
        self._show_next_cue = True
        wave_tools.addStretch(1)
        self.zoom_btn = QPushButton(f"{self._zoom_bars}b")
        self.zoom_btn.setObjectName("Chip")
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_btn.setToolTip("Per-deck zoom (cycles 1/2/4/8/16/32 bars). Right-click clears override.")
        self.zoom_btn.clicked.connect(self._cycle_zoom)
        self.zoom_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.zoom_btn.customContextMenuRequested.connect(lambda *_: self.clear_zoom_override())
        wave_tools.addWidget(self.zoom_btn)
        wave_col.addLayout(wave_tools)
        self.wave = WaveformView()
        self.wave.set_deck_color(self._color)
        self.wave.hover_ms_changed.connect(self._on_hover_ms)
        self.wave.cue_ms_clicked.connect(self._on_cue_ms)
        self.wave.zoom_seconds_changed.connect(self._on_wheel_zoom)
        wave_col.addWidget(self.wave, 1)
        body.addLayout(wave_col, 1)

        # readouts
        read = QVBoxLayout()
        read.setContentsMargins(0, 0, 0, 0)
        read.setSpacing(1)
        self.bpm = QLabel("— BPM")
        self.bpm.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bpm.setStyleSheet(
            f"font-family:monospace; font-size:{BPM_PX}px; font-weight:700;"
        )
        self.bpm.setMinimumWidth(READOUT_PANEL_MIN - 8)
        self.pitch = QLabel("")
        self.pitch.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pitch.setStyleSheet(
            f"font-family:monospace; font-size:{PITCH_PX}px; color:{COLORS['dim']};"
        )
        self.times_wrap = QWidget()
        times = QHBoxLayout(self.times_wrap)
        times.setContentsMargins(0, 0, 0, 0)
        times.setSpacing(0)
        self.elapsed = QLabel("00:00")
        self.elapsed.setStyleSheet(
            f"font-family:monospace; font-size:{TIME_PX}px; font-weight:700;"
        )
        self.remaining = QLabel("-00:00")
        self.remaining.setStyleSheet(
            f"font-family:monospace; font-size:{TIME_PX}px; font-weight:700; color:{COLORS['dim']};"
        )
        times.addWidget(self.elapsed)
        times.addSpacing(12)
        times.addWidget(self.remaining)
        times.addStretch(1)
        self.state = QLabel("")
        self.loop_badge = QLabel("")
        self.loop_badge.setObjectName("LoopBadge")
        self.loop_badge.setTextFormat(Qt.RichText)
        self.loop_badge.setStyleSheet(
            "font-family:monospace; font-size:10px; font-weight:700; "
            "letter-spacing:1px; color:#2ee89a; "
            "border:1px solid rgba(46,232,154,140); border-radius:9px; "
            "padding:2px 7px 2px 5px; background:rgba(46,232,154,26);"
        )
        self.loop_badge.hide()
        self.phase = QWidget()
        self.phase.setFixedHeight(10)
        phase_row = QHBoxLayout(self.phase)
        phase_row.setContentsMargins(0, 0, 0, 0)
        phase_row.setSpacing(3)
        self._phase_dots: list[QFrame] = []
        for _ in range(4):
            dot = QFrame()
            dot.setFixedSize(PHASE_W, PHASE_H)
            dot.setStyleSheet(
                f"background:{COLORS['line']}; border:none; border-radius:1px;"
            )
            phase_row.addWidget(dot)
            self._phase_dots.append(dot)
        self.phrase = QLabel("")
        self.phrase.setWordWrap(False)
        phase_row.addWidget(self.phrase, 1)
        self._phase_beat = 0
        self.tags = QLabel("")
        self.tags.setObjectName("Mono")
        self.tags.setWordWrap(False)
        self.state_wrap = QWidget()
        state_row = QHBoxLayout(self.state_wrap)
        state_row.setContentsMargins(0, 0, 0, 0)
        state_row.setSpacing(6)
        state_row.addWidget(self.loop_badge, 0)
        state_row.addWidget(self.state, 1)
        self.next_cue = QLabel("")
        self.next_cue.setObjectName("NextCue")
        self.next_cue.setWordWrap(False)
        self.next_cue.hide()
        self._read = read
        self._pitch_color = COLORS["dim"]
        self._remain_color = COLORS["dim"]
        self._fill_h = 0
        self._laying_out = False
        self._empty = True
        self._packed: set[str] = set()
        self._fit_sig = None
        self._art_image: QImage | None = None
        self._title_raw = ""
        self._artist_raw = ""
        self._album_raw = ""
        self._label_raw = ""
        self._year_raw = ""
        self._key_raw = ""
        self._rating_raw = ""
        self._color_html = ""
        self._comment_raw = ""
        self._phrase_raw = ""
        self._next_raw = ""
        self._text_w = 200
        self._read_w = READOUT_PANEL_MIN - 8
        self._pitch_px = PITCH_PX
        self._time_px = TIME_PX
        self._next_px = 13
        self._title_lines = 1
        read.addWidget(self.bpm)
        read.addWidget(self.pitch)
        read.addWidget(self.times_wrap)
        read.addWidget(self.tags)
        read.addWidget(self.state_wrap)
        read.addWidget(self.phase)
        read.addWidget(self.next_cue)
        read.addStretch(1)
        self.wrap_read = QWidget()
        self.wrap_read.setMinimumWidth(READOUT_PANEL_MIN)
        self.wrap_read.setMaximumWidth(READOUT_PANEL_MAX)
        self.wrap_read.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.wrap_read.setLayout(read)
        body.addWidget(self.wrap_read, 0)

        self.setMinimumHeight(96)
        self.apply_elements(self._elements)

    def apply_elements(self, elements: dict[str, bool] | None) -> None:
        """Show or hide deck pieces; collapse empty columns so the waveform grows."""
        self._elements = deck_elements_from(elements)
        e = self._elements

        self.art.setVisible(e["deck_show_artwork"])
        self.wave.setVisible(e["deck_show_waveform"])
        self.zoom_btn.setVisible(e["deck_show_waveform"])
        self.cue_readout.setVisible(e["deck_show_waveform"])
        self.bpm.setVisible(e["deck_show_bpm"])
        self.pitch.setVisible(e["deck_show_tempo"])
        self.times_wrap.setVisible(e["deck_show_time"])
        self.phase.setVisible(e["deck_show_state"] or e["deck_show_bpm"])

        # Left column stays if any identity piece (or always the deck number) shows.
        left_bits = (
            e["deck_show_artwork"] or e["deck_show_title"] or e["deck_show_artist"]
            or e["deck_show_meta"] or e["deck_show_key"]
        )
        self.wrap_ident.setVisible(True)  # deck number always present
        self.wrap_ident.setFixedWidth(IDENT_WIDTH if left_bits else IDENT_COLLAPSED)

        right_bits = (
            e["deck_show_bpm"] or e["deck_show_tempo"] or e["deck_show_time"]
            or e["deck_show_state"] or e["deck_show_tags"]
        )
        self.wrap_read.setVisible(right_bits)

        # Base mins — MonitorPage raises these further so cards fill the panel.
        # The floor stays under a 120px row so four decks can pack instead of scroll.
        if e["deck_show_waveform"] and not left_bits and not right_bits:
            floor = 112
        elif e["deck_show_waveform"]:
            floor = 96
        else:
            floor = 72
        if self._fill_h:
            self.set_fill_height(self._fill_h)
            return
        self.setMinimumHeight(floor)
        self.setMaximumHeight(16777215)
        self._layout_details()

    def set_fill_height(self, height: int) -> None:
        """Stretch this card so its waveform uses the allotted panel slice."""
        floor = 72
        if self._elements.get("deck_show_waveform", True):
            floor = 96
        fitted = max(floor, int(height))
        self._fill_h = fitted
        self._laying_out = True
        try:
            self.setMinimumHeight(fitted)
            # Cap max so equal stretch stays even when the host is viewport-sized.
            self.setMaximumHeight(fitted)
        finally:
            self._laying_out = False
        self._layout_details()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_details()

    def _row_height(self) -> int:
        """Height the packer should trust.

        A stale minimum (the card is born at 96, and used to be born at 200)
        must not win over the pixels the row was actually given. ``_fill_h``
        is the slice MonitorPage asked for; once the widget is on screen its
        live height is the one that clips.
        """
        live = int(self.height() or 0)
        fill = int(self._fill_h or 0)
        if live > 1 and fill > 1:
            return min(live, fill)
        if live > 1:
            return live
        if fill > 1:
            return fill
        return max(int(self.minimumHeight()), 80)

    def _dpr(self) -> float:
        return effective_dpr(float(self.devicePixelRatioF() or 1.0))

    def _make_font(self, px: int, *, bold: bool = False, mono: bool = False) -> QFont:
        # Name a family that is installed. A missing DejaVu face on Windows
        # makes QFontMetrics treat every glyph as a square of ``pixelSize``,
        # so a line that fits on Linux ellipsizes after a few words.
        font = QFont()
        if mono:
            font.setFamilies(["DejaVu Sans Mono", "Cascadia Mono", "Consolas", "Menlo", "Courier New"])
            font.setStyleHint(QFont.StyleHint.Monospace)
        else:
            font.setFamilies(["DejaVu Sans", "Segoe UI", "Helvetica Neue"])
            font.setStyleHint(QFont.StyleHint.SansSerif)
        matched = QFontInfo(font).family()
        if matched:
            font.setFamily(matched)
        font.setPixelSize(max(1, int(px)))
        font.setBold(bool(bold))
        return font

    def _px_that_fits(self, text: str, px: int, minimum: int, width: int, lines: int, *,
                      mono: bool = False, bold: bool = False) -> int:
        """Shrink ``px`` until ``text`` fits, or stop at ``minimum``."""
        px = max(minimum, int(px))
        raw = " ".join(str(text or "").split())
        if not raw or width <= 8:
            return px
        while px >= minimum:
            font = self._make_font(px, bold=bold, mono=mono)
            shown = elide_lines(raw, QFontMetrics(font), width, lines)
            if "…" not in shown and shown.replace("\n", " ") == raw:
                return px
            if px == minimum:
                break
            px -= 1
        return minimum

    def _line_box(self, font: QFont, lines: int = 1) -> int:
        spacing = QFontMetrics(font).lineSpacing()
        return snap_line(spacing, self._dpr()) * max(1, int(lines))

    def _paint_font(self, widget: QLabel, font: QFont, color: str) -> None:
        """Set the same pixel size on the QFont and the stylesheet.

        Measuring ``widget.font()`` after a stylesheet ``font-size`` used to
        see the application default, so elision and the line box disagreed
        with what was painted.
        """
        widget.setFont(font)
        weight = 700 if font.bold() else 400
        family = font.family().replace("'", "")
        widget.setStyleSheet(
            f"font-family:'{family}'; font-size:{font.pixelSize()}px;"
            f"font-weight:{weight}; color:{color};"
        )

    def _set_art_placeholder(self) -> None:
        """Phosphor music note in place of artwork."""
        self.art.setText("")
        size = max(16, int(self.art.width() * 0.42))
        self.art.setPixmap(icons.icon_pixmap(icons.NOTE, COLORS["dimmest"], size, self.devicePixelRatioF()))

    def _rating_html(self, font_px: int) -> str:
        if not self._rating_raw:
            return ""
        return icons.stars_html(self._rating_raw.count("★"), "#ffd45e", max(9, int(font_px or 12)))

    @staticmethod
    def _loop_text(label: str) -> str:
        return f"{icons.icon_html(icons.LOOP, '#2ee89a', 11)}&nbsp;{label}"

    def _put(self, widget: QLabel, text: str) -> None:
        widget.setText(text or "")
        widget.setProperty("filled", bool(text))

    def _packed_has(self, name: str) -> bool:
        return name in self._packed

    def _fit_signature(self) -> tuple:
        e = self._elements
        return (
            self._row_height(),
            round(self._dpr(), 2),
            bool(self._empty),
            self._title_raw,
            self._artist_raw,
            self._album_raw,
            self._label_raw,
            self._genre_raw,
            self._year_raw,
            self._key_raw,
            self._rating_raw,
            self._color_html,
            self._comment_raw,
            bool(self._phrase_raw),
            bool(self._next_raw),
            self._last_tags,
            bool(self.state.property("filled")),
            not self.loop_badge.isHidden(),
            bool(self.played.property("filled")),
            bool(e.get("deck_show_artwork")),
            bool(e.get("deck_show_title")),
            bool(e.get("deck_show_artist")),
            bool(e.get("deck_show_meta")),
            bool(e.get("deck_show_key")),
            bool(e.get("deck_show_bpm")),
            bool(e.get("deck_show_tempo")),
            bool(e.get("deck_show_time")),
            bool(e.get("deck_show_state")),
            bool(e.get("deck_show_tags")),
            bool(getattr(self, "_show_next_cue", True)),
        )

    def _layout_details(self) -> None:
        """Pack the identity and readout columns into the row height."""
        if self._laying_out:
            return
        self._laying_out = True
        try:
            self._pack_card()
            self._fit_sig = self._fit_signature()
        finally:
            self._laying_out = False

    def _pack_card(self) -> None:
        row = self._row_height()
        inner = max(32, row - BODY_MARGINS[1] - BODY_MARGINS[3])
        scale = row_scale(row)
        dpr = self._dpr()
        e = self._elements
        gap = 1
        show_art = bool(e.get("deck_show_artwork")) and not (
            self.wrap_ident.width() and self.wrap_ident.width() <= IDENT_COLLAPSED
        )
        side = art_side(row, art_max=ART_PX, margin_y=BODY_MARGINS[1] + BODY_MARGINS[3])
        if self._empty:
            side = min(side, 40)
        if show_art:
            self.art.setFixedSize(side, side)
            self.art.setVisible(True)
            self._rescale_art()
        else:
            self.art.setVisible(False)
            side = 0
        ident_w = self.wrap_ident.width() or (
            IDENT_COLLAPSED if not show_art and self.wrap_ident.width() == IDENT_COLLAPSED
            else IDENT_WIDTH
        )
        if ident_w < 80 and not self._empty:
            ident_w = IDENT_WIDTH
        text_w = ident_w - (side + 8 if side else 0) - 2
        self._text_w = max(48, text_w)
        self._read_w = max(48, (self.wrap_read.width() or READOUT_PANEL_MIN) - 4)

        def box(px: int, *, bold: bool = False, mono: bool = False, lines: int = 1) -> tuple[QFont, int]:
            font = self._make_font(px, bold=bold, mono=mono)
            return font, self._line_box(font, lines)

        title_lines = title_line_count(row)
        self._title_lines = title_lines
        title_px = max(TITLE_MIN, int(round(TITLE_PX * scale)))
        artist_px = max(ARTIST_MIN, int(round(ARTIST_PX * scale)))
        detail_px = max(DETAIL_MIN, int(round(DETAIL_PX * scale)))
        num_px = max(NUM_MIN, int(round(NUM_PX * scale)))
        show_title = bool(e.get("deck_show_title")) and bool(self._title_raw) and not self._empty
        show_artist = bool(e.get("deck_show_artist")) and bool(self._artist_raw)
        show_num = True

        if show_title and self._title_raw:
            while title_px > TITLE_MIN:
                font = self._make_font(title_px, bold=True)
                if QFontMetrics(font).horizontalAdvance(self._title_raw) <= self._text_w * title_lines:
                    break
                title_px -= 1

        def required_height(t_px: int, a_px: int, n_px: int) -> int:
            total = 0
            count = 0
            if show_num:
                total += box(n_px, bold=True, mono=True)[1]
                count += 1
            if show_title:
                total += box(t_px, bold=True, lines=title_lines)[1]
                count += 1
            if show_artist:
                total += box(a_px, mono=False)[1]
                count += 1
            if count > 1:
                total += gap * (count - 1)
            return total

        while required_height(title_px, artist_px, num_px) > inner and title_px > TITLE_MIN:
            title_px -= 1
        while required_height(title_px, artist_px, num_px) > inner and artist_px > ARTIST_MIN:
            artist_px -= 1
        while required_height(title_px, artist_px, num_px) > inner and num_px > NUM_MIN:
            num_px -= 1

        remain = inner - required_height(title_px, artist_px, num_px)
        # The stretch between the text block and the next line costs one gap
        # once an optional line joins the column.
        genre_lines = 1
        if (
            not self._empty
            and title_lines > 1
            and self._genre_raw
            and QFontMetrics(self._make_font(detail_px, mono=True)).horizontalAdvance(self._genre_raw)
            > self._text_w
        ):
            genre_lines = 2
        if self._genre_raw and not self._empty:
            detail_px = self._px_that_fits(
                self._genre_raw, detail_px, DETAIL_MIN, self._text_w, genre_lines, mono=True,
            )

        optional: list[tuple[str, int]] = []
        if not self._empty and e.get("deck_show_meta"):
            if self._album_raw:
                optional.append(("album", box(detail_px, mono=True)[1] + gap))
            if self._label_raw:
                optional.append(("label", box(detail_px, mono=True)[1] + gap))
            if self._genre_raw:
                optional.append(("genre", box(detail_px, mono=True, lines=genre_lines)[1] + gap))
            facts_filled = any((self._year_raw, self._key_raw, self._rating_raw, self._color_html))
            if facts_filled and (e.get("deck_show_meta") or e.get("deck_show_key")):
                key_px = max(DETAIL_MIN, int(round(KEY_PX * scale)))
                facts_h = max(box(detail_px, mono=True)[1], box(key_px, bold=True, mono=True)[1])
                optional.append(("facts", facts_h + gap))
            if self._comment_raw:
                optional.append(("comment", box(detail_px, mono=True)[1] + gap))
        # Key-only rows still get the facts line when meta is hidden.
        if (
            not self._empty
            and e.get("deck_show_key")
            and self._key_raw
            and not any(name == "facts" for name, _h in optional)
        ):
            key_px = max(DETAIL_MIN, int(round(KEY_PX * scale)))
            optional.append(("facts", box(key_px, bold=True, mono=True)[1] + gap))

        # Detail lines spend the leftover first. The played bar is last, so it
        # cannot push the artist (or a detail that already fit) off the card.
        chosen = choose_optional(remain, optional)
        used = sum(h for name, h in optional if name in set(chosen))
        bar_h = 10  # 6px bar plus the gap the identity column leaves above it
        show_bar = (
            bool(e.get("deck_show_time"))
            and bool(self.played.property("filled"))
            and not self._empty
            and (remain - used) >= bar_h
        )

        packed = {"num"}
        if show_title:
            packed.add("title")
        if show_artist:
            packed.add("artist")
        packed.update(chosen)
        if show_bar:
            packed.add("played")

        self._apply_identity(
            packed,
            num_px=num_px,
            title_px=title_px,
            artist_px=artist_px,
            detail_px=detail_px,
            title_lines=title_lines,
            genre_lines=genre_lines if "genre" in packed else 1,
            key_px=max(DETAIL_MIN, int(round(16 * scale))),
            show_art_side=side,
        )
        self._pack_readout(inner, scale, dpr, packed)
        self._packed = packed
        self.played.setVisible("played" in packed)

    def _apply_identity(
        self,
        packed: set[str],
        *,
        num_px: int,
        title_px: int,
        artist_px: int,
        detail_px: int,
        title_lines: int,
        genre_lines: int,
        key_px: int,
        show_art_side: int,
    ) -> None:
        e = self._elements
        num_font = self._make_font(num_px, bold=True, mono=True)
        num_h = self._line_box(num_font)
        self._paint_font(self.num, num_font, self._color)
        self.num.setText(str(self.number))
        self.num.setFixedHeight(num_h)
        self.num.setVisible(True)

        title_font = self._make_font(title_px, bold=True)
        shown_title = elide_lines(self._title_raw, QFontMetrics(title_font), self._text_w, title_lines)
        title_h = self._line_box(title_font, shown_title.count("\n") + 1 if shown_title else 1)
        self._paint_font(self.title, title_font, COLORS["text"])
        self.title.setText(shown_title)
        self.title.setWordWrap(False)
        self.title.setFixedHeight(title_h)
        self.title.setMaximumWidth(self._text_w)
        self.title.setVisible("title" in packed and bool(e.get("deck_show_title")))

        artist_font = self._make_font(artist_px)
        self._paint_font(self.artist, artist_font, COLORS["dim"])
        self.artist.setText(elide_lines(self._artist_raw, QFontMetrics(artist_font), self._text_w, 1))
        self.artist.setWordWrap(False)
        self.artist.setFixedHeight(self._line_box(artist_font))
        self.artist.setMaximumWidth(self._text_w)
        self.artist.setVisible("artist" in packed and bool(e.get("deck_show_artist")))

        detail_font = self._make_font(detail_px, mono=True)
        detail_h = self._line_box(detail_font)
        for widget, raw, name in (
            (self.album, self._album_raw, "album"),
            (self.label_line, self._label_raw, "label"),
            (self.comment, self._comment_raw, "comment"),
        ):
            self._paint_font(widget, detail_font, COLORS["dim"])
            widget.setWordWrap(False)
            widget.setFixedHeight(detail_h)
            widget.setMaximumWidth(self._text_w)
            widget.setText(elide_lines(raw, QFontMetrics(detail_font), self._text_w, 1))
            widget.setVisible(name in packed and bool(raw))

        g_lines = genre_lines if "genre" in packed else 1
        genre_font = detail_font
        genre_shown = elide_lines(self._genre_raw, QFontMetrics(genre_font), self._text_w, g_lines)
        self._paint_font(self.genre, genre_font, COLORS["dim"])
        self.genre.setWordWrap(False)
        self.genre.setText(genre_shown)
        self.genre.setFixedHeight(self._line_box(genre_font, genre_shown.count("\n") + 1 if genre_shown else 1))
        self.genre.setMaximumWidth(self._text_w)
        self.genre.setVisible("genre" in packed and bool(self._genre_raw))

        key_font = self._make_font(key_px, bold=True, mono=True)
        facts_h = max(detail_h, self._line_box(key_font))
        self._paint_font(self.year, detail_font, COLORS["dim"])
        self.year.setText(self._year_raw)
        self.year.setFixedHeight(detail_h)
        self._paint_font(self.key, key_font, self._color)
        self.key.setText(self._key_raw)
        self.key.setFixedHeight(self._line_box(key_font))
        self._paint_font(self.rating, detail_font, "#ffd45e")
        self.rating.setText(self._rating_html(detail_font.pixelSize()))
        self.rating.setFixedHeight(detail_h)
        self._paint_font(self.color_line, detail_font, COLORS["dim"])
        self.color_line.setText(self._color_html)
        self.color_line.setFixedHeight(detail_h)
        show_facts = "facts" in packed
        self.year.setVisible(show_facts and bool(self._year_raw) and bool(e.get("deck_show_meta")))
        self.rating.setVisible(show_facts and bool(self._rating_raw) and bool(e.get("deck_show_meta")))
        self.color_line.setVisible(show_facts and bool(self._color_html) and bool(e.get("deck_show_meta")))
        self.key.setVisible(
            show_facts
            and bool(self._key_raw)
            and bool(e.get("deck_show_key") or e.get("deck_show_meta"))
        )
        self._facts.setFixedHeight(facts_h)
        # isHidden, not isVisible: a hidden card makes isVisible() false and
        # would drop the facts row on the first layout.
        self._facts.setVisible(show_facts and any(not w.isHidden() for w in (
            self.year, self.key, self.rating, self.color_line,
        )))
        if show_art_side:
            note = max(10, show_art_side // 3)
            self.art.setStyleSheet(
                f"background:{COLORS['panel_high']}; border:1px solid {COLORS['line']};"
                f"border-radius:{ART_RADIUS}px; color:{COLORS['dimmest']}; font-size:{note}px;"
            )

    def _pack_readout(self, inner: int, scale: float, dpr: float, packed: set[str]) -> None:
        """One readout row per line, dropped from the bottom when the row is short."""
        del dpr  # line boxes already snap with effective_dpr
        e = self._elements
        gap = 1
        bpm_px = max(BPM_MIN, int(round(BPM_PX * scale)))
        pitch_px = max(PITCH_MIN, int(round(PITCH_PX * scale)))
        time_px = max(TIME_MIN, int(round(TIME_PX * scale)))
        meta_px = max(META_MIN, int(round(DETAIL_PX * scale)))
        next_px = max(META_MIN, int(round(13 * scale)))

        def total(items: list[tuple[str, int]]) -> int:
            if not items:
                return 0
            heights = []
            for name, px in items:
                font = self._make_font(px, bold=(name == "bpm"), mono=True)
                heights.append(self._line_box(font))
            return sum(heights) + gap * (len(items) - 1)

        # Display order. Lower-priority names are the ones READOUT_DROP lists.
        rows: list[tuple[str, int]] = []
        if self._empty:
            if e.get("deck_show_bpm"):
                rows.append(("bpm", BPM_MIN))
        else:
            if e.get("deck_show_bpm"):
                rows.append(("bpm", bpm_px))
            if e.get("deck_show_tempo") and self.pitch.text():
                rows.append(("pitch", pitch_px))
            if e.get("deck_show_time"):
                rows.append(("time", time_px))
            if e.get("deck_show_tags") and self.tags.property("filled"):
                rows.append(("tags", meta_px))
            state_on = bool(self.state.property("filled")) or not self.loop_badge.isHidden()
            if e.get("deck_show_state") and state_on:
                rows.append(("state", meta_px))
            if e.get("deck_show_state") and self._phrase_raw:
                rows.append(("phrase", meta_px))
            if self._next_raw and getattr(self, "_show_next_cue", True):
                rows.append(("next", next_px))

        droppable = set(READOUT_DROP)
        while total(rows) > inner and any(name in droppable for name, _px in rows):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index][0] in droppable:
                    rows.pop(index)
                    break
        # Shrink BPM before giving up pitch or the clock.
        while total(rows) > inner:
            bpm_i = next((i for i, (name, _px) in enumerate(rows) if name == "bpm"), None)
            if bpm_i is None:
                break
            name, px = rows[bpm_i]
            if px <= BPM_MIN:
                break
            rows[bpm_i] = (name, px - 1)
        while total(rows) > inner and len(rows) > 1:
            # Last resort: drop the clock, then pitch, so glyphs cannot overlap.
            rows.pop()

        shown = {name for name, _px in rows}
        packed.update(shown)
        self._pitch_px = pitch_px
        self._time_px = time_px
        self._next_px = next_px

        bpm_font_px = next((px for name, px in rows if name == "bpm"), BPM_MIN)
        bpm_font = self._make_font(bpm_font_px, bold=True, mono=True)
        self._paint_font(self.bpm, bpm_font, COLORS["text"])
        self.bpm.setFixedHeight(self._line_box(bpm_font))
        self.bpm.setVisible("bpm" in shown)

        self._style_pitch()
        pitch_font = self._make_font(self._pitch_px, mono=True)
        self.pitch.setFixedHeight(self._line_box(pitch_font))
        self.pitch.setVisible("pitch" in shown and bool(self.pitch.text()))

        self._style_times()
        time_font = self._make_font(self._time_px, bold=True, mono=True)
        time_h = self._line_box(time_font)
        self.elapsed.setFixedHeight(time_h)
        self.remaining.setFixedHeight(time_h)
        self.times_wrap.setFixedHeight(time_h)
        self.times_wrap.setVisible("time" in shown)

        meta_font = self._make_font(meta_px, mono=True)
        meta_h = self._line_box(meta_font)
        self.tags.setFont(meta_font)
        self.tags.setFixedHeight(meta_h)
        self.tags.setVisible("tags" in shown and bool(self.tags.property("filled")))

        self._paint_font(self.state, meta_font, COLORS["dim"])
        self.state.setFixedHeight(meta_h)
        self.state.setVisible("state" in shown and bool(self.state.property("filled")))
        self.loop_badge.setStyleSheet(
            f"font-family:monospace; font-size:{meta_px}px; font-weight:700; "
            "letter-spacing:1px; color:#2ee89a; "
            "border:1px solid rgba(46,232,154,140); border-radius:8px; "
            "padding:0px 6px; background:rgba(46,232,154,26);"
        )
        self.state_wrap.setFixedHeight(meta_h)
        # isHidden, not isVisible: a hidden parent makes isVisible() false and
        # used to keep this row hidden forever after the first layout.
        self.state_wrap.setVisible(
            "state" in shown and (
                bool(self.state.property("filled")) or not self.loop_badge.isHidden()
            )
        )

        phrase_w = self._phrase_text_width()
        # One step under the usual detail floor: a wide Windows face was
        # cutting "Chorus" down to "Chor…" at 11px in this column.
        phrase_px = self._px_that_fits(
            self._phrase_raw, meta_px, META_MIN - 2, phrase_w, 1, mono=True,
        )
        phrase_font = self._make_font(phrase_px, mono=True)
        phrase_h = self._line_box(phrase_font)
        self._paint_font(self.phrase, phrase_font, COLORS["dim"])
        self.phrase.setText(elide_lines(self._phrase_raw, QFontMetrics(phrase_font), phrase_w, 1))
        self.phrase.setFixedHeight(phrase_h)
        self.phase.setFixedHeight(max(meta_h, phrase_h))
        self.phase.setVisible("phrase" in shown and bool(self._phrase_raw))

        next_font = self._make_font(next_px, bold=True, mono=True)
        self._paint_font(self.next_cue, next_font, self._next_color())
        self.next_cue.setText(elide_lines(self._next_raw, QFontMetrics(next_font), self._read_w, 1))
        self.next_cue.setFixedHeight(self._line_box(next_font))
        self.next_cue.setVisible("next" in shown and bool(self._next_raw))

    def _readout_width(self) -> int:
        live = int(self.wrap_read.width() or 0)
        if live >= READOUT_PANEL_MIN:
            return live
        return READOUT_PANEL_MIN

    def _phrase_text_width(self) -> int:
        """Width left for the phrase once the four beat dots are in the row."""
        chrome = PHASE_W * 4 + 3 * 4
        return max(64, self._readout_width() - chrome)

    def _next_color(self) -> str:
        return getattr(self, "_next_cue_color", "#e8eaf0")

    def _style_pitch(self) -> None:
        px = int(self._pitch_px or PITCH_PX)
        font = self._make_font(px, mono=True)
        self._paint_font(self.pitch, font, self._pitch_color)

    def _style_times(self) -> None:
        px = int(self._time_px or TIME_PX)
        font = self._make_font(px, bold=True, mono=True)
        self._paint_font(self.elapsed, font, COLORS["text"])
        self._paint_font(self.remaining, font, self._remain_color)

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
        self._paint_zoom_btn()

    def effective_bars(self) -> int | None:
        """Bars the strip is showing: the bar setting, or the bar button a
        wheel zoom matches at this BPM. None when the wheel sits between two."""
        sec = self.wave._zoom_seconds
        if sec is None:
            return self._zoom_bars
        return bars_for_seconds(sec, self.wave._bpm, ZOOM_BARS)

    def _paint_zoom_btn(self) -> None:
        bars = self.effective_bars()
        if bars is None:
            label = f"{self.wave._zoom_seconds:.0f}s"
        else:
            label = f"{bars}b"
        override = self._zoom_override or self.wave._zoom_seconds is not None
        if override:
            label += "*"
        if getattr(self, "_zoom_btn_painted", None) == (label, override):
            return
        self._zoom_btn_painted = (label, override)
        self.zoom_btn.setText(label)
        self.zoom_btn.setProperty("active", "true" if override else "false")
        self.zoom_btn.style().unpolish(self.zoom_btn)
        self.zoom_btn.style().polish(self.zoom_btn)

    def _on_wheel_zoom(self, seconds: float) -> None:
        self._paint_zoom_btn()
        self.zoom_seconds_changed.emit(self.number, float(seconds))

    def clear_wheel_zoom(self) -> bool:
        """Drop the wheel zoom so the bar setting shows again. One zoom state:
        a bar button (toolbar, deck chip or All) replaces whatever the wheel set."""
        if self.wave._zoom_seconds is None:
            return False
        self.wave.set_zoom_seconds(None)
        self._paint_zoom_btn()
        self.zoom_seconds_changed.emit(self.number, -1.0)
        return True

    def clear_zoom_override(self) -> None:
        if not self._zoom_override:
            return
        self._zoom_override = False
        self.zoom_override_changed.emit(self.number, -1)  # -1 = clear

    def _cycle_zoom(self) -> None:
        idx = ZOOM_BARS.index(self._zoom_bars) if self._zoom_bars in ZOOM_BARS else 2
        bars = ZOOM_BARS[(idx + 1) % len(ZOOM_BARS)]
        self.clear_wheel_zoom()
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

    def _paint_next(self, pos_ms: float) -> None:
        """NEXT: <name> in m:ss, coloured as the mark gets close. Seek-aware."""
        label = getattr(self, "next_cue", None)
        if label is None:
            return
        text = ""
        if getattr(self, "_show_next_cue", True) and self._meta:
            meta = self._meta or {}
            mark = next_mark(
                pos_ms,
                cues=meta.get("cues"),
                phrases=meta.get("phrases"),
                beats=meta.get("beats"),
            )
            text = format_countdown(mark)
            if mark:
                self._next_cue_color = urgency_color(mark["remain_ms"])
        was_empty = not self._next_raw
        self._next_raw = text
        if self._laying_out:
            return
        if was_empty != (not text):
            self._layout_details()
            return
        if not text or not self._packed_has("next"):
            label.hide()
            label.setText("")
            return
        font = self._make_font(int(self._next_px or 13), bold=True, mono=True)
        self._paint_font(label, font, self._next_color())
        label.setText(elide_lines(text, QFontMetrics(font), self._read_w, 1))
        label.setFixedHeight(self._line_box(font))
        label.show()

    def _on_cue_ms(self, ms: float) -> None:
        self._cue_ms = ms
        self.cue_readout.setText(f"cue {mmss(ms)}")
        self.cue_readout.setStyleSheet(
            f"font-family:monospace; font-size:11px; color:{self._color};"
        )

    def set_wave_style(self, style: str, fallback: str | None = None) -> None:
        """User choice (may be ``auto``); auto follows the player's 0xDA report."""
        self._wave_choice = _ws.normalize_style(style)
        if fallback is not None:
            self._wave_fallback = _ws.normalize_style(fallback, allow_auto=False)
        self._apply_wave_style()

    def _apply_wave_style(self) -> None:
        deck = getattr(self, "_last_deck", None) or {}
        self.wave.set_style(_ws.resolve_style(
            getattr(self, "_wave_choice", "rgb"), deck.get("waveform_color"),
            getattr(self, "_wave_fallback", "rgb")))

    def set_wave_palette(self, name: str) -> None:
        self.wave.set_palette(name)

    def set_zoom_seconds(self, seconds: float | None) -> None:
        self.wave.set_zoom_seconds(seconds)
        self._paint_zoom_btn()

    def set_show_phrases(self, enabled: bool) -> None:
        self.wave.set_show_phrases(enabled)

    def set_show_vocals(self, enabled: bool) -> None:
        self.wave.set_show_vocals(enabled)

    def set_show_next_cue(self, enabled: bool) -> None:
        self._show_next_cue = bool(enabled)
        self._paint_next(self.wave._pos_ms)

    def needs_artwork(self, token: str = "") -> bool:
        """True when this card has no image yet for the loaded track."""
        token = token or ""
        if getattr(self, "_art_miss_key", "") == token and token:
            return False
        if self._art_key == token and token:
            pix = self.art.pixmap()
            if pix is not None and not pix.isNull():
                return False
        pix = self.art.pixmap()
        return pix is None or pix.isNull()

    def set_artwork(self, art: bytes | None, token: str) -> None:
        """Attach a JPEG without clearing a picture that is already showing."""
        token = token or ""
        if not art:
            if token:
                self._art_miss_key = token
            return
        if token and self._art_key == token:
            pix = self.art.pixmap()
            if pix is not None and not pix.isNull():
                return
        img = QImage.fromData(art)
        if img.isNull():
            if token:
                self._art_miss_key = token
            return
        self._art_image = img
        self._art_key = token
        self._art_miss_key = ""
        self._rescale_art()

    def _rescale_art(self) -> None:
        """Draw the stored cover at the artwork box the row actually has."""
        img = self._art_image
        if img is None or img.isNull():
            return
        side = max(1, self.art.width())
        pix = QPixmap.fromImage(img).scaled(
            side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.art.setPixmap(pix)
        self.art.setText("")

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

    def _fill_meta(self, meta: dict) -> None:
        self._empty = False
        self._title_raw = display_text(meta.get("title"))
        self._artist_raw = display_text(meta.get("artist"))
        self._album_raw = display_text(meta.get("album"))
        self._label_raw = display_text(meta.get("label"))
        self._genre_raw = display_text(meta.get("genre"))
        self._year_raw = display_year(meta.get("year"))
        self._key_raw = display_text(meta.get("key"))
        self._rating_raw = rating_stars(meta.get("rating"))
        name, swatch = color_label(meta.get("color"))
        if name and swatch:
            self._color_html = f'<span style="color:{swatch}">●</span> {name}'
        else:
            self._color_html = name
        self._comment_raw = display_text(meta.get("comment"))
        self._put(self.artist, self._artist_raw)
        self._put(self.album, self._album_raw)
        self._put(self.label_line, self._label_raw)
        self.genre.setProperty("filled", bool(self._genre_raw))
        self.genre.setText(self._genre_raw)
        self._put(self.year, self._year_raw)
        self._put(self.key, self._key_raw)
        self._put(self.rating, self._rating_raw)
        if self._rating_raw:
            self.rating.setText(self._rating_html(self.rating.font().pixelSize()))
        self._put(self.color_line, self._color_html)
        self._put(self.comment, self._comment_raw)
        self.title.setText(self._title_raw)

    def _clear_details(self, note: str = "") -> None:
        self._title_raw = ""
        self._artist_raw = note
        self._album_raw = ""
        self._label_raw = ""
        self._genre_raw = ""
        self._year_raw = ""
        self._key_raw = ""
        self._rating_raw = ""
        self._color_html = ""
        self._comment_raw = ""
        self._phrase_raw = ""
        self._next_raw = ""
        self.title.setText("")
        for widget in (
            self.album, self.label_line, self.genre, self.year, self.key,
            self.rating, self.color_line, self.comment, self.phrase,
        ):
            self._put(widget, "")
        self._put(self.artist, note)
        self.played.setProperty("filled", False)
        self.played.set_fraction(0)

    def _paint_phrase(self, pos_ms: float, bar: int, beat: int = 0) -> None:
        meta = self._meta or {}
        text = beat_phrase_text(
            bar,
            current_phrase(pos_ms, meta.get("phrases"), meta.get("beats")),
            beat,
        )
        was_empty = not self._phrase_raw
        self._phrase_raw = text
        self.phrase.setProperty("filled", bool(text))
        if self._laying_out:
            return
        if was_empty != (not text):
            self._layout_details()
            return
        if not text or not self._packed_has("phrase"):
            self.phase.setVisible(False)
            return
        font = self.phrase.font()
        if font.pixelSize() <= 0:
            font = self._make_font(META_MIN, mono=True)
        self.phrase.setText(elide_lines(
            text, QFontMetrics(font), self._phrase_text_width(), 1,
        ))
        self.phase.setVisible(True)

    def set_track_data(self, meta: dict | None, detail, overview, art: bytes | None) -> None:
        if meta and self._track_key and meta.get("track_key") != self._track_key:
            return
        self._meta = meta
        self._detail = detail
        self._overview = overview
        token = (meta or {}).get("track_key") or ""
        if detail is not None or overview is not None:
            self._wave_track_id = (meta or {}).get("id") or self._track_id
            self._wave_track_key = token
        self.wave.set_track(detail, overview, meta)
        self._paint_next(self.wave._pos_ms)
        if meta:
            self._fill_meta(meta)
        self._layout_details()
        if art and self._art_key != token:
            img = QImage.fromData(art)
            if not img.isNull():
                self._art_image = img
                self._art_id = (meta or {}).get("id", 0)
                self._art_key = token
                self._rescale_art()

    def needs_waveform(self, track_id: int, track_key: str = "") -> bool:
        key = track_key or ""
        return bool(track_id) and (
            self._wave_track_id != track_id
            or self._wave_track_key != key
            or self._detail is None
        )

    def advance_playhead(self, pos_ms: float, playing: bool, duration_ms: float = 0,
                         *, bar: int = 0, beat: int = 0, looping: bool = False,
                         loop_start_ms: float | None = None,
                         loop_end_ms: float | None = None) -> None:
        """Lightweight per-frame update: times + waveform position + phase."""
        if looping and loop_end_ms is not None and loop_start_ms is not None:
            deck = dict(getattr(self, "_last_deck", None) or {})
            deck["loop_start_ms"] = loop_start_ms
            deck["loop_end_ms"] = loop_end_ms
            self._last_deck = deck
        if getattr(self, "_wave_choice", "rgb") == "auto":
            self._apply_wave_style()
        meta_dur = float((self._meta or {}).get("duration_ms") or 0)
        dur = sane_duration_ms(duration_ms, meta_dur)
        if dur:
            pos_ms = min(pos_ms, dur)
        self.elapsed.setText(mmss(pos_ms))
        remain = max(0.0, dur - pos_ms) if dur else 0.0
        self.remaining.setText(("-" + mmss(remain)) if dur else "—")
        self._remain_color = COLORS["danger"] if (dur and remain < 30000) else COLORS["dim"]
        self._style_times()
        if dur:
            self.played.set_fraction(max(0.0, min(1.0, pos_ms / dur)))
            self.played.setProperty("filled", True)
        else:
            self.played.set_fraction(0)
            self.played.setProperty("filled", False)
        self.played.setVisible(
            self._packed_has("played") and bool(self.played.property("filled"))
        )
        self._set_phase(bar)
        self._paint_phrase(pos_ms, bar, beat)
        self._apply_loop_region(pos_ms, looping)
        self._paint_next(pos_ms)
        self.wave.set_position(pos_ms, playing)
        if not self._laying_out and self._fit_signature() != self._fit_sig:
            self._layout_details()

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
        pick = None
        # The player's own loop in/out wins over a stored cue. A manual loop
        # is not in the ANLZ cues, and a drifting playhead must not slide the
        # highlight onto a different cue.
        deck = getattr(self, "_last_deck", None) or {}
        packet_start = deck.get("loop_start_ms")
        packet_end = deck.get("loop_end_ms")
        if looping and packet_start is not None and packet_end is not None:
            try:
                p_start, p_end = float(packet_start), float(packet_end)
            except (TypeError, ValueError):
                p_start = p_end = 0.0
            if p_end > p_start:
                start, end = p_start, p_end
                pick = {"t": start, "end": end}
        if pick is None:
            cues = (self._meta or {}).get("cues") or []
            loops = [c for c in cues
                     if c.get("type") == "loop" and (c.get("end") or 0) > (c.get("t") or 0)]
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
            self.loop_badge.setText(self._loop_text(str(beats) if beats else "LOOP"))
            self.loop_badge.show()
        elif looping:
            self.loop_badge.setText(self._loop_text("LOOP"))
            self.loop_badge.show()
        else:
            self.loop_badge.hide()
            self.loop_badge.setText("")
        if self._laying_out:
            return
        self.state_wrap.setVisible(
            self._packed_has("state")
            and (bool(self.state.property("filled")) or not self.loop_badge.isHidden())
        )

    def update_deck(self, deck: dict, pos_ms: float) -> None:
        tid = deck.get("track_id") or 0
        tkey = deck.get("track_key") or ""
        if tid != self._track_id or tkey != self._track_key:
            self._track_id = tid
            self._track_key = tkey
            self._meta = None
            self._detail = None
            self._overview = None
            self._wave_track_id = 0
            self._wave_track_key = ""
            self._art_id = 0
            self._art_key = ""
            self._art_miss_key = ""
            self._last_offair = None
            self._last_tags = ""
            self._clear_details("" if tid else self._i18n.t("no_track"))
            self._art_image = None
            if tid:
                self._empty = False
                self._title_raw = self._i18n.t("loading")
                self.title.setText(self._title_raw)
            if not tid:
                self._empty = True
                self._title_raw = ""
                self._set_art_placeholder()
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
            self.tags.setProperty("filled", bool(tags_html))

        bpm = deck.get("bpm") or 0
        self._last_bpm = float(bpm or 0)
        self.wave.set_bpm(float(deck.get("track_bpm") or bpm or 120.0))
        self.bpm.setText(f"{bpm:.2f} BPM" if bpm else "— BPM")
        pitch = float(deck.get("pitch") or 0)
        sign = "+" if pitch >= 0 else ""
        self.pitch.setText(f"{sign}{pitch:.2f} %" if tid else "")
        if pitch > 0.01:
            self._pitch_color = "#ff9f45"
        elif pitch < -0.01:
            self._pitch_color = "#5eb0ff"
        else:
            self._pitch_color = COLORS["dim"]
        self._style_pitch()

        state = display_text(self._i18n.state(deck.get("state") or ""))
        if state.lower() == "unknown":
            state = ""
        self._put(self.state, state)
        self._last_deck = deck
        if getattr(self, "_wave_choice", "rgb") == "auto":
            self._apply_wave_style()
        self._paint_next(pos_ms)
        self._apply_playhead()
        if empty and not self._meta:
            self._empty = True
            self._title_raw = ""
            self._artist_raw = self._i18n.t("no_track")
            self.title.setText("")
            self._put(self.artist, self._artist_raw)
        elif tid and not self._meta:
            self._empty = False
            self._title_raw = self._i18n.t("loading")
            self._artist_raw = ""
            self.title.setText(self._title_raw)
            self._put(self.artist, "")
        else:
            self._empty = False
        self._layout_details()

        self.advance_playhead(
            pos_ms,
            bool(deck.get("playing")),
            float(deck.get("duration_ms") or 0),
            bar=int(deck.get("bar") or 0),
            beat=int(deck.get("beat") or 0),
            looping=(deck.get("state") == "looping"),
            loop_start_ms=deck.get("loop_start_ms"),
            loop_end_ms=deck.get("loop_end_ms"),
        )

    @staticmethod
    def _chip(text: str, color: str) -> str:
        return (
            f'<span style="font-family:monospace; font-size:9px; font-weight:700; '
            f'letter-spacing:1px; color:{color}; border:1px solid {color}; '
            f'padding:1px 5px; margin-right:4px;">{text}</span>'
        )
