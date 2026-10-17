"""1.0.18 performance: UI tick never waits on I/O, bounded caches, NFS timing."""
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import PySide6  # noqa: F401
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class UiTickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_tick_with_slow_nfs_does_not_block(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import bench_ui_tick
        slow = bench_ui_tick.run(0.3, 12)
        # Before 1.0.18 every tick waited ~4 x 300 ms here (two decks, wave + art).
        self.assertLess(slow["median_ms"], 60.0)
        # The miss is fetched once and then backs off; not 20 times a second.
        self.assertLessEqual(slow["calls"]["waveform"], 4)
        self.assertLessEqual(slow["calls"]["artwork"], 4)

    def test_miss_backoff_grows(self):
        from gui.backend import Backend
        b = Backend()
        b._note_miss("waveform", "k", 0.0)
        self.assertTrue(b._backing_off("waveform", "k", 0.5))
        self.assertFalse(b._backing_off("waveform", "k", 1.1))
        b._note_miss("waveform", "k", 2.0)
        self.assertTrue(b._backing_off("waveform", "k", 3.5))     # 2 s now
        for _ in range(10):
            b._note_miss("waveform", "k", 10.0)
        self.assertEqual(b._miss[("waveform", "k")][0], 40.0)     # capped at 30 s
        b.shutdown_workers()


class EnsureDedupeTests(unittest.TestCase):
    def test_second_caller_does_not_repeat_or_block(self):
        import app
        mon = app.Monitor.__new__(app.Monitor)
        mon._lock = threading.RLock()
        mon._resolve_key = lambda tid, deck: ("h", "", (0, 0), "usb", tid)
        calls = []
        gate = threading.Event()

        def slow_once(track_id, deck=None, force_waveform=False):
            calls.append(track_id)
            gate.wait(2)

        mon._ensure_once = slow_once
        t = threading.Thread(target=mon.ensure, args=(7,))
        t.start()
        time.sleep(0.05)
        t0 = time.monotonic()
        mon.ensure(7, wait=0.1)
        waited = time.monotonic() - t0
        gate.set()
        t.join(2)
        self.assertEqual(calls, [7])
        self.assertLess(waited, 0.5)
        mon.ensure(7)            # next call after the first finished runs again
        self.assertEqual(calls, [7, 7])


class NfsTimingTests(unittest.TestCase):
    def test_adaptive_timeouts(self):
        from prolink.nfs import RpcClient
        c = RpcClient.__new__(RpcClient)
        c.timeout, c.retries = 2.0, 5
        c._rtt_ewma_ms = None
        self.assertEqual(c.attempt_timeouts(), [0.5, 1.0, 2.0, 2.0, 2.0])
        c._rtt_ewma_ms = 3.0             # 4 x 3 ms -> clamp to 0.25 s
        self.assertEqual(c.attempt_timeouts()[0], 0.25)
        c._rtt_ewma_ms = 100.0           # 0.4 s
        self.assertAlmostEqual(c.attempt_timeouts()[0], 0.4)
        c._rtt_ewma_ms = 900.0
        self.assertEqual(c.attempt_timeouts()[0], 0.5)
        self.assertLessEqual(sum(c.attempt_timeouts()), 8.0)      # bounded worst case

    def test_read_size(self):
        from prolink import nfs
        with mock.patch.dict(os.environ, {"PROLINK_NFS_READ_SIZE": ""}):
            self.assertEqual(nfs.read_size(), 4096)
        with mock.patch.dict(os.environ, {"PROLINK_NFS_READ_SIZE": "100000"}):
            self.assertEqual(nfs.read_size(), 8192)
        with mock.patch.dict(os.environ, {"PROLINK_NFS_READ_SIZE": "1"}):
            self.assertEqual(nfs.read_size(), 2048)

    def test_reads_are_serial_4k(self):
        from prolink import nfs
        cli = nfs.NfsClient.__new__(nfs.NfsClient)
        sizes = []
        active = []

        class FakeRpc:
            def call(self, proc, args):
                active.append(1)
                assert len(active) == 1      # never two outstanding
                import struct
                off, n, _ = struct.unpack(">III", args[-12:])
                sizes.append(n)
                active.pop()
                body = struct.pack(">I", 0) + b"\0" * 68 + struct.pack(">I", n) + b"x" * n
                return nfs._Unpacker(body)

        cli._nfs = FakeRpc()
        with mock.patch.object(nfs, "_parse_fattr", lambda u: u.skip(68)):
            data = cli.read_at(b"fh", 0, 10000)
        self.assertEqual(len(data), 10000)
        self.assertEqual(sizes, [4096, 4096, 1808])


class NumpyDecodeTests(unittest.TestCase):
    def test_byte_identical(self):
        from prolink import anlz
        for n in (0, 1, 63, 64, 65, 1000, 40000):
            for name, unit in (("color_detail", 2), ("blue_colors", 1)):
                data = os.urandom(n * unit + (1 if unit > 1 else 0))
                fast = getattr(anlz, f"decode_{name}")(data)
                slow = getattr(anlz, f"_decode_{name}_py")(data)
                self.assertEqual(fast, slow, (name, n))
                self.assertIsInstance(fast[0], bytearray)

    def test_pure_python_fallback(self):
        from prolink import anlz
        data = os.urandom(5000)
        with mock.patch.object(anlz, "_np", None):
            self.assertEqual(anlz.decode_color_detail(data), anlz._decode_color_detail_py(data))


class CacheBoundTests(unittest.TestCase):
    def test_lru_and_pins(self):
        from prolink.lru import BoundedLRU
        hot = {"keep"}
        c = BoundedLRU(3, pinned=lambda: hot)
        c["keep"] = b"x"
        for i in range(10):
            c[i] = b"y"
        self.assertIn("keep", c)
        self.assertEqual(len(c), 3)
        self.assertEqual(list(c)[-2:], [8, 9])

    def test_bytes_bound(self):
        from prolink.lru import BoundedLRU
        c = BoundedLRU(100, max_bytes=1000)
        for i in range(10):
            c[i] = b"z" * 300
        self.assertLessEqual(c.bytes, 1000)
        self.assertEqual(len(c), 3)

    def test_monitor_caches_are_bounded(self):
        import app
        from prolink.lru import BoundedLRU
        self.assertTrue(app.TRACK_CACHE_ITEMS <= 64)
        mon = app.Monitor.__new__(app.Monitor)
        mon._deck_keys = {1: ("a",)}
        self.assertEqual(mon._hot_keys(), [("a",)])
        self.assertTrue(issubclass(BoundedLRU, dict))


class SettingsCacheTests(unittest.TestCase):
    def test_cached_until_saved(self):
        import tempfile
        from gui import settings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with mock.patch.object(settings, "settings_path", lambda: path):
                settings.invalidate_settings_cache()
                settings.save_settings({"name": "one"})
                with mock.patch.object(settings, "_load_settings_uncached",
                                       wraps=settings._load_settings_uncached) as spy:
                    a = settings.load_settings()
                    b = settings.load_settings()
                    self.assertEqual(spy.call_count, 1)
                    a["name"] = "mutated"            # callers get a copy
                    self.assertEqual(settings.load_settings()["name"], "one")
                    settings.save_settings({"name": "two"})
                    self.assertEqual(settings.load_settings()["name"], "two")
                    self.assertEqual(spy.call_count, 2)
                settings.invalidate_settings_cache()


class SseShareTests(unittest.TestCase):
    def test_one_encode_per_tick(self):
        from prolink import web_server
        calls = []
        mon = SimpleNamespace(state=lambda: calls.append(1) or {"a": 1},
                              paint_state=lambda: {"p": 1})
        f1 = web_server.shared_frame(mon, "state", 0.5)
        f2 = web_server.shared_frame(mon, "state", 0.5)
        self.assertIs(f1, f2)
        self.assertEqual(len(calls), 1)
        self.assertTrue(f1.startswith(b"data: "))
        time.sleep(0.02)
        web_server.shared_frame(mon, "state", 0.01)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
