"""Waveform style helpers shared by the desktop app and the web server.

* The 3-band palettes live in ``web/wavepalette.json``. This module is the
  Python reader for that file; the web pages get the same JSON injected.
* ``resolve_style`` turns the user's style choice (which may be ``auto``) and
  the player's reported waveform colour (status byte 0xDA) into the style to
  draw.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

WAVE_STYLES = ("rgb", "3band", "blue")
STYLE_CHOICES = ("auto",) + WAVE_STYLES
PALETTES = ("beatlink", "cdj3000")
DEFAULT_PALETTE = "beatlink"
# Mask order used by the "mask" palette mode (low=1, mid=2, high=4).
MASK_KEYS = ("low", "mid", "high", "low_mid", "low_high", "mid_high", "all")
MASK_BITS = (1, 2, 4, 3, 5, 6, 7)

# Status byte 0xDA (waveform colour) to style.
PLAYER_COLOR_BYTES = {0x01: "blue", 0x03: "rgb", 0x04: "3band"}

from .bundle import bundle_root

PALETTE_PATH = os.path.join(bundle_root(), "web", "wavepalette.json")


@lru_cache(maxsize=1)
def palette_json() -> str:
    with open(PALETTE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def palettes() -> dict:
    return json.loads(palette_json())["palettes"]


def normalize_palette(name) -> str:
    name = str(name or "").lower()
    return name if name in PALETTES else DEFAULT_PALETTE


def palette(name) -> dict:
    return palettes()[normalize_palette(name)]


def hex_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def normalize_style(style, *, allow_auto: bool = True, default: str = "rgb") -> str:
    s = str(style or "").lower()
    choices = STYLE_CHOICES if allow_auto else WAVE_STYLES
    return s if s in choices else default


def style_for_player_byte(raw) -> str | None:
    try:
        return PLAYER_COLOR_BYTES.get(int(raw))
    except (TypeError, ValueError):
        return None


def resolve_style(choice, player_color=None, fallback: str = "rgb") -> str:
    """The style to draw for one deck.

    A fixed choice wins. ``auto`` follows the player's 0xDA report
    (``blue``/``rgb``/``3band``), else the user's fallback choice.
    """
    choice = normalize_style(choice)
    if choice != "auto":
        return choice
    if player_color in WAVE_STYLES:
        return player_color
    return normalize_style(fallback, allow_auto=False)
