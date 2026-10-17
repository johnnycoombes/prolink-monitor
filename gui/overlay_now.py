"""Now Playing overlay style / position helpers."""

from __future__ import annotations

CARD_CORNERS = ("tl", "tr", "bl", "br")
# Back-compat aliases from the original left/right card placement.
_CARD_ALIASES = {"left": "bl", "right": "br"}


def normalize_now_style(value: str | None) -> str:
    """Card, panel (large art + visualiser), or bar (small art strip)."""
    s = str(value or "card").lower()
    if s in ("panel", "bar"):
        return s
    return "card"


def is_strip_style(value: str | None) -> bool:
    """Full-width now-playing layouts: panel and bar."""
    return normalize_now_style(value) in ("panel", "bar")


def normalize_card_corner(value: str | None) -> str:
    raw = str(value or "bl").lower()
    raw = _CARD_ALIASES.get(raw, raw)
    return raw if raw in CARD_CORNERS else "bl"


def normalize_now_pos(style: str, value: str | None) -> str:
    """Return a valid position for the given now-playing style."""
    style = normalize_now_style(style)
    if is_strip_style(style):
        raw = str(value or "bottom").lower()
        return "top" if raw == "top" else "bottom"
    return normalize_card_corner(value)


def migrate_now_pos_for_style(old_style: str, new_style: str, pos: str) -> str:
    """Map position when switching overlay style in the UI."""
    old_style = normalize_now_style(old_style)
    new_style = normalize_now_style(new_style)
    if old_style == new_style or (is_strip_style(old_style) and is_strip_style(new_style)):
        return normalize_now_pos(new_style, pos)
    p = str(pos or "").lower()
    if is_strip_style(new_style):
        if p in ("top", "tr", "tl"):
            return "top"
        return "bottom"
    if p == "top":
        return "tr"
    if p == "bottom":
        return "br"
    if p in CARD_CORNERS:
        return normalize_card_corner(p)
    return normalize_card_corner(p)
