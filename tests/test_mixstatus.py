"""SmartTiming / MixStatus unit tests (no live players required)."""

from __future__ import annotations

import unittest

from prolink.mixstatus import MixStatus, MixStatusConfig, _beat_ms


def _snap(number: int, *, playing=True, on_air=True, track_id=1,
          bpm=120.0, pitch=0.0, title="", artist="", **extra):
    d = {
        "number": number,
        "playing": playing,
        "on_air": on_air,
        "track_id": track_id,
        "bpm": bpm,
        "track_bpm": bpm,
        "pitch": pitch,
        "title": title,
        "artist": artist,
    }
    d.update(extra)
    return d


class BeatMsTests(unittest.TestCase):
    def test_120_bpm(self):
        self.assertAlmostEqual(_beat_ms(120.0), 500.0)

    def test_pitch_raises_tempo(self):
        # +6% at 120 → effective 127.2 → shorter beat
        self.assertLess(_beat_ms(120.0, 6.0), 500.0)


class MixStatusTests(unittest.TestCase):
    def setUp(self):
        # Short thresholds so tests don't wait for 128 real beats.
        self.cfg = MixStatusConfig(
            beats_until_reported=4,
            allowed_interrupt_beats=2,
            time_between_sets=5.0,
            use_on_air=True,
        )
        self.mix = MixStatus(self.cfg)

    def test_alone_promotes_immediately(self):
        t0 = 1_000.0
        self.mix.handle(_snap(1, title="A", artist="X"), now=t0)
        self.assertIsNotNone(self.mix.now_playing)
        self.assertEqual(self.mix.now_playing["number"], 1)
        self.assertEqual(self.mix.now_playing["title"], "A")
        self.assertTrue(self.mix.set_active)
        self.assertEqual(len(self.mix.setlist), 1)
        self.assertEqual(self.mix.setlist[0].track_id, 1)

    def test_second_deck_waits_for_beats(self):
        t0 = 1_000.0
        self.mix.handle(_snap(1, track_id=10, title="A"), now=t0)
        self.assertEqual(self.mix.now_playing["number"], 1)

        # Deck 2 starts while deck 1 is still live — must wait SmartTiming.
        self.mix.handle(_snap(1, track_id=10, title="A"), now=t0 + 0.1)
        self.mix.handle(_snap(2, track_id=20, title="B"), now=t0 + 0.1)
        self.assertEqual(self.mix.now_playing["number"], 1)

        # 4 beats at 120 BPM = 2.0 s
        self.mix.handle(_snap(1, track_id=10, title="A"), now=t0 + 2.2)
        self.mix.handle(_snap(2, track_id=20, title="B"), now=t0 + 2.2)
        self.assertEqual(self.mix.now_playing["number"], 2)
        self.assertEqual(self.mix.now_playing["title"], "B")
        self.assertEqual(len(self.mix.setlist), 2)

    def test_brief_interrupt_ignored(self):
        t0 = 1_000.0
        self.mix.handle(_snap(1, track_id=10), now=t0)
        self.assertEqual(self.mix.now_playing["number"], 1)

        # Brief stop shorter than allowed_interrupt_beats (2 × 0.5s = 1.0s)
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 0.2)
        self.mix.handle(_snap(1, track_id=10, playing=True), now=t0 + 0.5)
        self.assertEqual(self.mix.now_playing["number"], 1)
        self.assertIn(1, self.mix._live)

    def test_long_stop_clears_now_playing(self):
        t0 = 1_000.0
        self.mix.handle(_snap(1, track_id=10), now=t0)
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 0.1)
        # 2 beats at 120 = 1.0 s
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 1.2)
        self.assertIsNone(self.mix.now_playing)
        self.assertNotIn(1, self.mix._live)

    def test_set_ends_after_silence(self):
        t0 = 1_000.0
        self.mix.handle(_snap(1, track_id=10), now=t0)
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 0.1)
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 1.2)
        self.assertTrue(self.mix.set_active)
        # time_between_sets = 5s after last live deck stopped
        self.mix.handle(_snap(1, track_id=10, playing=False), now=t0 + 1.2 + 5.1)
        self.assertFalse(self.mix.set_active)

    def test_as_state_shape(self):
        self.mix.handle(_snap(1, track_id=7, title="T", artist="A", key="8A"), now=50.0)
        st = self.mix.as_state()
        self.assertTrue(st["set_active"])
        self.assertEqual(st["now_playing"]["track_id"], 7)
        self.assertEqual(st["setlist"][0]["title"], "T")
        self.assertIn("pending", st)
        self.assertEqual(st["config"]["beats_until_reported"], 4)

    def test_no_on_air_when_required(self):
        self.mix.handle(_snap(1, on_air=False, track_id=3), now=10.0)
        self.assertIsNone(self.mix.now_playing)

    def test_use_on_air_false(self):
        mix = MixStatus(MixStatusConfig(
            beats_until_reported=4, use_on_air=False,
        ))
        mix.handle(_snap(1, on_air=False, track_id=3), now=10.0)
        self.assertIsNotNone(mix.now_playing)


if __name__ == "__main__":
    unittest.main()
