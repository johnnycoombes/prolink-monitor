"""Zoom maths for the scrolling detail waveform.

The visible window is a number of seconds between ``MIN_SECONDS`` and
``MAX_SECONDS``. A wheel notch (120 units) multiplies or divides it by
``STEP``, so zoom feels the same at every level. Every overlay on the strip
(loop, cues, beats, phrase strip) maps time to x with ``x_for_ms`` from the
same window, so they stay aligned at any zoom. ``web/wavepack.js`` mirrors
these functions; ``tests/test_wave_zoom.py`` checks both agree.
"""

from __future__ import annotations

import math

MIN_SECONDS = 2.0
MAX_SECONDS = 30.0
STEP = 1.15          # per 120-unit wheel notch


def clamp_seconds(seconds: float) -> float:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return MIN_SECONDS
    if not math.isfinite(s):
        return MIN_SECONDS
    return max(MIN_SECONDS, min(MAX_SECONDS, s))


def wheel_zoom(seconds: float, delta: float) -> float:
    """New window after a wheel delta (positive = wheel up = zoom in)."""
    return clamp_seconds(clamp_seconds(seconds) * STEP ** (-float(delta) / 120.0))


def pinch_zoom(seconds: float, start_distance: float, distance: float) -> float:
    """New window after a pinch (fingers apart = zoom in)."""
    if start_distance <= 0 or distance <= 0:
        return clamp_seconds(seconds)
    return clamp_seconds(seconds * start_distance / distance)


def x_for_ms(t_ms: float, pos_ms: float, visible_s: float, width: float,
             playhead_frac: float = 0.5) -> float:
    """X of track time ``t_ms`` when ``pos_ms`` is under the playhead."""
    return width * playhead_frac + (t_ms - pos_ms) / (visible_s * 1000.0) * width


def ms_for_x(x: float, pos_ms: float, visible_s: float, width: float,
             playhead_frac: float = 0.5) -> float:
    return pos_ms + (x - width * playhead_frac) / width * visible_s * 1000.0


def clean_zoom_seconds(raw) -> dict[str, float]:
    """Per-deck saved zoom: {"1": 6.5, ...}, clamped, junk dropped."""
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        try:
            deck = int(key)
            sec = float(value)
        except (TypeError, ValueError):
            continue
        if 1 <= deck <= 6 and math.isfinite(sec):
            out[str(deck)] = round(clamp_seconds(sec), 3)
    return out
