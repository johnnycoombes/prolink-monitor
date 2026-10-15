"""Bar buttons and the wheel share one zoom state (1.0.14)."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _deck(n, bpm=120.0):
    return {"number": n, "track_id": 0, "playing": False, "position_ms": 0,
            "bpm": bpm, "track_bpm": bpm, "speed": 1, "state": "paused", "name": "XDJ-AZ"}


class ZoomMathTests(unittest.TestCase):
    def test_32_bars_and_wheel_range(self):
        from prolink.session import ZOOM_BARS, bars_to_seconds
        from prolink import wavezoom

        self.assertEqual(ZOOM_BARS, (1, 2, 4, 8, 16, 32))
        # 32 bars at 100 BPM (76.8 s) fits inside the wheel range.
        self.assertGreaterEqual(wavezoom.MAX_SECONDS, bars_to_seconds(32, 100))
        self.assertGreaterEqual(wavezoom.MAX_SECONDS, bars_to_seconds(32, 80))
        self.assertEqual(wavezoom.wheel_zoom(90.0, -1200), wavezoom.MAX_SECONDS)

    def test_bars_for_seconds(self):
        from prolink.session import ZOOM_BARS
        from prolink.wavezoom import bars_for_seconds

        self.assertEqual(bars_for_seconds(16.0, 120, ZOOM_BARS), 8)
        self.assertEqual(bars_for_seconds(64.4, 120, ZOOM_BARS), 32)
        self.assertIsNone(bars_for_seconds(11.0, 120, ZOOM_BARS))
        self.assertIsNone(bars_for_seconds(None, 120, ZOOM_BARS))

    def test_web_mirrors_limits(self):
        js = (ROOT / "web" / "wavepack.js").read_text(encoding="utf-8")
        self.assertIn("ZOOM_MAX = 96", js)
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const ZOOM_BARS = [1, 2, 4, 8, 16, 32];", html)
        # A bar change from the desktop drops the browser's wheel zoom.
        self.assertRegex(html, r"zoomSecByDeck\.delete\(d\.n\)")

    def test_settings_keep_32(self):
        from gui.settings import display_prefs_from

        prefs = display_prefs_from({"zoom_bars": 32, "zoom_bars_by_deck": {"2": 32}})
        self.assertEqual(prefs["zoom_bars"], 32)
        self.assertEqual(prefs["zoom_bars_by_deck"], {"2": 32})


class MonitorZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def _page(self):
        from gui.i18n import I18n
        from gui.pages import MonitorPage

        backend = mock.Mock()
        backend.meta.return_value = None
        page = MonitorPage(I18n(), backend)
        page.update_state({"decks": [_deck(1), _deck(2)]})
        return page

    def _lit(self, page):
        return [b for b, w in page._zoom_btns.items() if w.property("active") == "true"]

    def test_toolbar_has_32(self):
        page = self._page()
        self.assertIn(32, page._zoom_btns)
        page.deleteLater()

    def test_button_click_replaces_wheel_zoom(self):
        page = self._page()
        card = page._cards[1]
        card.wave.set_zoom_seconds(30.0)
        card.wave.zoom_seconds_changed.emit(30.0)   # what a wheel notch does
        self.assertEqual(page._zoom_seconds, {1: 30.0})
        self.assertAlmostEqual(card.wave._visible_seconds(), 30.0)

        page._zoom_btns[8].click()
        # 8 bars x 4 beats at 120 BPM = 16 s, on both decks, and saved state forgets the wheel.
        self.assertAlmostEqual(card.wave._visible_seconds(), 16.0)
        self.assertAlmostEqual(page._cards[2].wave._visible_seconds(), 16.0)
        self.assertEqual(page._zoom_seconds, {})
        self.assertEqual(self._lit(page), [8])

        page._zoom_btns[32].click()
        self.assertAlmostEqual(card.wave._visible_seconds(), 64.0)
        self.assertEqual(self._lit(page), [32])
        page.deleteLater()

    def test_highlight_follows_the_wheel(self):
        page = self._page()
        page._zoom_btns[4].click()
        self.assertEqual(self._lit(page), [4])
        for card in page._cards.values():
            card.wave.set_zoom_seconds(11.0)          # between 4 and 8 bars
            card.wave.zoom_seconds_changed.emit(11.0)
        self.assertEqual(self._lit(page), [])
        for card in page._cards.values():
            card.wave.set_zoom_seconds(16.0)          # exactly 8 bars at 120
            card.wave.zoom_seconds_changed.emit(16.0)
        self.assertEqual(self._lit(page), [8])
        self.assertTrue(page._cards[1].zoom_btn.text().startswith("8b"))
        page.deleteLater()

    def test_all_clears_wheel_and_deck_overrides(self):
        page = self._page()
        card = page._cards[2]
        card.wave.set_zoom_seconds(40.0)
        card.wave.zoom_seconds_changed.emit(40.0)
        page.reset_zoom_btn.click()
        self.assertIsNone(card.wave._zoom_seconds)
        self.assertEqual(page._zoom_seconds, {})
        page.deleteLater()

    def test_deck_chip_cycle_clears_wheel(self):
        page = self._page()
        card = page._cards[1]
        card.wave.set_zoom_seconds(25.0)
        card.wave.zoom_seconds_changed.emit(25.0)
        card.zoom_btn.click()
        self.assertIsNone(card.wave._zoom_seconds)
        self.assertNotIn(1, page._zoom_seconds)
        page.deleteLater()


class OverlayOverviewTests(unittest.TestCase):
    def test_panel_and_bar_include_the_overview(self):
        html = (ROOT / "web" / "overlay.html").read_text(encoding="utf-8")
        body = html[html.index("function renderNowPanel"):html.index("function renderNow(")]
        self.assertIn("panel-overview", body)
        self.assertIn("waveHtml(deck.track_id)", body)
        self.assertRegex(body, r"\$\{info\}\$\{overview\}")
        paint = html[html.index("function paintPanelWaves"):html.index("function paintWaves")]
        self.assertIn(".panel-overview canvas[data-wave]", paint)
        self.assertTrue(re.search(r"paintWaveCanvas\(ov,", paint))


if __name__ == "__main__":
    unittest.main()
