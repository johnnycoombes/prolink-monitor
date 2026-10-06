"""The playhead highlight stays inside the loop instead of drifting out."""

from __future__ import annotations

import struct
import time
import unittest

from prolink import link, proto
from gui.pages import extrapolate_playhead


def _looping_status(**kwargs) -> proto.Status:
    status = proto.Status(
        track_id=kwargs.pop("track_id", 7),
        play_state="looping",
        play_state_raw=0x04,
        pitch=1.0,
        beat_count=kwargs.pop("beat_count", 1),
        beat_in_bar=1,
    )
    for key, value in kwargs.items():
        setattr(status, key, value)
    return status


def _abs(pos_ms: int) -> proto.AbsolutePosition:
    return proto.AbsolutePosition(
        device_number=1,
        name="XDJ-AZ",
        track_length_s=501,
        position_ms=pos_ms,
        pitch_percent=0.0,
        effective_bpm=128.0,
    )


def highlight_offset_px(pos_ms: float, loop_start: float, loop_end: float,
                        *, width: int = 800, needle: float = 0.25,
                        cps: float = 150.0, bpm: float = 128.0) -> float:
    """Pixels from the fixed needle to the start of the loop highlight.

    This is the same window the detail waveform uses. While the playhead stays
    inside the loop, the highlight stays within one loop-width of the needle.
    """
    visible_seconds = max(0.25, 4 * 4 * 60.0 / bpm)
    visible_cols = visible_seconds * cps
    px_per_col = width / visible_cols
    current = (pos_ms / 1000.0) * cps
    start = current - visible_cols * needle
    x0 = ((loop_start / 1000.0) * cps - start) * px_per_col
    return x0 - (width * needle)


class LoopWrapTests(unittest.TestCase):
    def test_short_backward_wrap_snaps_instead_of_smoothing(self):
        deck = link.Deck(1)
        # 180 ms loop — under JUMP_MS, so the old smoother would only pull
        # 20% of the way back and the playhead would walk forward each cycle.
        times = [0] * 12
        times[9] = 10000  # beat 10
        times[8] = 9820   # beat 9, the loop start
        # Prime the track first. A new track id drops the beat grid.
        deck.status = _looping_status(beat_count=10)
        deck.set_beat_grid(times, length_ms=501_000)
        now = time.monotonic()
        deck.on_status(_looping_status(beat_count=10), now)
        self.assertAlmostEqual(deck._model[0], 10000, delta=5)

        deck.on_status(_looping_status(beat_count=9), now)
        self.assertAlmostEqual(deck._model[0], 9820, delta=5)
        self.assertLess(deck._model[0], 9900)
        window = deck.loop_window()
        self.assertIsNotNone(window)
        assert window is not None
        self.assertLess(window[1] - window[0], link.JUMP_MS)

    def test_extrapolation_stays_inside_the_loop(self):
        deck = link.Deck(1)
        now = time.monotonic()
        deck.status = _looping_status()
        deck._loop_start_ms = 10000
        deck._loop_end_ms = 12000
        deck._loop_from_packet = True
        deck._model = (11980.0, now - 0.1, 1.0)
        pos = deck.position_ms
        self.assertGreaterEqual(pos, 10000)
        self.assertLess(pos, 12000)
        self.assertAlmostEqual(pos, 10080, delta=30)

    def test_absolute_position_that_keeps_counting_is_folded_into_the_loop(self):
        deck = link.Deck(1)
        now = time.monotonic()
        primed = _looping_status(loop_start_ms=10000, loop_end_ms=12000)
        deck.status = primed
        deck._model = (11000.0, now, 1.0)
        deck.on_status(primed, now)
        self.assertEqual(deck.loop_window(), (10000.0, 12000.0))

        offsets = []
        loop_px = ((12000 - 10000) / 1000.0) * 150.0
        # 150 cps and the highlight helper's px_per_col; compare in ms first.
        for n in range(12):
            raw = 10000 + n * 500  # climbs through the loop and out the far side
            deck.on_absolute_position(_abs(raw), now)
            pos = deck._model[0]
            self.assertGreaterEqual(pos, 10000, raw)
            self.assertLess(pos, 12000, raw)
            offset = highlight_offset_px(pos, 10000, 12000)
            offsets.append(offset)
            self.assertLessEqual(offset, 1.0)
            self.assertGreater(offset, -loop_px - 1.0)
        # Same point in the loop, two cycles apart: the highlight has not drifted.
        self.assertAlmostEqual(offsets[0], offsets[4], delta=1.0)
        self.assertAlmostEqual(offsets[1], offsets[5], delta=1.0)

    def test_client_extrapolation_does_not_run_past_the_loop(self):
        pos = extrapolate_playhead(
            11950, playing=True, speed=1.0, dt=0.2, duration_ms=501000,
            looping=True, loop_start_ms=10000, loop_end_ms=12000,
        )
        self.assertAlmostEqual(pos, 10150, delta=1)
        self.assertLess(pos, 12000)

    def test_non_loop_error_under_the_jump_threshold_is_still_smoothed(self):
        deck = link.Deck(1)
        times = [i * 500 for i in range(40)]
        now = time.monotonic()
        status = proto.Status(
            track_id=3, play_state="playing", play_state_raw=0x03,
            pitch=1.0, beat_count=10, bpm=120.0,
        )
        deck.status = status
        deck.set_beat_grid(times, length_ms=20000)
        deck.on_status(status, now)
        # 100 ms behind the next beat — noise, not a seek and not a loop wrap.
        deck._model = (4900.0, now, 1.0)
        deck.on_status(proto.Status(
            track_id=3, play_state="playing", play_state_raw=0x03,
            pitch=1.0, beat_count=11, bpm=120.0,
        ), now)
        # target 5000, error +100, smoothed by 20% → 4920. A snap would be 5000.
        self.assertAlmostEqual(deck._model[0], 4920, delta=5)
        self.assertLess(deck._model[0], 4960)
        self.assertIsNone(deck.loop_window())

    def test_stopping_the_loop_clears_the_window(self):
        deck = link.Deck(1)
        now = time.monotonic()
        primed = _looping_status(loop_start_ms=10000, loop_end_ms=12000)
        deck.status = primed
        deck._model = (11000.0, now, 1.0)
        deck.on_status(primed, now)
        self.assertIsNotNone(deck.loop_window())
        playing = proto.Status(
            track_id=7, play_state="playing", play_state_raw=0x03,
            pitch=1.0, beat_count=1,
        )
        deck.on_status(playing, now)
        self.assertIsNone(deck.loop_window())


