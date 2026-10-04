"""Audience deck selection (Now Playing) — independent of SmartTiming."""

from __future__ import annotations

import unittest

from prolink.audience_deck import AudienceDeckTracker, merge_live_deck


def _deck(n, *, playing=True, master=False, on_air=False, track_id=1, **kw):
    d = {
        "number": n,
        "playing": playing,
        "master": master,
        "on_air": on_air,
        "track_id": track_id,
    }
    d.update(kw)
    return d


class AudienceDeckTests(unittest.TestCase):
    def test_master_on_air_wins(self):
        t = AudienceDeckTracker()
        t.on_air_ever = True
        t.on_air_since = {1: 10.0, 2: 20.0}
        decks = [
            _deck(1, master=True, on_air=True, track_id=10),
            _deck(2, on_air=True, track_id=20),
        ]
        pick = t.pick(decks)
        self.assertEqual(pick["number"], 1)
        self.assertEqual(pick["track_id"], 10)

    def test_most_recent_on_air_without_master(self):
        t = AudienceDeckTracker()
        t.on_air_ever = True
        t.on_air_since = {1: 10.0, 2: 30.0}
        decks = [
            _deck(1, on_air=True, track_id=10),
            _deck(2, on_air=True, track_id=20),
        ]
        pick = t.pick(decks)
        self.assertEqual(pick["number"], 2)

    def test_fallback_master_when_no_on_air_ever(self):
        t = AudienceDeckTracker()
        decks = [
            _deck(1, track_id=10),
            _deck(2, master=True, track_id=20),
        ]
        pick = t.pick(decks)
        self.assertEqual(pick["number"], 2)

    def test_observe_sets_on_air_ever(self):
        t = AudienceDeckTracker()
        t.observe([_deck(1, on_air=True)], now=1.0)
        self.assertTrue(t.on_air_ever)
        self.assertIn(1, t.on_air_since)

    def test_merge_live_drops_stale_title_on_track_change(self):
        aud = {"number": 1, "track_id": 5, "title": "Old", "artist": "X"}
        live = {"number": 1, "track_id": 9, "position_ms": 1000, "playing": True}
        merged = merge_live_deck(aud, [live])
        self.assertEqual(merged["track_id"], 9)
        self.assertNotIn("title", merged)
        self.assertEqual(merged["position_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
