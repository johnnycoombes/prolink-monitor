"""Session recorder + bars-to-seconds helpers."""

from __future__ import annotations

import unittest

from prolink.session import (
    DEFAULT_ZOOM_BARS,
    SessionRecorder,
    ZOOM_BARS,
    bars_to_seconds,
    format_session_ts,
)


class BarsZoomTests(unittest.TestCase):
    def test_120_bpm_four_bars_is_eight_seconds(self):
        # 4 bars × 4 beats × 0.5 s/beat = 8 s
        self.assertAlmostEqual(bars_to_seconds(4, 120.0), 8.0)

    def test_default_bpm_when_missing(self):
        self.assertAlmostEqual(bars_to_seconds(1, 0), 2.0)
        self.assertAlmostEqual(bars_to_seconds(1, -1), 2.0)

    def test_zoom_levels(self):
        self.assertEqual(ZOOM_BARS, (1, 2, 4, 8, 16))
        self.assertIn(DEFAULT_ZOOM_BARS, ZOOM_BARS)


class FormatTsTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_session_ts(0), "00:00:00")

    def test_hours(self):
        self.assertEqual(format_session_ts(3661), "01:01:01")


class SessionRecorderTests(unittest.TestCase):
    def test_first_track_is_zero(self):
        rec = SessionRecorder()
        rec.start(now=1000.0)
        rec.handle({
            "number": 1, "track_id": 10, "playing": True, "on_air": True,
            "title": "A", "artist": "X", "bpm": 128.0,
        }, now=1000.5)
        st = rec.as_state(now=1000.5)
        self.assertTrue(st["recording"])
        self.assertEqual(len(st["tracks"]), 1)
        self.assertEqual(st["tracks"][0]["timestamp"], "00:00:00")
        self.assertEqual(st["tracks"][0]["title"], "A")

    def test_second_track_offset(self):
        rec = SessionRecorder()
        rec.start(now=1000.0)
        rec.handle({
            "number": 1, "track_id": 10, "playing": True, "on_air": True,
            "title": "A",
        }, now=1000.0)
        rec.handle({
            "number": 2, "track_id": 20, "playing": True, "on_air": True,
            "title": "B",
        }, now=1065.0)  # +65 s
        st = rec.as_state(now=1065.0)
        self.assertEqual(len(st["tracks"]), 2)
        self.assertEqual(st["tracks"][1]["timestamp"], "00:01:05")
        self.assertEqual(st["tracks"][1]["title"], "B")

    def test_same_track_not_duplicated(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        snap = {"number": 1, "track_id": 5, "playing": True, "on_air": True, "title": ""}
        rec.handle(snap, now=1.0)
        snap2 = dict(snap, title="Filled")
        rec.handle(snap2, now=2.0)
        self.assertEqual(len(rec.tracks), 1)
        self.assertEqual(rec.tracks[0].title, "Filled")

    def test_ignores_when_not_recording(self):
        rec = SessionRecorder()
        rec.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
        }, now=1.0)
        self.assertEqual(len(rec.tracks), 0)

    def test_stop_keeps_playlist(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        rec.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
            "title": "A",
        }, now=1.0)
        rec.stop()
        self.assertFalse(rec.recording)
        self.assertEqual(len(rec.tracks), 1)

    def test_clear_resets(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        rec.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
        }, now=1.0)
        rec.clear()
        self.assertFalse(rec.recording)
        self.assertEqual(len(rec.tracks), 0)
        self.assertIsNone(rec.t0)

    def test_off_air_skipped(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        rec.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": False,
        }, now=1.0)
        self.assertEqual(len(rec.tracks), 0)


if __name__ == "__main__":
    unittest.main()
