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


def beat_phrase_text(bar: int, phrase: str) -> str:
    """Beat-in-bar and the phrase the playhead is in, when either is known."""
    parts: list[str] = []
    try:
        beat = int(bar or 0)
    except (TypeError, ValueError):
        beat = 0
    if 1 <= beat <= 4:
        parts.append(f"Beat {beat}/4")
    label = display_text(phrase)
    if label:
        parts.append(label)
    return " · ".join(parts)
