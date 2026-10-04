"""Floating now playing helpers."""

from __future__ import annotations

import unittest

from gui.floating_now import pick_now_deck
from gui.settings import load_settings, save_settings, _sanitize


class PickNowDeckTests(unittest.TestCase):
    def test_audience_deck_wins_over_mix(self):
        state = {
            "audience_deck": {"number": 1, "track_id": 5, "title": "Live"},
            "now_playing": {"number": 2, "track_id": 99, "title": "NP"},
            "decks": [
                {"number": 1, "track_id": 5, "playing": True, "bpm": 130.0},
                {"number": 2, "track_id": 99, "playing": True, "bpm": 128.0},
            ],
        }
        deck = pick_now_deck(state)
        self.assertEqual(deck["number"], 1)
        self.assertEqual(deck["track_id"], 5)
        self.assertEqual(deck["bpm"], 130.0)

    def test_falls_back_to_playing_deck(self):
        state = {
            "decks": [
                {"number": 1, "track_id": 5, "playing": True},
                {"number": 2, "track_id": 99, "playing": False},
            ],
        }
        deck = pick_now_deck(state)
        self.assertEqual(deck["number"], 1)


class OverlayNowPosSettingsTests(unittest.TestCase):
    def test_sanitize_now_pos(self):
        clean = _sanitize({**load_settings(), "overlay_now_pos": "RIGHT"})
        self.assertEqual(clean["overlay_now_pos"], "right")
        clean = _sanitize({**load_settings(), "overlay_now_pos": "nope"})
        self.assertEqual(clean["overlay_now_pos"], "left")

    def test_panel_pos_when_style_panel(self):
        base = {**load_settings(), "overlay_now_style": "panel"}
        clean = _sanitize({**base, "overlay_now_pos": "top"})
        self.assertEqual(clean["overlay_now_pos"], "top")
        clean = _sanitize({**base, "overlay_now_pos": "nope"})
        self.assertEqual(clean["overlay_now_pos"], "bottom")


if __name__ == "__main__":
    unittest.main()
