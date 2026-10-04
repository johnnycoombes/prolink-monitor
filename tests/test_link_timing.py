"""Rolling status-packet timing for flaky-link diagnosis."""

from __future__ import annotations

import os
import struct
import time
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink import proto
from prolink.link import ProLink
from prolink.link_timing import LATE_GAP_S, LinkTiming, status_counter


def _status(device: int = 1, seq: int | None = None, name: bytes = b"XDJ-AZ",
            length: int = 0xD4) -> bytes:
    pkt = bytearray(length)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_CDJ_STATUS
    pkt[0x0B:0x0B + len(name)] = name[:20]
    if length > 0x21:
        pkt[0x21] = device
    if seq is not None and length >= 0xCC:
        struct.pack_into(">I", pkt, 0xC8, seq & 0xFFFFFFFF)
    return bytes(pkt)


class StatusCounterTests(unittest.TestCase):
    def test_reads_big_endian_counter(self):
        self.assertEqual(status_counter(_status(seq=0x01020304)), 0x01020304)

    def test_short_packet_has_no_counter(self):
        self.assertIsNone(status_counter(_status(seq=7, length=0xA7)))

    def test_non_status_has_no_counter(self):
        pkt = bytearray(_status(seq=3))
        pkt[10] = proto.TYPE_BEAT
        self.assertIsNone(status_counter(bytes(pkt)))


class LinkTimingTests(unittest.TestCase):
    def test_interval_jitter_and_age(self):
        timing = LinkTiming()
        for t in (0.0, 0.1, 0.4):
            timing.observe(1, t, _status(seq=None), ip="10.0.0.2", name="XDJ-AZ")
        row = timing.snapshot(0.45)[0]
        self.assertEqual(row["device"], 1)
        self.assertEqual(row["name"], "XDJ-AZ")
        self.assertEqual(row["ip"], "10.0.0.2")
        self.assertAlmostEqual(row["age_ms"], 50.0, places=1)
        self.assertAlmostEqual(row["interval_ms"], 200.0, places=1)
        self.assertAlmostEqual(row["jitter_ms"], 100.0, places=1)
        self.assertAlmostEqual(row["max_gap_ms"], 300.0, places=1)
        self.assertEqual(row["late"], 0)
        self.assertIsNone(row["seq_holes"])
        self.assertEqual(row["seq_counter"], "unused")

    def test_late_gap(self):
        timing = LinkTiming()
        for t in (0.0, 0.2, 0.2 + LATE_GAP_S + 0.05):
            timing.observe(2, t, _status(device=2))
        row = timing.snapshot(0.2 + LATE_GAP_S + 0.05)[0]
        self.assertEqual(row["late"], 1)
        self.assertGreater(row["max_gap_ms"], 400.0)

    def test_single_packet_has_age_but_no_interval(self):
        timing = LinkTiming()
        timing.observe(1, 5.0, _status())
        row = timing.snapshot(5.2)[0]
        self.assertAlmostEqual(row["age_ms"], 200.0, places=1)
        self.assertIsNone(row["interval_ms"])
        self.assertIsNone(row["jitter_ms"])
        self.assertIsNone(row["max_gap_ms"])

    def test_sequence_holes_after_counter_is_trusted(self):
        timing = LinkTiming()
        # 10,11,12,13 proves the counter; 16 skips two values.
        for i, seq in enumerate((10, 11, 12, 13, 16)):
            timing.observe(1, float(i), _status(seq=seq))
        row = timing.snapshot(4.0)[0]
        self.assertEqual(row["seq_counter"], "live")
        self.assertEqual(row["seq_holes"], 2)

    def test_jump_before_trust_is_not_a_hole(self):
        timing = LinkTiming()
        for i, seq in enumerate((1, 100, 101, 102, 103)):
            timing.observe(1, float(i), _status(seq=seq))
        row = timing.snapshot(4.0)[0]
        self.assertEqual(row["seq_counter"], "live")
        self.assertEqual(row["seq_holes"], 0)

    def test_stuck_counter_stays_unused(self):
        timing = LinkTiming()
        for i in range(20):
            timing.observe(1, float(i) * 0.2, _status(seq=0))
        row = timing.snapshot(4.0)[0]
        self.assertEqual(row["seq_counter"], "unused")
        self.assertIsNone(row["seq_holes"])

    def test_counter_that_freezes_is_retired(self):
        timing = LinkTiming()
        seqs = [1, 2, 3, 4] + [4] * 8
        for i, seq in enumerate(seqs):
            timing.observe(1, float(i), _status(seq=seq))
        row = timing.snapshot(float(len(seqs)))[0]
        self.assertEqual(row["seq_counter"], "unused")
        self.assertIsNone(row["seq_holes"])

    def test_counter_wraps_at_32_bits(self):
        timing = LinkTiming()
        seqs = [0xFFFFFFFE, 0xFFFFFFFF, 0, 1, 4]
        for i, seq in enumerate(seqs):
            timing.observe(1, float(i), _status(seq=seq))
        row = timing.snapshot(4.0)[0]
        self.assertEqual(row["seq_counter"], "live")
        self.assertEqual(row["seq_holes"], 2)

    def test_window_forgets_old_gaps(self):
        timing = LinkTiming(window_s=1.0)
        timing.observe(1, 0.0, _status())
        timing.observe(1, 0.8, _status())
        timing.observe(1, 2.0, _status())
        timing.observe(1, 2.2, _status())
        row = timing.snapshot(2.2)[0]
        self.assertEqual(row["late"], 0)
        self.assertAlmostEqual(row["max_gap_ms"], 200.0, places=1)
        self.assertLessEqual(row["samples"], 3)

    def test_sample_cap_bounds_memory(self):
        timing = LinkTiming(window_s=1000.0, max_samples=4)
        for i in range(30):
            timing.observe(1, i * 0.01, _status())
        row = timing.snapshot(0.3)[0]
        self.assertLessEqual(row["samples"], 4)
        self.assertEqual(len(timing._devices[1].samples), 4)

    def test_device_cap_drops_the_quietest(self):
        timing = LinkTiming(max_devices=4, stale_s=100.0)
        for device in range(1, 8):
            timing.observe(device, float(device), _status(device=device))
        rows = timing.snapshot(8.0)
        numbers = [row["device"] for row in rows]
        self.assertLessEqual(len(numbers), 4)
        self.assertIn(7, numbers)
        self.assertNotIn(1, numbers)

    def test_stale_device_drops_off(self):
        timing = LinkTiming(stale_s=5.0)
        timing.observe(1, 0.0, _status())
        self.assertEqual(timing.snapshot(4.0)[0]["device"], 1)
        self.assertEqual(timing.snapshot(6.0), [])


