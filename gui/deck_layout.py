"""Geometry shared by the desktop Monitor cards and the web panel.

``web/index.html`` keeps a JSON copy (``#monitor-layout``). The panel applies
those numbers as CSS variables, and ``tests/test_spectrum_and_panel.py``
fails if that copy drifts from the constants here. Each surface still paints
with its own toolkit (Qt versus canvas); the card measurements stay one set.
"""

from __future__ import annotations

# Left identity column: artwork plus the title block. Tags sit under that.
IDENT_WIDTH = 300
# Deck number only, once artwork / title / artist / meta / tags are hidden.
IDENT_COLLAPSED = 44
# Right readout column: wide enough for "174.00 BPM" at 28px monospace.
READOUT_PANEL_MIN = 188
READOUT_PANEL_MAX = 220

ART_PX = 72
ART_RADIUS = 4
ACCENT_PX = 4
CARD_RADIUS = 6
CARD_GAP = 8
# left, top, right, bottom — Monitor page margins around the deck stack.
PAGE_MARGINS = (20, 14, 20, 12)
# left, top, right, bottom — padding inside the card, beside the accent bar.
BODY_MARGINS = (12, 10, 12, 10)
BODY_GAP = 12

PHRASE_H = 16
OVERVIEW_FRAC = 0.14
OVERVIEW_MIN = 28
OVERVIEW_MAX = 48

BPM_PX = 28
PITCH_PX = 12
TIME_PX = 15
PHASE_W = 12
PHASE_H = 4

# Visual stack order (Pioneer-style): 2-deck is 1–2, 4-deck is 3–1–2–4.
DECK_ORDER = {
    2: (1, 2),
    4: (3, 1, 2, 4),
}


def overview_height(wave_h: int) -> int:
    """Overview band height for a waveform column of ``wave_h`` pixels."""
    return max(OVERVIEW_MIN, min(OVERVIEW_MAX, int(wave_h * OVERVIEW_FRAC)))
