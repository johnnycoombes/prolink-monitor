"""1.0.19: XDJ-XZ-only changes. Every other model, the XDJ-AZ above all,
must behave exactly as before."""
import unittest

from prolink import allinone, models, proto
from prolink.link import NetInfo, ProLink, SocketSource, XZ_NO_STATUS_S
from prolink.remotedb_client import choose_requesting_player


def xz_mixer_fixture(name: bytes = b"XDJ-XZ") -> bytes:
    """Hand re-creation of prodj-beats' BridgeRegressionTest mixer packet."""
    b = bytearray(56)
    b[0:10] = b"Qspt1WmJOL"
    b[10] = 0x29                       # mixer status on UDP 50002
    b[11:11 + len(name)] = name
    b[0x21] = 33
    b[0x23] = 20
    b[0x24] = 33
    b[0x27] = 0xD0
    b[0x29] = 0x10                     # neutral pitch 0x00100000
    b[0x2E], b[0x2F] = 0x36, 0xA6      # 139.90 BPM
    b[0x36] = 0xFF
    b[0x37] = 3
    return bytes(b)


def keepalive(name: str, number: int, ip: str) -> bytes:
    return proto.build_keepalive(name, number, b"\x00\x11\x22\x33\x44\x55", ip)


def status(name: bytes, number: int) -> bytes:
    b = bytearray(0xD4)
    b[0:10] = proto.MAGIC
    b[10] = proto.TYPE_CDJ_STATUS
    b[0x0B:0x0B + len(name)] = name
    b[0x21] = number
    return bytes(b)


class Src:
    kind = "vcdj"

    def start(self, cb):
        pass


class XzNumberOrder(unittest.TestCase):
    def test_xz_tries_3_then_4_then_usual(self):
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 33}, xz=True)[0], 3)
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 3, 33}, xz=True)[0], 4)
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 3, 4, 33}, xz=True)[0], 5)
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 3, 4, 5, 33}, xz=True)[0], 6)
        self.assertEqual(allinone.XZ_AUTO_ORDER, (3, 4, 5, 6, 7, 8, 13, 14, 15))

    def test_xz_none_free_stops(self):
        with self.assertRaises(RuntimeError):
            allinone.choose_device_number(0, set(range(1, 16)), xz=True)

    def test_preferred_number_still_wins(self):
        self.assertEqual(allinone.choose_device_number(7, {1, 2, 33}, xz=True)[0], 7)

    def test_other_models_unchanged(self):
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 3, 4, 33})[0], 5)
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 33})[0], 5)

    def test_source_uses_xz_order_only_when_xz_announced(self):
        net = NetInfo("169.254.10.99", 16, "en15")
        src = SocketSource(net, device_number=0)
        src._note_announce(keepalive("XDJ-XZ", 1, "169.254.10.5"), "169.254.10.5")
        src._note_announce(keepalive("XDJ-XZ", 2, "169.254.10.5"), "169.254.10.5")
        src._pick_number()
        self.assertTrue(src.xz_seen)
        self.assertEqual(src.device_number, 3)

        az = SocketSource(net, device_number=0)
        for n in (1, 2, 3, 4):
            az._note_announce(keepalive("XDJ-AZ", n, "169.254.10.6"), "169.254.10.6")
        az._pick_number()
        self.assertFalse(az.xz_seen)
        self.assertEqual(az.device_number, 5)

        cdj = SocketSource(net, device_number=0)
        cdj._note_announce(keepalive("CDJ-3000", 1, "169.254.10.7"), "169.254.10.7")
        cdj._pick_number()
        self.assertEqual(cdj.device_number, 5)

    def test_dbserver_requester_never_at_xz_ip(self):
        # We announce 3; the XZ holds 1, 2 and 33 on one IP.
        n = choose_requesting_player(3, target_player=1, taken={1, 2, 33},
                                     same_ip={1, 2, 33})
        self.assertNotIn(n, (1, 2, 33))


