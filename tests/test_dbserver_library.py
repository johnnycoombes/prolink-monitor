"""Live dbserver client number, port discovery, and a bad export.pdb."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
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