class LoopPacketTests(unittest.TestCase):
    def _packet(self, length: int, start_raw: int = 0, end_raw: int = 0) -> bytes:
        pkt = bytearray(length)
        pkt[0:10] = proto.MAGIC
        pkt[10] = proto.TYPE_CDJ_STATUS
        pkt[0x0B:0x0B + 6] = b"XDJ-AZ"
        pkt[0x21] = 1
        struct.pack_into(">I", pkt, 0x2C, 42)  # track id
        struct.pack_into(">I", pkt, 0x78, 0x04)  # looping
        struct.pack_into(">I", pkt, 0x98, 0x100000)  # speed 1.0
        if length >= proto._LOOP_PACKET_MIN:
            struct.pack_into(">I", pkt, proto.LOOP_START_OFFSET, start_raw)
            struct.pack_into(">I", pkt, proto.LOOP_END_OFFSET, end_raw)
        return bytes(pkt)

    def test_cdj3000_scaling(self):
        start_raw = int(round(10000 * 1000 / 65536))
        end_raw = int(round(20000 * 1000 / 65536))
        parsed = proto.parse(self._packet(0x200, start_raw, end_raw))
        assert isinstance(parsed, proto.Status)
        self.assertAlmostEqual(parsed.loop_start_ms, 10000, delta=80)
        self.assertAlmostEqual(parsed.loop_end_ms, 20000, delta=80)
        self.assertEqual(parsed.play_state, "looping")

    def test_plain_milliseconds_when_scaling_is_not_a_loop(self):
        # 60s–90s as plain milliseconds. The documented scaling of that span
        # is longer than a loop, so the raw reading is the one we keep.
        parsed = proto.parse(self._packet(0x200, 60_000, 90_000))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.loop_start_ms, 60_000)
        self.assertEqual(parsed.loop_end_ms, 90_000)

    def test_short_packet_has_no_loop(self):
        parsed = proto.parse(self._packet(0xC0))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.loop_start_ms, 0)
        self.assertEqual(parsed.loop_end_ms, 0)

    def test_far_window_is_not_adopted(self):
        deck = link.Deck(1)
        now = time.monotonic()
        primed = _looping_status(beat_count=400, loop_start_ms=1000, loop_end_ms=2000)
        deck.status = primed
        deck.set_beat_grid([i * 500 for i in range(500)], length_ms=250_000)
        deck._model = (200_000.0, now, 1.0)
        deck._last_beat = 400
        deck.on_status(
            _looping_status(beat_count=400, loop_start_ms=1000, loop_end_ms=2000),
            now,
        )
        self.assertIsNone(deck.loop_window())
        self.assertGreater(deck._model[0], 190_000)


if __name__ == "__main__":
    unittest.main()
