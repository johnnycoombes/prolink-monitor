"""Session recorder + bars-to-seconds helpers."""

from __future__ import annotations

import json
import os
import tempfile
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
        self.assertEqual(st["clock"], "set")

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

    def test_set_clock_pauses_on_dead_air(self):
        """Dead air between tracks must not advance the set clock."""
        rec = SessionRecorder()
        rec.start(now=1000.0)
        # Track A live for 30 s.
        rec.observe([{
            "number": 1, "track_id": 10, "playing": True, "on_air": True,
            "title": "A",
        }], now=1000.0)
        rec.observe([{
            "number": 1, "track_id": 10, "playing": True, "on_air": True,
            "title": "A",
        }], now=1030.0)
        # Dead air for 120 s (nothing on-air).
        rec.observe([{
            "number": 1, "track_id": 10, "playing": False, "on_air": False,
            "title": "A",
        }], now=1030.0)
        st = rec.as_state(now=1150.0)
        self.assertTrue(st["paused"])
        self.assertAlmostEqual(st["elapsed_s"], 30.0, places=3)
        # Track B starts after the gap — set clock should still be ~30 s.
        rec.observe([{
            "number": 2, "track_id": 20, "playing": True, "on_air": True,
            "title": "B",
        }], now=1150.0)
        st = rec.as_state(now=1150.0)
        self.assertFalse(st["paused"])
        self.assertEqual(len(st["tracks"]), 2)
        self.assertEqual(st["tracks"][1]["timestamp"], "00:00:30")
        self.assertAlmostEqual(st["elapsed_s"], 30.0, places=3)

    def test_observe_all_decks_for_clock(self):
        """Clock stays running if any deck is live, even if another is quiet."""
        rec = SessionRecorder()
        rec.start(now=100.0)
        rec.observe([
            {"number": 1, "track_id": 1, "playing": True, "on_air": True, "title": "A"},
            {"number": 2, "track_id": 0, "playing": False, "on_air": False},
        ], now=100.0)
        rec.observe([
            {"number": 1, "track_id": 1, "playing": True, "on_air": True, "title": "A"},
            {"number": 2, "track_id": 0, "playing": False, "on_air": False},
        ], now=140.0)
        self.assertAlmostEqual(rec.set_clock_elapsed(140.0), 40.0, places=3)
        self.assertFalse(rec.as_state(now=140.0)["paused"])

    def test_export_csv_json_m3u(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        rec.handle({
            "number": 1, "track_id": 7, "playing": True, "on_air": True,
            "title": "Song", "artist": "DJ", "bpm": 126.0, "key": "8A",
        }, now=1.0)
        csv_body = rec.export_csv()
        self.assertIn("timestamp,deck,title,artist,bpm,key,track_id", csv_body)
        self.assertIn("Song", csv_body)
        self.assertIn("DJ", csv_body)

        data = json.loads(rec.export_json(now=1.0))
        self.assertEqual(data["clock"], "set")
        self.assertEqual(data["tracks"][0]["title"], "Song")

        m3u = rec.export_m3u()
        self.assertTrue(m3u.startswith("#EXTM3U"))
        self.assertIn("DJ - Song", m3u)
        self.assertIn("#PROLINK:deck=1;id=7;t=00:00:00", m3u)

    def test_save_exports(self):
        rec = SessionRecorder()
        rec.start(now=1.0)
        rec.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
            "title": "A", "artist": "B",
        }, now=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            paths = rec.save_exports(tmp, prefix="test-set", now=1.0)
            self.assertEqual(len(paths), 3)
            names = sorted(os.path.basename(p) for p in paths)
            self.assertEqual(names, ["test-set.csv", "test-set.json", "test-set.m3u"])
            for p in paths:
                self.assertTrue(os.path.isfile(p))
                self.assertGreater(os.path.getsize(p), 0)


class SettingsSessionsDirTests(unittest.TestCase):
    def test_sessions_dir_default_and_custom(self):
        from gui.settings import sessions_dir
        default = sessions_dir({})
        self.assertTrue(default.endswith("sessions"))
        custom = sessions_dir({"session_autosave_dir": "/tmp/my-sets"})
        self.assertEqual(custom, "/tmp/my-sets")


if __name__ == "__main__":
    unittest.main()
