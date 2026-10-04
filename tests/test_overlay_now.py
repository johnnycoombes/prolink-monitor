"""Now playing overlay style / position helpers."""

from __future__ import annotations

import unittest

from gui.overlay_now import (
    migrate_now_pos_for_style,
    normalize_card_corner,
    normalize_now_pos,
    normalize_now_style,
)
from gui.settings import _sanitize, load_settings


class OverlayNowHelperTests(unittest.TestCase):
    def test_normalize_style(self):
        self.assertEqual(normalize_now_style("panel"), "panel")
        self.assertEqual(normalize_now_style("PANEL"), "panel")
        self.assertEqual(normalize_now_style("card"), "card")
        self.assertEqual(normalize_now_style(None), "card")

    def test_normalize_pos_card_corners(self):
        self.assertEqual(normalize_now_pos("card", "tr"), "tr")
        self.assertEqual(normalize_now_pos("card", "tl"), "tl")
        self.assertEqual(normalize_now_pos("card", "right"), "br")
        self.assertEqual(normalize_now_pos("card", "left"), "bl")
        self.assertEqual(normalize_card_corner("nope"), "bl")

    def test_normalize_pos_panel(self):
        self.assertEqual(normalize_now_pos("panel", "top"), "top")
        self.assertEqual(normalize_now_pos("panel", "left"), "bottom")

    def test_migrate_card_to_panel(self):
        self.assertEqual(migrate_now_pos_for_style("card", "panel", "tr"), "top")
        self.assertEqual(migrate_now_pos_for_style("card", "panel", "bl"), "bottom")

    def test_migrate_panel_to_card(self):
        self.assertEqual(migrate_now_pos_for_style("panel", "card", "top"), "tr")
        self.assertEqual(migrate_now_pos_for_style("panel", "card", "bottom"), "br")


class OverlayNowSettingsTests(unittest.TestCase):
    def test_sanitize_style_and_panel_pos(self):
        base = load_settings()
        clean = _sanitize({**base, "overlay_now_style": "panel", "overlay_now_pos": "TOP"})
        self.assertEqual(clean["overlay_now_style"], "panel")
        self.assertEqual(clean["overlay_now_pos"], "top")
        clean = _sanitize({**base, "overlay_now_style": "card", "overlay_now_pos": "bottom"})
        self.assertEqual(clean["overlay_now_pos"], "bl")
        clean = _sanitize({**base, "overlay_now_style": "card", "overlay_now_pos": "RIGHT"})
        self.assertEqual(clean["overlay_now_pos"], "br")


if __name__ == "__main__":
    unittest.main()
