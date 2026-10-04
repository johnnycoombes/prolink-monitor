"""Diagnostic capture file format, and replay that does not transmit."""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from prolink import proto
from prolink.capture import (
    HEADER, CapturedPacket, PacketCapture, ReplaySource, clamp_duration,
    implied_port, read_capture, write_capture,
)
from prolink.link import ProLink


def _packet(kind: int, payload: bytes = b"") -> bytes:
    return proto.MAGIC + bytes([kind]) + payload


class CaptureFormatTests(unittest.TestCase):
    def test_round_trip_preserves_bytes_time_and_address(self):
        packets = [
            CapturedPacket(0.0, "192.168.1.10", 0, proto.PORT_STATUS,
                           _packet(proto.TYPE_CDJ_STATUS, b"\x01\x02")),
            CapturedPacket(0.2, "10.0.0.5", 0, proto.PORT_BEAT,
                           _packet(proto.TYPE_BEAT, b"\x28")),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "prolink-test.plc")
            write_capture(path, packets, started_unix_ms=1_700_000_000_000,
                          duration_s=10, truncated=False)
            loaded = read_capture(path)
        self.assertEqual(loaded.started_unix_ms, 1_700_000_000_000)
        self.assertEqual(loaded.duration_s, 10)
        self.assertFalse(loaded.truncated)
        self.assertEqual(len(loaded.packets), 2)
        self.assertEqual(loaded.packets[0].data, packets[0].data)
        self.assertEqual(loaded.packets[0].ip, "192.168.1.10")
        self.assertEqual(loaded.packets[0].dst_port, proto.PORT_STATUS)
        self.assertAlmostEqual(loaded.packets[1].offset_s, 0.2, places=6)
        self.assertEqual(loaded.packets[1].data, packets[1].data)
        self.assertEqual(loaded.packets[1].ip, "10.0.0.5")

    def test_truncated_flag_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "short.plc")
            write_capture(path, [], started_unix_ms=1, duration_s=5, truncated=True)
            loaded = read_capture(path)
        self.assertTrue(loaded.truncated)
        self.assertEqual(loaded.packets, [])

    def test_rejects_a_foreign_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nope.plc")
            with open(path, "wb") as handle:
                handle.write(b"XXXX" + b"\x00" * 24)
            with self.assertRaises(ValueError):
                read_capture(path)

    def test_rejects_a_record_cut_short(self):
        header = HEADER.pack(b"PLC1", 1, HEADER.size, 0, 5, 0, 0)
        # offset, ip, ports, length=10 but no payload follows
        record = struct.pack("<QIHHI", 0, 0, 0, 0, 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cut.plc")
            with open(path, "wb") as handle:
                handle.write(header + record)
            with self.assertRaises(ValueError):
                read_capture(path)

    def test_duration_is_clamped_to_the_supported_range(self):
        self.assertEqual(clamp_duration(1), 5.0)
        self.assertEqual(clamp_duration(90), 60.0)
        self.assertEqual(clamp_duration(15), 15.0)
        self.assertEqual(clamp_duration("nope"), 10.0)

    def test_implied_port_follows_the_packet_type(self):
        self.assertEqual(implied_port(_packet(proto.TYPE_KEEPALIVE)), proto.PORT_ANNOUNCE)
        self.assertEqual(implied_port(_packet(proto.TYPE_BEAT)), proto.PORT_BEAT)
        self.assertEqual(implied_port(_packet(proto.TYPE_ABSOLUTE_POSITION)), proto.PORT_BEAT)
        self.assertEqual(implied_port(_packet(proto.TYPE_CDJ_STATUS)), proto.PORT_STATUS)
        self.assertEqual(implied_port(b"hello"), 0)


class PacketCaptureTests(unittest.TestCase):
    def test_capture_writes_a_file_probe_can_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = PacketCapture()
            self.assertEqual(cap.start(12, directory=tmp), "")
            now = time.monotonic()
            status = _packet(proto.TYPE_CDJ_STATUS, b"\x0a" + b"\x00" * 8)
            beat = _packet(proto.TYPE_BEAT, b"\x00" * 4)
            cap.observe(status, "192.168.1.10", now)
            cap.observe(beat, "192.168.1.10", now + 0.25)
            cap.observe(b"ignore-me", "192.168.1.10", now + 0.3)
            info = cap.close()
            self.assertFalse(info["active"])
            self.assertEqual(info["last_packets"], 2)
            self.assertTrue(info["last_path"].endswith(".plc"))
            self.assertTrue(info["last_path"].startswith(tmp))
            self.assertFalse(any(name == "master.db" for name in os.listdir(tmp)))

            loaded = read_capture(info["last_path"])
            self.assertEqual(loaded.duration_s, 12)
            self.assertEqual([pkt.data for pkt in loaded.packets], [status, beat])
            self.assertEqual(loaded.packets[0].dst_port, proto.PORT_STATUS)
            self.assertEqual(loaded.packets[1].dst_port, proto.PORT_BEAT)
            self.assertAlmostEqual(loaded.packets[1].offset_s, 0.25, places=3)

            seen = []
            replay = ReplaySource(info["last_path"], pace=False)
            with patch("socket.socket") as sock:
                replay.start(lambda data, ip, ts: seen.append((data, ip)))
                replay.stop()
                sock.assert_not_called()
            self.assertEqual(seen, [(status, "192.168.1.10"), (beat, "192.168.1.10")])

    def test_second_start_is_refused_until_the_first_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = PacketCapture()
            self.assertEqual(cap.start(5, directory=tmp), "")
            self.assertEqual(cap.start(5, directory=tmp), "health_capture_busy")
            cap.close()

    def test_packet_cap_marks_the_file_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = PacketCapture()
            cap._write_async = cap._write  # finish on the calling thread
            cap.start(10, directory=tmp)
            now = time.monotonic()
            with patch("prolink.capture.MAX_PACKETS", 2):
                for i in range(4):
                    cap.observe(_packet(proto.TYPE_CDJ_STATUS, bytes([i])),
                                "10.0.0.1", now + i * 0.01)
            info = cap.status()
            self.assertFalse(info["active"])
            self.assertTrue(info["last_truncated"])
            self.assertEqual(info["last_packets"], 2)
            loaded = read_capture(info["last_path"])
            self.assertTrue(loaded.truncated)
            self.assertEqual(len(loaded.packets), 2)

    def test_listener_capture_does_not_send(self):
        source = MagicMock()
        engine = ProLink(source)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(engine.capture.start(5, directory=tmp), "")
            now = time.monotonic()
            engine._on_packet(_packet(proto.TYPE_BEAT, b"\x00" * 16), "10.1.1.1", now)
            engine._on_packet(b"nope", "10.1.1.1", now)
            info = engine.capture.close()
            source.assert_not_called()
            loaded = read_capture(info["last_path"])
            self.assertEqual(len(loaded.packets), 1)
            self.assertEqual(loaded.packets[0].ip, "10.1.1.1")

    def test_does_not_open_a_socket_while_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = PacketCapture()
            with patch("socket.socket") as sock:
                cap.start(5, directory=tmp)
                cap.observe(_packet(proto.TYPE_CDJ_STATUS), "192.168.0.2", time.monotonic())
                cap.close()
                sock.assert_not_called()


class ProbeReplayTests(unittest.TestCase):
    def test_help_offers_replay(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.check_output(
            [sys.executable, os.path.join(root, "probe.py"), "--help"],
            text=True,
        )
        self.assertIn("--replay", out)


if __name__ == "__main__":
    unittest.main()
