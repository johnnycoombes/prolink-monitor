"""Parser for the rekordbox analysis files (ANLZ0000.DAT / .EXT / .2EX).

They hold the beat grid, the cues and the several waveform flavours:

    .DAT   PQTZ beat grid, PCOB cues, PWAV overview waveform (400 columns),
           PWV2 tiny preview
    .EXT   PWV3 blue detail waveform, PWV4 colour overview, PWV5 colour detail,
           PCO2 extended cues with names and colours, PSSI song structure
    .2EX   PWV6/PWV7 three-band waveform (CDJ-3000 and newer)

Format documented by the crate-digger project (Deep Symmetry).
Every integer is big-endian.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

# Detail waveform columns per second of audio at normal speed
DETAIL_COLUMNS_PER_SECOND = 150


@dataclass
class Beat:
    number: int          # position within the bar, 1..4
    tempo: float         # BPM at this point
    time: int            # milliseconds from the start


@dataclass
class Cue:
    hot_cue: int         # 0 = memory cue, 1..8 = hot cue A..H
    type: str            # "cue" or "loop"
    time: int            # ms
    loop_time: int       # ms (loop end)
    comment: str = ""
    color: tuple[int, int, int] | None = None


@dataclass
class Phrase:
    beat: int
    kind: int
    label: str = ""


@dataclass
class Analysis:
    beats: list[Beat] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    phrases: list[Phrase] = field(default_factory=list)
    path: str = ""
    # raw waveforms, exactly as stored in the file
    preview: bytes = b""          # PWAV  400 columns, 1 byte each
    tiny: bytes = b""             # PWV2  100 columns
    detail: bytes = b""           # PWV3  1 byte per column, 150 columns/s
    color_preview: bytes = b""    # PWV4  6 bytes per column
    color_detail: bytes = b""     # PWV5  2 bytes per column
    band3_preview: bytes = b""    # PWV6  3 bytes per column
    band3_detail: bytes = b""     # PWV7  3 bytes per column
    # PWVC thresholds. The lane is computed from these plus the 3-band columns.
    vocal_low: int = 0
    vocal_mid: int = 0
    vocal_high: int = 0
    has_vocals: bool = False

    @property
    def duration_ms(self) -> int:
        return self.beats[-1].time if self.beats else 0


def _u4(b: bytes, off: int) -> int:
    return struct.unpack_from(">I", b, off)[0]


_TAG_MAGICS = (
    b"PQTZ", b"PCOB", b"PCO2", b"PSSI", b"PWAV", b"PWV2",
    b"PWV3", b"PWV4", b"PWV5", b"PWV6", b"PWV7", b"PWVC",
)


def analysis_from_tag_blob(blob: bytes, into: Analysis | None = None) -> Analysis | None:
    """Parse one dbserver ANLZ tag (or a whole PMAI file) into ``into``."""
    if not blob:
        return into
    if blob[:4] == b"PMAI":
        return parse(blob, into)
    start = -1
    for magic in _TAG_MAGICS:
        found = blob.find(magic)
        if found >= 0 and (start < 0 or found < start):
            start = found
    if start < 0:
        return into
    chunk = blob[start:]
    if len(chunk) >= 12:
        tag_len = struct.unpack_from(">I", chunk, 8)[0]
        if 12 <= tag_len <= len(chunk):
            chunk = chunk[:tag_len]
    header_len = 28
    file_len = header_len + len(chunk)
    header = b"PMAI" + struct.pack(">II", header_len, file_len) + (b"\x00" * 16)
    try:
        return parse(header + chunk, into)
    except ValueError:
        return into


def parse(data: bytes, into: Analysis | None = None) -> Analysis:
    """Parse an ANLZ file. .DAT + .EXT + .2EX can accumulate into one Analysis."""
    a = into or Analysis()
    if len(data) < 12 or data[:4] != b"PMAI":
        raise ValueError("not a rekordbox analysis file (missing PMAI)")
    len_header = _u4(data, 4)
    pos = len_header
    while pos + 12 <= len(data):
        fourcc = data[pos:pos + 4]
        tag_header = _u4(data, pos + 4)
        tag_len = _u4(data, pos + 8)
        if tag_len < 12 or pos + tag_len > len(data):
            break
        body = data[pos + 12:pos + tag_len]
        try:
            _section(a, fourcc, body, tag_header)
        except (struct.error, IndexError, ValueError):
            pass                      # one broken section must not sink the rest
        pos += tag_len
    return a


def _section(a: Analysis, fourcc: bytes, body: bytes, tag_header: int) -> None:
    if fourcc == b"PQTZ":
        count = _u4(body, 8)
        for i in range(count):
            off = 12 + i * 8
            if off + 8 > len(body):
                break
            number, tempo, time = struct.unpack_from(">HHI", body, off)
            a.beats.append(Beat(number, tempo / 100.0, time))

    elif fourcc == b"PPTH":
        n = _u4(body, 0)
        if n > 2:
            a.path = body[4:4 + n - 2].decode("utf-16-be", "replace")

    elif fourcc == b"PWAV":
        a.preview = body[8:8 + _u4(body, 0)]
    elif fourcc == b"PWV2":
        a.tiny = body[8:8 + _u4(body, 0)]
    elif fourcc == b"PWV3":
        entry, count = struct.unpack_from(">II", body, 0)
        a.detail = body[12:12 + count * entry]
    elif fourcc == b"PWV4":
        entry, count = struct.unpack_from(">II", body, 0)
        a.color_preview = body[12:12 + count * entry]
    elif fourcc == b"PWV5":
        entry, count = struct.unpack_from(">II", body, 0)
        a.color_detail = body[12:12 + count * entry]
    elif fourcc == b"PWV6":
        entry, count = struct.unpack_from(">II", body, 0)
        a.band3_preview = body[8:8 + count * entry]
    elif fourcc == b"PWV7":
        entry, count = struct.unpack_from(">II", body, 0)
        a.band3_detail = body[12:12 + count * entry]
    elif fourcc == b"PWVC":
        from .vocal import thresholds_from_body

        thresholds = thresholds_from_body(body)
        if thresholds is not None:
            a.vocal_low = thresholds.low
            a.vocal_mid = thresholds.mid
            a.vocal_high = thresholds.high
            a.has_vocals = True

    elif fourcc == b"PCOB":
        _cues_basic(a, body)
    elif fourcc == b"PCO2":
        _cues_extended(a, body)
    elif fourcc == b"PSSI":
        _phrases(a, body)


def _cues_basic(a: Analysis, body: bytes) -> None:
    if any(c.comment or c.color for c in a.cues):
        return                                    # extended cues already read, better
    count = struct.unpack_from(">H", body, 6)[0]
    pos = 12
    for _ in range(count):
        if pos + 12 > len(body) or body[pos:pos + 4] != b"PCPT":
            break
        entry_len = _u4(body, pos + 8)
        hot = _u4(body, pos + 12)
        cue_type = body[pos + 28]
        time, loop_time = struct.unpack_from(">II", body, pos + 32)
        a.cues.append(Cue(hot, "loop" if cue_type == 2 else "cue", time, loop_time))
        pos += max(entry_len, 12)


def _cues_extended(a: Analysis, body: bytes) -> None:
    count = struct.unpack_from(">H", body, 4)[0]
    pos = 8
    new: list[Cue] = []
    for _ in range(count):
        if pos + 12 > len(body) or body[pos:pos + 4] != b"PCP2":
            break
        entry_len = _u4(body, pos + 8)
        hot = _u4(body, pos + 12)
        cue_type = body[pos + 16]
        time, loop_time = struct.unpack_from(">II", body, pos + 20)
        comment = ""
        color = None
        len_comment = 0
        if entry_len > 43:
            len_comment = _u4(body, pos + 40)
            if len_comment:
                raw = body[pos + 44:pos + 44 + len_comment]
                comment = raw.decode("utf-16-be", "replace").rstrip("\0")
        color_pos = pos + 44 + len_comment
        if entry_len - len_comment > 47 and color_pos + 4 <= len(body):
            color = (body[color_pos + 1], body[color_pos + 2], body[color_pos + 3])
        new.append(Cue(hot, "loop" if cue_type == 2 else "cue", time, loop_time,
                       comment, color))
        pos += max(entry_len, 12)
    if new:
        # extended cues replace the basic ones of the same kind
        keep = [c for c in a.cues if (c.hot_cue > 0) != (new[0].hot_cue > 0)]
        a.cues = keep + new


# Phrase labels by mood (Deep Symmetry Song Structure / Beat Link).
# mood 1 = high, 2 = mid, 3 = low. High mood variants use the k1/k2/k3 flags.
_PHRASE_LOW = {
    1: "Intro", 2: "Verse 1", 3: "Verse 1", 4: "Verse 1",
    5: "Verse 2", 6: "Verse 2", 7: "Verse 2", 8: "Bridge",
    9: "Chorus", 10: "Outro",
}
_PHRASE_MID = {
    1: "Intro", 2: "Verse 1", 3: "Verse 2", 4: "Verse 3",
    5: "Verse 4", 6: "Verse 5", 7: "Verse 6", 8: "Bridge",
    9: "Chorus", 10: "Outro",
}

# rekordbox 6+ XOR mask for PSSI bytes after len_entries (Deep Symmetry).
_PSSI_MASK = bytes((
    0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
    0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1,
))


def _high_phrase_label(kind: int, k1: int, k2: int, k3: int) -> str:
    if kind == 1:
        return "Intro 1" if k1 == 1 else "Intro 2"
    if kind == 2:
        if k2 == 0 and k3 == 0:
            return "Up 1"
        if k2 == 0 and k3 == 1:
            return "Up 2"
        if k2 == 1 and k3 == 0:
            return "Up 3"
        return "Up"
    if kind == 3:
        return "Down"
    if kind == 5:
        return "Chorus 1" if k1 == 1 else "Chorus 2"
    if kind == 6:
        return "Outro 1" if k1 == 1 else "Outro 2"
    return f"Phrase {kind}"


def _unmask_pssi(body: bytes, len_entries: int) -> bytes:
    """XOR-unmask PSSI bytes after len_entries (RB6+ export obfuscation)."""
    out = bytearray(body)
    add = len_entries & 0xFF
    for i in range(6, len(out)):
        out[i] ^= (_PSSI_MASK[(i - 6) % len(_PSSI_MASK)] + add) & 0xFF
    return bytes(out)


def _phrases(a: Analysis, body: bytes) -> None:
    """Parse a PSSI song-structure tag (crate-digger / Deep Symmetry layout).

    ``body`` starts at tag+12: ``len_entry_bytes``, ``len_entries``, then
    (for rekordbox 6+) XOR-masked mood / bank / entries. Entry size is 24.
    """
    if len(body) < 20:
        return
    entry_bytes = _u4(body, 0)
    len_entries = struct.unpack_from(">H", body, 4)[0]
    if entry_bytes < 6 or len_entries <= 0:
        return

    # RB5 writes the tag in the clear; RB6+ XOR-masks everything after len_entries.
    mood = struct.unpack_from(">H", body, 6)[0]
    clear = body
    if mood not in (1, 2, 3):
        clear = _unmask_pssi(body, len_entries)
        mood = struct.unpack_from(">H", clear, 6)[0]
    if mood not in (1, 2, 3):
        return

    labels = _PHRASE_MID if mood == 2 else _PHRASE_LOW
    # Entries begin at absolute tag offset 0x20 → body offset 0x14.
    pos = 0x14
    a.phrases.clear()
    for _ in range(len_entries):
        if pos + entry_bytes > len(clear):
            break
        # index (unused), beat, kind — then k1 @ +7, k2 @ +9, k3 @ +19
        _index, beat, kind = struct.unpack_from(">HHH", clear, pos)
        if mood == 1:
            k1 = clear[pos + 7]
            k2 = clear[pos + 9]
            k3 = clear[pos + 19] if entry_bytes > 19 else 0
            label = _high_phrase_label(kind, k1, k2, k3)
        else:
            label = labels.get(kind, f"Phrase {kind}")
        a.phrases.append(Phrase(beat, kind, label))
        pos += entry_bytes

# ------------------------------------------------------------------- decoding

# WaveformDetail.COLOR_MAP: the eight blue-waveform shades, indexed by bits 5-7.
BLUE_COLOR_MAP = (
    (0, 104, 144),
    (0, 136, 176),
    (0, 168, 232),
    (0, 184, 216),
    (120, 184, 216),
    (136, 192, 232),
    (136, 192, 232),
    (200, 224, 232),
)


def _jround(value: float) -> int:
    """Math.round for a non-negative value."""
    return int(value + 0.5)


def _decode_color_detail_py(data: bytes) -> tuple[bytearray, bytearray]:
    """Colour detail waveform (PWV5), as Beat Link ``WaveformDetail`` RGB.

    Each column is one big-endian 16-bit word. Height is bits 2-6 (0-31).
    ``segmentColor`` reads the three 3-bit fields and passes them to
    ``new Color(r, g, b)`` as:

        red   = bits 13-15
        green = bits 7-9
        blue  = bits 10-12

    each scaled by 255/7. Returns (heights 0-31, interleaved RGB 0-255).
    """
    n = len(data) // 2
    heights = bytearray(n)
    rgb = bytearray(n * 3)
    for i in range(n):
        x = (data[i * 2] << 8) | data[i * 2 + 1]
        heights[i] = (x >> 2) & 0x1F
        red = (x >> 13) & 0x07
        green = (x >> 7) & 0x07
        blue = (x >> 10) & 0x07
        rgb[i * 3] = red * 255 // 7
        rgb[i * 3 + 1] = green * 255 // 7
        rgb[i * 3 + 2] = blue * 255 // 7
    return heights, rgb


def _decode_blue_colors_py(data: bytes) -> tuple[bytearray, bytearray]:
    """Blue detail (PWV3): height in bits 0-4, shade in bits 5-7.

    The shade indexes ``WaveformDetail.COLOR_MAP``.
    """
    n = len(data)
    heights = bytearray(n)
    rgb = bytearray(n * 3)
    for i, b in enumerate(data):
        heights[i] = b & 0x1F
        color = BLUE_COLOR_MAP[(b >> 5) & 0x07]
        rgb[i * 3:i * 3 + 3] = bytes(color)
    return heights, rgb


# ---------------------------------------------------------- numpy fast paths
# numpy is optional (it ships with the OneLibrary extras). Each fast path is
# byte-identical to the pure-Python decoder above; tests compare them.
try:  # pragma: no cover - depends on the environment
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

_NP_MIN = 64        # tiny inputs are faster in pure Python


def _decode_color_detail_np(data: bytes) -> tuple[bytearray, bytearray]:
    n = len(data) // 2
    x = _np.frombuffer(data, dtype=">u2", count=n).astype(_np.int32)
    heights = ((x >> 2) & 0x1F).astype(_np.uint8)
    rgb = _np.empty((n, 3), dtype=_np.int32)
    rgb[:, 0] = (x >> 13) & 0x07
    rgb[:, 1] = (x >> 7) & 0x07
    rgb[:, 2] = (x >> 10) & 0x07
    rgb = (rgb * 255 // 7).astype(_np.uint8)
    return bytearray(heights.tobytes()), bytearray(rgb.tobytes())


_BLUE_LUT = None


def _decode_blue_colors_np(data: bytes) -> tuple[bytearray, bytearray]:
    global _BLUE_LUT
    if _BLUE_LUT is None:
        _BLUE_LUT = _np.array(BLUE_COLOR_MAP, dtype=_np.uint8)
    b = _np.frombuffer(data, dtype=_np.uint8)
    heights = b & 0x1F
    rgb = _BLUE_LUT[(b >> 5) & 0x07]
    return bytearray(heights.tobytes()), bytearray(rgb.tobytes())


def _pick(fast, slow, data):
    if _np is not None and len(data) >= _NP_MIN:
        try:
            return fast(bytes(data))
        except Exception:  # pragma: no cover - fall back on any numpy surprise
            pass
    return slow(data)


def decode_color_detail(data: bytes) -> tuple[bytearray, bytearray]:
    """PWV5 colour detail; see :func:`_decode_color_detail_py`. numpy when available."""
    return _pick(_decode_color_detail_np, _decode_color_detail_py, data)


def decode_blue_colors(data: bytes) -> tuple[bytearray, bytearray]:
    """PWV3 blue detail; see :func:`_decode_blue_colors_py`. numpy when available."""
    return _pick(_decode_blue_colors_np, _decode_blue_colors_py, data)


def vocal_spans(analysis: Analysis, duration_ms: float | None = None) -> list[dict]:
    """Vocal regions for the strip. Prefers the whole-track PWV6 preview.

    The detection rule is adapted from chrisle/alphatheta-connect (MIT):
    a column is vocal when mid is above its threshold and low and high stay
    under theirs. Byte order follows the pwv6_order / pwv7_order setting.
    """
    from .vocal import VocalThresholds, vocal_regions

    if not analysis.has_vocals:
        return []
    band = analysis.band3_preview or analysis.band3_detail
    order = BAND_ORDERS[_pwv6_order if analysis.band3_preview else _pwv7_order]
    dur = analysis.duration_ms if duration_ms is None else duration_ms
    return vocal_regions(
        band,
        VocalThresholds(analysis.vocal_low, analysis.vocal_mid, analysis.vocal_high),
        float(dur or 0),
        order,
    )


log = logging.getLogger("prolink.anlz")

# ---------------------------------------------------------------- 3-band order
# Byte order of one 3-byte PWV7 / PWV6 entry, as (low, mid, high) indices.
# Default low, mid, high for both tags: rbxport reads and writes that order
# and measured it against rekordbox's own files (PWV4's bass channel tracks
# PWV6 byte 0 at r = 0.83-0.96; the last byte is the quiet high band), and
# OneLibraryCompanion reads PWV7 the same way. The Deep Symmetry spec and
# Beat Link say mid, high, low; that stays an option. Kick drums should draw
# blue: flip ``pwv7_order`` / ``pwv6_order`` in Settings if they do not.
BAND_ORDERS = {
    "low_mid_high": (0, 1, 2),   # rbxport / OneLibraryCompanion (default)
    "mid_high_low": (2, 0, 1),   # Deep Symmetry spec / Beat Link
}
DEFAULT_PWV7_ORDER = "low_mid_high"
DEFAULT_PWV6_ORDER = "low_mid_high"
_pwv7_order = DEFAULT_PWV7_ORDER
_pwv6_order = DEFAULT_PWV6_ORDER


def normalize_band_order(name) -> str:
    name = str(name or "").lower()
    return name if name in BAND_ORDERS else DEFAULT_PWV7_ORDER


def pwv7_order() -> str:
    return _pwv7_order


def set_pwv7_order(name) -> str:
    """Select the PWV7 byte order. Returns the order now in use (and logs it)."""
    global _pwv7_order
    order = normalize_band_order(name)
    if order != _pwv7_order:
        _pwv7_order = order
        log.info("PWV7 3-band byte order: %s", order)
    return order


def pwv6_order() -> str:
    return _pwv6_order


def set_pwv6_order(name) -> str:
    """Select the PWV6 (overview) byte order. Returns the order in use (and logs it)."""
    global _pwv6_order
    order = normalize_band_order(name)
    if order != _pwv6_order:
        _pwv6_order = order
        log.info("PWV6 3-band byte order: %s", order)
    return order


def _band_index(order: str | None) -> tuple[int, int, int]:
    return BAND_ORDERS[normalize_band_order(order or _pwv7_order)]


def split_bands(data: bytes, order: str | None = None):
    """Raw (low, mid, high) byte lanes of 3-byte entries in ``order``."""
    li, mi, hi = _band_index(order)
    return data[li::3][: len(data) // 3], data[mi::3][: len(data) // 3], data[hi::3][: len(data) // 3]


# Per-band detail scales, tied to the byte order they were derived for.
# Beat Link's (low x0.4, mid x0.3, high x0.06) only make sense with its
# mid, high, low reading. rbxport draws every band as value / 127 of the
# half height, so the low, mid, high order keeps the bytes as they are.
PWV7_SCALES = {
    "low_mid_high": (1.0, 1.0, 1.0),
    "mid_high_low": (0.4, 0.3, 0.06),
}


def decode_3band_heights(data: bytes, order: str | None = None) -> tuple[bytearray, bytearray, bytearray]:
    """PWV7 column heights per band, in the detail display scale.

    Bytes are split with the configured ``pwv7_order`` and scaled with that
    order's ``PWV7_SCALES``. The painters normalise to the track's peak.
    """
    order = normalize_band_order(order or _pwv7_order)
    low_b, mid_b, high_b = split_bands(data, order)
    sl, sm, sh = PWV7_SCALES[order]
    n = len(low_b)
    lows, mids, highs = bytearray(n), bytearray(n), bytearray(n)
    for i in range(n):
        lows[i] = min(255, _jround(low_b[i] * sl))
        mids[i] = min(255, _jround(mid_b[i] * sm))
        highs[i] = min(255, _jround(high_b[i] * sh))
    return lows, mids, highs


def decode_3band(data: bytes, order: str | None = None) -> tuple[bytearray, bytearray]:
    """RGB fallback when a track has PWV7 but no PWV5.

    The column colour is mixed from the band amplitudes and the height is
    their peak, scaled to 0-31.
    """
    low_b, mid_b, high_b = split_bands(data, order)
    n = len(low_b)
    heights = bytearray(n)
    rgb = bytearray(n * 3)
    for i in range(n):
        lo, md, hi = low_b[i], mid_b[i], high_b[i]
        peak = max(lo, md, hi)
        heights[i] = min(31, peak * 31 // 255)
        if peak:
            rgb[i * 3] = hi * 255 // peak
            rgb[i * 3 + 1] = md * 255 // peak
            rgb[i * 3 + 2] = lo * 255 // peak
    return heights, rgb


# PWV6 stack scales (rbxport's fit against rekordbox captures): a band's
# height is value / scale of the full stack, low and high over 128, mid over
# 256. Values are 7-bit.
PWV6_STACK_SCALE = (128.0, 256.0, 128.0)


def stacked_overview(data: bytes, width: int, order: str | None = None):
    """3-band overview from PWV6, stacked bottom-up.

    Returns (low, mid, high) contribution lanes of ``width`` columns in
    0-255 of the full stack height: low/128, mid/256, high/128, clipped at
    the top like rekordbox. Each output column is the source column with the
    largest stacked total in its range (max pooling), so transients survive.
    Upsampling repeats the nearest column.
    """
    low_b, mid_b, high_b = split_bands(data, order or _pwv6_order)
    n = len(low_b)
    out = bytearray(width), bytearray(width), bytearray(width)
    if n == 0 or width <= 0:
        return out
    sl, sm, sh = PWV6_STACK_SCALE
    lo_c = [b / sl for b in low_b]
    md_c = [b / sm for b in mid_b]
    hi_c = [b / sh for b in high_b]
    totals = [lo_c[i] + md_c[i] + hi_c[i] for i in range(n)]
    scale = 255.0
    for x in range(width):
        a = (x * n) // width
        b = max(a + 1, ((x + 1) * n) // width)
        best = max(range(a, min(n, b)), key=totals.__getitem__)
        l = int(lo_c[best] * scale + 0.5)
        m = int(md_c[best] * scale + 0.5)
        h = int(hi_c[best] * scale + 0.5)
        # Clip at the top of the strip, taking from the top layer first.
        l = min(l, 255)
        m = min(m, 255 - l)
        h = min(h, 255 - l - m)
        out[0][x], out[1][x], out[2][x] = l, m, h
    return out


def waveform_levels(a: Analysis):
    """Decode one analysis the way Beat Link's ``WaveformDetail`` does.

    Returns ``(heights, rgb, lows, mids, highs, blue_h, blue_rgb)``.

    * ``heights`` / ``rgb`` are the RGB waveform (PWV5, else PWV7, else PWV3).
    * ``lows`` / ``mids`` / ``highs`` are the 3-band pixel heights from PWV7.
      They are empty when the track has no three-band data.
    * ``blue_h`` / ``blue_rgb`` are the classic blue waveform (PWV3 and
      ``COLOR_MAP``). When PWV3 is missing they repeat the RGB heights in a
      single blue shade so the Blue switch still has a shape to draw.
    """
    empty = bytearray()
    if a.color_detail:
        heights, rgb = decode_color_detail(a.color_detail)
    elif a.band3_detail:
        heights, rgb = decode_3band(a.band3_detail)
    elif a.detail:
        heights, rgb = decode_blue_colors(a.detail)
    else:
        heights, rgb = empty, empty

    if a.band3_detail:
        lows, mids, highs = decode_3band_heights(a.band3_detail)
    else:
        n = len(heights)
        lows, mids, highs = bytearray(n), bytearray(n), bytearray(n)

    if a.detail:
        blue_h, blue_rgb = decode_blue_colors(a.detail)
    elif heights:
        blue_h = bytearray(heights)
        blue_rgb = bytearray(len(heights) * 3)
        shade = bytes(BLUE_COLOR_MAP[2])
        for i in range(len(heights)):
            blue_rgb[i * 3:i * 3 + 3] = shade
    else:
        blue_h, blue_rgb = empty, empty

    return heights, rgb, lows, mids, highs, blue_h, blue_rgb


def downsample_bands(lows, mids, highs, width: int):
    """Peak-preserve three band lanes down to `width` columns."""
    n = len(lows)
    out_l, out_m, out_h = bytearray(width), bytearray(width), bytearray(width)
    if n == 0:
        return out_l, out_m, out_h
    step = n / width
    for i in range(width):
        lo = int(i * step)
        hi = max(lo + 1, min(n, int((i + 1) * step)))
        pl = pm = ph = 0
        for j in range(lo, hi):
            if lows[j] > pl:
                pl = lows[j]
            if mids[j] > pm:
                pm = mids[j]
            if highs[j] > ph:
                ph = highs[j]
        out_l[i] = pl
        out_m[i] = pm
        out_h[i] = ph
    return out_l, out_m, out_h


def downsample(heights, rgb, width: int) -> tuple[bytearray, bytearray]:
    """Reduce a detail waveform to the requested number of columns.

    Takes the peak of each window rather than the mean, so the overview keeps
    the real shape of the track, and averages the colour over that same window.
    """
    n = len(heights)
    out_h = bytearray(width)
    out_rgb = bytearray(width * 3)
    if n == 0:
        return out_h, out_rgb
    step = n / width
    for i in range(width):
        lo = int(i * step)
        hi = max(lo + 1, min(n, int((i + 1) * step)))
        peak = 0
        r = g = b = 0
        for j in range(lo, hi):
            if heights[j] > peak:
                peak = heights[j]
            r += rgb[j * 3]
            g += rgb[j * 3 + 1]
            b += rgb[j * 3 + 2]
        count = hi - lo
        out_h[i] = peak
        out_rgb[i * 3] = r // count
        out_rgb[i * 3 + 1] = g // count
        out_rgb[i * 3 + 2] = b // count
    return out_h, out_rgb
