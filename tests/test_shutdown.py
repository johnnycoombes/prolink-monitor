"""Quit joins worker threads and only hard-exits once the app is armed."""

from __future__ import annotations

import threading
import time
import unittest

from prolink.link import SnifferSource, SocketSource
from prolink.shutdown import (
    arm_process_exit,
    force_exit_if_lingering,
    is_worker,
    join_workers,
    request_process_exit,
)


class ShutdownTests(unittest.TestCase):
    def test_join_stops_a_named_worker(self):
        started = threading.Event()
        release = threading.Event()

        def run():
            started.set()
            release.wait(5)

        thread = threading.Thread(target=run, name="prolink-http", daemon=True)
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertIn("prolink-http", join_workers(0.05))
        release.set()
        left = join_workers(1.0)
        self.assertNotIn("prolink-http", left)
        self.assertFalse(thread.is_alive())

    def test_force_exit_is_armed_and_mockable(self):
        calls = []
        self.assertFalse(force_exit_if_lingering([], exit_fn=calls.append))
        self.assertTrue(force_exit_if_lingering(["prolink-dbserver"], exit_fn=calls.append))
        self.assertEqual(calls, [0])
        # Unarmed: a lingering name is reported and the process is not killed.
        names = request_process_exit(grace=0.05, timeout=0.05)
        self.assertNotIn("prolink-quit", names)

    def test_armed_exit_calls_os_exit_when_a_worker_remains(self):
        calls = []
        original = force_exit_if_lingering

        def fake(names, exit_fn=None):
            if names:
                calls.append(list(names))
            return bool(names)

        import prolink.shutdown as shutdown
        shutdown.force_exit_if_lingering = fake
        shutdown._armed = True
        try:
            hold = threading.Event()

            def run():
                hold.wait(5)

            thread = threading.Thread(target=run, name="prolink-dbserver", daemon=True)
            thread.start()
            request_process_exit(grace=0.05, timeout=0.05)
            time.sleep(0.3)
            self.assertTrue(calls)
            self.assertTrue(any("prolink-dbserver" in names or "prolink-quit" in names
                                for names in calls))
        finally:
            hold.set()
            shutdown.force_exit_if_lingering = original
            shutdown._armed = False
            thread.join(1)

    def test_socket_stop_unblocks_announce(self):
        source = SocketSource.__new__(SocketSource)
        source._running = True
        source._stop = threading.Event()
        source._socks = []
        source._threads = []
        started = threading.Event()

        def announce():
            started.set()
            while source._running:
                if source._stop.wait(30):
                    return

        thread = threading.Thread(target=announce, name="prolink-announce", daemon=True)
        source._threads.append(thread)
        thread.start()
        self.assertTrue(started.wait(1))
        began = time.monotonic()
        source.stop()
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertFalse(thread.is_alive())

    def test_sniffer_stop_kills_a_hung_process(self):
        source = SnifferSource.__new__(SnifferSource)
        source._running = True
        source._thread = None
        killed = {}

        class Proc:
            def terminate(self):
                killed["term"] = True

            def wait(self, timeout=None):
                raise __import__("subprocess").TimeoutExpired(cmd="tshark", timeout=timeout)

            def kill(self):
                killed["kill"] = True

        source._proc = Proc()
        source.stop()
        self.assertTrue(killed.get("term"))
        self.assertTrue(killed.get("kill"))
        self.assertIsNone(source._proc)

    def test_monitor_stop_joins_the_dbserver_thread(self):
        from unittest import mock
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._stop = threading.Event()
        mon._workers = []
        mon.loopback = mock.Mock()
        mon.engine = mock.Mock()
        mon.library = mock.Mock()
        started = threading.Event()

        def work():
            started.set()
            mon._stop.wait(5)

        mon._spawn(work, "prolink-dbserver")
        self.assertTrue(started.wait(1))
        mon.stop()
        self.assertTrue(mon.engine.stop.called)
        self.assertTrue(mon.library.close.called)
        self.assertFalse(any(thread.is_alive() for thread in mon._workers))

    def test_worker_prefix(self):
        self.assertTrue(is_worker("prolink-http-accept"))
        self.assertFalse(is_worker("MainThread"))
        # arm_process_exit is what the real entry calls. Leave tests unarmed.
        arm_process_exit()
        import prolink.shutdown as shutdown
        self.assertTrue(shutdown._armed)
        shutdown._armed = False
