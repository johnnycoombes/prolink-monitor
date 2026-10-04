"""Now Playing overlay style / position helpers."""

from __future__ import annotations

NOW_STYLES = ("card", "panel")
CARD_CORNERS = ("tl", "tr", "bl", "br")
PANEL_POSITIONS = ("bottom", "top")
# Back-compat aliases from the original left/right card placement.
_CARD_ALIASES = {"left": "bl", "right": "br"}


def normalize_now_style(value: str | None) -> str:
    s = str(value or "card").lower()
    return "panel" if s == "panel" else "card"


def normalize_card_corner(value: str | None) -> str:
    raw = str(value or "bl").lower()
    raw = _CARD_ALIASES.get(raw, raw)
    return raw if raw in CARD_CORNERS else "bl"


def normalize_now_pos(style: str, value: str | None) -> str:
    """Return a valid position for the given now-playing style."""
    style = normalize_now_style(style)
    if style == "panel":
        raw = str(value or "bottom").lower()
        return "top" if raw == "top" else "bottom"
    return normalize_card_corner(value)


def card_corner_to_legacy_pos(corner: str) -> str:
    """Map corner to left/right for older consumers (bottom corners only)."""
    c = normalize_card_corner(corner)
    return "right" if c.endswith("r") else "left"


def migrate_now_pos_for_style(old_style: str, new_style: str, pos: str) -> str:
    """Map position when switching overlay style in the UI."""
    old_style = normalize_now_style(old_style)
    new_style = normalize_now_style(new_style)
    if old_style == new_style:
        return normalize_now_pos(new_style, pos)
    p = str(pos or "").lower()
    if new_style == "panel":
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
