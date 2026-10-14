"""1.0.11: full playlist tree (live + fallback), track row columns, diagnostics."""

import struct
import unittest
from types import SimpleNamespace
from unittest import mock

from prolink import library_tree, onelibrary, pdb
from prolink import remotedb as wire
from prolink.remotedb_client import RemoteDbBrowser


def _folder_rows(tree):
    def fake(folder_id=0):
        return [dict(r) for r in tree.get(folder_id, [])]
    return fake


class LivePlaylistTreeTests(unittest.TestCase):
    def _browser(self):
        b = RemoteDbBrowser(host="192.0.2.1", target_player=1, requesting_player=5)
        b.call = lambda fn: fn()  # no socket
        return b

    def test_walks_nested_folders(self):
        tree = {
            0: [{"id": 10, "name": "House", "folder": True},
                {"id": 1, "name": "Loose", "folder": False}],
            10: [{"id": 11, "name": "Deep", "folder": True},
                 {"id": 2, "name": "Warmup", "folder": False}],
            11: [{"id": 3, "name": "Peak", "folder": False}],
        }
        b = self._browser()
        with mock.patch.object(RemoteDbBrowser, "_playlist_folder", side_effect=_folder_rows(tree)):
            rows = b.playlist_tree()
        names = [(r["name"], r["depth"], r["parent_id"]) for r in rows]
        self.assertEqual(names, [("House", 0, 0), ("Deep", 1, 10), ("Peak", 2, 11),
                                 ("Warmup", 1, 10), ("Loose", 0, 0)])
        stats = library_tree.tree_stats(rows)
        self.assertEqual(stats, {"folders": 2, "playlists": 3, "nested": 3, "max_depth": 2})

    def test_empty_subfolder_error_does_not_drop_the_tree(self):
        tree = {0: [{"id": 10, "name": "Empty", "folder": True},
                    {"id": 1, "name": "A", "folder": False}]}

        def fake(folder_id=0):
            if folder_id == 10:
                raise wire.DbServerError("menu unavailable type 0x4003")
            return _folder_rows(tree)(folder_id)

        b = self._browser()
        with mock.patch.object(RemoteDbBrowser, "_playlist_folder", side_effect=fake):
            rows = b.playlist_tree()
        self.assertEqual([r["name"] for r in rows], ["Empty", "A"])

    def test_folder_loop_terminates(self):
        tree = {0: [{"id": 10, "name": "F", "folder": True}],
                10: [{"id": 10, "name": "F again", "folder": True}]}
        b = self._browser()
        with mock.patch.object(RemoteDbBrowser, "_playlist_folder", side_effect=_folder_rows(tree)):
            rows = b.playlist_tree()
        self.assertEqual(len(rows), 2)

    def test_paging_reads_every_batch(self):
        b = self._browser()
        b._sock = mock.Mock()
        asked = []

        def encode(tx, req, slot, offset, limit, total, track_type=0):
            asked.append((offset, limit, total))
            return b""

        msgs = []

        def read(_sock):
            if not msgs:
                off, lim, _t = asked[-1]
                msgs.append(wire.DbMessage(0, wire.TYPE_MENU_HEADER, []))
                msgs.extend(wire.DbMessage(0, wire.TYPE_MENU_ITEM, [0, off + i, 0, "x", 0, "", 1])
                            for i in range(lim))
                msgs.append(wire.DbMessage(0, wire.TYPE_MENU_FOOTER, []))
            return msgs.pop(0)

        with mock.patch.object(wire, "encode_render_menu", side_effect=encode), \
                mock.patch.object(wire, "read_message", side_effect=read):
            items = b._render_all(150)
        self.assertEqual(asked, [(0, 64, 150), (64, 64, 150), (128, 22, 150)])
        self.assertEqual(len(items), 150)


class TrackColumnTests(unittest.TestCase):
    def test_subtitle_follows_the_item_type(self):
        f = wire.menu_track_fields("Song", "Some Album", 0x0204)
        self.assertEqual((f["artist"], f["album"]), ("", "Some Album"))
        f = wire.menu_track_fields("Song", "Techno", 0x0604)
        self.assertEqual((f["artist"], f["genre"]), ("", "Techno"))
        f = wire.menu_track_fields("Song", "Artist X", 0x0704)
        self.assertEqual(f["artist"], "Artist X")
        f = wire.menu_track_fields("Song", "great tune", 0x2304)
        self.assertEqual(f["artist"], "")
        f = wire.menu_track_fields("Song", "", 0x0d04, number=12450)
        self.assertEqual(f["bpm"], 124.5)

    def test_key_bpm_line(self):
        f = wire.menu_track_fields("Song", "3A - 124.0 bpm")
        self.assertEqual((f["key"], f["bpm"], f["artist"]), ("3A", 124.0, ""))

    def test_cache_with_another_title_is_ignored(self):
        cached = SimpleNamespace(title="Other", artist="Wrong", album="Wrong LP", key="", tempo=0)
        f = wire.columns_for_menu_track("Song", "Artist X", cached, item_type=0x0704)
        self.assertEqual((f["artist"], f["album"]), ("Artist X", ""))
        cached.title = "song"
        f = wire.columns_for_menu_track("Song", "", cached, item_type=0x0704)
        self.assertEqual((f["artist"], f["album"]), ("Wrong", "Wrong LP"))


