"""Zoomable scrolling waveform (1.0.10): zoom maths and overlay alignment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from prolink import wavezoom as z

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ZoomMathTests(unittest.TestCase):
    def test_wheel_is_multiplicative_and_reversible(self):
        s = 8.0
        zin = z.wheel_zoom(s, 120)
        self.assertAlmostEqual(zin, s / z.STEP)
        self.assertAlmostEqual(z.wheel_zoom(zin, -120), s)
        # Two half-notches equal one notch (smooth trackpads).
        self.assertAlmostEqual(z.wheel_zoom(z.wheel_zoom(s, 60), 60), zin)

    def test_clamped_to_2_and_96_seconds(self):
        s = 8.0
        for _ in range(100):
            s = z.wheel_zoom(s, 120)
        self.assertEqual(s, z.MIN_SECONDS)
        for _ in range(100):
            s = z.wheel_zoom(s, -120)
        self.assertEqual(s, z.MAX_SECONDS)
        self.assertEqual(z.clamp_seconds(float("nan")), z.MIN_SECONDS)
        self.assertEqual(z.clamp_seconds("x"), z.MIN_SECONDS)

    def test_pinch(self):
        self.assertAlmostEqual(z.pinch_zoom(10, 100, 200), 5)     # spread = zoom in
        self.assertAlmostEqual(z.pinch_zoom(10, 200, 100), 20)
        self.assertEqual(z.pinch_zoom(10, 0, 100), 10)

    def test_x_and_ms_are_inverse_and_playhead_is_fixed(self):
        for frac in (0.5, 0.25):
            for secs in (2, 5.5, 12, 30):
                self.assertAlmostEqual(z.x_for_ms(60000, 60000, secs, 800, frac), 800 * frac)
                x = z.x_for_ms(61234, 60000, secs, 800, frac)
                self.assertAlmostEqual(z.ms_for_x(x, 60000, secs, 800, frac), 61234)

    def test_saved_zoom_is_cleaned(self):
        self.assertEqual(z.clean_zoom_seconds({"1": 120, "2": 0.5, "x": 3, "3": "nan", "4": 6}),
                         {"1": 96.0, "2": 2.0, "4": 6.0})
        self.assertEqual(z.clean_zoom_seconds(None), {})

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        cases = [[8, 120], [8, -120], [3, 600], [25, -600], [8, 37]]
        script = ("const w=require('./web/wavepack.js');console.log(JSON.stringify({"
                  "wheel:%s.map(c=>w.wheelZoom(...c)),pinch:[w.pinchZoom(10,100,200),w.pinchZoom(10,200,100)],"
                  "x:[w.xForMs(61234,60000,5.5,800,0.25),w.xForMs(59000,60000,30,1200,0.5)],"
                  "lim:[w.ZOOM_MIN,w.ZOOM_MAX,w.ZOOM_STEP]}))" % json.dumps(cases))
        out = json.loads(subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True,
                                        text=True, check=True, timeout=30).stdout)
        for got, c in zip(out["wheel"], cases):
            self.assertAlmostEqual(got, z.wheel_zoom(*c))
        self.assertEqual(out["pinch"], [5, 20])
        self.assertAlmostEqual(out["x"][0], z.x_for_ms(61234, 60000, 5.5, 800, 0.25))
        self.assertAlmostEqual(out["x"][1], z.x_for_ms(59000, 60000, 30, 1200, 0.5))
        self.assertEqual(out["lim"], [z.MIN_SECONDS, z.MAX_SECONDS, z.STEP])


class QtZoomAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _view(self, frac="centre"):
        from gui.widgets import WaveformView
        view = WaveformView()
        view.resize(800, 220)
        view.set_playhead_position(frac)
        n = 150 * 120
        detail = {"n": n, "cps": 150.0, "dur": 120000, "h": bytes([12]) * n, "rgb": bytes([90]) * (n * 3)}
        beats = [[i * 468.75, i % 4 + 1] for i in range(256)]
        meta = {"duration_ms": 120000, "beats": beats,
                "cues": [{"t": 59400.0, "hot": 0, "color": [255, 0, 255]}],
                "phrases": [{"beat": 1, "kind": "intro", "text": "Intro"},
                            {"beat": 107, "kind": "chorus", "text": "Chorus"}]}
        view.set_track(detail, None, meta)
        view.set_deck_color("#ff8800")      # keep the needle out of the loop-green test
        view.set_bpm(128)
        view.set_position(60000.0, False)
        view.set_loop_region(60800.0, 62400.0, True)
        return view

    @staticmethod
    def _columns(img, y, pred):
        return [x for x in range(img.width()) if pred(img.pixelColor(x, y))]

    def test_loop_and_cue_track_the_zoom(self):
        for frac in ("centre", "left"):
            view = self._view(frac)
            for secs in (2.0, 4.3, 9.0, 18.0, 30.0):
                view.set_zoom_seconds(secs)
                img = view.grab().toImage()
                y = img.height() - 30           # inside the scrolling strip
                loop_x = view.detail_x(60800.0)
                cue_x = view.detail_x(59400.0)
                green = self._columns(img, y, lambda c: c.green() > 200 and c.red() < 100 and 120 < c.blue() < 190)
                magenta = self._columns(img, y, lambda c: c.red() > 200 and c.blue() > 200 and c.green() < 80)
                self.assertTrue(green, (frac, secs))
                self.assertLessEqual(abs(min(green) - loop_x), 1.5, (frac, secs, min(green), loop_x))
                if 2 <= cue_x < img.width() - 2:
                    self.assertTrue(magenta, (frac, secs))
                    self.assertLessEqual(min(abs(x - cue_x) for x in magenta), 1.5, (frac, secs))
                else:
                    self.assertFalse(magenta, (frac, secs))   # off-screen cue is not drawn
                # The playhead stays put while zooming.
                self.assertAlmostEqual(view.detail_x(60000.0), 800 * view._playhead_frac)

    def test_phrase_strip_is_the_same_at_every_zoom(self):
        view = self._view()
        view.set_zoom_seconds(2.0)
        a = view.grab().toImage()
        view.set_zoom_seconds(30.0)
        b = view.grab().toImage()
        y = view._overview_h + 4 + 4        # a row inside the phrase strip
        self.assertGreater(view._phrase_h, 0)
        row_a = [a.pixelColor(x, y).name() for x in range(a.width())]
        row_b = [b.pixelColor(x, y).name() for x in range(b.width())]
        self.assertEqual(row_a, row_b)

    def test_wheel_zooms_and_double_click_resets(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QMouseEvent, QWheelEvent

        view = self._view()
        view.grab()
        seen = []
        view.zoom_seconds_changed.connect(seen.append)
        before = view._visible_seconds()
        pos = QPointF(400, 200)
        ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                         Qt.NoScrollPhase, False)
        view.wheelEvent(ev)
        self.assertAlmostEqual(view._visible_seconds(), z.wheel_zoom(before, 120))
        self.assertAlmostEqual(seen[-1], view._visible_seconds())
        dbl = QMouseEvent(QMouseEvent.MouseButtonDblClick, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        view.mouseDoubleClickEvent(dbl)
        self.assertIsNone(view._zoom_seconds)
        self.assertEqual(seen[-1], -1.0)
        self.assertAlmostEqual(view._visible_seconds(), before)

    def test_zoomed_out_view_is_max_pooled(self):
        """A one-column transient survives a 30 s window (no decimation)."""
        from gui.widgets import WaveformView
        view = WaveformView()
        view.resize(400, 220)
        n = 150 * 60
        h = bytearray([2]) * n
        h[150 * 30 + 7] = 31                     # a single loud column at 30.047 s
        view.set_track({"n": n, "cps": 150.0, "dur": 60000, "h": bytes(h), "rgb": bytes([200]) * (n * 3)},
                       None, {"duration_ms": 60000})
        view.set_position(30000.0, False)
        view.set_zoom_seconds(30.0)
        img = view.grab().toImage()
        x = int(view.detail_x(30000 + 7 / 150 * 1000, 400))
        top_lit = min(y for y in range(img.height())
                      if any(img.pixelColor(xx, y).red() > 150 for xx in range(x - 1, x + 2)))
        self.assertLess(top_lit, img.height() - 90)   # drawn tall, not dropped


if __name__ == "__main__":
    unittest.main()
