"""Tests for NFS RPC latency tracking and health state shape."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class RpcClientLatencyTests(unittest.TestCase):
    def test_record_rtt_sets_ewma(self):
        from prolink.nfs import RpcClient

        client = RpcClient.__new__(RpcClient)
        client.last_rtt_ms = None
        client._rtt_ewma_ms = None
        client.call_count = 0
        client.timeout_count = 0

        with patch("prolink.nfs.time.monotonic", return_value=100.025):
            client._record_rtt(100.0)
        self.assertAlmostEqual(client.last_rtt_ms, 25.0, places=3)
        self.assertAlmostEqual(client.rtt_ms, 25.0, places=3)

        with patch("prolink.nfs.time.monotonic", return_value=200.010):
            client._record_rtt(200.0)
        # 0.3*10 + 0.7*25 = 20.5
        self.assertAlmostEqual(client.last_rtt_ms, 10.0, places=3)
        self.assertAlmostEqual(client.rtt_ms, 20.5, places=3)


class NfsLatencyInfoTests(unittest.TestCase):
    def test_latency_info_reads_rpc_clients(self):
        from prolink.nfs import NfsClient

        nfs = NfsClient.__new__(NfsClient)
        mount = MagicMock()
        mount.rtt_ms = 12.0
        mount.call_count = 3
        mount.timeout_count = 1
        rpc = MagicMock()
        rpc.rtt_ms = 8.5
        rpc.last_rtt_ms = 9.0
        rpc.call_count = 40
        rpc.timeout_count = 2
        nfs._mount = mount
        nfs._nfs = rpc

        info = NfsClient.latency_info(nfs)
        self.assertEqual(info["nfs_rtt_ms"], 8.5)
        self.assertEqual(info["nfs_last_rtt_ms"], 9.0)
        self.assertEqual(info["mount_rtt_ms"], 12.0)
        self.assertEqual(info["nfs_calls"], 40)
        self.assertEqual(info["nfs_timeouts"], 2)


class MonitorHealthStateTests(unittest.TestCase):
    def test_state_includes_nfs_list(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.host = "192.168.1.10"
        mon.engine = MagicMock()
        mon.engine.active_decks.return_value = []
        mon.engine.packets = 100
        mon.engine.source = MagicMock(description="vcdj", kind="vcdj", detail="")
        mon.engine.lock = MagicMock()
        mon.engine.lock.__enter__ = MagicMock(return_value=None)
        mon.engine.lock.__exit__ = MagicMock(return_value=False)
        mon.engine.devices = {}
        mon.mixstatus = MagicMock()
        mon.mixstatus.as_state.return_value = {
            "now_playing": None, "pending": None, "setlist": [],
        }
        mon.session = MagicMock()
        mon.session.as_state.return_value = {"recording": False, "tracks": []}
        mon.library = MagicMock()
        mon.library.lock = MagicMock()
        mon.library.lock.__enter__ = MagicMock(return_value=None)
        mon.library.lock.__exit__ = MagicMock(return_value=False)
        media = MagicMock()
        media.export = "/C/"
        media.nfs.latency_info.return_value = {
            "nfs_rtt_ms": 4.2,
            "nfs_last_rtt_ms": 4.0,
            "mount_rtt_ms": 5.0,
            "nfs_calls": 10,
            "nfs_timeouts": 0,
        }
        mon.library.media = {"192.168.1.10": media}
        mon._library_snapshot = MagicMock(return_value=(None, None, None))
        from prolink.audience_deck import AudienceDeckTracker

        mon.audience = AudienceDeckTracker()
        mon.meta = MagicMock(return_value=None)

        out = Monitor.state(mon)
        self.assertIn("audience_deck", out)
        self.assertIn("nfs", out)
        self.assertEqual(len(out["nfs"]), 1)
        self.assertEqual(out["nfs"][0]["host"], "192.168.1.10")
        self.assertEqual(out["nfs"][0]["rtt_ms"], 4.2)
        self.assertEqual(out["nfs"][0]["calls"], 10)


class HealthPageHelpersTests(unittest.TestCase):
    def test_source_labels(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage, _POSITION_SOURCE_KEYS

        self.assertEqual(_POSITION_SOURCE_KEYS["exact"], "health_source_exact")
        self.assertEqual(_POSITION_SOURCE_KEYS["beat_grid"], "health_source_beat_grid")
        i18n = I18n()
        page = HealthPage.__new__(HealthPage)
        page._i18n = i18n
        self.assertIn("Absolute", page._source_label("exact"))
        self.assertIn("Beat", page._source_label("beat_grid"))
        self.assertEqual(page._source_label("none"), "—")


if __name__ == "__main__":
    unittest.main()
