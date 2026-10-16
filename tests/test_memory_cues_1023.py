"""1.0.23: memory cues and stored loops show in every view."""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app
from prolink import anlz

ROOT = os.path.join(os.path.dirname(__file__), "..")


class CuePayloadTests(unittest.TestCase):
    def test_memory_cues_kept_and_black_means_no_colour(self):
        cues = [
            anlz.Cue(0, "cue", 141, 0xFFFFFFFF, "", (0, 0, 0)),
            anlz.Cue(0, "cue", 141, 0xFFFFFFFF),                       # .EXT PCOB copy
            anlz.Cue(1, "cue", 1000, 0xFFFFFFFF, "Drop", (255, 0, 23)),
            anlz.Cue(0, "loop", 5000, 9000),
        ]
        out = app._cue_payload(cues)
        self.assertEqual(len(out), 3)
        mem = [c for c in out if c["memory"] and c["type"] == "cue"]
        self.assertEqual(len(mem), 1)
        self.assertIsNone(mem[0]["color"])
        self.assertEqual(out[1]["color"], (255, 0, 23))
        loop = [c for c in out if c["type"] == "loop"][0]
        self.assertEqual((loop["t"], loop["end"]), (5000, 9000))

    def test_richer_duplicate_wins(self):
        out = app._cue_payload([anlz.Cue(0, "cue", 10, 0),
                                anlz.Cue(0, "cue", 10, 0, "Intro", (10, 20, 30))])
        self.assertEqual(out[0]["text"], "Intro")


class WebViewsDrawMemoryCues(unittest.TestCase):
    def test_pages_use_cue_fill_and_loop_bars(self):
        for name in ("index.html", "overlay.html"):
            with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("function cueFill(c)", src, name)
            self.assertIn("'#ff3b30'", src, name)
            self.assertNotIn("'#ffd45e' : '#ffffff'", src, name)
            self.assertNotRegex(src, r"if \(c\.type === 'loop'\) continue;", name)
            self.assertIn("drawLoopBar(", src, name)


class QtMemoryCueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(str(exc))
        cls.app = QApplication.instance() or QApplication([])

    def test_memory_cue_colour_is_red_not_black(self):
        from gui.widgets import _cue_color
        self.assertEqual(_cue_color({"hot": 0, "color": (0, 0, 0)}).name(), "#ff3b30")
        self.assertEqual(_cue_color({"hot": 0, "color": None}).name(), "#ff3b30")
        self.assertEqual(_cue_color({"hot": 2, "color": None}).name(), "#ffd45e")
        self.assertEqual(_cue_color({"hot": 0, "color": (1, 2, 3)}).name(), "#010203")

    def test_monitor_draws_memory_cue_and_loop_bar(self):
        import random
        from gui.widgets import WaveformView
        random.seed(1)
        n = 9000
        d = {"n": n, "cps": 150.0, "dur": 60000, "h": bytes([1] * n), "rgb": bytes(n * 3)}
        ov = {"n": 400, "cps": 0, "dur": 60000, "h": bytes([1] * 400), "rgb": bytes(1200)}
        meta = {"duration_ms": 60000, "cues": [
            {"hot": 0, "type": "cue", "t": 30000, "color": None},
            {"hot": 0, "type": "loop", "t": 10000, "end": 20000, "color": None}]}
        w = WaveformView()
        w.resize(600, 200)
        w.set_track(d, ov, meta)
        w.set_position(50000, False)   # cues sit in the played part
        img = w.grab().toImage()
        # Memory cue line at 50% of the overview (red), loop bar at ~25% (orange).
        red = img.pixelColor(300, 20)
        self.assertGreater(red.red(), 200)
        self.assertLess(red.green(), 90)
        bar = img.pixelColor(int(600 * 0.25), 1)
        self.assertGreater(bar.red(), 200)
        self.assertGreater(bar.green(), 100)


if __name__ == "__main__":
    unittest.main()