class FallbackTreeTests(unittest.TestCase):
    def test_onelibrary_rows(self):
        items = [
            SimpleNamespace(playlist_id=1, name="Folder", attribute=1, playlist_id_parent=0, sequenceNo=1),
            SimpleNamespace(playlist_id=2, name="Smart", attribute=4, playlist_id_parent=1, sequenceNo=1),
            SimpleNamespace(playlist_id=3, name="Two", attribute=2, playlist_id_parent=1, sequenceNo=2),
            SimpleNamespace(playlist_id=4, name="Orphan", attribute=0, playlist_id_parent=99, sequenceNo=3),
        ]
        db = SimpleNamespace(get_playlist=lambda: items)
        rows = library_tree.with_depth(onelibrary.list_playlists(db))
        self.assertEqual([(r["name"], r["folder"], r["depth"]) for r in rows],
                         [("Folder", True, 0), ("Smart", False, 1), ("Two", False, 1),
                          ("Orphan", False, 0)])
        self.assertTrue(rows[1]["smart"])

    def test_pdb_playlist_tree(self):
        def name(text):
            raw = text.encode("ascii")
            return bytes([((len(raw) + 1) << 1) | 1]) + raw

        rows = [
            struct.pack("<IIIII", 0, 0, 0, 5, 1) + name("Crates"),
            struct.pack("<IIIII", 5, 0, 1, 6, 0) + name("Inside"),
            struct.pack("<IIIII", 0, 0, 2, 7, 0) + name("Root"),
        ]
        entries = [struct.pack("<III", 2, 200, 6), struct.pack("<III", 1, 100, 6)]
        data = b"".join(rows) + b"".join(entries)
        offs = []
        pos = 0
        for r in rows:
            offs.append(pos)
            pos += len(r)
        eoffs = [pos, pos + 12]
        db = pdb.PdbDatabase.__new__(pdb.PdbDatabase)
        db.data = data
        db.playlists, db.playlist_entries = {}, {}
        db._rows = lambda t: iter(offs if t == pdb.PLAYLIST_TREE else eoffs)
        db._load_playlists()
        tree = db.playlist_tree()
        self.assertEqual([(r["name"], r["folder"], r["depth"]) for r in tree],
                         [("Crates", True, 0), ("Inside", False, 1), ("Root", False, 0)])
        self.assertEqual(db.playlist_track_ids(6), [100, 200])


class MonitorTreeTests(unittest.TestCase):
    def _monitor(self, browser, media):
        import threading

        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        mon.library.media = {"192.168.1.212": media}
        mon._library_db_slots = lambda: [wire.SLOT_USB]
        mon._open_remotedb = lambda host, slot=None: browser
        mon._db_unavailable_reason = lambda host: "x"
        return mon

    def _browser(self):
        b = mock.Mock()
        b.target_player, b.requesting_player, b.slot = 1, 5, wire.SLOT_USB
        b.last_status.port = 1051
        b.__enter__ = lambda *_a: b
        b.__exit__ = lambda *_a: None
        b.playlist_tree.return_value = [
            {"id": 10, "name": "House", "folder": True, "parent_id": 0, "depth": 0},
            {"id": 2, "name": "Warmup", "folder": False, "parent_id": 10, "depth": 1},
        ]
        b.history_entries.return_value = []
        b.playlist_tracks.return_value = [
            {"id": 7, "name": "Song", "subtitle": "LP", "type": 0x0204, "number": 0}]
        return b

    def test_live_playlists_include_folders_and_nested(self):
        media = mock.Mock()
        mon = self._monitor(self._browser(), media)
        out = mon.browse_playlists()
        rows = [(p["name"], p["folder"], p["depth"]) for p in out["playlists"]]
        self.assertEqual(rows, [("House", True, 0), ("Warmup", False, 1)])

    def test_diagnostics_side_by_side(self):
        media = mock.Mock()
        media.onelibrary_db = None
        media.db.playlist_tree.return_value = [
            {"id": 9, "name": "Warmup", "folder": False, "parent_id": 0, "depth": 0}]
        media.db.playlist_track_ids.return_value = [7]
        media.db.get.return_value = SimpleNamespace(
            title="Song", artist="A", album="LP", genre="", key="", tempo=120.0)
        mon = self._monitor(self._browser(), media)
        rep = mon.library_diagnostics()
        host = rep["hosts"][0]
        self.assertEqual(host["live"]["stats"]["max_depth"], 1)
        self.assertEqual(host["fallback"]["source"], "export.pdb")
        self.assertEqual(host["rows"][0]["live"]["album"], "LP")
        self.assertEqual(host["rows"][0]["live"]["artist"], "")
        self.assertEqual(host["rows"][0]["fallback"]["artist"], "A")


if __name__ == "__main__":
    unittest.main()
