"""Reconnect, header-only artwork, PWVC, playhead smoothing, and the countdown."""

from __future__ import annotations

import struct
import time
import unittest
from unittest import mock

from prolink import anlz, artwork as artmod
from prolink import remotedb as wire
from prolink.art_header import cover_from_ranges
from prolink.countdown import format_countdown, next_mark, urgency_color
from prolink.db_recovery import STATS, is_connection_drop
from prolink.link import Deck
from prolink.nfs import NfsClient
from prolink.playhead_smooth import correction_ms, freeze_instant
from prolink.proto import Status
from prolink.remotedb_client import RemoteDbBrowser
from prolink.vocal import vocal_regions, VocalThresholds


def _syncsafe(n: int) -> bytes:
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def _id3_with_jpeg() -> bytes:
    jpeg = b"\xff\xd8\xff" + b"J" * 32 + b"\xff\xd9"
    payload = bytes([0]) + b"image/jpeg\x00" + bytes([3]) + b"\x00" + jpeg
    frame = b"APIC" + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
    return b"ID3" + bytes([3, 0, 0]) + _syncsafe(len(frame)) + frame


def _pmai(*tags: bytes) -> bytes:
    body = b"".join(tags)
    header = b"PMAI" + struct.pack(">II", 28, 28 + len(body)) + (b"\x00" * 16)
    return header + body


def _tag(fourcc: bytes, content: bytes) -> bytes:
    return fourcc + struct.pack(">II", 12, 12 + len(content)) + content


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self._reconnects = STATS.reconnects
        STATS.reconnects = 0
        STATS.last_error = ""

    def tearDown(self):
        STATS.reconnects = self._reconnects

    def _browser(self):
        browser = RemoteDbBrowser(host="192.0.2.10", target_player=1, requesting_player=2)
        browser.sleeper = lambda _delay: None
        browser._sock = object()

        def connect():
            browser._sock = object()
            browser._dead = False

        browser.connect = connect
        return browser

    def test_dropped_request_is_retried_after_backoff(self):
        browser = self._browser()
        delays = []
        browser.sleeper = delays.append
        calls = {"n": 0}

        def once():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("connection reset by peer")
            return {"title": "Night Drive"}

        self.assertEqual(browser.call(once), {"title": "Night Drive"})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(delays, [0.05])
        self.assertEqual(STATS.reconnects, 1)
        self.assertIn("reset", STATS.last_error)

    def test_menu_unavailable_is_not_a_reconnect(self):
        browser = self._browser()

        def once():
            raise wire.DbServerError("menu unavailable type 0x1")

        with self.assertRaises(wire.DbServerError):
            browser.call(once)
        self.assertEqual(STATS.reconnects, 0)
        self.assertFalse(is_connection_drop(wire.DbServerError("menu unavailable type 0x1")))

    def test_album_art_retries_the_in_flight_read(self):
        browser = self._browser()
        reads = {"n": 0}

        def read_message(_sock):
            reads["n"] += 1
            if reads["n"] == 1:
                raise wire.DbServerError("unexpected EOF on dbserver socket")
            return wire.DbMessage(
                tx_id=browser._tx,
                msg_type=wire.TYPE_ALBUM_ART_RESP,
                args=[0, 0, 0, b"\xff\xd8\xffJPEG"],
            )

        class Sock:
            def sendall(self, _data):
                return None

            def close(self):
                return None

        browser._sock = Sock()
        browser.connect = lambda: setattr(browser, "_sock", Sock()) or setattr(browser, "_dead", False)
        with mock.patch.object(wire, "read_message", read_message):
            blob = browser.album_art(42, high_res=True)
        self.assertEqual(blob, b"\xff\xd8\xffJPEG")
        self.assertEqual(reads["n"], 2)
        self.assertEqual(STATS.reconnects, 1)


class HeaderArtTests(unittest.TestCase):
    def test_ranged_read_never_pulls_the_audio(self):
        tag = _id3_with_jpeg()
        total = 8_000_000
        blob = tag + (b"\x00" * (total - len(tag)))
        reads = []

        class Medium:
            def remote_size(self, _remote):
                return total

            def read_range(self, _remote, offset, length):
                reads.append((int(offset), int(length)))
                return blob[int(offset):int(offset) + int(length)]

            def _fetch(self, *_args, **_kwargs):
                raise AssertionError("artwork read the whole audio file")

        track = mock.Mock(file_path="contents/long.mp3", artwork_path="", artwork_id=0)
        data, source = artmod.resolve_artwork(
            track, Medium(), host="h", slot="usb",
            open_remotedb=None, local_music_root="", size="large",
        )
        self.assertEqual(source, "embedded")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        self.assertTrue(reads)
        for offset, length in reads:
            self.assertLess(offset + length, len(tag) + 64)
            self.assertLess(offset + length, total // 10)

    def test_cover_from_ranges_stops_at_the_tag(self):
        tag = _id3_with_jpeg()
        asked = []

        def read(offset, length):
            asked.append((offset, length))
            return tag[offset:offset + length]

        found = cover_from_ranges(read, 8_000_000, "song.mp3")
        self.assertTrue(found.startswith(b"\xff\xd8\xff"))
        self.assertLess(max(off + n for off, n in asked), len(tag) + 8)


class PwvcTests(unittest.TestCase):
    def test_pwvc_and_pwv6_become_vocal_spans(self):
        beats = (
            b"\x00" * 8
            + struct.pack(">I", 2)
            + struct.pack(">HHI", 1, 12000, 0)
            + struct.pack(">HHI", 2, 12000, 10000)
        )
        # mid, high, low. First column is vocal; the second is not.
        columns = bytes([200, 10, 10, 10, 10, 200])
        pwv6 = struct.pack(">II", 3, 2) + columns
        pwvc = struct.pack(">HHHH", 0, 100, 80, 150)
        parsed = anlz.parse(_pmai(_tag(b"PQTZ", beats), _tag(b"PWV6", pwv6), _tag(b"PWVC", pwvc)))
        self.assertTrue(parsed.has_vocals)
        self.assertEqual((parsed.vocal_low, parsed.vocal_mid, parsed.vocal_high), (100, 80, 150))
        spans = anlz.vocal_spans(parsed)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["t"], 0.0)
        self.assertAlmostEqual(spans[0]["end"], 5000.0, delta=1)

    def test_detection_uses_mid_high_low_order(self):
        # mid above, low under, high under → vocal. Swapped order would miss it.
        band = bytes([200, 10, 10])
        spans = vocal_regions(band, VocalThresholds(100, 80, 150), 3000)
        self.assertEqual(spans, [{"t": 0.0, "end": 3000.0}])
        quiet = bytes([10, 10, 10])
        self.assertEqual(vocal_regions(quiet, VocalThresholds(100, 80, 150), 3000), [])


