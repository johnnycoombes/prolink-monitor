"""XDJ-AZ: OneLibrary can be readable with no content rows and still hide nothing.

Waveforms come from ANLZ files named by export.pdb. A readable OneLibrary
whose content table is empty must not stop that fetch. The Absolute Position
track-length field is a separate value and must not replace the real length.
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink import anlz, link, onelibrary, proto, remotedb as wire
from prolink.library import Media
from prolink.pdb import Track

ANLZ_PATH = "/PIONEER/USBANLZ/P016/000077AE/ANLZ0000.DAT"


def _abs_packet(*, track_s: int, pos_ms: int = 1000) -> bytes:
    pkt = bytearray(0x3C)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_ABSOLUTE_POSITION
    pkt[0x0B:0x0B + 8] = b"XDJ-AZ\x00\x00"
    pkt[proto.ABS_POS_DEVICE] = 1
    struct.pack_into(">H", pkt, 0x22, 0x3C - 0x24)
    struct.pack_into(">I", pkt, proto.ABS_POS_TRACK_LEN, track_s)
    struct.pack_into(">I", pkt, proto.ABS_POS_PLAYHEAD, pos_ms)
    struct.pack_into(">i", pkt, proto.ABS_POS_PITCH, 0)
    struct.pack_into(">I", pkt, proto.ABS_POS_BPM, 1280)
    return bytes(pkt)


def _pwv5(count: int = 8) -> bytes:
    word = (22 << 2) | (6 << 13) | (3 << 10) | (2 << 7)
    pixels = word.to_bytes(2, "big") * count
    body = struct.pack(">II", 2, count) + b"\x00\x00\x00\x00" + pixels
    return b"PWV5" + struct.pack(">II", 24, 12 + len(body)) + body


def _analysis():
    found = anlz.Analysis()
    word = (22 << 2) | (6 << 13) | (3 << 10) | (2 << 7)
    found.color_detail = word.to_bytes(2, "big") * 48
    found.beats = [anlz.Beat(1, 128.0, 501_000)]
    found.cues = [anlz.Cue(1, "cue", 4_000, 0, comment="Drop")]
    return found


class AnlzWireTests(unittest.TestCase):
    def test_fourcc_and_tag_request(self):
        self.assertEqual(wire.fourcc_code("PWV5"), 0x50575635)
        self.assertEqual(wire.fourcc_code("EXT"), 0x45585400)
        self.assertEqual(wire.fourcc_code("2EX"), 0x32455800)
        self.assertEqual(wire.fourcc_code("DAT"), 0x44415400)
        pkt = wire.encode_anlz_tag_request(
            4, 5, wire.SLOT_USB, wire.TRACK_REKORDBOX, 30638, "PWV5", "EXT")
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.msg_type, wire.TYPE_ANLZ_TAG)
        self.assertEqual(msg.msg_type, 0x2C04)
        self.assertEqual(msg.args[1], 30638)
        self.assertEqual(msg.args[2], 0x50575635)
        self.assertEqual(msg.args[3], 0x45585400)
        rmst = wire.build_rmst(5, wire.MENU_MAIN, wire.SLOT_USB, wire.TRACK_REKORDBOX)
        self.assertEqual(msg.args[0], rmst)

    def test_tag_blob_becomes_a_detail_wave(self):
        found = anlz.analysis_from_tag_blob(_pwv5(16))
        self.assertIsNotNone(found)
        self.assertTrue(found.color_detail)
        self.assertGreater(len(found.color_detail), 8)


class OneLibraryRawContentTests(unittest.TestCase):
    def test_empty_orm_still_reads_the_analysis_path(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE content ("
            "content_id INTEGER, title TEXT, artist TEXT, "
            "analysisDataFilePath TEXT, length INTEGER)"
        )
        conn.execute(
            "INSERT INTO content VALUES (30638, 'Night Drive', 'AZ', ?, 501000)",
            (ANLZ_PATH,),
        )
        conn.commit()

        def sql_rows(session, statement, params=None):
            cur = session.execute(statement, params or {})
            return cur.fetchall()

        class DB:
            session = conn

            def get_content(self, **_kwargs):
                return []

            def get_playlist(self):
                return [object()] * 63

            def get_history(self):
                return [object()] * 28

        with mock.patch("prolink.onelibrary._sql_rows", sql_rows):
            summary = onelibrary.summarize(DB(), present=True)
            track = onelibrary.find_content(DB(), 30638)
        self.assertGreater(summary.tracks, 0)
        self.assertIn("content=1", summary.detail)
        self.assertEqual(summary.playlists, 63)
        self.assertIsNotNone(track)
        self.assertEqual(track.title, "Night Drive")
        self.assertEqual(track.artist, "AZ")
        self.assertEqual(track.analyze_path, ANLZ_PATH)


class AzFourDeckAnlzTests(unittest.TestCase):
    def _monitor(self, media):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._lock = threading.RLock()
        mon._deck_keys = {}
        mon._meta = {}
        mon._waveforms = {}
        mon._anlz_by_deck = {}
        mon._wave_retry_at = {}
        mon.library = mock.Mock()
        mon.library.get.return_value = media
        mon.library.retry = mock.Mock()
        mon.engine = mock.Mock()
        mon.engine.decks = {}
        mon._fetch_live_metadata = lambda *args, **kwargs: {
            "title": "Night Drive",
            "artist": "AZ",
            "album": "LP",
            "artwork_id": 9,
            "tempo": 128.0,
            "duration_s": 501,
        }
        mon._host_for = lambda status: "192.168.1.212"
        return mon

    def _deck(self):
        deck = link.Deck(1)
        deck.status = proto.Status(
            slot="usb", slot_raw=3, track_type="rekordbox", track_type_raw=1,
            track_id=30638, name="XDJ-AZ", loaded_from=1,
        )
        ap = proto.parse(_abs_packet(track_s=655_861))
        assert isinstance(ap, proto.AbsolutePosition)
        deck.on_absolute_position(ap, time.monotonic())
        return deck

    def _media(self, track, analysis):
        media = Media.__new__(Media)
        media.export = "C"
        media._pdb_fingerprint = (10, 20)
        media.track = lambda track_id: track if track_id == 30638 else None
        media.onelibrary_track = lambda track_id: None
        media.onelibrary = onelibrary.OneLibrarySummary(
            present=True, readable=True, tracks=0, playlists=63, history=28,
            error="get_content returned no rows; content=0",
        )
        media.db = None
        media.analysis = mock.Mock(return_value=analysis)
        return media

    def test_empty_onelibrary_still_fetches_export_pdb_anlz(self):
        analysis = _analysis()
        track = Track(
            id=30638, title="Night Drive", artist="AZ", duration=501,
            analyze_path=ANLZ_PATH, tempo=128.0,
        )
        media = self._media(track, analysis)
        mon = self._monitor(media)
        deck = self._deck()
        self.assertEqual(deck.track_length_ms, 655_861)
        mon.engine.decks = {1: deck}
        mon._on_track_change(deck, 30638)

        media.analysis.assert_called_once()
        used = media.analysis.call_args.kwargs.get("track")
        if used is None and media.analysis.call_args.args:
            used = media.analysis.call_args.args[-1]
        self.assertEqual(getattr(used, "analyze_path", ""), ANLZ_PATH)

        data = mon.waveform(30638, deck=1)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"PLWF"))
        meta = mon.meta(30638, deck=1, load=False)
        self.assertEqual(meta["title"], "Night Drive")
        self.assertEqual(meta["artist"], "AZ")
        self.assertEqual(meta["library_source"], "remotedb")
        self.assertGreater(meta["detail_columns"], 0)
        self.assertEqual(meta["beats"], [[501_000, 1]])
        self.assertEqual(meta["cues"][0]["text"], "Drop")
        self.assertEqual(deck.track_length_ms, 501_000)

        again = proto.parse(_abs_packet(track_s=655_861, pos_ms=30_000))
        assert isinstance(again, proto.AbsolutePosition)
        deck.on_absolute_position(again, time.monotonic())
        self.assertEqual(deck.track_length_ms, 501_000)
        self.assertEqual(mon._deck_duration_ms(deck), 501_000)

        row = mon._anlz_by_deck[1]
        self.assertEqual(row["source"], "pdb-id")
        self.assertEqual(row["path"], ANLZ_PATH)
        self.assertEqual(row["error"], "")
        self.assertIn("title matches", row["detail"])
        self.assertIn("content=0", row["detail"])

    def test_dbserver_tags_when_no_analysis_path(self):
        analysis = _analysis()
        track = Track(id=30638, title="Night Drive", artist="AZ", duration=501)
        media = self._media(track, analysis)
        mon = self._monitor(media)
        mon._analysis_from_dbserver = mock.Mock(return_value=(analysis, ""))
        deck = self._deck()
        mon.engine.decks = {1: deck}
        mon._on_track_change(deck, 30638)
        media.analysis.assert_not_called()
        mon._analysis_from_dbserver.assert_called_once()
        data = mon.waveform(30638, deck=1)
        self.assertTrue(data.startswith(b"PLWF"))
        self.assertEqual(mon._anlz_by_deck[1]["source"], "dbserver")
        self.assertEqual(mon._anlz_by_deck[1]["path"], "")

    def test_health_line_shows_the_resolved_path(self):
        from PySide6.QtWidgets import QApplication

        from gui.i18n import I18n
        from gui.pages import HealthPage

        _ = QApplication.instance() or QApplication([])
        page = HealthPage(I18n())
        page.update_state({
            "t": time.time(),
            "packets": 1,
            "mode": "virtual device",
            "host": "192.168.1.212",
            "decks": [],
            "nfs": [],
            "onelibrary": {"present": True, "readable": True, "tracks": 0,
                           "playlists": 63, "detail": "0 tracks"},
            "anlz": [{
                "deck": 1,
                "track_id": 30638,
                "source": "pdb-id",
                "path": ANLZ_PATH,
                "error": "",
                "detail": "analysis path from export.pdb",
            }],
            "link_timing": [],
            "capture": {"active": False, "saving": False, "error": "", "last_path": ""},
            "wnp": {},
            "library_source": {"source": "remotedb"},
        })
        text = page._anlz_val.text()
        self.assertIn("Deck 1", text)
        self.assertIn("pdb-id", text)
        self.assertIn(ANLZ_PATH, text)


class LibraryBrowseTests(unittest.TestCase):
    def test_stale_nfs_refresh_does_not_hide_remotedb_rows(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        media = mock.Mock()
        media.refresh_if_stale.side_effect = OSError("stale nfs")
        mon.library.media = {"192.168.1.212": media}
        mon._library_db_slots = lambda: [wire.SLOT_USB]
        browser = mock.Mock()
        browser.target_player = 5
        browser.requesting_player = 3
        browser.slot = wire.SLOT_USB
        browser.track_search_page.return_value = [
            {"id": 30638, "name": "Night Drive", "subtitle": "AZ"},
        ]
        browser.__enter__ = lambda *_a: browser
        browser.__exit__ = lambda *_a: None
        mon._open_remotedb = lambda host, slot=None: browser
        out = mon.browse_tracks("", limit=10)
        self.assertEqual(out["library_source"], "remotedb")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["tracks"][0]["title"], "Night Drive")
        self.assertEqual(out["tracks"][0]["slot"], wire.SLOT_USB)

    def test_library_page_fills_off_the_gui_thread(self):
        from PySide6.QtWidgets import QApplication

        from gui.i18n import I18n
        from gui.pages import LibraryPage

        _ = QApplication.instance() or QApplication([])
        backend = mock.Mock()
        backend.browse_tracks.return_value = {
            "tracks": [{
                "id": 30638, "title": "Night Drive", "artist": "AZ",
                "album": "LP", "bpm": 128, "key": "8A",
            }],
            "total": 1,
            "multi_player": False,
        }
        backend.browse_playlists.return_value = {
            "playlists": [{"id": 3, "name": "Tonight", "folder": False}],
            "history": [{"id": 9, "name": "History"}],
        }
        page = LibraryPage(I18n(), backend)
        page.update_state({
            "library": {"tracks": 2048, "artists": 1, "albums": 1},
            "host": "192.168.1.212",
            "onelibrary": {"present": True, "readable": True},
            "library_source": {"source": "remotedb"},
            "library_error": "",
        })
        for _i in range(40):
            QApplication.processEvents()
            if page.table.rowCount():
                break
            time.sleep(0.05)
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 0).text(), "Night Drive")
        self.assertEqual(page.playlist_list.count(), 1)
        self.assertEqual(page.history_list.count(), 1)
        self.assertEqual(page.cards["tracks"].text(), "2048")

    def test_library_page_shows_a_browse_error(self):
        from PySide6.QtWidgets import QApplication

        from gui.i18n import I18n
        from gui.pages import LibraryPage

        _ = QApplication.instance() or QApplication([])
        backend = mock.Mock()
        backend.browse_tracks.side_effect = RuntimeError("dbserver timed out")
        backend.browse_playlists.return_value = {"playlists": [], "history": []}
        page = LibraryPage(I18n(), backend)
        page._schedule_browse()
        for _i in range(40):
            QApplication.processEvents()
            if "dbserver timed out" in page.error_label.text():
                break
            time.sleep(0.05)
        self.assertIn("dbserver timed out", page.error_label.text())
        self.assertIn("dbserver timed out", page.empty.text())


if __name__ == "__main__":
    unittest.main()
