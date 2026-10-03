"""PSSI song-structure decoding against rekordcrate / pyrekordbox fixtures."""

from __future__ import annotations

import unittest
from pathlib import Path

from prolink import anlz

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "anlz"

# Real RB6+ exports (XOR-masked). Counts match Holzhaus/rekordcrate samples:
# sweep → 8, demo P016 → 19, demo P053 → 11. The old decoder returned 13/29/17.
CASES = (
    ("sweep.pssi.anlz", 8, 1, ("Intro 2", "Up 3", "Chorus 1", "Down", "Up 1")),
    ("demo_p016.pssi.anlz", 19, 2, ("Chorus", "Verse 1", "Verse 1", "Verse 2", "Verse 3")),
    ("demo_p053.pssi.anlz", 11, 2, ("Intro", "Verse 1", "Verse 2", "Verse 2", "Verse 3")),
)


class PhraseDecodingTests(unittest.TestCase):
    def test_fixture_counts_and_labels(self):
        for name, count, mood, prefix in CASES:
            with self.subTest(name=name):
                path = FIXTURES / name
                self.assertTrue(path.is_file(), f"missing fixture {path}")
                parsed = anlz.parse(path.read_bytes())
                self.assertEqual(len(parsed.phrases), count)
                labels = tuple(p.label for p in parsed.phrases[: len(prefix)])
                self.assertEqual(labels, prefix)
                # beats must be strictly increasing for a usable structure map
                beats = [p.beat for p in parsed.phrases]
                self.assertEqual(beats, sorted(beats))
                self.assertTrue(all(b >= 1 for b in beats))

    def test_unmasked_rb5_style_tag(self):
        """A clear (unmasked) mid-mood tag with two entries still parses."""
        # Minimal PMAI + raw PSSI (mood=2, 2 entries, no XOR).
        entries = bytearray()
        # index=1 beat=1 kind=1 Intro; index=2 beat=33 kind=9 Chorus
        entries += bytes.fromhex("000100010001000000000000000000000000000000000000")
        entries += bytes.fromhex("000200210009000000000000000000000000000000000000")
        assert len(entries) == 48
        body = (
            b"\x00\x00\x00\x18"  # len_entry_bytes
            + b"\x00\x02"        # len_entries
            + b"\x00\x02"        # mood mid
            + b"\x00" * 6
            + b"\x00\x40"        # end_beat
            + b"\x00\x00"
            + b"\x00"            # bank
            + b"\x00"
            + bytes(entries)
        )
        tag = b"PSSI" + b"\x00\x00\x00\x20" + (12 + len(body)).to_bytes(4, "big") + body
        header = bytearray(b"PMAI" + b"\x00\x00\x00\x1c" + b"\x00\x00\x00\x00" + b"\x00" * 16)
        file_bytes = bytes(header) + tag
        file_bytes = bytearray(file_bytes)
        struct_pack = __import__("struct").pack_into
        struct_pack(">I", file_bytes, 8, len(file_bytes))
        parsed = anlz.parse(bytes(file_bytes))
        self.assertEqual([(p.beat, p.label) for p in parsed.phrases],
                         [(1, "Intro"), (33, "Chorus")])


if __name__ == "__main__":
    unittest.main()