class SmoothingTests(unittest.TestCase):
    def _playing(self, beat: int, speed_pitch: float = 1.0) -> Status:
        return Status(
            track_id=3, play_state="playing", play_state_raw=0x03,
            pitch=speed_pitch, beat_count=beat, bpm=120.0,
        )

    def test_large_error_is_capped_not_snapped(self):
        deck = Deck(1)
        now = time.monotonic()
        deck.status = self._playing(10)
        deck.set_beat_grid([i * 500 for i in range(40)], length_ms=20000)
        deck.on_status(self._playing(10), now)
        deck._model = (4900.0, now, 1.0)
        deck.smoothing = "normal"
        deck.on_status(self._playing(11), now)
        self.assertAlmostEqual(deck._model[0], 4925, delta=1)
        self.assertLess(deck._model[0], 4960)

    def test_small_error_uses_the_tighter_cap(self):
        step = correction_ms(40, 1.0, "normal")
        self.assertLess(step, 40)
        self.assertAlmostEqual(step, correction_ms(1000, 1.0, "normal") * (0.08 / 0.15), delta=1)

    def test_direct_snaps_and_strong_is_tighter(self):
        self.assertEqual(correction_ms(100, 1.0, "direct"), 100)
        self.assertLess(correction_ms(100, 1.0, "strong"), correction_ms(100, 1.0, "normal"))

    def test_reverse_resyncs_immediately(self):
        deck = Deck(1)
        now = time.monotonic()
        deck.status = self._playing(10)
        deck.set_beat_grid([i * 500 for i in range(40)], length_ms=20000)
        deck.on_status(self._playing(10), now)
        deck._model = (4900.0, now, 1.0)
        deck.on_status(self._playing(11, speed_pitch=-1.0), now)
        self.assertAlmostEqual(deck._model[0], 5000, delta=5)

    def test_freeze_after_one_second(self):
        now = time.monotonic()
        self.assertEqual(freeze_instant(now, now - 5), now - 5 + 1)
        self.assertEqual(freeze_instant(now, now - 0.2), now)
        deck = Deck(1)
        deck.status = self._playing(1)
        deck.last_seen = now - 5
        deck._model = (1000.0, now - 5, 1.0)
        self.assertAlmostEqual(deck.position_ms, 2000, delta=30)

    def test_nfs_read_at_starts_at_the_offset(self):
        seen = []

        class Rpc:
            def call(self, _proc, payload):
                offset, count, _zero = struct.unpack_from(">III", payload, len(b"fh"))
                seen.append((offset, count))
                chunk = b"abc"
                fattr = struct.pack(">17I", 1, 0, 1, 0, 0, 3, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)
                pad = b"\x00" * (-len(chunk) % 4)
                data = struct.pack(">I", 0) + fattr + struct.pack(">I", len(chunk)) + chunk + pad
                from prolink.nfs import _Unpacker
                return _Unpacker(data)

        client = NfsClient.__new__(NfsClient)
        client._nfs = Rpc()
        got = client.read_at(b"fh", 4096, 3)
        self.assertEqual(got, b"abc")
        self.assertEqual(seen, [(4096, 3)])


class CountdownTests(unittest.TestCase):
    def test_seek_picks_the_next_mark_and_skips_loops(self):
        cues = [
            {"t": 8000, "type": "loop", "text": "LOOP"},
            {"t": 10000, "type": "cue", "hot": 1, "text": "Drop"},
            {"t": 20000, "type": "cue", "hot": 0},
        ]
        phrases = [{"t": 30000, "text": "Outro"}]
        early = next_mark(5000, cues=cues, phrases=phrases)
        self.assertEqual(early["name"], "Drop")
        self.assertAlmostEqual(early["remain_ms"], 5000, delta=1)
        later = next_mark(15000, cues=cues, phrases=phrases)
        self.assertEqual(later["name"], "MEMORY")
        self.assertEqual(format_countdown(early), "NEXT: Drop in 0:05")
        self.assertEqual(urgency_color(2000), "#ff3b30")
        self.assertEqual(urgency_color(8000), "#ff9f45")
        self.assertEqual(urgency_color(20000), "#e8eaf0")

    def test_phrase_without_a_timestamp_uses_the_beat_grid(self):
        mark = next_mark(100, phrases=[{"beat": 2, "text": "Verse"}], beats=[[0, 1], [4000, 2]])
        self.assertEqual(mark["name"], "Verse")
        self.assertEqual(mark["t"], 4000)


if __name__ == "__main__":
    unittest.main()
