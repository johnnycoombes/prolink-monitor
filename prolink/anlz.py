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

    @property
    def duration_ms(self) -> int:
        return self.beats[-1].time if self.beats else 0


def _u4(b: bytes, off: int) -> int:
    return struct.unpack_from(">I", b, off)[0]


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


_PHRASE_LABELS = {
    1: {1: "Intro", 2: "Verse 1", 3: "Verse 1", 4: "Verse 1", 5: "Verse 2",
        6: "Verse 2", 7: "Verse 2", 8: "Bridge", 9: "Chorus", 10: "Outro"},
    2: {1: "Intro", 2: "Verse 1", 3: "Verse 2", 4: "Verse 3", 5: "Verse 4",
        6: "Verse 5", 7: "Verse 6", 8: "Bridge", 9: "Chorus", 10: "Outro"},
    3: {1: "Intro 1", 2: "Intro 2", 3: "Up 1", 4: "Up 2", 5: "Up 3", 6: "Down",
        7: "Chorus 1", 8: "Chorus 2", 9: "Outro 1", 10: "Outro 2"},
}


def _phrases(a: Analysis, body: bytes) -> None:
    mood = _u4(body, 4)
    count = struct.unpack_from(">H", body, 8)[0]
    labels = _PHRASE_LABELS.get(mood, {})
    pos = 16
    for _ in range(count):
        if pos + 4 > len(body):
            break
        beat, kind = struct.unpack_from(">HH", body, pos)
        a.phrases.append(Phrase(beat, kind, labels.get(kind, f"Phrase {kind}")))
        pos += 0x10


# ------------------------------------------------------------------- decoding

def decode_blue(data: bytes) -> tuple[bytes, bytes]:
    """Classic blue waveform (PWAV/PWV3): returns (heights 0-31, whiteness 0-7)."""
    heights = bytes(b & 0x1F for b in data)
    whiteness = bytes((b >> 5) & 0x07 for b in data)
    return heights, whiteness


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


def decode_color_detail(data: bytes) -> tuple[bytearray, bytearray]:
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


def decode_color_preview(data: bytes) -> tuple[bytearray, bytearray]:
    """Colour overview (PWV4, 6 bytes), as Beat Link ``WaveformPreview`` RGB.

    Bytes 3, 4 and 5 are the red, green and blue levels. The front height is
    byte 5; the back height is the max of the three. The front colour is each
    channel times 255 / back height. Heights are scaled into 0-31.
    """
    n = len(data) // 6
    heights = bytearray(n)
    rgb = bytearray(n * 3)
    for i in range(n):
        base = i * 6
        red_b, green_b, blue_b = data[base + 3], data[base + 4], data[base + 5]
        back = max(red_b, green_b, blue_b)
        heights[i] = min(31, back * 31 // 255) if back else 0
        if back:
            rgb[i * 3] = red_b * 255 // back
            rgb[i * 3 + 1] = green_b * 255 // back
            rgb[i * 3 + 2] = blue_b * 255 // back
    return heights, rgb


def decode_blue_colors(data: bytes) -> tuple[bytearray, bytearray]:
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


def decode_3band_heights(data: bytes) -> tuple[bytearray, bytearray, bytearray]:
    """PWV7 column heights, as ``WaveformDetail.segmentHeight`` for each band.

    Byte order in the file is mid, high, low. Beat Link scales them
    independently (these are pixel heights, not 0-31):

        low  = round(byte2 * 0.4)
        mid  = round(byte0 * 0.3)
        high = round(byte1 * 0.06)
    """
    n = len(data) // 3
    lows, mids, highs = bytearray(n), bytearray(n), bytearray(n)
    for i in range(n):
        mid_b = data[i * 3]
        high_b = data[i * 3 + 1]
        low_b = data[i * 3 + 2]
        lows[i] = min(255, _jround(low_b * 0.4))
        mids[i] = min(255, _jround(mid_b * 0.3))
        highs[i] = min(255, _jround(high_b * 0.06))
    return lows, mids, highs


def decode_3band(data: bytes) -> tuple[bytearray, bytearray]:
    """RGB fallback when a track has PWV7 but no PWV5.

    Uses the same mid/high/low byte order. The column colour is mixed from
    those amplitudes and the height is their peak, scaled to 0-31.
    """
    n = len(data) // 3
    heights = bytearray(n)
    rgb = bytearray(n * 3)
    for i in range(n):
        mid_b, high_b, low_b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
        peak = max(low_b, mid_b, high_b)
        heights[i] = min(31, peak * 31 // 255)
        if peak:
            rgb[i * 3] = high_b * 255 // peak
            rgb[i * 3 + 1] = mid_b * 255 // peak
            rgb[i * 3 + 2] = low_b * 255 // peak
    return heights, rgb


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