class XzMixerStatus(unittest.TestCase):
    def test_fixture_decodes(self):
        m = proto.parse_xz_mixer_status(xz_mixer_fixture())
        self.assertIsNotNone(m)
        self.assertEqual(m.device_number, 33)
        self.assertEqual(m.bpm, 139.9)
        self.assertAlmostEqual(m.pitch, 0.0)
        self.assertEqual(m.flags, 0xD0)
        self.assertFalse(m.tempo_master)
        self.assertTrue(m.synced)
        self.assertTrue(m.playing)
        self.assertIsNone(m.handoff_to)

    def test_master_and_handoff(self):
        b = bytearray(xz_mixer_fixture())
        b[0x27] = 0x20
        b[0x36] = 2
        m = proto.parse_xz_mixer_status(bytes(b))
        self.assertTrue(m.tempo_master)
        self.assertEqual(m.handoff_to, 2)

    def test_other_models_not_decoded(self):
        self.assertIsNone(proto.parse_xz_mixer_status(xz_mixer_fixture(b"XDJ-AZ")))
        self.assertIsNone(proto.parse_xz_mixer_status(xz_mixer_fixture(b"DJM-900NXS2")))
        self.assertIsNone(proto.parse_xz_mixer_status(xz_mixer_fixture()[:0x30]))
        # The general parser still ignores mixer status (unchanged).
        self.assertIsNone(proto.parse(xz_mixer_fixture()))

    def test_engine_api(self):
        eng = ProLink(Src())
        eng._on_packet(xz_mixer_fixture(), "192.168.178.47", 100.0)
        self.assertEqual(eng.xz_mixer_state(now=101.0)["bpm"], 139.9)
        self.assertFalse(eng.xz_mixer_state(now=101.0)["tempo_master"])
        self.assertEqual(eng.xz_mixer_state(now=200.0), {})
        self.assertEqual(eng.packets, 0)        # counted as before (not parsed)

    def test_az_mixer_packet_ignored(self):
        eng = ProLink(Src())
        eng._on_packet(xz_mixer_fixture(b"XDJ-AZ"), "10.0.0.2", 100.0)
        self.assertEqual(eng.xz_mixer, {})
        self.assertEqual(eng.xz_mixer_state(now=100.5), {})


class XzProfile(unittest.TestCase):
    def test_xz_notes(self):
        d = models.PROFILES["xdj-xz"].as_dict()
        self.assertEqual(d["not_available"], ["master_level", "meters", "beat_fx"])
        self.assertEqual(d["on_air"], "reported channel state")
        self.assertIn("no master level", d["notes"])

    def test_az_profile_unchanged(self):
        d = models.PROFILES["xdj-az"].as_dict()
        self.assertNotIn("not_available", d)
        self.assertNotIn("on_air", d)
        self.assertEqual(d["notes"], "Decks 1-4 and mixer 33 on one IP.")
        self.assertEqual(d["numbers"], [1, 2, 3, 4, 33])
        for key, p in models.PROFILES.items():
            if key != "xdj-xz":
                self.assertNotIn("not_available", p.as_dict(), key)


class XzNoStatusWarning(unittest.TestCase):
    def test_warns_after_15s_without_status(self):
        eng = ProLink(Src())
        eng._on_packet(keepalive("XDJ-XZ", 1, "10.0.0.5"), "10.0.0.5", 100.0)
        self.assertFalse(eng.xz_no_status(now=100.0 + XZ_NO_STATUS_S - 1))
        self.assertTrue(eng.xz_no_status(now=100.0 + XZ_NO_STATUS_S + 1))
        eng._on_packet(status(b"XDJ-XZ", 1), "10.0.0.5", 120.0)
        self.assertFalse(eng.xz_no_status(now=130.0))

    def test_mixer_status_counts(self):
        eng = ProLink(Src())
        eng._on_packet(keepalive("XDJ-XZ", 33, "10.0.0.5"), "10.0.0.5", 100.0)
        eng._on_packet(xz_mixer_fixture(), "10.0.0.5", 101.0)
        self.assertFalse(eng.xz_no_status(now=130.0))

    def test_az_never_warns(self):
        eng = ProLink(Src())
        eng._on_packet(keepalive("XDJ-AZ", 1, "10.0.0.6"), "10.0.0.6", 100.0)
        self.assertFalse(eng.xz_no_status(now=200.0))

    def test_state_keys_only_for_xz(self):
        import app
        eng = ProLink(Src())
        eng._on_packet(keepalive("XDJ-AZ", 1, "10.0.0.6"), "10.0.0.6", 100.0)
        self.assertEqual(app._xz_state(eng), {})


class XzHealthPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from gui.i18n import I18n
        from gui.pages import HealthPage
        return HealthPage(I18n())

    def test_xz_rows(self):
        page = self._page()
        prof = models.PROFILES["xdj-xz"].as_dict()
        page._update_xz({"model": {"profile": prof},
                         "xz_mixer": {"bpm": 139.9, "tempo_master": False},
                         "xz_warnings": ["no_status"]})
        self.assertFalse(page._xz_mixer_val.isHidden())
        txt = page._xz_mixer_val.text()
        self.assertIn("139.90 BPM", txt)
        self.assertIn("not on this model (not missing)", txt)
        self.assertNotIn("missing:", txt.lower())
        self.assertFalse(page._xz_warn_val.isHidden())

    def test_az_rows_hidden(self):
        page = self._page()
        page._update_xz({"model": {"profile": models.PROFILES["xdj-az"].as_dict()},
                         "az_mode": "pro_dj_link"})
        self.assertTrue(page._xz_mixer_val.isHidden())
        self.assertTrue(page._xz_warn_val.isHidden())


if __name__ == "__main__":
    unittest.main()
