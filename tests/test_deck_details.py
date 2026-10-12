"""Deck rows show the spare fields and hide rekordbox's (blank) placeholder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
from gui.deck_fit import (
    art_side,
    choose_optional,
    snap_line,
    title_line_count,
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


class FitMathTests(unittest.TestCase):
    def test_title_stays_one_line_until_the_row_is_tall(self):
        self.assertEqual(title_line_count(120), 1)
        self.assertEqual(title_line_count(239), 1)
        self.assertEqual(title_line_count(240), 2)
        self.assertEqual(title_line_count(300), 2)

    def test_artwork_shrinks_with_the_row(self):
        self.assertLess(art_side(120), 72)
        self.assertGreaterEqual(art_side(120), 28)
        self.assertEqual(art_side(300), 72)

    def test_optional_lines_stop_when_the_budget_is_spent(self):
        lines = [("album", 12), ("label", 12), ("genre", 12), ("facts", 14)]
        self.assertEqual(choose_optional(30, lines), ["album", "label"])
        self.assertEqual(choose_optional(12, lines), ["album"])
        self.assertEqual(choose_optional(0, lines), [])

    def test_snap_line_rounds_up_on_fractional_dpr(self):
        self.assertGreaterEqual(snap_line(15.2, 1.25), 16)
        self.assertEqual(snap_line(18, 1), 18)
        self.assertGreaterEqual(snap_line(15.2, 1.5), 16)


def _ancestor(a, b) -> bool:
    parent = b.parentWidget()
    while parent is not None:
        if parent is a:
            return True
        parent = parent.parentWidget()
    return False


def _row_errors(card) -> list[str]:
    """Geometry problems: overflow, overlap, or a font bigger than its line."""
    from PySide6.QtCore import QPoint, QRect

    names = (
        "art", "num", "title", "artist", "album", "label_line", "genre",
        "_facts", "comment", "played", "bpm", "pitch", "times_wrap", "tags",
        "state_wrap", "phase", "next_cue",
    )
    skip_font = {"art", "times_wrap", "phase", "_facts", "played", "state_wrap"}
    items = []
    for name in names:
        widget = getattr(card, name)
        if widget.isHidden():
            continue
        origin = widget.mapTo(card, QPoint(0, 0))
        rect = QRect(origin, widget.size())
        if rect.width() <= 0 or rect.height() <= 0:
            continue
        items.append((name, widget, rect))
    errors = []
    for name, widget, rect in items:
        if rect.top() < -1 or rect.bottom() > card.height() + 1:
            errors.append(f"{name} falls outside the card ({rect.top()},{rect.bottom()} h={card.height()})")
        px = widget.font().pixelSize()
        if name not in skip_font and px > 0 and px > widget.height() + 2:
            errors.append(f"{name} font {px}px is taller than its {widget.height()}px row")
    if card.times_wrap.isVisible():
        px = card.elapsed.font().pixelSize()
        if px > 0 and px > card.times_wrap.height() + 2:
            errors.append("clock font is taller than the time row")
    if card.phase.isVisible():
        px = card.phrase.font().pixelSize()
        if px > 0 and px > card.phase.height() + 2:
            errors.append("phrase font is taller than the beat row")
    for i, (name, widget, rect) in enumerate(items):
        for other, other_widget, other_rect in items[i + 1:]:
            if _ancestor(widget, other_widget) or _ancestor(other_widget, widget):
                continue
            hit = rect.intersected(other_rect)
            if hit.width() > 1 and hit.height() > 1:
                errors.append(f"{name} overlaps {other}")
    if card.height() < 240 and not card.title.isHidden() and "\n" in card.title.text():
        errors.append("title wrapped on a short row")
    if not card.title.isHidden():
        spacing = max(1, card.title.fontMetrics().lineSpacing())
        lines = card.title.text().count("\n") + 1
        if card.title.height() > spacing * lines + 4:
            errors.append("title box is taller than its line count")
    return errors


def _load(card, meta, packet, pos):
    card.update_deck(packet, pos)
    if meta:
        meta = dict(meta)
        meta["track_key"] = packet.get("track_key") or ""
        meta["id"] = packet.get("track_id") or 0
        card.set_track_data(meta, None, None, None)
        card.update_deck(packet, pos)


COLA = {
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
}

SPREAD = {
    "title": "Spread Love 2014 - Club Mix",
    "artist": "Lee Foss, MK, Anabel Englund",
    "album": "Spread Love",
    "label": "Hot Creations",
    "genre": "Tech House, Deep House",
    "year": 2014,
    "key": "4A",
    "rating": 5,
    "color": "Aqua",
    "comment": "Peak time",
    "duration_ms": 507000,
    "phrases": [
        {"t": 0, "text": "Intro"},
        {"t": 60000, "text": "Up 7"},
        {"t": 91000, "text": "Up 1"},
    ],
    "beats": [],
    "cues": [{"t": 91000, "type": "cue", "hot": 1, "text": "Up 1"}],
}


def _packet(number, track_id, bpm, pitch, bar, duration, *, master=False, on_air=False, sync=False):
    return {
        "number": number,
        "track_id": track_id,
        "track_key": f"k{track_id}" if track_id else "",
        "bpm": bpm,
        "pitch": pitch,
        "state": "playing" if track_id else "no_track",
        "playing": bool(track_id),
        "bar": bar,
        "master": master,
        "sync": sync,
        "on_air": on_air,
        "duration_ms": duration,
        "name": "CDJ-3000",
    }


def probe_deck_rows() -> dict:
    """Pack 4 short rows and 2 tall rows. Used in-process and under QT_SCALE_FACTOR."""
    from PySide6.QtWidgets import QApplication

    from gui.i18n import I18n
    from gui.widgets import DeckCard

    app = QApplication.instance() or QApplication([])
    errors: list[str] = []

    def check(card, height, *, loaded: bool, empty: bool = False):
        card.set_fill_height(height)
        card.resize(1400, height)
        card.show()
        app.processEvents()
        card._layout_details()
        app.processEvents()
        for problem in _row_errors(card):
            errors.append(f"deck {card.number} @{height}: {problem}")
        if loaded:
            if card.artist.isHidden() or "Lee" not in card.artist.text() and "Camel" not in card.artist.text():
                errors.append(f"deck {card.number} @{height}: artist missing ({card.artist.text()!r})")
            if card.bpm.font().pixelSize() < 15:
                errors.append(f"deck {card.number} @{height}: BPM below the readable minimum")
        if empty:
            if not card.title.isHidden() and card.title.text().strip() in {"—", "-"}:
                errors.append(f"deck {card.number} @{height}: empty deck still shows a dash title")
            if "no track loaded" in card.artist.text():
                errors.append(f"deck {card.number} @{height}: empty deck is not compact")
            if card.artist.isHidden() or "no track" not in card.artist.text():
                errors.append(f"deck {card.number} @{height}: empty deck has no compact label")
            if card.artist.font().pixelSize() > 16:
                errors.append(f"deck {card.number} @{height}: empty label is still large")

    i18n = I18n()
    # Four decks, the short row JC is looking at.
    for number in (1, 2, 3, 4):
        card = DeckCard(number, i18n)
        if number == 1:
            _load(card, SPREAD, _packet(1, 11, 130.21, 0.16, 2, 507000, master=True), 72000)
            check(card, 120, loaded=True)
            if "\n" in card.title.text():
                errors.append("4-deck title wrapped")
            if card.art.width() >= 72:
                errors.append(f"4-deck artwork stayed {card.art.width()}px")
        elif number == 2:
            _load(card, COLA, _packet(2, 22, 122.2, 0.16, 3, 320000, master=True, sync=True, on_air=True), 50000)
            check(card, 120, loaded=True)
        else:
            _load(card, None, _packet(number, 0, 0, 0, 0, 0), 0)
            check(card, 120, loaded=False, empty=True)

    # Two decks, a tall row where the extra lines earn their place.
    tall = DeckCard(1, i18n)
    _load(tall, COLA, _packet(1, 7, 122.2, 0.16, 3, 320000, master=True, sync=True, on_air=True), 50000)
    check(tall, 420, loaded=True)
    if tall.title.font().pixelSize() < 22:
        errors.append(f"tall title is {tall.title.font().pixelSize()}px")
    if tall.album.isHidden() or "Cola" not in tall.album.text():
        errors.append("tall row hid the album")
    if not tall.label_line.isHidden() or "(blank)" in tall.label_line.text():
        errors.append("blank label was shown")
    if not tall.comment.isHidden() or "(blank)" in tall.comment.text():
        errors.append("blank comment was shown")
    if not tall.color_line.isHidden():
        errors.append("blank colour was shown")
    genre_shown = tall.genre.text().replace("\n", " ")
    if "Nu Disco" not in tall._genre_raw:
        errors.append("genre was dropped")
    if "Nu Disco" not in genre_shown and "Disco" not in genre_shown:
        errors.append(f"genre was elided away: {tall.genre.text()!r}")
    if tall.key.text() != "3A" or tall.key.isHidden():
        errors.append("key missing on a tall row")
    if "★" not in tall.rating.text():
        errors.append("rating missing on a tall row")
    if tall.year.text() != "2017":
        errors.append("year missing on a tall row")
    if "Chorus" not in tall.phrase.text() or "Beat 3/4" not in tall.phrase.text():
        errors.append(f"phrase missing: {tall.phrase.text()!r}")
    if "MASTER" not in tall.tags.text() or "SYNC" not in tall.tags.text():
        errors.append("tags missing")
    if tall.next_cue.isHidden() or "NEXT" not in tall.next_cue.text():
        errors.append(f"next cue missing: {tall.next_cue.text()!r}")
    if "(blank)" in tall.state.text():
        errors.append("blank state was shown")
    if tall.played._frac <= 0 or tall.played.isHidden():
        errors.append("played bar missing on a tall row")

    wide = DeckCard(1, i18n)
    _load(wide, SPREAD, _packet(1, 11, 130.21, 0.16, 2, 507000, master=True), 72000)
    check(wide, 300, loaded=True)
    if wide.album.isHidden():
        errors.append("2-deck row hid the album")
    if wide.next_cue.isHidden() or "Up 1" not in wide.next_cue.text():
        errors.append(f"2-deck next cue missing: {wide.next_cue.text()!r}")
    if "Lee" not in wide.artist.text():
        errors.append("2-deck artist missing")

    return {"ok": not errors, "errors": errors}


class DeckCardDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_blank_fields_stay_hidden_and_genre_is_kept(self):
        report = probe_deck_rows()
        self.assertTrue(report["ok"], "\n".join(report["errors"]))

    def test_rows_at_125_and_150_percent(self):
        for scale in ("1.25", "1.5"):
            env = os.environ.copy()
            env["QT_QPA_PLATFORM"] = "offscreen"
            env["QT_SCALE_FACTOR"] = scale
            env["CI"] = "true"
            env.pop("QT_SCREEN_SCALE_FACTORS", None)
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import json, os, sys; from tests.test_deck_details import probe_deck_rows; "
                 "print(json.dumps(probe_deck_rows())); sys.stdout.flush(); os._exit(0)"],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(report["ok"], f"DPI {scale}: " + "\n".join(report["errors"]))
