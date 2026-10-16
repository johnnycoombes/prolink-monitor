"""1.0.21: web panel / overlay transport. No ProLink code is involved."""
import gzip
import http.client
import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace

import app
from prolink import web_server


def _full_state():
    return {
        "t": time.time(), "paint": False,
        "decks": [{"number": 1, "track_id": 5, "playing": False, "position_ms": 10.0,
                   "playhead_age_s": time.monotonic()}],
        "display": {"show_phrases": True}, "spectrum": None,
        "audience_deck": None, "now_playing": None, "pending": None,
        "setlist": [], "mix": {},
        "nfs": [{"host": "x"}], "dbserver": {}, "link_timing": [], "anlz": [],
        "devices": [], "capture": {}, "library": {}, "session": {}, "model": {},
    }


class LitePayloadTests(unittest.TestCase):
    def test_lite_keys_only(self):
        lite = web_server.lite_state(_full_state())
        self.assertEqual(set(lite), web_server.LITE_KEYS)
        for k in ("nfs", "dbserver", "link_timing", "anlz", "devices", "capture"):
            self.assertNotIn(k, lite)

    def test_lite_frame_and_full_share_one_snapshot(self):
        calls = []
        mon = SimpleNamespace(state=lambda: calls.append(1) or _full_state(),
                              paint_state=lambda: {"decks": []})
        lite = web_server.shared_frame(mon, "state", 0.5, lite=True)
        full = web_server.shared_frame(mon, "state", 0.5)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("nfs", json.loads(lite[6:]))
        self.assertIn("nfs", json.loads(full[6:]))

    def test_overlay_uses_lite_and_panel_full(self):
        with open(app.os.path.join(app.WEB_DIR, "overlay.html"), encoding="utf-8") as f:
            self.assertIn("/api/events?lite=1", f.read())
        with open(app.os.path.join(app.WEB_DIR, "index.html"), encoding="utf-8") as f:
            self.assertIn("new EventSource('/api/events')", f.read())


class FrameGateTests(unittest.TestCase):
    def test_unchanged_skipped_then_resent_after_1s(self):
        g = web_server.FrameGate(resend_s=1.0)
        self.assertTrue(g.should_send("state", "a", 0.0))
        self.assertFalse(g.should_send("state", "a", 0.5))
        self.assertFalse(g.should_send("state", "a", 0.99))
        self.assertTrue(g.should_send("state", "a", 1.0))
        self.assertTrue(g.should_send("state", "b", 1.1))

    def test_paint_always_sent_while_playing(self):
        g = web_server.FrameGate()
        self.assertTrue(g.should_send("paint", "a", 0.0, playing=True))
        self.assertTrue(g.should_send("paint", "a", 0.01, playing=True))
        self.assertFalse(g.should_send("paint", "a", 0.02, playing=False))

    def test_digest_ignores_timestamps(self):
        a, b = _full_state(), _full_state()
        b["t"] += 5
        b["decks"][0]["playhead_age_s"] += 3
        self.assertEqual(web_server._digest(a), web_server._digest(b))
        b["decks"][0]["position_ms"] = 99.0
        self.assertNotEqual(web_server._digest(a), web_server._digest(b))


class _Mon:
    def __init__(self):
        self.wave = bytes(range(256)) * 40
        self.art = b"\xff\xd8" + b"j" * 3000

    def state(self):
        return _full_state()

    def paint_state(self):
        return {"t": time.time(), "paint": True, "decks": []}

    def meta(self, track_id, deck=None, load=True):
        return {"track_key": "k1"}

    def waveform(self, track_id, deck=None):
        return self.wave

    def artwork(self, track_id, deck=None, size="large"):
        return self.art


class HttpCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type("H", (app.Handler,), {"monitor": _Mon()}) if hasattr(app, "Handler") else None
        if handler is None:
            raise unittest.SkipTest("no Handler")
        cls.srv = app.QuietServer(("127.0.0.1", 0), handler)
        cls.thread = web_server.start_http_thread(cls.srv)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        web_server.close_http_server(cls.srv, cls.thread)

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_waveform_etag_and_304(self):
        r, body = self.get("/api/waveform/5?deck=1&key=k1")
        self.assertEqual(r.status, 200)
        self.assertIn("max-age", r.getheader("Cache-Control"))
        etag = r.getheader("ETag")
        self.assertTrue(etag)
        r2, body2 = self.get("/api/waveform/5?deck=1&key=k1", {"If-None-Match": etag})
        self.assertEqual(r2.status, 304)
        self.assertEqual(body2, b"")

    def test_unkeyed_waveform_stays_no_store(self):
        r, _ = self.get("/api/waveform/5?deck=1")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_artwork_revalidates(self):
        r, body = self.get("/api/artwork/5?deck=1&key=k1")
        self.assertEqual(r.status, 200)
        self.assertIn("no-cache", r.getheader("Cache-Control"))
        self.assertIsNone(r.getheader("Content-Encoding"))   # jpeg not gzipped
        r2, _ = self.get("/api/artwork/5?deck=1&key=k1", {"If-None-Match": r.getheader("ETag")})
        self.assertEqual(r2.status, 304)

    def test_gzip_waveform_and_html(self):
        r, body = self.get("/api/waveform/5?deck=1&key=k1", {"Accept-Encoding": "gzip"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), _Mon().wave)
        r, body = self.get("/overlay", {"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertIn(b"<html", gzip.decompress(body).lower())
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertTrue(r.getheader("ETag"))

    def test_no_gzip_without_accept_or_q0(self):
        r, body = self.get("/overlay")
        self.assertIsNone(r.getheader("Content-Encoding"))
        r, body = self.get("/overlay", {"Accept-Encoding": "gzip;q=0"})
        self.assertIsNone(r.getheader("Content-Encoding"))

    def test_lite_event_stream(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /api/events?lite=1 HTTP/1.1\r\nHost: x\r\n\r\n")
        buf = b""
        end = time.time() + 3
        while b"\ndata: " not in buf and b"\r\n\r\ndata: " not in buf and time.time() < end:
            buf += s.recv(65536)
        s.close()
        data = buf.split(b"data: ", 1)[1].split(b"\n\n", 1)[0]
        self.assertNotIn("nfs", json.loads(data))


if __name__ == "__main__":
    unittest.main()