class EngineTimingTests(unittest.TestCase):
    def test_status_updates_timing_without_touching_the_source(self):
        source = MagicMock()
        engine = ProLink(source)
        now = time.monotonic()
        engine._on_packet(_status(device=3, seq=1, name=b"CDJ-3000"), "192.168.1.9", now)
        engine._on_packet(b"not a prolink packet", "192.168.1.9", now)
        source.assert_not_called()
        rows = engine.link_timing.snapshot(now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device"], 3)
        self.assertEqual(rows[0]["name"], "CDJ-3000")
        self.assertEqual(rows[0]["ip"], "192.168.1.9")

    def test_state_includes_link_timing(self):
        from app import Monitor

        source = MagicMock()
        source.description = "virtual device"
        source.kind = "vcdj"
        source.detail = ""
        engine = ProLink(source)
        now = time.monotonic()
        engine._on_packet(_status(device=1, name=b"XDJ-AZ"), "192.168.1.10", now)

        mon = Monitor.__new__(Monitor)
        mon.host = "192.168.1.10"
        mon.engine = engine
        mon.mixstatus = MagicMock()
        mon.mixstatus.as_state.return_value = {
            "now_playing": None, "pending": None, "setlist": [],
        }
        mon.mixstatus.handle = MagicMock()
        mon.session = MagicMock()
        mon.session.as_state.return_value = {"recording": False, "tracks": []}
        mon.session.observe = MagicMock()
        mon.library = MagicMock()
        mon.library.lock = MagicMock()
        mon.library.lock.__enter__ = MagicMock(return_value=None)
        mon.library.lock.__exit__ = MagicMock(return_value=False)
        mon.library.media = {}
        mon._library_snapshot = MagicMock(return_value=(None, None, None))
        from prolink.audience_deck import AudienceDeckTracker
        mon.audience = AudienceDeckTracker()
        mon.meta = MagicMock(return_value=None)
        mon._lock = None

        out = Monitor.state(mon)
        self.assertIn("link_timing", out)
        self.assertEqual(out["link_timing"][0]["device"], 1)
        self.assertIn("capture", out)
        self.assertFalse(out["capture"]["active"])
        source.assert_not_called()


class HealthTimingPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_page_shows_timing_and_blank_holes(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage

        page = HealthPage(I18n())
        page.update_state({
            "t": time.time(),
            "packets": 40,
            "mode": "virtual device",
            "host": "192.168.1.10",
            "decks": [],
            "nfs": [],
            "onelibrary": {},
            "link_timing": [{
                "device": 1,
                "name": "XDJ-AZ",
                "age_ms": 42.0,
                "interval_ms": 168.0,
                "jitter_ms": 6.0,
                "max_gap_ms": 190.0,
                "late": 0,
                "seq_holes": None,
                "seq_counter": "unused",
                "samples": 20,
            }, {
                "device": 2,
                "name": "XDJ-AZ",
                "age_ms": 1400.0,
                "interval_ms": 210.0,
                "jitter_ms": 90.0,
                "max_gap_ms": 910.0,
                "late": 4,
                "seq_holes": 3,
                "seq_counter": "live",
                "samples": 20,
            }],
            "capture": {"active": False, "saving": False, "error": "", "last_path": ""},
        })
        table = page.link_panel.table
        self.assertEqual(table.rowCount(), 2)
        self.assertIn("42", table.item(0, 1).text())
        self.assertEqual(table.item(0, 6).text(), "—")
        self.assertIn("1.40", table.item(1, 1).text())
        self.assertEqual(table.item(1, 5).text(), "4")
        self.assertEqual(table.item(1, 6).text(), "3")
        self.assertFalse(page.link_panel._empty.isVisible())

    def test_capture_button_needs_a_connection(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage

        page = HealthPage(I18n())
        page.link_panel.seconds.setValue(15)
        page.link_panel.capture_btn.click()
        self.assertIn("Connect", page.link_panel._status.text())

    def test_capture_button_asks_for_the_selected_duration(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage

        backend = MagicMock()
        backend.start_link_capture.return_value = ""
        page = HealthPage(I18n(), backend)
        page.link_panel.seconds.setValue(27)
        page.link_panel.capture_btn.click()
        backend.start_link_capture.assert_called_once_with(27)


if __name__ == "__main__":
    unittest.main()
