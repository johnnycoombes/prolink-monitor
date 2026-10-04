"""Now playing overlay style / position helpers."""

from __future__ import annotations

import unittest

from gui.overlay_now import (
    migrate_now_pos_for_style,
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

    def test_normalize_pos_card(self):
        self.assertEqual(normalize_now_pos("card", "right"), "right")
        self.assertEqual(normalize_now_pos("card", "top"), "left")

    def test_normalize_pos_panel(self):
        self.assertEqual(normalize_now_pos("panel", "top"), "top")
        self.assertEqual(normalize_now_pos("panel", "left"), "bottom")

    def test_migrate_card_to_panel(self):
        self.assertEqual(migrate_now_pos_for_style("card", "panel", "right"), "top")
        self.assertEqual(migrate_now_pos_for_style("card", "panel", "left"), "bottom")

    def test_migrate_panel_to_card(self):
        self.assertEqual(migrate_now_pos_for_style("panel", "card", "top"), "right")
        self.assertEqual(migrate_now_pos_for_style("panel", "card", "bottom"), "left")


class OverlayNowSettingsTests(unittest.TestCase):
    def test_sanitize_style_and_panel_pos(self):
        base = load_settings()
        clean = _sanitize({**base, "overlay_now_style": "panel", "overlay_now_pos": "TOP"})
        self.assertEqual(clean["overlay_now_style"], "panel")
        self.assertEqual(clean["overlay_now_pos"], "top")
        clean = _sanitize({**base, "overlay_now_style": "card", "overlay_now_pos": "bottom"})
        self.assertEqual(clean["overlay_now_pos"], "left")


if __name__ == "__main__":
    unittest.main()
