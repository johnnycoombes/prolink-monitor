"""Smoke tests for lightweight paint_state / library cache behaviour."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class PaintStateShapeTests(unittest.TestCase):
    def test_paint_state_flags_and_fields(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.host = None
        mon.engine = MagicMock()
        deck = MagicMock()
        deck.number = 1
        deck.is_playing = True
        deck.position_ms = 1234.5
        deck.track_length_ms = 200000
        deck.position_source = "absolute"
        st = MagicMock()
        st.track_id = 9
        st.effective_bpm = 128.0
        st.bpm = 126.0
        st.pitch_percent = 1.5
        st.speed = 1.015
        st.beat_count = 40
        st.beat_in_bar = 2
        st.play_state = "playing"
        st.master = True
        st.sync = False
        st.on_air = True
        deck.status = st
        mon.engine.active_decks.return_value = [deck]

        out = Monitor.paint_state(mon)
        self.assertTrue(out["paint"])
        self.assertEqual(len(out["decks"]), 1)
        d = out["decks"][0]
        self.assertEqual(d["number"], 1)
        self.assertEqual(d["track_id"], 9)
        self.assertEqual(d["position_ms"], 1234.5)
        self.assertNotIn("devices", out)
        self.assertNotIn("library", out)


if __name__ == "__main__":
    unittest.main()
