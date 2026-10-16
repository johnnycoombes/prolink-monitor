"""1.0.20: XDJ-XZ-only fixes. The XDJ-AZ and every other model must be unchanged."""
import struct
import types
import unittest

from prolink import proto
from prolink.link import ProLink
from tests.test_xz_1019 import Src, xz_mixer_fixture


def status(name: bytes, number: int, playing=False, track_id=0, bpm=0) -> bytes:
    b = bytearray(0xD4)
    b[0:10] = proto.MAGIC
    b[10] = proto.TYPE_CDJ_STATUS
    b[0x0B:0x0B + len(name)] = name
    b[0x21] = number
    struct.pack_into(">I", b, 0x2C, track_id)
    struct.pack_into(">H", b, 0x88, 0x40 if playing else 0)
    struct.pack_into(">H", b, 0x92, bpm)
    b[0x8B] = 0x9A
    b[0xCC] = 0x1F
    return bytes(b)


def mixer(handoff=0xFF, flags=0xD0):
    b = bytearray(xz_mixer_fixture())
    b[0x36] = handoff
    b[0x27] = flags
    return bytes(b)


class Handoff(unittest.TestCase):
    def test_00_and_ff_mean_none(self):
        self.assertIsNone(proto.parse_xz_mixer_status(mixer(0x00)).handoff_to)
        self.assertIsNone(proto.parse_xz_mixer_status(mixer(0xFF)).handoff_to)
        self.assertEqual(proto.parse_xz_mixer_status(mixer(2)).handoff_to, 2)


class Flags(unittest.TestCase):
    def test_only_tempo_master_exposed(self):
        for flags in (0xF0, 0xD0):
            d = proto.parse_xz_mixer_status(mixer(flags=flags)).as_dict()
            self.assertNotIn("synced", d)
            self.assertNotIn("playing", d)
        self.assertTrue(proto.parse_xz_mixer_status(mixer(flags=0xF0)).tempo_master)
        self.assertFalse(proto.parse_xz_mixer_status(mixer(flags=0xD0)).tempo_master)


class MixerBpm(unittest.TestCase):
    def test_na_until_analysed_track_plays(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"XDJ-XZ", 1, playing=True, track_id=5, bpm=0), "10.0.0.5", 99.0)
        eng._on_packet(mixer(), "10.0.0.5", 100.0)
        st = eng.xz_mixer_state(now=100.5)
        self.assertIsNone(st["bpm"])
        self.assertFalse(st["bpm_valid"])
        eng._on_packet(status(b"XDJ-XZ", 1, playing=True, track_id=5, bpm=12800), "10.0.0.5", 100.6)
        st = eng.xz_mixer_state(now=100.7)
        self.assertEqual(st["bpm"], 139.9)
        self.assertTrue(st["bpm_valid"])

    def test_az_status_records_nothing(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"XDJ-AZ", 1, playing=True, track_id=5, bpm=12800), "10.0.0.6", 1.0)
        self.assertEqual(eng._xz_status_ip, {})
        self.assertEqual(eng._xz_deck_analysed_playing, {})
        self.assertEqual(eng.xz_fingerprints(), {})
        self.assertIsNone(eng.xz_status_ip(1))


class Fingerprints(unittest.TestCase):
    def test_recorded_for_xz(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"XDJ-XZ", 2), "10.0.0.5", 1.0)
        self.assertEqual(eng.xz_fingerprints()["2"]["0x8b"], "9a")
        self.assertEqual(eng.xz_fingerprints()["2"]["0xcc"], "1f")


class HostFor(unittest.TestCase):
    def _call(self, eng, st):
        import app
        fake = types.SimpleNamespace(engine=eng, host="192.168.1.1")
        return app.Monitor._host_for(fake, st)

    def test_xz_falls_back_to_status_ip(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"XDJ-XZ", 2), "10.0.0.5", 1.0)
        st = proto.parse(status(b"XDJ-XZ", 2))
        self.assertEqual(self._call(eng, st), "10.0.0.5")

    def test_az_unchanged(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"XDJ-AZ", 2), "10.0.0.6", 1.0)
        st = proto.parse(status(b"XDJ-AZ", 2))
        self.assertEqual(self._call(eng, st), "192.168.1.1")

    def test_cdj_unchanged(self):
        eng = ProLink(Src())
        eng._on_packet(status(b"CDJ-3000", 2), "10.0.0.7", 1.0)
        st = proto.parse(status(b"CDJ-3000", 2))
        self.assertEqual(self._call(eng, st), "192.168.1.1")


if __name__ == "__main__":
    unittest.main()
