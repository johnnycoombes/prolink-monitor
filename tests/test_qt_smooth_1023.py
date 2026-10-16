"""1.0.23: desktop Monitor scroll smoothness (Qt only; no protocol code)."""
from __future__ import annotations

import os
import random
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink.playhead_smooth import DisplayPlayhead


class DisplayPlayheadTests(unittest.TestCase):
    def test_jittery_updates_never_step_back(self):
        sm = DisplayPlayhead()
        random.seed(3)
        shown, t = [], 0.0
        for i in range(600):
            t += 1 / 60
            target = t * 1000 + random.uniform(-40, 40)   # ~±40 ms packet jitter
            shown.append(sm.update(target, playing=True, speed=1.0, now=t, key=1))
        steps = [b - a for a, b in zip(shown, shown[1:])]
        self.assertGreater(min(steps), 0)                 # always forward
        self.assertLess(max(steps) - min(steps), 25)      # even frame steps
        self.assertLess(abs(shown[-1] - t * 1000), 60)    # stays on the playhead

    def test_seek_pause_and_track_change_snap(self):
        sm = DisplayPlayhead()
        sm.update(1000, playing=True, speed=1.0, now=0.0, key=1)
        self.assertEqual(sm.update(90000, playing=True, speed=1.0, now=0.016, key=1), 90000)
        self.assertEqual(sm.update(500, playing=False, speed=1.0, now=0.03, key=1), 500)
        self.assertEqual(sm.update(700, playing=True, speed=1.0, now=0.05, key=2), 700)
        self.assertEqual(sm.update(800, playing=True, speed=1.0, now=0.06, key=2,
                                   looping=True), 800)


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(str(exc))
        cls.app = QApplication.instance() or QApplication([])


def _wave():
    from gui.widgets import WaveformView
    random.seed(4)
    n = 30000
    d = {"n": n, "cps": 150.0, "dur": 200000, "h": bytes(random.randint(2, 31) for _ in range(n)),
         "rgb": bytes(random.randint(0, 255) for _ in range(n * 3))}
    w = WaveformView()
    w.resize(800, 200)
    w.set_bpm(128)
    w.set_track(d, None, {"duration_ms": 200000})
    return w


class WaveformSubPixelTests(_QtCase):
    def test_strip_tracks_playhead_within_half_device_px(self):
        w = _wave()
        pos = 40000.0
        for _ in range(90):
            pos += 1000 / 60 * 1.013
            w.set_position(pos, True)
            w.grab()
            cps = 150.0
            vis = max(1.0, w._visible_seconds() * cps)
            ppc = 800 / vis
            start = pos / 1000 * cps - vis * w._playhead_frac
            ideal = (w._detail_start - start) * ppc * w._dpr()
            self.assertLessEqual(abs(ideal - w._blit_align_dev), 0.5 + 1e-6)

    def test_hidpi_pixmap_uses_device_pixels(self):
        w = _wave()
        w._dpr = lambda: 2.0
        pm = w._hidpi_pixmap(800, 200)
        self.assertEqual((pm.width(), pm.height()), (1600, 400))
        self.assertEqual(pm.devicePixelRatio(), 2.0)
        w.set_position(40000.0, True)
        w.grab()
        self.assertEqual(w._detail_pm.width(), 1600)
        w.set_position(40100.0, True)          # incremental scroll at 2x
        w.grab()
        self.assertEqual(w._detail_pm.width(), 1600)


class PaintTimerTests(_QtCase):
    def test_paint_timer_is_precise(self):
        from PySide6.QtCore import Qt
        from gui.backend import Backend
        b = Backend()
        try:
            self.assertEqual(b._paint_timer.timerType(), Qt.PreciseTimer)
        finally:
            b.shutdown_workers()


if __name__ == "__main__":
    unittest.main()
