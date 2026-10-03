"""MixStatus pending promotion + phrase time helpers."""

from __future__ import annotations

import unittest

from gui.widgets import phrase_time_ms
from prolink.mixstatus import MixStatus
from prolink.pdb import Track


class PhraseTimeTests(unittest.TestCase):
    def test_beat_to_ms(self):
        meta = {"beats": [[0, 1], [500, 2], [1000, 3]]}
        self.assertEqual(phrase_time_ms({"beat": 1}, meta), 0.0)
        self.assertEqual(phrase_time_ms({"beat": 2}, meta), 500.0)
        self.assertIsNone(phrase_time_ms({"beat": 9}, meta))
        self.assertIsNone(phrase_time_ms({"beat": 1}, {"beats": []}))


class MixPendingTests(unittest.TestCase):
    def test_pending_while_other_deck_is_live(self):
        m = MixStatus()
        # Deck 1 alone → promotes immediately.
        m.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
            "bpm": 120, "track_bpm": 120, "title": "A",
        }, now=1000.0)
        self.assertIsNotNone(m.now_playing)
        self.assertEqual(m.now_playing["number"], 1)
        # Deck 2 starts while 1 is live — accumulates toward promotion.
        m.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
            "bpm": 120, "track_bpm": 120,
        }, now=1000.0)
        m.handle({
            "number": 2, "track_id": 2, "playing": True, "on_air": True,
            "bpm": 128, "track_bpm": 128, "title": "Next", "artist": "X",
        }, now=1000.0)
        # ~45s at 128 BPM ≈ 96 beats of 128 needed → progress ~0.75
        st = m.as_state(now=1045.0)
        pending = st["pending"]
        self.assertIsNotNone(pending)
        self.assertEqual(pending["deck"], 2)
        self.assertEqual(pending["title"], "Next")
        self.assertGreater(pending["progress"], 0.5)
        self.assertLess(pending["progress"], 1.0)

    def test_no_pending_when_already_promoted(self):
        m = MixStatus()
        m.handle({
            "number": 1, "track_id": 1, "playing": True, "on_air": True,
            "bpm": 120, "track_bpm": 120,
        }, now=1.0)
        st = m.as_state(now=10.0)
        self.assertIsNone(st["pending"])


class PdbSearchTests(unittest.TestCase):
    def test_search_filters(self):
        # Build a minimal in-memory DB-like object by constructing PdbDatabase
        # is heavy; exercise Track dict filter via a tiny stub using search logic.
        from prolink import pdb as pdbmod

        class Tiny:
            tracks = {
                1: Track(id=1, title="Alpha", artist="DJ One", album="A", tempo=128),
                2: Track(id=2, title="Beta", artist="DJ Two", album="B", tempo=130),
                3: Track(id=3, title="Alpha Remix", artist="DJ One", album="C", tempo=126),
            }
            search = pdbmod.PdbDatabase.search

        rows, total = Tiny.search(Tiny(), "alpha", limit=10, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual({r["id"] for r in rows}, {1, 3})
        rows, total = Tiny.search(Tiny(), "dj two", limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["title"], "Beta")


if __name__ == "__main__":
    unittest.main()
