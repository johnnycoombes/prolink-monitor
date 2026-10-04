"""Floating now playing helpers."""

from __future__ import annotations

import unittest

from gui.floating_now import pick_now_deck, webengine_available
from gui.overlay_url import build_floating_overlay_url, build_floating_overlay_file_url
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


class FloatingOverlayUrlTests(unittest.TestCase):
    def test_build_floating_url_embed_and_mix_off(self):
        settings = {
            **load_settings(),
            "overlay_now_style": "card",
            "overlay_now_pos": "left",
            "overlay_waveform_style": "rgb",
        }
        url = build_floating_overlay_url(9876, settings)
        self.assertIn("layout=nowplaying", url)
        self.assertIn("embed=1", url)
        self.assertIn("mix=0", url)
        self.assertIn("next=0", url)
        self.assertIn("style=card", url)
        self.assertIn("pos=left", url)
        self.assertIn(":9876/overlay", url)

    def test_file_url_for_mock_screenshots(self):
        settings = {**load_settings(), "overlay_now_style": "panel", "overlay_now_pos": "bottom"}
        url = build_floating_overlay_file_url(settings)
        self.assertIn("overlay.html", url)
        self.assertIn("mock=1", url)
        self.assertIn("embed=1", url)
        self.assertIn("style=panel", url)

    def test_webengine_flag(self):
        # Addons installed in dev/CI for floating window; flag reflects import success.
        self.assertIsInstance(webengine_available(), bool)


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
