"""1.0.13: bar.beat counter (4/4) and smaller deck identity text."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from gui.deck_fit import TITLE_MIN, title_line_count
from gui.deck_layout import ARTIST_PX, KEY_PX, NUM_PX, TITLE_PX
from prolink.fields import bar_beat, bar_beat_text, beat_phrase_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = [
    # (status beat number, beat_in_bar, expected)
    (1, 1, "Bar 1.1"),
    (4, 4, "Bar 1.4"),
    (5, 1, "Bar 2.1"),
    (130, 2, "Bar 33.2"),
    (131, 3, "Bar 33.3"),
    (132, 0, "Bar 33.4"),       # no beat_in_bar: plain 4/4 from beat 1
    (3, 1, "Bar 1.1"),          # grid whose first beat is not a downbeat
    (7, 1, "Bar 2.1"),
    (0, 2, ""),
    (0xFFFFFFFF, 1, ""),        # no track loaded
    (-1, 1, ""),
]


class BarBeatTests(unittest.TestCase):
    def test_cases(self):
        for beat, k, want in CASES:
            with self.subTest(beat=beat, k=k):
                self.assertEqual(bar_beat_text(beat, k), want)

    def test_tuple(self):
        self.assertEqual(bar_beat(130, 2), (33, 2))
        self.assertIsNone(bar_beat("x", 1))

    def test_phrase_text(self):
        self.assertEqual(beat_phrase_text(2, "Chorus", 130), "Bar 33.2 · Chorus")
        self.assertEqual(beat_phrase_text(2, "Verse 1"), "Beat 2/4 · Verse 1")
        self.assertEqual(beat_phrase_text(0, "", 0), "")

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        script = (
            "const p=require(process.argv[1]);"
            "const c=JSON.parse(process.argv[2]);"
            "console.log(JSON.stringify(c.map(x=>p.barBeatText(x[0],x[1]))));"
        )
        out = subprocess.run(
            [node, "-e", script, os.path.join(ROOT, "web", "playhead.js"),
             json.dumps([[b, k] for b, k, _w in CASES])],
            capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(out.stdout), [w for _b, _k, w in CASES])

    def test_pages_use_the_bar_counter(self):
        for name in ("index.html", "overlay.html"):
            html = open(os.path.join(ROOT, "web", name), encoding="utf-8").read()
            self.assertIn("barBeatText", html, name)
            self.assertIn("bar_beat", html, name)


class SmallerIdentityTextTests(unittest.TestCase):
    def test_sizes_are_about_a_third_smaller(self):
        self.assertLessEqual(TITLE_PX, round(22 * 0.7))
        self.assertLessEqual(ARTIST_PX, round(16 * 0.7))
        self.assertLessEqual(NUM_PX, round(16 * 0.7))
        self.assertLessEqual(KEY_PX, round(16 * 0.8))
        self.assertLess(TITLE_MIN, 13)

    def test_two_title_lines_on_normal_rows(self):
        # A 4-deck row on a 1440p screen is ~120px; the title gets two lines.
        self.assertEqual(title_line_count(120), 2)


if __name__ == "__main__":
    unittest.main()
