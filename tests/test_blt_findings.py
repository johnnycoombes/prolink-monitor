"""1.0.17: findings from Beat Link Trigger docs/source (reference only)."""
import unittest

from prolink import allinone, models, proto
from prolink.link import ProLink
from unittest.mock import MagicMock


def _on_air(name=b"XDJ-XZ", number=33, flags=(1, 0, 0, 0), six=False):
    pkt = bytearray(0x35 if six else 0x2D)
    pkt[:10] = proto.MAGIC
    pkt[10] = proto.TYPE_CHANNELS_ON_AIR
    pkt[0x0B:0x0B + len(name)] = name
    pkt[0x21] = number
    for i, f in enumerate(flags[:4]):
        pkt[0x24 + i] = f
    if six:
        pkt[0x2D], pkt[0x2E] = flags[4], flags[5]
    return bytes(pkt)


def _keepalive(name, number, ip):
    return proto.build_keepalive(name, number, b"\x00\x01\x02\x03\x04\x05", ip)


class XzAdviceTests(unittest.TestCase):
    def test_link_port_first_usb_experimental(self):
        xz = models.PROFILES["xdj-xz"]
        self.assertEqual(xz.recommended_mode, "vcdj")
        self.assertTrue(xz.connection.startswith("LINK port"))
        self.assertIn("unreliable", xz.connection)
        self.assertIn("remote controlled", xz.notes)
        from gui import i18n
        self.assertIn("experimental", i18n.STRINGS["mode_observe"])

    def test_opus_one_usb_and_id_drift(self):
        notes = models.PROFILES["opus-quad"].notes
        self.assertIn("one USB at a time", notes)
        self.assertIn("drift", notes)


class DuplicatePathTests(unittest.TestCase):
    def test_same_device_two_addresses(self):
        eng = ProLink(source=MagicMock())
        eng._on_packet(_keepalive("XDJ-XZ", 1, "169.254.2.3"), "169.254.2.3", 1.0)
        eng._on_packet(_keepalive("XDJ-XZ", 1, "192.168.1.40"), "192.168.1.40", 2.0)
        eng._on_packet(_keepalive("XDJ-XZ", 2, "192.168.1.40"), "192.168.1.40", 2.0)
        dups = eng.duplicate_paths(now=3.0)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["device"], 1)
        self.assertEqual(dups[0]["paths"], ["169.254.2.3", "192.168.1.40"])
        self.assertEqual(eng.duplicate_paths(now=30.0), [])   # stale paths drop out

    def test_single_path_is_quiet(self):
        self.assertEqual(allinone.duplicate_paths([("CDJ-3000", 2, "10.0.0.2", "")] * 3), [])


class SlotLabelTests(unittest.TestCase):
    def test_xz_reported_sd_is_usb1(self):
        self.assertEqual(allinone.slot_display("sd", "XDJ-XZ"), "USB 1")
        self.assertEqual(allinone.slot_display("usb", "XDJ-XZ"), "USB 2")

    def test_az_is_reverse(self):
        self.assertEqual(allinone.slot_display("usb", "XDJ-AZ"), "USB 1")
        self.assertEqual(allinone.slot_display("sd", "XDJ-AZ"), "USB 2")
        self.assertEqual(allinone.slot_display("usb2", "XDJ-AZ"), "USB 2")

    def test_others_unchanged(self):
        self.assertEqual(allinone.slot_display("sd", "CDJ-3000"), "SD")
        self.assertEqual(allinone.slot_display("usb", "CDJ-3000"), "USB")
        self.assertEqual(allinone.slot_display("empty", "XDJ-XZ"), "")

    def test_nfs_mapping_stays_by_reported_slot(self):
        self.assertEqual(allinone.export_for_slot(["/B/", "/C/"], "sd"), "/B/")
        self.assertEqual(allinone.export_for_slot(["/B/", "/C/"], "usb"), "/C/")


class MixerOnAirTests(unittest.TestCase):
    def test_xz_mixer_33_channels(self):
        pkt = proto.parse(_on_air(flags=(1, 0, 0, 0)))
        self.assertEqual(pkt.device_number, 33)
        self.assertEqual(pkt.channels, {1: True, 2: False, 3: False, 4: False})
        eng = ProLink(source=MagicMock())
        eng._on_packet(_on_air(flags=(0, 1, 0, 0)), "169.254.2.3", 5.0)
        self.assertEqual(eng.mixer_on_air[2], True)
        self.assertEqual(eng.mixer_on_air_from, (33, "XDJ-XZ"))
        self.assertIsNone(eng.az_mode(now=5.1))     # an XZ is not an AZ

    def test_six_channel(self):
        pkt = proto.parse(_on_air(b"DJM-V10", flags=(0, 0, 0, 0, 1, 1), six=True))
        self.assertTrue(pkt.channels[5] and pkt.channels[6])

    def test_state_helper(self):
        import app
        eng = ProLink(source=MagicMock())
        eng._on_packet(_on_air(flags=(1, 1, 0, 0)), "169.254.2.3", 5.0)
        out = app._mixer_on_air(eng)
        self.assertEqual(out["device"], 33)
        self.assertEqual(out["channels"]["1"], True)


if __name__ == "__main__":
    unittest.main()
