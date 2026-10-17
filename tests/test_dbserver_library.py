"""Live dbserver client number, port discovery, and a bad export.pdb."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from prolink import library, remotedb as wire
from prolink.db_recovery import STATS, is_connection_drop
from prolink.remotedb_client import choose_requesting_player


class ClientNumberTests(unittest.TestCase):
    def test_virtual_five_is_not_rewritten_to_deck_three(self):
        # XDJ-AZ already uses 1–4. Announcing as 5 and querying as 3 clashes.
        number = choose_requesting_player(
            5, target_player=1, taken={1, 2, 3, 4, 33})
        self.assertEqual(number, 5)

    def test_announced_player_in_range_stays(self):
        self.assertEqual(
            choose_requesting_player(2, target_player=1, taken={1, 3, 4}),
            2,
        )

    def test_does_not_query_as_the_target(self):
        number = choose_requesting_player(1, target_player=1, taken={2, 3, 4})
        self.assertNotEqual(number, 1)
        self.assertEqual(number, 5)

    def test_slot_three_is_the_usb_slot_not_deck_three(self):
        self.assertEqual(wire.SLOT_USB, 3)
        self.assertEqual(wire.slot_label(3), "USB slot")
        text = wire.session_detail(port=42258, target=1, client=5, slot=3)
        self.assertIn("client #5", text)
        self.assertIn("USB slot", text)
        self.assertIn("42258", text)
        self.assertNotIn("slot 3", text)


class PortDiscoveryTests(unittest.TestCase):
    def test_query_uses_the_discovery_service(self):
        got: dict = {}
        ready = threading.Event()

        def serve() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            got["port"] = sock.getsockname()[1]
            sock.listen(1)
            sock.settimeout(3)
            ready.set()
            conn, _addr = sock.accept()
            try:
                conn.settimeout(2)
                got["query"] = conn.recv(64)
                conn.sendall((1051).to_bytes(2, "big"))
            finally:
                conn.close()
                sock.close()

        thread = threading.Thread(target=serve)
        thread.start()
        self.assertTrue(ready.wait(2))
        try:
            with mock.patch.object(wire, "REMOTE_DB_DISCOVERY_PORT", got["port"]):
                found = wire.discover_db_port("127.0.0.1", timeout=2)
        finally:
            thread.join(3)
        self.assertEqual(got["query"], wire.REMOTE_DB_QUERY)
        self.assertEqual(found, 1051)

    def test_closed_port_names_the_discovery_service(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with mock.patch.object(wire, "REMOTE_DB_DISCOVERY_PORT", port):
            with self.assertRaises(wire.DbServerError) as caught:
                wire.discover_db_port("127.0.0.1", timeout=0.4)
        text = str(caught.exception)
        self.assertIn("port discovery", text)
        self.assertIn(str(port), text)
        self.assertTrue(is_connection_drop(caught.exception))


class BadPdbTests(unittest.TestCase):
    def test_short_file_does_not_raise(self):
        db, err = library.read_export_pdb(b"not a database")
        self.assertIsNone(db)
        self.assertIn("export.pdb", err)

    def test_parse_failure_keeps_the_medium(self):
        lib = library.Library(cache_dir=tempfile.mkdtemp())
        media = mock.Mock()
        media.load_database.side_effect = RuntimeError("bad page")
        media.pdb_error = ""
        media.library_source = "none"
        media.db = None
        with mock.patch.object(library, "Media", return_value=media):
            got = lib.get("192.0.2.9")
        self.assertIs(got, media)
        self.assertNotIn("192.0.2.9", lib.errors)
        self.assertIn("export.pdb", media.pdb_error)
        media._load_onelibrary.assert_called_once()
        media.set_library_source.assert_called()


class ProbeTests(unittest.TestCase):
    def test_probe_records_client_and_discovery_port(self):
        from app import Monitor

        STATS.reconnects = 0
        STATS.last_error = ""
        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        media = mock.Mock()
        media.library_source = "nfs-cache"
        media.db = object()
        media.cache_dir = r"C:\Users\User\.prolink-cache\192.168.1.212\C"
        mon.library.media = {"192.168.1.212": media}
        browser = mock.Mock()
        browser.target_player = 1
        browser.requesting_player = 5
        browser.slot = 3
        browser.last_status.port = 1051
        browser.__enter__ = lambda *_a: browser
        browser.__exit__ = lambda *_a: None
        mon._open_remotedb = lambda host, slot=None: browser
        mon._probe_live_library("192.168.1.212")
        _source, detail = media.set_library_source.call_args[0]
        self.assertEqual(_source, "remotedb")
        self.assertIn("client #5", detail)
        self.assertIn("USB slot", detail)
        self.assertNotIn("slot 3", detail)
        self.assertIn("12523", detail)
        self.assertIn("1051", detail)
        self.assertEqual(STATS.last_error, "")

    def test_missing_player_is_the_health_reason(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        media = mock.Mock()
        media.library_source = "none"
        media.db = object()
        media.cache_dir = "cache"
        mon.library.media = {"192.168.1.212": media}
        mon._open_remotedb = lambda host, slot=None: None
        mon._db_unavailable_reason = lambda host: (
            "dbserver not attempted: 192.168.1.212 has no player device, "
            "only 33 (mixer)"
        )
        mon._probe_live_library("192.168.1.212")
        self.assertIn("33 (mixer)", mon._remotedb_fail["192.168.1.212"])
        self.assertIn("33 (mixer)", STATS.last_error)
        args = media.set_library_source.call_args[0]
        self.assertEqual(args[0], "nfs-cache")
        self.assertIn("33 (mixer)", args[1])


class LiveLoadTests(unittest.TestCase):
    def test_nfs_load_does_not_wait_to_open_dbserver(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._stop = threading.Event()
        mon._live_detail = {}
        gate = threading.Event()
        order: list[str] = []
        media = mock.Mock()
        media.library_source = "none"
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        mon.library.media = {}

        def probe(host: str) -> None:
            order.append("db")
            gate.set()
            media.library_source = "remotedb"

        def get(host: str):
            self.assertTrue(gate.wait(2), "dbserver did not start before export.pdb returned")
            order.append("nfs")
            mon.library.media[host] = media
            return media

        mon.library.get = get
        mon.library.retry = mock.Mock()
        mon._probe_live_library = probe
        mon._apply_live_source = lambda: None
        mon._load_player_library("192.168.1.212")
        self.assertEqual(order[0], "db")
        self.assertIn("nfs", order)

    def test_probe_stops_once_the_session_is_live(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._stop = threading.Event()
        mon._live_detail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        mon.library.media = {}
        calls = {"n": 0}

        def probe(host: str) -> None:
            calls["n"] += 1
            mon._live_detail[host] = ("remotedb", "ok")

        mon._probe_live_library = probe
        mon._keep_probing("192.168.1.212")
        self.assertEqual(calls["n"], 1)

    def test_missing_announce_still_opens_the_discovered_port(self):
        from app import Monitor

        discovered: dict = {}
        ready = threading.Event()

        def serve_discovery() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            discovered["disc"] = sock.getsockname()[1]
            sock.listen(1)
            sock.settimeout(3)
            ready.set()
            conn, _addr = sock.accept()
            try:
                conn.settimeout(2)
                conn.recv(64)
                conn.sendall(int(discovered["db"]).to_bytes(2, "big"))
            finally:
                conn.close()
                sock.close()

        def serve_db() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            discovered["db"] = sock.getsockname()[1]
            sock.listen(1)
            sock.settimeout(3)
            conn, _addr = sock.accept()
            try:
                conn.settimeout(2)
                got = b""
                while len(got) < 5:
                    chunk = conn.recv(5 - len(got))
                    if not chunk:
                        break
                    got += chunk
                conn.sendall(wire._num_field(wire.GREETING, 4))
                wire.read_message(conn)
                conn.sendall(wire.encode_message(
                    wire.SETUP_TX, wire.TYPE_MENU_AVAILABLE, [],
                ))
                discovered["opened"] = True
                try:
                    conn.recv(8)
                except OSError:
                    pass
            finally:
                conn.close()
                sock.close()

        db_thread = threading.Thread(target=serve_db)
        db_thread.start()
        for _ in range(50):
            if "db" in discovered:
                break
            time_sleep = __import__("time").sleep
            time_sleep(0.01)
        disc_thread = threading.Thread(target=serve_discovery)
        disc_thread.start()
        self.assertTrue(ready.wait(2))

        mon = Monitor.__new__(Monitor)
        mon._stop = threading.Event()
        mon._live_detail = {}
        mon._remotedb_fail = {}
        mon.local_ip = "127.0.0.1"
        mon.engine = SimpleNamespace(
            lock=threading.RLock(),
            devices={},
            source=SimpleNamespace(device_number=5),
            active_decks=lambda: [],
        )
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        media = mock.Mock()
        media.library_source = "nfs-cache"
        media.db = object()
        media.cache_dir = "cache"
        mon.library.media = {"127.0.0.1": media}
        STATS.last_error = "stale"
        try:
            with mock.patch.object(wire, "REMOTE_DB_DISCOVERY_PORT", discovered["disc"]):
                mon._probe_live_library("127.0.0.1")
        finally:
            disc_thread.join(3)
            db_thread.join(3)
        self.assertTrue(discovered.get("opened"))
        source, detail = media.set_library_source.call_args[0]
        self.assertEqual(source, "remotedb")
        self.assertIn(str(discovered["db"]), detail)
        self.assertIn(str(discovered["disc"]), detail)
        self.assertIn("client #5", detail)
        self.assertEqual(STATS.last_error, "")
        self.assertEqual(mon._live_detail["127.0.0.1"][0], "remotedb")


class LibrarySourceBrowseTests(unittest.TestCase):
    def _monitor(self, media, browser):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        mon.library.media = {"192.168.1.212": media}
        mon._library_db_slots = lambda: [wire.SLOT_USB]
        mon._open_remotedb = lambda host, slot=None: browser
        mon._db_unavailable_reason = lambda host: "dbserver not attempted"
        return mon

    def _browser(self):
        browser = mock.Mock()
        browser.target_player = 1
        browser.requesting_player = 5
        browser.slot = wire.SLOT_USB
        browser.__enter__ = lambda *_a: browser
        browser.__exit__ = lambda *_a: None
        return browser

    def test_live_menu_puts_key_and_bpm_in_their_columns(self):
        media = mock.Mock()
        media.library_source = "remotedb"
        media.db = None
        browser = self._browser()
        browser.track_search_page.return_value = [
            {"id": 7, "name": "Night Drive", "subtitle": "3A - 124.0 bpm"},
        ]
        out = self._monitor(media, browser).browse_tracks("", limit=10)
        row = out["tracks"][0]
        self.assertEqual(out["library_source"], "remotedb")
        self.assertEqual(row["title"], "Night Drive")
        self.assertEqual(row["artist"], "")
        self.assertEqual(row["key"], "3A")
        self.assertEqual(row["bpm"], 124.0)
        self.assertNotIn("3A - 124.0", row["artist"])

    def test_cache_fills_artist_without_leaving_dbserver(self):
        media = mock.Mock()
        media.library_source = "remotedb"
        media.db = mock.Mock()
        media.db.get.return_value = mock.Mock(
            title="Night Drive",
            artist="AZ",
            album="LP",
            key="8A",
            tempo=124.0,
        )
        # MagicMock answers every attribute with another mock. Use a real object.
        media.db.get.return_value = type("Track", (), {
            "title": "Night Drive", "artist": "AZ", "album": "LP",
            "key": "8A", "tempo": 124.0,
        })()
        browser = self._browser()
        browser.track_search_page.return_value = [
            {"id": 7, "name": "Night Drive", "subtitle": "3A - 124.0 bpm"},
        ]
        out = self._monitor(media, browser).browse_tracks("", limit=10)
        row = out["tracks"][0]
        self.assertEqual(row["artist"], "AZ")
        self.assertEqual(row["album"], "LP")
        self.assertEqual(row["key"], "3A")
        self.assertEqual(out["library_source"], "remotedb")

    def test_a_live_menu_failure_does_not_relabel_as_nfs(self):
        media = mock.Mock()
        media.library_source = "remotedb"
        media.db = mock.Mock()
        media.db.search.return_value = ([{
            "id": 9, "title": "From NFS", "artist": "AZ", "album": "", "bpm": 1, "key": "",
        }], 1)
        media.cache_dir = r"C:\Users\User\.prolink-cache\192.168.1.212\C"
        browser = self._browser()
        browser.track_search_page.side_effect = RuntimeError("menu failed")
        out = self._monitor(media, browser).browse_tracks("", limit=10)
        self.assertEqual(out["library_source"], "remotedb")
        self.assertEqual(out["tracks"], [])
        self.assertIn("menu failed", out["error"])
        media.set_library_source.assert_not_called()

    def test_nfs_fallback_states_why_dbserver_was_not_used(self):
        media = mock.Mock()
        media.library_source = "none"
        media.cache_dir = r"C:\Users\User\.prolink-cache\192.168.1.212\C"
        media.db = mock.Mock()
        media.db.search.return_value = ([{
            "id": 9, "title": "Cached", "artist": "AZ", "album": "LP", "bpm": 120, "key": "8A",
        }], 1)
        media.onelibrary_db = None
        browser = self._browser()
        browser.track_search_page.side_effect = RuntimeError("port discovery failed")
        out = self._monitor(media, browser).browse_tracks("", limit=10)
        self.assertEqual(out["library_source"], "nfs-cache")
        self.assertEqual(out["tracks"][0]["title"], "Cached")
        source, detail = media.set_library_source.call_args[0]
        self.assertEqual(source, "nfs-cache")
        self.assertIn("NFS export.pdb cache", detail)
        self.assertIn("live dbserver failed", detail)
        self.assertIn("port discovery failed", detail)


class HealthLabelTests(unittest.TestCase):
    def test_source_line_includes_the_failure(self):
        from gui.pages import format_library_source

        class _I18n:
            def t(self, key: str) -> str:
                return {
                    "library_source_none": "Not connected",
                    "library_source_nfs": "Fallback (NFS file cache)",
                    "library_source_remotedb": "Live (dbserver / ProLink)",
                    "library_source_onelibrary": "Fallback (OneLibrary copy)",
                }.get(key, key)

        text = format_library_source(
            {
                "source": "none",
                "detail": "",
                "remotedb_error": "setup failed type 0x0",
            },
            _I18n(),
        )
        self.assertIn("Not connected", text)
        self.assertIn("setup failed", text)


class AppLogTests(unittest.TestCase):
    def test_library_line_is_written_once(self):
        from prolink import applog

        folder = tempfile.mkdtemp()
        path = applog.reset_for_tests(
            __import__("os").path.join(folder, "prolink-monitor.log"))
        applog.log_library(
            host="192.168.1.212",
            source="nfs-cache",
            detail="export.pdb",
            counts={"tracks": 10, "artists": 4, "albums": 3},
            error="export.pdb: bad page",
            onelibrary_tracks=0,
        )
        applog.log_library(
            host="192.168.1.212",
            source="nfs-cache",
            detail="export.pdb",
            counts={"tracks": 10, "artists": 4, "albums": 3},
            error="export.pdb: bad page",
            onelibrary_tracks=0,
        )
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        self.assertEqual(body.count("library host=192.168.1.212"), 1)
        self.assertIn("source=nfs-cache", body)
        self.assertIn("tracks=10", body)
        self.assertIn("error=export.pdb: bad page", body)
        applog.reset_for_tests(path)


if __name__ == "__main__":
    unittest.main()
