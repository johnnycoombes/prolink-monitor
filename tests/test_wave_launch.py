"""Browser launch and waveform regressions after the panel/card change."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _anlz_payload() -> bytes:
    """Pack a short track the same way /api/waveform does."""
    from app import _pack_bands, _pack_blue, _pack_wave
    from prolink import anlz

    # PWV5 word: height 22 in bits 2-6, plus a little colour.
    word = (22 << 2) | (6 << 13) | (3 << 10) | (2 << 7)
    analysis = anlz.Analysis()
    analysis.color_detail = word.to_bytes(2, "big") * 48
    analysis.detail = bytes([0x40 | 16] * 48)          # blue shade + height
    analysis.band3_detail = bytes([210, 160, 240] * 48)  # mid, high, low
    heights, rgb, lows, mids, highs, blue_h, blue_rgb = anlz.waveform_levels(analysis)
    if not any(heights):
        raise AssertionError("ANLZ fixture produced an empty waveform")
    length = 8000
    cps = anlz.DETAIL_COLUMNS_PER_SECOND
    ov_h, ov_rgb = anlz.downsample(heights, rgb, 32)
    ov_l, ov_m, ov_hi = anlz.downsample_bands(lows, mids, highs, 32)
    ov_blue_h, ov_blue_rgb = anlz.downsample(blue_h, blue_rgb, 32)
    return (
        _pack_wave(heights, rgb, cps, length)
        + _pack_wave(ov_h, ov_rgb, 0.0, length)
        + _pack_bands(lows, mids, highs, cps, length)
        + _pack_bands(ov_l, ov_m, ov_hi, 0.0, length)
        + _pack_blue(blue_h, blue_rgb, cps, length)
        + _pack_blue(ov_blue_h, ov_blue_rgb, 0.0, length)
    )


class BrowserLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_open_overlay_and_panel_call_webbrowser(self):
        from gui.backend import Backend

        backend = Backend()
        backend._bound_port = 9
        with mock.patch("gui.backend.webbrowser.open") as opened:
            with mock.patch("subprocess.Popen") as popped:
                backend.open_overlay("http://127.0.0.1:9/overlay?style=card&preview=1")
                backend.open_web_panel()
        self.assertEqual(opened.call_count, 2)
        overlay_url = opened.call_args_list[0].args[0]
        panel_url = opened.call_args_list[1].args[0]
        self.assertEqual(overlay_url, "http://127.0.0.1:9/overlay?style=card&preview=1")
        self.assertEqual(panel_url, "http://127.0.0.1:9/")
        self.assertNotIn("kiosk", overlay_url)
        self.assertNotIn("kiosk", panel_url)
        popped.assert_not_called()

    def test_app_startup_uses_webbrowser_not_kiosk(self):
        path = os.path.join(ROOT, "app.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("webbrowser.open", src)
        self.assertNotIn("--kiosk", src)
        self.assertNotIn("--edge-kiosk-type", src)
        self.assertNotIn("--start-fullscreen", src)
        self.assertNotIn("open_monitor_panel", src)


class WaveformPayloadTests(unittest.TestCase):
    def test_anlz_pack_is_plwf_and_js_renders_it(self):
        payload = _anlz_payload()
        self.assertTrue(payload.startswith(b"PLWF"))
        self.assertIn(b"PLWB", payload)
        self.assertIn(b"PLBC", payload)
        # Heights live just after the 24-byte header and are not all zero.
        _magic, _ver, n, _cps, _dur, _flags = struct.unpack_from("<4sIIfII", payload, 0)
        heights = payload[24:24 + n]
        self.assertGreater(n, 0)
        self.assertTrue(any(heights))

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(payload)
            path = tmp.name
        script = r"""
