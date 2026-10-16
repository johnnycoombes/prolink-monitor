"""Web server reconnect, SSE cleanup, and the single-instance handoff.

A reconnect used to call shutdown() and leave port 8787 in LISTEN with the
accept loop stopped, so new browsers and OBS got "unable to connect". SSE
handlers also never noticed a closed client, so each one kept a thread.
"""

from __future__ import annotations

import os
import socket
import tempfile
import time
import unittest
import urllib.request
from types import SimpleNamespace
from unittest import mock

from app import Handler
from prolink.single_instance import (
    InstanceHub,
    apply_instance_command,
    claim_or_handoff,
    launch_command,
)
from prolink.web_server import (
    close_http_server,
    http_thread_count,
    start_http_thread,
)
from app import open_http_server


class _Monitor:
    def state(self):
        return {"ok": True, "decks": []}

    def paint_state(self):
        return {"paint": True, "ok": True}


def _get(port: int) -> int:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/state", timeout=2,
    ) as resp:
        body = resp.read()
        status = resp.status
    if b'"ok"' not in body:
        raise AssertionError(body)
    return status


def _open_sse(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.settimeout(2.0)
    sock.connect(("127.0.0.1", port))
    sock.sendall(
        b"GET /api/events HTTP/1.1\r\nHost: test\r\nConnection: keep-alive\r\n\r\n"
    )
    return sock


def _wait_until(predicate, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class WebServerReconnectTests(unittest.TestCase):
    def setUp(self):
        self._previous = Handler.monitor
        Handler.monitor = _Monitor()
        self.server = None
        self.thread = None

    def tearDown(self):
        close_http_server(self.server, self.thread)
        Handler.monitor = self._previous
        self.assertTrue(_wait_until(lambda: http_thread_count() == 0, 2.0))

    def _start(self, port: int = 0):
        self.server, bound = open_http_server(port, Handler, host="127.0.0.1")
        self.thread = start_http_thread(self.server)
        return bound

    def test_reconnect_keeps_the_same_port_serving(self):
        port = self._start(0)
        self.assertEqual(_get(port), 200)
        for _ in range(20):
            close_http_server(self.server, self.thread)
            self.server, self.thread = None, None
            rebound, bound = open_http_server(port, Handler, host="127.0.0.1")
            self.assertEqual(bound, port)
            self.server = rebound
            self.thread = start_http_thread(self.server)
            self.assertEqual(_get(port), 200)
            snap = self.server.snapshot()
            self.assertTrue(snap["listening"])
            self.assertEqual(snap["port"], port)

    def test_sse_disconnect_closes_the_client(self):
        port = self._start(0)
        sock = _open_sse(port)
        chunk = sock.recv(256)
        self.assertIn(b"text/event-stream", chunk)
        self.assertTrue(_wait_until(lambda: self.server.snapshot()["clients"] == 1))
        sock.close()
        self.assertTrue(
            _wait_until(lambda: self.server.snapshot()["clients"] == 0, 2.0),
            self.server.snapshot(),
        )
        # The request thread has to leave as well. Only the accept thread stays.
        self.assertTrue(_wait_until(lambda: http_thread_count() == 1, 2.0), http_thread_count())
        self.assertEqual(_get(port), 200)

    def test_stalled_sse_client_is_dropped(self):
        port = self._start(0)
        self.server.sse_write_timeout = 0.4
        self.server.sse_sndbuf = 1024
        self.server.sse_heartbeat_s = 0.05
        # Send every frame so the tiny static test state still fills the buffer.
        self.server.sse_resend_s = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        sock.settimeout(2.0)
        sock.connect(("127.0.0.1", port))
        sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: test\r\n\r\n")
        # Do not read. The write timeout has to drop the stream.
        try:
            self.assertTrue(
                _wait_until(lambda: self.server.snapshot()["clients"] == 0, 5.0),
                self.server.snapshot(),
            )
        finally:
            sock.close()
        self.assertTrue(_wait_until(lambda: http_thread_count() == 1, 2.0))

    def test_no_thread_growth_across_reconnects(self):
        port = self._start(0)
        before = http_thread_count()
        for _ in range(20):
            sock = _open_sse(port)
            sock.recv(128)
            sock.close()
            self.assertTrue(_wait_until(lambda: self.server.snapshot()["clients"] == 0, 2.0))
            close_http_server(self.server, self.thread)
            self.server, bound = open_http_server(port, Handler, host="127.0.0.1")
            self.assertEqual(bound, port)
            self.thread = start_http_thread(self.server)
            self.assertEqual(_get(port), 200)
        self.assertTrue(_wait_until(lambda: http_thread_count() <= before, 2.0))
        self.assertLessEqual(http_thread_count(), before)

    def test_too_many_streams_is_rejected(self):
        from prolink.web_server import MAX_SSE_CLIENTS

        port = self._start(0)
        held: list[socket.socket] = []
        try:
            for _ in range(MAX_SSE_CLIENTS):
                sock = _open_sse(port)
                held.append(sock)
                sock.recv(64)
            self.assertTrue(
                _wait_until(lambda: self.server.snapshot()["clients"] == MAX_SSE_CLIENTS, 2.0)
            )
            extra = _open_sse(port)
            try:
                data = b""
                while b"\r\n\r\n" not in data and len(data) < 2048:
                    data += extra.recv(256)
            finally:
                extra.close()
            self.assertIn(b"503", data.split(b"\r\n", 1)[0])
            self.assertLessEqual(self.server.snapshot()["clients"], MAX_SSE_CLIENTS)
        finally:
            for sock in held:
                sock.close()


class SingleInstanceTests(unittest.TestCase):
    def test_second_launch_hands_the_command_over(self):
        hub = InstanceHub(port=0)
        self.assertTrue(hub.start())
        try:
            self.assertIsNone(claim_or_handoff("open-panel", port=hub.port))
            seen: list[str] = []
            self.assertTrue(_wait_until(lambda: seen.extend(hub.poll()) or seen, 1.0))
            self.assertEqual(seen, ["open-panel"])
            self.assertIsNone(claim_or_handoff("show", port=hub.port))
            seen.clear()
            self.assertTrue(_wait_until(lambda: seen.extend(hub.poll()) or seen, 1.0))
            self.assertEqual(seen, ["show"])
        finally:
            hub.close()

    def test_show_brings_a_hidden_window_forward(self):
        calls: list[str] = []
        apply_instance_command(
            "show",
            show=lambda: calls.append("show"),
            open_panel=lambda: calls.append("panel"),
            open_overlay=lambda: calls.append("overlay"),
        )
        apply_instance_command(
            "open-panel",
            show=lambda: calls.append("show"),
            open_panel=lambda: calls.append("panel"),
            open_overlay=lambda: calls.append("overlay"),
        )
        self.assertEqual(calls, ["show", "show", "panel"])

    def test_launch_flags(self):
        self.assertEqual(launch_command(["ProlinkListener.exe"]), "show")
        self.assertEqual(launch_command(["ProlinkListener.exe", "--open-panel"]), "open-panel")
        self.assertEqual(launch_command(["ProlinkListener.exe", "--open-web"]), "open-panel")
        self.assertEqual(launch_command(["ProlinkListener.exe", "--open-overlay"]), "open-overlay")

    def test_port_is_free_after_the_primary_exits(self):
        hub = InstanceHub(port=0)
        self.assertTrue(hub.start())
        port = hub.port
        hub.close()
        again = InstanceHub(port=port)
        self.assertTrue(again.start())
        again.close()


class HealthWebLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_health_shows_listening_clients_and_the_last_error(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage

        page = HealthPage(I18n())
        page.update_state({
            "t": 1, "packets": 0, "decks": [], "nfs": [], "onelibrary": {},
            "web": {
                "listening": True,
                "host": "0.0.0.0",
                "port": 8787,
                "clients": 3,
                "threads": 4,
                "last_error": "too many event streams",
            },
        })
        self.assertIn("Listening", page._web_listen.text())
        self.assertIn("8787", page._web_listen.text())
        self.assertIn("3", page._web_clients.text())
        self.assertIn("4", page._web_clients.text())
        self.assertEqual(page._web_error.text(), "too many event streams")
        self.assertIn("Restart", page._web_restart.text())
        page.update_state({
            "t": 2, "packets": 0, "decks": [], "nfs": [], "onelibrary": {},
            "web": {"listening": False, "clients": 0, "threads": 0, "last_error": ""},
        })
        self.assertIn("Not listening", page._web_listen.text())


class FloatingReloadTests(unittest.TestCase):
    def test_reload_live_loads_again_on_the_same_url(self):
        from gui.floating_now import FloatingNowPlayingWindow
        from gui.settings import load_settings

        loads: list[str] = []

        def _load(url):
            loads.append(url.toString() if hasattr(url, "toString") else str(url))

        fake = SimpleNamespace(
            _loaded_url="http://127.0.0.1:8787/overlay",
            _backend=SimpleNamespace(port=8787),
            _settings_getter=lambda: load_settings(),
            _web=SimpleNamespace(load=_load),
        )
        fake._reload_overlay = lambda: FloatingNowPlayingWindow._reload_overlay(fake)
        FloatingNowPlayingWindow.reload_live(fake)
        FloatingNowPlayingWindow.reload_live(fake)
        self.assertEqual(len(loads), 2)
        self.assertIn("/overlay", loads[0])
        self.assertEqual(loads[0], loads[1])


class ExcludedPortRangeTests(unittest.TestCase):
    def test_parse_netsh_excludedportrange(self):
        from app import excluded_range_for, parse_excluded_port_ranges

        text = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      8756        8855
     50000       50059     *

* - Administered port exclusions.
"""
        ranges = parse_excluded_port_ranges(text)
        self.assertEqual(ranges, [(8756, 8855), (50000, 50059)])
        self.assertEqual(excluded_range_for(8777, ranges), (8756, 8855))
        self.assertEqual(excluded_range_for(8787, ranges), (8756, 8855))
        self.assertIsNone(excluded_range_for(8877, ranges))
        self.assertIsNone(excluded_range_for(18777, ranges))

    def test_excluded_port_is_skipped_for_the_next_free_candidate(self):
        from prolink import applog

        path = applog.reset_for_tests(
            os.path.join(tempfile.mkdtemp(), "prolink-monitor.log"))
        server = None
        try:
            with mock.patch("app.windows_excluded_tcp_ranges", return_value=[(8770, 8790)]):
                with mock.patch("app._HTTP_PORT_FALLBACKS", (8787, 0)):
                    server, bound = open_http_server(8777, host="127.0.0.1")
            self.assertNotEqual(bound, 8777)
            self.assertNotEqual(bound, 8787)
            self.assertIn("excluded port range", server.last_error)
            self.assertIn("8770-8790", server.last_error)
            self.assertIn("8777", server.last_error)
            self.assertIn("8787", server.last_error)
            self.assertIn(f"listening on {bound}", server.last_error)
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
            self.assertIn("8777 is inside a Windows excluded port range 8770-8790", body)
            self.assertIn("netsh interface ipv4 show excludedportrange", body)
        finally:
            if server is not None:
                server.server_close()
            applog.reset_for_tests(path)


class HttpBindFallbackTests(unittest.TestCase):
    def test_busy_port_is_logged_and_the_next_port_is_used(self):
        from prolink import applog

        # PanelServer sets SO_REUSEADDR so a reconnect can rebind. On Windows
        # that also lets a second socket share a listening port, so the holder
        # has to be exclusive or the fallback never runs.
        holder = socket.socket()
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            holder.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        holder.bind(("127.0.0.1", 0))
        busy = holder.getsockname()[1]
        holder.listen(1)
        path = applog.reset_for_tests(
            os.path.join(tempfile.mkdtemp(), "prolink-monitor.log"))
        server = None
        try:
            with mock.patch("app._HTTP_PORT_FALLBACKS", (0,)):
                server, bound = open_http_server(busy, host="127.0.0.1")
            self.assertNotEqual(bound, busy)
            self.assertIn(str(busy), server.last_error)
            # Linux reports the holder as "port in use". Windows reports an
            # exclusive holder as WinError 10013, "port blocked or reserved".
            self.assertTrue(
                "port in use" in server.last_error
                or "port blocked or reserved" in server.last_error,
                server.last_error,
            )
            self.assertIn(f"listening on {bound}", server.last_error)
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
            self.assertIn(str(busy), body)
            self.assertTrue(
                "port in use" in body or "port blocked or reserved" in body,
                body,
            )
        finally:
            holder.close()
            if server is not None:
                server.server_close()
            applog.reset_for_tests(path)
