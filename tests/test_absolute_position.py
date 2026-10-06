"""Absolute / Precise Position packet parsing and playhead use."""

from __future__ import annotations

import struct
import time
import unittest

from prolink import link, proto


def _abs_packet(*, device: int = 1, track_s: int = 360, pos_ms: int = 12345,
                pitch_x100: int = 326, bpm_x10: int = 1202) -> bytes:
    """Build a minimal 60-byte Absolute Position packet."""
    pkt = bytearray(0x3C)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_ABSOLUTE_POSITION
    pkt[0x0B:0x0B + 8] = b"CDJ-3000"
    pkt[0x20] = 0x00
    pkt[proto.ABS_POS_DEVICE] = device
    struct.pack_into(">H", pkt, 0x22, 0x3C - 0x24)
    struct.pack_into(">I", pkt, proto.ABS_POS_TRACK_LEN, track_s)
    struct.pack_into(">I", pkt, proto.ABS_POS_PLAYHEAD, pos_ms)
    struct.pack_into(">i", pkt, proto.ABS_POS_PITCH, pitch_x100)
    struct.pack_into(">I", pkt, proto.ABS_POS_BPM, bpm_x10)
    return bytes(pkt)


class AbsolutePositionParseTests(unittest.TestCase):
    def test_parse_fields(self):
        pkt = _abs_packet(device=3, track_s=245, pos_ms=65432, pitch_x100=-50, bpm_x10=1280)
        parsed = proto.parse(pkt)
        self.assertIsInstance(parsed, proto.AbsolutePosition)
        assert isinstance(parsed, proto.AbsolutePosition)
        self.assertEqual(parsed.device_number, 3)
        self.assertEqual(parsed.track_length_s, 245)
        self.assertEqual(parsed.position_ms, 65432)
        self.assertAlmostEqual(parsed.pitch_percent, -0.5)
        self.assertAlmostEqual(parsed.effective_bpm, 128.0)
        self.assertAlmostEqual(parsed.speed, 0.995)

    def test_too_short_rejected(self):
        pkt = _abs_packet()[:40]
        self.assertIsNone(proto.parse(pkt))


class AbsolutePositionPlayheadTests(unittest.TestCase):
    def test_exact_overrides_beat_grid(self):
        deck = link.Deck(1)
        # Pretend we had a beat-grid estimate first.
        deck._model = (1000.0, time.monotonic(), 1.0)
        deck.position_source = "beat_grid"
        deck.status = proto.Status(track_id=9, play_state_raw=0x03, pitch=1.0)
        deck.status.playing = True

        ap = proto.parse(_abs_packet(pos_ms=5000, pitch_x100=0))
        assert isinstance(ap, proto.AbsolutePosition)
        now = time.monotonic()
        deck.on_absolute_position(ap, now)
        self.assertEqual(deck.position_source, "exact")
        self.assertEqual(deck.absolute_packets, 1)
        self.assertTrue(deck._exact)
        self.assertAlmostEqual(deck._model[0], 5000.0)

        # Later status with a different beat must not yank the playhead.
        status = proto.Status(track_id=9, play_state_raw=0x03, pitch=1.0, beat_count=40)
        status.playing = True
        deck.on_status(status, now + 0.05)
        self.assertEqual(deck.position_source, "exact")
        # Model still extrapolates from the exact anchor, not beat 40.
        self.assertLess(abs(deck._model[0] - 5000.0), 200.0)

    def test_abs_length_seconds_vs_milliseconds(self):
        self.assertEqual(link._abs_track_length_ms(245), 245_000)
        # Values too large to be seconds are treated as already-ms (XDJ-AZ).
        self.assertEqual(link._abs_track_length_ms(649_750), 649_750)

    def test_abs_length_does_not_inflate_grid(self):
        deck = link.Deck(1)
        deck.track_length_ms = 650_000  # from ANLZ beat grid (~10:50)
        deck.status = proto.Status(track_id=1, play_state_raw=0x03, pitch=1.0)
        deck.status.playing = True
        # Firmware sent milliseconds in the seconds field (would become ~180h if *1000).
        ap = proto.parse(_abs_packet(track_s=649_750, pos_ms=372_000, pitch_x100=0))
        assert isinstance(ap, proto.AbsolutePosition)
        deck.on_absolute_position(ap, time.monotonic())
        self.assertEqual(deck.track_length_ms, 650_000)

    def test_metadata_length_replaces_an_inflated_packet(self):
        deck = link.Deck(1)
        deck.status = proto.Status(track_id=30638, play_state_raw=0x03, pitch=1.0)
        ap = proto.parse(_abs_packet(track_s=655_861, pos_ms=12_000, pitch_x100=0))
        assert isinstance(ap, proto.AbsolutePosition)
        deck.on_absolute_position(ap, time.monotonic())
        self.assertEqual(deck.track_length_ms, 655_861)
        deck.note_metadata_duration(501_000)
        self.assertEqual(deck.track_length_ms, 501_000)
        self.assertEqual(deck.metadata_duration_ms, 501_000)
        again = proto.parse(_abs_packet(track_s=655_861, pos_ms=20_000, pitch_x100=0))
        assert isinstance(again, proto.AbsolutePosition)
        deck.on_absolute_position(again, time.monotonic())
        self.assertEqual(deck.track_length_ms, 501_000)


if __name__ == "__main__":
    unittest.main()
