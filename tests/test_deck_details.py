"""Deck rows show the spare fields and hide rekordbox's (blank) placeholder."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink.countdown import current_phrase
from prolink.fields import (
    beat_phrase_text,
    color_label,
    display_rating,
    display_text,
    display_year,
    rating_stars,
)


class FieldTests(unittest.TestCase):
    def test_placeholder_and_empty_values_are_hidden(self):
        for raw in ("", "  ", "(blank)", "(Blank)", "NONE", "n/a", "—", None):
            self.assertEqual(display_text(raw), "")
        self.assertEqual(display_text("House, Deep House, Nu Disco"),
                         "House, Deep House, Nu Disco")
        self.assertEqual(display_year(0), "")
        self.assertEqual(display_year("2017"), "2017")
        self.assertEqual(display_rating(0), 0)
        self.assertEqual(display_rating(255), 5)
        self.assertEqual(rating_stars(4), "★★★★☆")
        self.assertEqual(rating_stars(0), "")
        self.assertEqual(color_label("(blank)"), ("", ""))
        self.assertEqual(color_label("Aqua")[0], "Aqua")
        self.assertTrue(color_label("Aqua")[1].startswith("#"))

    def test_phrase_follows_the_playhead(self):
        phrases = [{"t": 0, "text": "Intro"}, {"t": 30000, "text": "(blank)"},
                   {"t": 60000, "text": "Chorus"}]
        self.assertEqual(current_phrase(10000, phrases), "Intro")
        self.assertEqual(current_phrase(45000, phrases), "(blank)")
        self.assertEqual(beat_phrase_text(3, current_phrase(45000, phrases)), "Beat 3/4")
        self.assertEqual(beat_phrase_text(3, current_phrase(70000, phrases)), "Beat 3/4 · Chorus")
        self.assertEqual(beat_phrase_text(0, ""), "")


class DeckCardDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_blank_fields_stay_hidden_and_genre_is_kept(self):
        from gui.i18n import I18n
        from gui.widgets import DeckCard

        card = DeckCard(1, I18n())
        card.setMinimumHeight(420)
        card.resize(1400, 420)
        card.show()
        card.update_deck({
            "number": 1, "track_id": 7, "track_key": "", "bpm": 122.2, "pitch": 0.16,
            "state": "playing", "playing": True, "bar": 3, "master": True, "sync": True,
            "on_air": True, "duration_ms": 320000, "name": "XDJ-AZ",
        }, 50000)
        card.set_track_data({
            "title": "Cola (Club Mix)",
            "artist": "CamelPhat, Elderbrook",
            "album": "Cola",
            "genre": "House, Deep House, Nu Disco",
            "label": "(blank)",
            "year": 2017,
            "key": "3A",
            "comment": "(blank)",
            "color": "(blank)",
            "rating": 4,
            "duration_ms": 320000,
            "phrases": [{"t": 0, "text": "Intro"}, {"t": 40000, "text": "Chorus"}],
            "beats": [],
            "cues": [{"t": 90000, "type": "cue", "hot": 1, "text": "Break"}],
        }, None, None, None)
        card.update_deck({
            "number": 1, "track_id": 7, "track_key": "", "bpm": 122.2, "pitch": 0.16,
            "state": "playing", "playing": True, "bar": 3, "master": True, "sync": True,
            "on_air": True, "duration_ms": 320000, "name": "XDJ-AZ",
        }, 50000)

        self.assertIn("Cola", card.title.text())
        self.assertGreaterEqual(
            int(card.title.styleSheet().split("font-size:")[1].split("px")[0]), 22,
        )
        self.assertTrue(card.album.isVisible())
        self.assertFalse(card.label_line.isVisible())
        self.assertFalse(card.comment.isVisible())
        self.assertFalse(card.color_line.isVisible())
        self.assertNotIn("(blank)", card.label_line.text())
        self.assertNotIn("(blank)", card.comment.text())
        self.assertIn("Nu Disco", card.genre.text())
        self.assertTrue(card.genre.wordWrap())
        self.assertEqual(card.key.text(), "3A")
        self.assertIn("★", card.rating.text())
        self.assertEqual(card.year.text(), "2017")
        self.assertIn("Chorus", card.phrase.text())
        self.assertIn("Beat 3/4", card.phrase.text())
        self.assertIn("MASTER", card.tags.text())
        self.assertIn("SYNC", card.tags.text())
        self.assertIn("NEXT", card.next_cue.text())
        self.assertTrue(card.next_cue.isVisible())
        self.assertNotIn("(blank)", card.state.text())
        self.assertGreater(card.played._frac, 0)

    def test_short_row_hides_the_extra_lines(self):
        from gui.i18n import I18n
        from gui.widgets import DeckCard

        card = DeckCard(1, I18n())
        card.setMinimumHeight(160)
        card.setMaximumHeight(160)
        card.resize(900, 160)
        card.show()
        card.set_track_data({
            "title": "Cola (Club Mix)",
            "artist": "CamelPhat",
            "album": "Cola",
            "genre": "House",
            "comment": "Festival edit",
            "key": "3A",
        }, None, None, None)
        self.assertFalse(card._roomy)
        self.assertFalse(card.comment.isVisible())
        self.assertFalse(card.album.isVisible())
        self.assertTrue(card.genre.isVisible())
        self.assertIn("3A", card.key.text())