const fs = require("fs");
const {decodeWavePack, overviewInk} = require("./web/wavepack.js");
const file = process.argv[1];
const b = fs.readFileSync(file);
const ab = b.buffer.slice(b.byteOffset, b.byteOffset + b.length);
const pack = decodeWavePack(ab);
if (!pack.detail || !pack.detail.n) {
  console.error("detail missing");
  process.exit(1);
}
if (!pack.overview || !pack.overview.n) {
  console.error("overview missing");
  process.exit(1);
}
if (!pack.detail.low || !pack.detail.mid || !pack.detail.high) {
  console.error("3-band lanes were dropped");
  process.exit(1);
}
if (!pack.detail.blueH) {
  console.error("blue waveform was dropped");
  process.exit(1);
}
const ink = overviewInk(pack.overview, 160);
if (!(ink > 0)) {
  console.error("overview ink", ink);
  process.exit(1);
}
const off = pack.overview.end;
const indexed = String.fromCharCode(ab[off], ab[off + 1], ab[off + 2], ab[off + 3]);
if (indexed === "PLWB") {
  console.error("ArrayBuffer index read should not see PLWB");
  process.exit(1);
}
console.log("ok", pack.detail.n, ink);
"""
        try:
            proc = subprocess.run(
                [node, "-e", script, path],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_title_without_wave_is_retried(self):
        from app import Monitor
        from prolink import anlz
        from prolink.track_key import track_cache_key

        monitor = object.__new__(Monitor)
        monitor._lock = threading.RLock()
        monitor._waveforms = {}
        monitor._meta = {}
        monitor._deck_keys = {}
        monitor.engine = mock.Mock()
        monitor.engine.decks = {}
        key = track_cache_key("10.0.0.8", "export", None, "usb", 9)
        monitor._deck_keys[1] = key
        monitor._meta[key] = {"id": 9, "title": "Early title", "track_key": "tok"}

        analysis = anlz.Analysis()
        word = (18 << 2) | (4 << 13)
        analysis.color_detail = word.to_bytes(2, "big") * 16
        media = mock.Mock()
        media.export = "export"
        media._pdb_fingerprint = None
        media.copied_track.return_value = (mock.Mock(
            title="Early title", artist="A", album="", genre="", key="",
            label="", comment="", year=0, rating=0, tempo=120.0, duration=4,
            bitrate=320, artwork_path="", artwork_id=0, file_path="",
        ), "nfs")
        media.analysis.return_value = analysis
        monitor._media_for_key = lambda _key: ("10.0.0.8", media)

        data = monitor.waveform(9, deck=1)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"PLWF"))
        media.analysis.assert_called()

        media.analysis.reset_mock()
        media.analysis.return_value = None
        monitor._waveforms.clear()
        monitor._wave_retry_at = {}
        self.assertIsNone(monitor.waveform(9, deck=1))
        self.assertIsNone(monitor.waveform(9, deck=1))
        self.assertEqual(media.analysis.call_count, 1)


def _pmai(*tags: bytes) -> bytes:
    header = bytearray(b"PMAI" + b"\x00\x00\x00\x1c" + b"\x00\x00\x00\x00" + b"\x00" * 16)
    file_bytes = bytearray(header)
    file_bytes.extend(b"".join(tags))
    struct.pack_into(">I", file_bytes, 8, len(file_bytes))
    return bytes(file_bytes)


def _pwv5(count: int = 8) -> bytes:
    word = (22 << 2) | (6 << 13) | (3 << 10) | (2 << 7)
    pixels = word.to_bytes(2, "big") * count
    body = struct.pack(">II", 2, count) + b"\x00\x00\x00\x00" + pixels
    return b"PWV5" + struct.pack(">II", 24, 12 + len(body)) + body


class AzRemotedbWaveTests(unittest.TestCase):
    """XDJ-AZ: titles come from dbserver, waveforms from NFS ANLZ."""

    def _monitor(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._lock = threading.RLock()
        mon._deck_keys = {}
        mon._meta = {}
        mon._waveforms = {}
        mon._wave_retry_at = {}
        mon.library = mock.Mock()
        mon.engine = mock.Mock()
        mon.engine.decks = {}
        return mon

    def _deck(self, number: int, track_id: int):
        from prolink import proto

        deck = mock.Mock()
        deck.number = number
        deck.status = proto.Status(
            slot="usb", slot_raw=3, track_type="rekordbox", track_type_raw=1,
            track_id=track_id, name="XDJ-AZ",
        )
        return deck

    def _analysis(self):
        from prolink import anlz

        analysis = anlz.Analysis()
        word = (22 << 2) | (6 << 13) | (3 << 10) | (2 << 7)
        analysis.color_detail = word.to_bytes(2, "big") * 48
        analysis.beats = [anlz.Beat(1, 128.0, 200_000)]
        return analysis

    def _media(self, analysis):
        from prolink.pdb import Track

        media = mock.Mock()
        media.export = "PIONEER/rekordbox"
        media._pdb_fingerprint = (10, 20)
        media.copied_track.return_value = (Track(
            id=77, title="", artist="",
            analyze_path="/PIONEER/USBANLZ/x/ANLZ0000.DAT",
            artwork_id=0, tempo=128.0, duration=200,
        ), "onelibrary")
        media.analysis.return_value = analysis
        return media

    def test_title_from_remotedb_then_anlz_uses_one_cache_key(self):
        from prolink.track_key import track_key_token

        mon = self._monitor()
        # dbserver answers before the USB is mounted, which is the live AZ path.
        mon.library.get.return_value = None
        mon._fetch_live_metadata = lambda *args, **kwargs: {
            "title": "From the player",
            "artist": "AZ",
            "artwork_id": 99,
            "tempo": 128.0,
            "duration_s": 200,
        }
        mon._host_for = lambda status: "10.0.0.8"
        deck = self._deck(2, 77)
        mon.engine.decks = {2: deck}
        mon._on_track_change(deck, 77)

        self.assertEqual(mon._waveforms, {})
        early = next(iter(mon._meta.values()))
        self.assertEqual(early["title"], "From the player")
        self.assertEqual(early["library_source"], "remotedb")
        self.assertEqual(early["detail_columns"], 0)
        self.assertEqual(early["track_key"], mon.deck_track_key(2, 77))

        mon.library.get.return_value = self._media(self._analysis())
        data = mon.waveform(77, deck=2)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"PLWF"))
        stored = mon._deck_keys[2]
        self.assertEqual(stored[1], "PIONEER/rekordbox")
        self.assertIs(mon._waveforms.get(stored), data)
        meta = mon.meta(77, deck=2, load=False)
        self.assertEqual(meta["title"], "From the player")
        self.assertEqual(meta["artist"], "AZ")
        self.assertEqual(meta["artwork_id"], 99)
        self.assertEqual(meta["library_source"], "remotedb")
        self.assertGreater(meta["detail_columns"], 0)
        # state() copies deck_track_key. /api/waveform rejects any other token.
        self.assertEqual(meta["track_key"], mon.deck_track_key(2, 77))
        self.assertEqual(meta["track_key"], track_key_token(stored))

    def test_failed_anlz_is_retried_without_changing_the_key(self):
        mon = self._monitor()
        media = self._media(None)
        media.analysis.return_value = None
        mon.library.get.return_value = media
        mon._fetch_live_metadata = lambda *args, **kwargs: {
            "title": "Night", "artist": "AZ", "artwork_id": 4, "tempo": 126.0,
        }
        mon._host_for = lambda status: "10.0.0.8"
        deck = self._deck(1, 77)
        mon.engine.decks = {1: deck}
        mon._on_track_change(deck, 77)
        self.assertEqual(next(iter(mon._meta.values()))["detail_columns"], 0)
        bound = mon._deck_keys[1]

        media.analysis.return_value = self._analysis()
        data = mon.waveform(77, deck=1)
        self.assertTrue(data.startswith(b"PLWF"))
        self.assertEqual(mon._deck_keys[1], bound)
        meta = mon.meta(77, deck=1, load=False)
        self.assertEqual(meta["track_key"], mon.deck_track_key(1, 77))
        self.assertGreater(meta["detail_columns"], 0)
        self.assertEqual(meta["title"], "Night")


class AnlzRetryTests(unittest.TestCase):
    def test_missing_ext_is_not_cached(self):
        from collections import OrderedDict

        from prolink import anlz
        from prolink.library import Media
        from prolink.pdb import Track

        media = Media.__new__(Media)
        media.lock = threading.RLock()
        media._analysis = OrderedDict()
        media._max_analysis = 4
        media.cache_dir = tempfile.mkdtemp()
        fetches = {"ext": 0}

        def fetch(remote, cache_name):
            if remote.endswith(".DAT"):
                return _pmai()
            if remote.endswith(".EXT"):
                fetches["ext"] += 1
                if fetches["ext"] == 1:
                    return None
                return _pmai(_pwv5())
            return None

        media._fetch = fetch
        track = Track(id=3, title="T", analyze_path="/PIONEER/USBANLZ/x/ANLZ0000.DAT")
        first = media.analysis(3, track=track)
        self.assertIsNotNone(first)
        self.assertFalse(first.color_detail)
        self.assertNotIn(3, media._analysis)
        second = media.analysis(3, track=track)
        self.assertTrue(second.color_detail)
        self.assertIn(3, media._analysis)
        parsed = anlz.parse(_pmai(_pwv5()))
        self.assertTrue(parsed.color_detail)


class DesktopWaveAttachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_artwork_error_still_draws_the_wave(self):
        from gui.i18n import I18n
        from gui.pages import MonitorPage

        payload = _anlz_payload()
        backend = mock.Mock()
        backend.meta.return_value = {
            "id": 7, "track_key": "abc", "title": "Night Drive", "artist": "Demo",
            "album": "", "genre": "", "label": "", "year": 0, "key": "8A",
            "has_artwork": True, "duration_ms": 8000, "cues": [], "beats": [],
            "phrases": [],
        }
        backend.waveform.return_value = payload
        backend.artwork.side_effect = RuntimeError("bad jpeg")
        page = MonitorPage(I18n(), backend)
        page.update_state({
            "decks": [{
                "number": 1, "track_id": 7, "track_key": "abc", "playing": True,
                "position_ms": 1000, "duration_ms": 8000, "bpm": 120,
                "track_bpm": 120, "speed": 1, "state": "playing", "name": "XDJ-AZ",
            }],
        })
        card = page._cards[1]
        self.assertIsNotNone(card._detail)
        self.assertGreater(card._detail["n"], 0)
        self.assertIsNotNone(card._overview)
        self.assertGreater(card._overview["n"], 0)
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
