"""What's Now Playing remote input: one send per audience track, and a down server."""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prolink.wnp import (
    AUTH_HEADER,
    WnpPublisher,
    _explain_transport,
    audience_token,
    build_payload,
    normalize_target,
    settings_from,
    test_connection,
)


def _deck(**kwargs):
    base = {
        "number": 1,
        "track_id": 10,
        "playing": True,
        "master": True,
        "on_air": True,
        "title": "Midnight",
        "artist": "North Sea",
        "bpm": 128.0,
        "duration_ms": 200_000,
        "key": "8A",
    }
    base.update(kwargs)
    return base


def _meta(**kwargs):
    base = {
        "title": "Midnight",
        "artist": "North Sea",
        "album": "Harbour",
        "genre": "House",
        "label": "Pier",
        "year": 2024,
        "comment": "club edit",
        "bitrate": 320,
        "duration_ms": 200_000,
        "track_key": "",
        "track_bpm": 126.0,
    }
    base.update(kwargs)
    return base


class _Recorder(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        self.server.requests.append({
            "method": self.command,
            "path": self.path.split("?", 1)[0],
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })
        return body

    def _send(self, code, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._read_json()
        if self.path.split("?", 1)[0] == "/v1/status":
            self._send(200, {"status": "ok", "version": "9.9.9-test"})
            return
        self._send(404, {"error": "nope"})

    def do_POST(self):
        body = self._read_json()
        expected = getattr(self.server, "secret", "")
        if expected:
            got = self.headers.get(AUTH_HEADER, "")
            if got != expected:
                self._send(403, {"error": "Invalid secret"})
                return
        if self.path.split("?", 1)[0] != "/v1/remoteinput":
            self._send(404, {"error": "nope"})
            return
        if not body.get("title"):
            self._send(400, {"error": "title required"})
            return
        self._send(200, {"dbid": len(self.server.requests), "processed_metadata": body})


class MockWnp:
    def __init__(self, secret=""):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        self.httpd.requests = []
        self.httpd.secret = secret
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    def settings(self, **extra):
        data = {
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": self.port,
            "wnp_secret": self.httpd.secret,
        }
        data.update(extra)
        return data

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class PayloadTests(unittest.TestCase):
    def test_maps_overlay_fields_and_skips_artwork(self):
        body = build_payload(_deck(bpm=126.5), _meta())
        self.assertEqual(body["title"], "Midnight")
        self.assertEqual(body["artist"], "North Sea")
        self.assertEqual(body["album"], "Harbour")
        self.assertEqual(body["bpm"], "126.50")
        self.assertEqual(body["duration"], "200")
        self.assertEqual(body["date"], "2024")
        self.assertEqual(body["genre"], "House")
        self.assertEqual(body["label"], "Pier")
        self.assertEqual(body["key"], "8A")
        self.assertEqual(body["comments"], "club edit")
        self.assertEqual(body["bitrate"], "320")
        self.assertNotIn("coverurl", body)
        self.assertNotIn("coverimageraw", body)
        self.assertNotIn("filename", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("year", body)

    def test_waits_until_title_is_known(self):
        self.assertIsNone(build_payload(_deck(title="", artist="")))
        self.assertIsNone(audience_token({"number": 1, "track_id": 0}))

    def test_prefers_effective_bpm_shown_on_the_overlay(self):
        body = build_payload(_deck(bpm=130.25), _meta(track_bpm=120))
        self.assertEqual(body["bpm"], "130.25")

    def test_ignores_inflated_duration(self):
        body = build_payload(_deck(duration_ms=9_000_000_000), _meta(duration_ms=180_000))
        self.assertEqual(body["duration"], "180")

    def test_test_result_matches_only_the_saved_endpoint(self):
        pub = WnpPublisher()
        pub.configure({
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": 8899,
            "wnp_secret": "desk",
        })
        self.assertTrue(pub.same_saved_target({
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": 8899,
            "wnp_secret": "desk",
        }))
        self.assertFalse(pub.same_saved_target({
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": 8899,
            "wnp_secret": "other",
        }))
        pub.configure({
            "wnp_enabled": False,
            "wnp_host": "127.0.0.1",
            "wnp_port": 8899,
            "wnp_secret": "desk",
        })
        self.assertFalse(pub.same_saved_target({
            "wnp_enabled": False,
            "wnp_host": "127.0.0.1",
            "wnp_port": 8899,
            "wnp_secret": "desk",
        }))

    def test_normalize_host_and_port(self):
        self.assertEqual(normalize_target("", ""), ("localhost", 8899))
        self.assertEqual(normalize_target("http://studio.local:8900/v1", 1), ("studio.local", 8900))
        self.assertEqual(normalize_target("127.0.0.1:9000", 8899), ("127.0.0.1", 9000))
        cfg = settings_from({"wnp_enabled": 1, "wnp_host": "localhost", "wnp_port": "8899", "wnp_secret": "  abc "})
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.secret, "abc")
        self.assertEqual(cfg.port, 8899)


class SettingsRoundTripTests(unittest.TestCase):
    def test_defaults_and_secret_round_trip(self):
        import os
        import tempfile

        from gui.settings import load_settings, save_settings

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("PROLINK_CONFIG_DIR")
            os.environ["PROLINK_CONFIG_DIR"] = tmp
            try:
                fresh = load_settings()
                self.assertFalse(fresh["wnp_enabled"])
                self.assertEqual(fresh["wnp_host"], "localhost")
                self.assertEqual(fresh["wnp_port"], 8899)
                self.assertEqual(fresh["wnp_secret"], "")
                save_settings({
                    **fresh,
                    "wnp_enabled": True,
                    "wnp_host": "http://booth.local:8901",
                    "wnp_port": 1,
                    "wnp_secret": "desk",
                })
                saved = load_settings()
                self.assertTrue(saved["wnp_enabled"])
                self.assertEqual(saved["wnp_host"], "booth.local")
                self.assertEqual(saved["wnp_port"], 8901)
                self.assertEqual(saved["wnp_secret"], "desk")
            finally:
                if previous is None:
                    os.environ.pop("PROLINK_CONFIG_DIR", None)
                else:
                    os.environ["PROLINK_CONFIG_DIR"] = previous


class MockServerTests(unittest.TestCase):
    def setUp(self):
        self.server = MockWnp(secret="s3cret")
        self.pub = WnpPublisher(timeout=1.5)
        self.pub.configure(self.server.settings())

    def tearDown(self):
        self.server.close()

    def _posts(self):
        return [r for r in self.server.httpd.requests if r["method"] == "POST"]

    def test_sends_once_per_track_change(self):
        meta = _meta()
        self.pub.observe(_deck(track_id=10), meta)
        self.pub.observe(_deck(track_id=10, bpm=129), meta)
        self.pub.observe(_deck(track_id=10), meta)
        self.assertTrue(self.pub.wait_idle())
        self.assertEqual(len(self._posts()), 1)
        body = self._posts()[0]["body"]
        self.assertEqual(body["title"], "Midnight")
        self.assertEqual(body["artist"], "North Sea")
        self.assertEqual(body["album"], "Harbour")
        self.assertEqual(self._posts()[0]["headers"].get(AUTH_HEADER.lower()), "s3cret")
        self.assertNotIn("secret", body)

        self.pub.observe(_deck(track_id=11, title="Dawn"), meta)
        self.assertTrue(self.pub.wait_idle())
        self.assertEqual(len(self._posts()), 2)
        self.assertEqual(self._posts()[1]["body"]["title"], "Dawn")

        self.pub.observe(_deck(number=2, track_id=11, title="Dawn"), meta)
        self.assertTrue(self.pub.wait_idle())
        self.assertEqual(len(self._posts()), 3)
        self.assertEqual(self._posts()[2]["body"]["deck"], "2")

        health = self.pub.health()
        self.assertEqual(health["status"], "ok")
        self.assertIn("Dawn", health["detail"])

    def test_holds_send_until_title_arrives_then_sends_once(self):
        self.pub.observe(_deck(title=""), _meta(title=""))
        self.assertEqual(self.pub.health()["status"], "waiting")
        time.sleep(0.05)
        self.assertEqual(self._posts(), [])
        self.pub.observe(_deck(title="Midnight"), _meta())
        self.pub.observe(_deck(title="Midnight"), _meta())
        self.assertTrue(self.pub.wait_idle())
        self.assertEqual(len(self._posts()), 1)

    def test_disabled_sends_nothing(self):
        self.pub.configure(self.server.settings(wnp_enabled=False))
        self.pub.observe(_deck(), _meta())
        self.assertTrue(self.pub.wait_idle(0.3))
        self.assertEqual(self._posts(), [])
        self.assertEqual(self.pub.health()["status"], "disabled")

    def test_wrong_secret_is_an_error_and_is_not_retried(self):
        self.pub.configure(self.server.settings(wnp_secret="nope"))
        self.pub.observe(_deck(), _meta())
        self.pub.observe(_deck(), _meta())
        self.assertTrue(self.pub.wait_idle())
        self.assertEqual(len(self._posts()), 1)
        health = self.pub.health()
        self.assertEqual(health["status"], "error")
        self.assertIn("secret", health["detail"].lower())

    def test_button_sends_current_track_or_status(self):
        result = test_connection(self.server.settings(wnp_enabled=False), _deck(), _meta())
        self.assertTrue(result.ok)
        self.assertIn("Midnight", result.detail)
        self.assertEqual(len(self._posts()), 1)
        empty = test_connection(self.server.settings(), None, None)
        self.assertTrue(empty.ok)
        self.assertIn("9.9.9-test", empty.detail)
        self.assertIn("secret was not checked", empty.detail)
        self.assertEqual(len(self._posts()), 1)


class TransportMessageTests(unittest.TestCase):
    def _cfg(self):
        return settings_from({"wnp_host": "127.0.0.1", "wnp_port": 9923})

    def test_refused_connect_is_not_running(self):
        text = _explain_transport(ConnectionRefusedError(111, "Connection refused"), self._cfg())
        self.assertIn("not running", text)

    def test_windows_timeout_that_refused_is_not_running(self):
        # urllib sets a socket timeout. Windows then raises TimeoutError for a
        # closed port, with winerror 10061 and "actively refused" in the text.
        reason = TimeoutError("No connection could be made because the target machine actively refused it")
        reason.winerror = 10061
        reason.errno = 10061
        text = _explain_transport(reason, self._cfg())
        self.assertIn("not running", text)
        self.assertNotIn("did not answer", text)

    def test_plain_timeout_stays_a_slow_peer(self):
        text = _explain_transport(TimeoutError("timed out"), self._cfg())
        self.assertIn("did not answer", text)
        self.assertNotIn("not running", text)


class DownServerTests(unittest.TestCase):
    def test_observe_returns_immediately_and_records_one_error(self):
        import urllib.request

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        calls = {"n": 0}
        real = urllib.request.urlopen

        def counting(req, timeout=None):
            calls["n"] += 1
            return real(req, timeout=timeout)

        pub = WnpPublisher(timeout=1.5)
        pub.configure({
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": port,
            "wnp_secret": "",
        })
        with patch("urllib.request.urlopen", counting):
            started = time.monotonic()
            pub.observe(_deck(), _meta())
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.2)
            self.assertTrue(pub.wait_idle())
            health = pub.health()
            self.assertEqual(health["status"], "error")
            self.assertIn("not running", health["detail"])
            self.assertEqual(calls["n"], 1)

            pub.observe(_deck(), _meta())
            pub.observe(_deck(bpm=140), _meta())
            self.assertTrue(pub.wait_idle(0.4))
            self.assertEqual(calls["n"], 1)
            again = time.monotonic()
            pub.observe(_deck(), _meta())
            self.assertLess(time.monotonic() - again, 0.2)
            self.assertTrue(pub.wait_idle(0.3))
            self.assertEqual(calls["n"], 1)

    def test_hung_peer_does_not_block_observe(self):
        class Hang(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                for _ in range(50):
                    if getattr(self.server, "stop", False):
                        return
                    time.sleep(0.1)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Hang)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        pub = WnpPublisher(timeout=0.3)
        pub.configure({
            "wnp_enabled": True,
            "wnp_host": "127.0.0.1",
            "wnp_port": httpd.server_address[1],
            "wnp_secret": "",
        })
        try:
            started = time.monotonic()
            pub.observe(_deck(), _meta())
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(pub.wait_idle(2.0))
            self.assertEqual(pub.health()["status"], "error")
            self.assertIn("did not answer", pub.health()["detail"])
        finally:
            httpd.stop = True
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


class AudienceDeckSendTests(unittest.TestCase):
    def test_state_queues_the_audience_deck_not_the_other_player(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.host = "192.168.1.10"
        mon.engine = MagicMock()
        mon.engine.packets = 3
        mon.engine.source = MagicMock(description="vcdj", kind="vcdj", detail="")
        mon.engine.lock = MagicMock()
        mon.engine.lock.__enter__ = MagicMock(return_value=None)
        mon.engine.lock.__exit__ = MagicMock(return_value=False)
        mon.engine.devices = {}

        def status(track_id, master, on_air):
            return SimpleNamespace(
                name="XDJ-AZ", firmware="1.30", track_id=track_id, slot="1",
                track_type=1, loaded_from=0, play_state="playing",
                effective_bpm=128.0, bpm=128.0, pitch_percent=0.0, speed=1.0,
                beat_count=1, beat_in_bar=1, cue_distance=0x1FF,
                master=master, sync=False, on_air=on_air,
            )

        def deck(number, track_id, master):
            return SimpleNamespace(
                number=number,
                status=status(track_id, master, True),
                is_playing=True,
                track_length_ms=180000,
                position_ms=1000.0,
                beat_packets=1,
                absolute_packets=1,
                position_source="exact",
            )

        mon.engine.active_decks.return_value = [
            deck(2, 20, False),
            deck(1, 10, True),
        ]
        mon.mixstatus = MagicMock()
        mon.mixstatus.as_state.return_value = {"now_playing": None, "pending": None, "setlist": []}
        mon.session = MagicMock()
        mon.session.as_state.return_value = {"recording": False, "tracks": []}
        mon.library = MagicMock()
        mon.library.lock = MagicMock()
        mon.library.lock.__enter__ = MagicMock(return_value=None)
        mon.library.lock.__exit__ = MagicMock(return_value=False)
        mon.library.media = {}
        mon._library_snapshot = MagicMock(return_value=(None, None, None))
        mon._lock = MagicMock()
        mon._lock.__enter__ = MagicMock(return_value=None)
        mon._lock.__exit__ = MagicMock(return_value=False)
        mon.meta = MagicMock(return_value={
            "title": "From Library",
            "artist": "Audience",
            "album": "Side A",
            "track_key": "",
            "duration_ms": 180000,
            "year": 2021,
        })
        mon.deck_track_key = MagicMock(return_value="")

        seen = []

        class Pub:
            def refresh_from_disk(self):
                return None

            def observe(self, audience, meta=None):
                seen.append((audience, meta))

            def health(self):
                return {
                    "enabled": True, "status": "idle", "detail": "On",
                    "host": "localhost", "port": 8899, "track": "",
                }

        mon.wnp = Pub()
        from prolink.audience_deck import AudienceDeckTracker
        mon.audience = AudienceDeckTracker()

        out = Monitor.state(mon)
        self.assertEqual(len(seen), 1)
        audience, meta = seen[0]
        self.assertEqual(audience["number"], 1)
        self.assertEqual(audience["track_id"], 10)
        self.assertEqual(audience["title"], "From Library")
        self.assertEqual(meta["album"], "Side A")
        self.assertEqual(out["wnp"]["status"], "idle")
        self.assertEqual(out["audience_deck"]["track_id"], 10)


if __name__ == "__main__":
    unittest.main()
