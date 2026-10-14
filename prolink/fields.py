"""Track text for the Monitor cards and the web panel.

Rekordbox stores an empty colour, comment, or label as the literal ``(blank)``.
That placeholder is not a value, so the deck rows leave the field out.
"""

from __future__ import annotations

# Names rekordbox writes when the cell is empty. Case-insensitive.
_PLACEHOLDERS = {
    "(blank)",
    "blank",
    "none",
    "n/a",
    "na",
    "-",
    "—",
    "–",
    "null",
}

# Rekordbox colour names that are actually a colour. "(blank)" is not one.
COLOR_SWATCH = {
    "pink": "#ff4fa3",
    "red": "#ff3b30",
    "orange": "#ff9f45",
    "yellow": "#ffd45e",
    "green": "#3ddc84",
    "aqua": "#2ee0d0",
    "blue": "#5eb0ff",
    "purple": "#c084fc",
}


def display_text(value) -> str:
    """Return ``value`` for the UI, or ``""`` when it is empty or a placeholder."""
    text = str(value or "").strip()
    if not text or text.lower() in _PLACEHOLDERS:
        return ""
    return text


def display_year(value) -> str:
    try:
        year = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if year <= 0:
        return ""
    return str(year)


def display_rating(value) -> int:
    """Stars as 0–5. Rekordbox also stores 0–255 (about 51 per star)."""
    try:
        rating = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if rating <= 0:
        return 0
    if rating <= 5:
        return rating
    return max(0, min(5, int(round(rating / 51))))


def rating_stars(value) -> str:
    stars = display_rating(value)
    if stars <= 0:
        return ""
    return "★" * stars + "☆" * (5 - stars)


def color_label(value) -> tuple[str, str]:
    """``(name, swatch_hex)``. Both empty when the colour is a placeholder."""
    name = display_text(value)
    if not name:
        return "", ""
    return name, COLOR_SWATCH.get(name.lower(), "")


_NO_BEAT = 0x7FFFFFFF


def bar_beat(beat: int, beat_in_bar: int = 0) -> tuple[int, int] | None:
    """Absolute ``(bar, beat 1-4)`` from the status beat number (4/4).

    ``beat`` is the CDJ status beat number (1-based index into the track's
    beat grid). ``beat_in_bar`` is the player's own 1-4 counter; when known it
    anchors the downbeat, so the bar flips on the grid's beat 1 even when the
    track's first grid beat is not a downbeat. None when no beat is known.
    """
    try:
        b = int(beat or 0)
        k = int(beat_in_bar or 0)
    except (TypeError, ValueError):
        return None
    if b <= 0 or b >= _NO_BEAT:
        return None
    if 1 <= k <= 4 and b >= k:
        return (b - k) // 4 + 1, k
    return (b - 1) // 4 + 1, (b - 1) % 4 + 1


def bar_beat_text(beat: int, beat_in_bar: int = 0) -> str:
    """``Bar 33.2`` (bar 33, beat 2 of 4), or "" when no beat is known."""
    got = bar_beat(beat, beat_in_bar)
    if got is None:
        return ""
    return f"Bar {got[0]}.{got[1]}"


def beat_phrase_text(bar: int, phrase: str, beat: int = 0) -> str:
    """Bar.beat (or beat-in-bar) and the phrase the playhead is in."""
    parts: list[str] = []
    text = bar_beat_text(beat, bar)
    if text:
        parts.append(text)
    else:
        try:
            k = int(bar or 0)
        except (TypeError, ValueError):
            k = 0
        if 1 <= k <= 4:
            parts.append(f"Beat {k}/4")
    label = display_text(phrase)
    if label:
        parts.append(label)
    return " · ".join(parts)
