"""Now Playing overlay style / position helpers."""

from __future__ import annotations

NOW_STYLES = ("card", "panel")
CARD_POSITIONS = ("left", "right")
PANEL_POSITIONS = ("bottom", "top")


def normalize_now_style(value: str | None) -> str:
    s = str(value or "card").lower()
    return "panel" if s == "panel" else "card"


def normalize_now_pos(style: str, value: str | None) -> str:
    """Return a valid position for the given now-playing style."""
    style = normalize_now_style(style)
    raw = str(value or "").lower()
    if style == "panel":
        return "top" if raw == "top" else "bottom"
    return "right" if raw == "right" else "left"


def migrate_now_pos_for_style(old_style: str, new_style: str, pos: str) -> str:
    """Map position when switching overlay style in the UI."""
    old_style = normalize_now_style(old_style)
    new_style = normalize_now_style(new_style)
    if old_style == new_style:
        return normalize_now_pos(new_style, pos)
    p = str(pos or "").lower()
    if new_style == "panel":
        return "top" if p == "right" else "bottom"
    return "right" if p == "top" else "left"
