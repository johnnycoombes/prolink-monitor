"""Decide which deck-card lines fit before Qt paints them.

The Monitor used to scale type with a floor and then let the layout squash
whatever was left. On a short row the title wrapped over the artist, and the
readout column drew BPM-sized glyphs into a few pixels. These helpers measure
first: one title line (two only on a tall row), the artist always, then extra
lines in order while the budget lasts.
"""

from __future__ import annotations

import math
import os

# Two title lines only once the row can keep the artist under them.
TALL_ROW = 96

TITLE_MIN = 10
ARTIST_MIN = 9
DETAIL_MIN = 9
NUM_MIN = 9
BPM_MIN = 15
PITCH_MIN = 11
TIME_MIN = 12
META_MIN = 11

SCALE_MIN = 0.72
SCALE_MAX = 1.45

# Drop from the end of this list when the readout column runs out of room.
# BPM, pitch, and the clock stay until the lines above them are gone.
READOUT_DROP = ("next", "phrase", "state", "tags")


def row_scale(row_h: int) -> float:
    """Type scale for a row. Short rows go below the old 0.9 floor."""
    if row_h <= 0:
        return SCALE_MIN
    return max(SCALE_MIN, min(SCALE_MAX, row_h / 240.0))


def art_side(row_h: int, *, art_max: int = 72, margin_y: int = 20) -> int:
    """Artwork edge, capped so a short row is not a 72px thumbnail."""
    inner = max(0, int(row_h) - margin_y)
    return max(28, min(int(art_max), int(round(inner * 0.58))))


def title_line_count(row_h: int) -> int:
    return 2 if int(row_h) >= TALL_ROW else 1


def effective_dpr(widget_dpr: float, scale_env: str | None = None) -> float:
    """DPR the packer should ceil line boxes to.

    ``QT_SCALE_FACTOR`` is applied by the platform and does not always show up
    on ``devicePixelRatioF``. Fractional scales (125%, 150%) then round a line
    box down and the next glyph paints over it.
    """
    dpr = float(widget_dpr or 1.0)
    if scale_env is None:
        scale_env = os.environ.get("QT_SCALE_FACTOR") or ""
    try:
        scale = float(str(scale_env).strip()) if str(scale_env).strip() else 1.0
    except ValueError:
        scale = 1.0
    return max(1.0, dpr, scale)


def snap_line(logical: float, dpr: float) -> int:
    """Smallest integer logical height that covers ``logical`` at ``dpr``."""
    dpr = dpr if dpr and dpr > 0 else 1.0
    device = math.ceil(float(logical) * dpr - 1e-6)
    return max(1, math.ceil(device / dpr - 1e-6))


def choose_optional(budget: float, lines: list[tuple[str, float]]) -> list[str]:
    """Keep ``lines`` in order while their heights still fit in ``budget``."""
    chosen: list[str] = []
    used = 0.0
    slack = 0.51
    for name, height in lines:
        if height <= 0:
            continue
        if used + float(height) <= float(budget) + slack:
            chosen.append(name)
            used += float(height)
    return chosen


def elide_lines(text: str, fm, width: int, lines: int) -> str:
    """Wrap ``text`` into at most ``lines``, ellipsizing the last one."""
    from PySide6.QtCore import Qt

    raw = " ".join(str(text or "").split())
    width = int(width)
    lines = max(1, int(lines))
    if not raw or width <= 8:
        return ""
    if lines == 1 or fm.horizontalAdvance(raw) <= width:
        return fm.elidedText(raw, Qt.TextElideMode.ElideRight, width)
    words = raw.split(" ")
    rows: list[str] = []
    index = 0
    while index < len(words) and len(rows) < lines:
        if len(rows) == lines - 1:
            rows.append(fm.elidedText(
                " ".join(words[index:]), Qt.TextElideMode.ElideRight, width,
            ))
            break
        current = words[index]
        index += 1
        while index < len(words) and fm.horizontalAdvance(current + " " + words[index]) <= width:
            current = current + " " + words[index]
            index += 1
        if fm.horizontalAdvance(current) > width:
            current = fm.elidedText(current, Qt.TextElideMode.ElideRight, width)
        rows.append(current)
    return "\n".join(rows)
