"""XDJ-XZ / all-in-one support, driven by synthetic packets only."""
import json
import socket
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from prolink import allinone, proto
from prolink import remotedb as wire
from prolink.link import NetInfo, ObserveSource, SocketSource


def keepalive(name, number, ip="169.254.10.20"):
    return proto.build_keepalive(name, number, b"\x00\x11\x22\x33\x44\x55", ip)


class ModelTests(unittest.TestCase):
    def test_all_in_one_names(self):
        self.assertEqual(allinone.all_in_one_model("XDJ-XZ"), "XDJ-XZ")
        self.assertEqual(allinone.all_in_one_model("XDJ-AZ"), "XDJ-AZ")
        self.assertIsNone(allinone.all_in_one_model("CDJ-3000"))
        self.assertTrue(allinone.is_xdj_xz("xdj-xz"))

    def test_all_in_one_rows_group_by_ip(self):
        import app
        devs = {
            1: SimpleNamespace(name="XDJ-XZ", ip="169.254.1.2", device_number=1),
            2: SimpleNamespace(name="XDJ-XZ", ip="169.254.1.2", device_number=2),
            33: SimpleNamespace(name="XDJ-XZ", ip="169.254.1.2", device_number=33),
            3: SimpleNamespace(name="CDJ-3000", ip="169.254.1.9", device_number=3),
        }
        rows = app.all_in_one_rows(devs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["devices"], [1, 2, 33])


class NumberTests(unittest.TestCase):
    def test_preferred_kept_when_free(self):
        self.assertEqual(allinone.choose_device_number(5, {1, 2, 33})[0], 5)

    def test_taken_number_replaced(self):
        n, note = allinone.choose_device_number(1, {1, 2, 33})
        self.assertNotIn(n, {1, 2, 33})
        self.assertIn("in use", note)

    def test_auto_skips_az_decks(self):
        self.assertEqual(allinone.choose_device_number(0, {1, 2, 3, 4, 5, 33})[0], 6)

    def test_reserved_never_chosen(self):
        self.assertNotEqual(allinone.choose_device_number(33, set())[0], 33)


class SourceTests(unittest.TestCase):
    def test_socket_source_skips_announced_numbers(self):
        src = SocketSource(NetInfo("169.254.10.99", 16, "en15"), device_number=1)
        for n in (1, 2, 33):
            src._note_announce(keepalive("XDJ-XZ", n), "169.254.10.20")
        src._pick_number()
        self.assertEqual(src.device_number, 5)

    def test_own_keepalive_not_counted(self):
        src = SocketSource(NetInfo("169.254.10.99", 16, "en15"), device_number=5)
        src._note_announce(keepalive("monitor", 5, "169.254.10.99"), "169.254.10.99")
        self.assertNotIn(5, src.taken)

    def test_observe_never_announces(self):
        src = ObserveSource(NetInfo("127.0.0.1", 8, "lo"))
        self.assertFalse(src.announce)
        self.assertEqual(src.kind, "observe")
        with mock.patch.object(SocketSource, "_announce_loop",
                               side_effect=AssertionError("announced")):
            with mock.patch("socket.socket") as fake:
                fake.return_value.recvfrom.side_effect = OSError
                src.start(lambda *a: None)
                src._running = False
        fake.return_value.sendto.assert_not_called()


class AdapterTests(unittest.TestCase):
    IFCONFIG = (
        "lo0: flags=8049<UP,LOOPBACK> mtu 16384\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "en0: flags=8863<UP> mtu 1500\n"
        "\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255\n"
        "en15: flags=8863<UP> mtu 1500\n"
        "\tinet 169.254.33.7 netmask 0xffff0000 broadcast 169.254.255.255\n"
    )

    def test_ifconfig(self):
        found = allinone.parse_ifconfig_adapters(self.IFCONFIG)
        self.assertEqual([a.name for a in found], ["en0", "en15"])
        best = allinone.usb_link_candidates(sorted(found, key=lambda a: -a.score))
        self.assertEqual(best[0].name, "en15")
        self.assertEqual(best[0].prefix, 16)

    def test_windows_json(self):
        rows = [
            {"IPAddress": "192.168.1.5", "PrefixLength": 24, "InterfaceAlias": "Wi-Fi",
             "InterfaceDescription": "Intel Wireless"},
            {"IPAddress": "169.254.4.4", "PrefixLength": 16, "InterfaceAlias": "Ethernet 3",
             "InterfaceDescription": "Remote NDIS Compatible Device"},
        ]
        found = allinone.parse_windows_adapters(json.dumps(rows))
        self.assertEqual(max(found, key=lambda a: a.score).name, "Ethernet 3")
        self.assertEqual(allinone.parse_windows_adapters(json.dumps(rows[1]))[0].prefix, 16)
        self.assertEqual(allinone.parse_windows_adapters("not json"), [])


class ExportTests(unittest.TestCase):
    def test_slot_mapping(self):
        ex = ["/B/", "/C/"]
        self.assertEqual(allinone.export_for_slot(ex, "sd"), "/B/")
        self.assertEqual(allinone.export_for_slot(ex, "usb"), "/C/")
        self.assertIsNone(allinone.export_for_slot(ex, "usb2"))
        self.assertEqual(allinone.export_for_slot(["/C/"], "sd"), "/C/")
        self.assertEqual(allinone.export_for_slot(["/USB1/", "/USB2/"], "usb2"), "/USB2/")
        self.assertEqual(allinone.export_for_slot(["/USB1/", "/USB2/"], "usb"), "/USB1/")

    def test_library_single_export_unchanged(self):
        from prolink.library import Library
        lib = Library()
        base = SimpleNamespace(export="/C/", nfs=SimpleNamespace(exports=lambda: ["/C/"]),
                               is_stale=lambda: False)
        lib.media["10.0.0.2"] = base
        self.assertIs(lib.get("10.0.0.2", slot="sd"), base)
        self.assertEqual(lib.slot_media, {})

    def test_library_second_export_gets_own_media(self):
        from prolink import library
        lib = library.Library()
        base = SimpleNamespace(export="/C/", nfs=SimpleNamespace(exports=lambda: ["/B/", "/C/"]),
                               is_stale=lambda: False, close=lambda: None)
        lib.media["10.0.0.2"] = base
        made = []

        class FakeMedia:
            def __init__(self, host, export=None, cache_dir=None):
                made.append(export)
                self.export = export

            def load_database(self):
                pass

            def is_stale(self):
                return False

            def close(self):
                pass

        with mock.patch.object(library, "Media", FakeMedia):
            self.assertIs(lib.get("10.0.0.2", slot="usb"), base)
            sd = lib.get("10.0.0.2", slot="sd")
            self.assertEqual(sd.export, "/B/")
            self.assertIs(lib.get("10.0.0.2", slot="sd"), sd)
        self.assertEqual(made, ["/B/"])
        lib.drop("10.0.0.2")
        self.assertEqual(lib.slot_media, {})


class CensusTests(unittest.TestCase):
    def test_counts_and_health(self):
        c = allinone.Census(started=0.0)
        data = keepalive("XDJ-XZ", 1)
        c.observe(data, "169.254.10.20", proto.parse(data), now=1.0)
        d = c.as_dict()
        self.assertEqual(d["packets"][0]["type"], "0x06")
        self.assertEqual(d["packets"][0]["name"], "XDJ-XZ")
        self.assertEqual(allinone.observe_health("observe", c, now=10.0)["state"], "no_status")
        self.assertEqual(allinone.observe_health("observe", allinone.Census(started=0.0),
                                                 now=10.0)["state"], "silent")
        self.assertEqual(allinone.observe_health("observe", c, now=2.0)["state"], "waiting")
        self.assertEqual(allinone.observe_health("vcdj", c), {})


class PortCacheTests(unittest.TestCase):
    def test_discovery_once_per_ip(self):
        wire._PORT_CACHE.clear()
        calls = []
        with mock.patch.object(wire, "_discover_db_port",
                               lambda host, timeout=3.0: calls.append(host) or 1051):
            self.assertEqual(wire.discover_db_port("169.254.1.2"), 1051)
            self.assertEqual(wire.discover_db_port("169.254.1.2"), 1051)
            wire.forget_db_port("169.254.1.2")
            wire.discover_db_port("169.254.1.2")
        self.assertEqual(calls, ["169.254.1.2", "169.254.1.2"])
        wire._PORT_CACHE.clear()

    def test_requesting_player_avoids_xz_numbers(self):
        from prolink.remotedb_client import choose_requesting_player
        n = choose_requesting_player(None, target_player=1, taken={1, 2, 33})
        self.assertNotIn(n, {1, 2, 33})


if __name__ == "__main__":
    unittest.main()


class ProfileTests(unittest.TestCase):
    def test_names_map_to_profiles(self):
        from prolink import models
        cases = {"CDJ-3000X": "CDJ-3000X", "CDJ-3000": "CDJ-3000", "CDJ-2000nexus": "CDJ-2000NXS",
                 "CDJ-2000NXS2": "CDJ-2000NXS2", "XDJ-1000MK2": "XDJ-1000MK2",
                 "XDJ-XZ": "XDJ-XZ", "OPUS-QUAD": "OPUS-QUAD", "DJM-V10": "DJM mixer",
                 "rekordbox": "rekordbox"}
        for name, want in cases.items():
            self.assertEqual(models.profile_for_name(name).name, want, name)
        self.assertIsNone(models.profile_for_name("Mystery-9"))

    def test_auto_prefers_player_over_mixer(self):
        from prolink import models
        res = models.resolve("auto", ["DJM-900NXS2", "XDJ-XZ"])
        self.assertEqual(res["profile"]["name"], "XDJ-XZ")
        self.assertEqual(res["source"], "detected")

    def test_setting_wins_and_unknown_degrades(self):
        from prolink import models
        self.assertEqual(models.resolve("cdj-3000", ["XDJ-XZ"])["profile"]["name"], "CDJ-3000")
        res = models.resolve("auto", ["Mystery-9"])
        self.assertEqual(res["source"], "unknown")
        self.assertTrue(res["profile"]["dbserver"])        # nothing switched off
        self.assertEqual(models.resolve("bogus", [])["setting"], "auto")
        self.assertIn("deck", models.capability_line(res))

    def test_three_band_only_where_stored(self):
        from prolink import models
        three = {p.name for p in models.PROFILES.values() if "3band" in p.waveforms}
        self.assertIn("CDJ-3000", three)
        self.assertIn("XDJ-AZ", three)
        self.assertNotIn("XDJ-XZ", three)

    def test_opus_skips_dbserver(self):
        import app
        mon = app.Monitor.__new__(app.Monitor)
        mon.model_setting = "auto"
        mon.engine = SimpleNamespace(lock=__import__("threading").RLock(), devices={
            1: SimpleNamespace(name="OPUS-QUAD", ip="10.0.0.9", device_number=1)},
            source=SimpleNamespace(kind="vcdj"))
        self.assertIsNone(mon._open_remotedb("10.0.0.9"))
        mon.model_setting = "xdj-az"
        self.assertTrue(mon._profile_for_host("10.0.0.9")["dbserver"])

    def test_settings_sanitize_model(self):
        from gui import settings
        self.assertEqual(settings._sanitize({"device_model": "XDJ-9"})["device_model"], "auto")
        self.assertEqual(settings._sanitize({"device_model": "xdj-xz"})["device_model"], "xdj-xz")
        self.assertEqual(settings._sanitize({"number": 0})["number"], 0)
        self.assertEqual(settings._sanitize({"mode": "observe"})["mode"], "observe")


class StcIdeaTests(unittest.TestCase):
    """Ideas from SuperTimecodeConverter (MIT), re-implemented here."""

    def test_db_player_never_at_target_ip(self):
        from prolink.remotedb_client import choose_requesting_player
        n = choose_requesting_player(5, target_player=1, taken={1, 2, 33}, same_ip={1, 2, 33})
        self.assertEqual(n, 4)
        n = choose_requesting_player(5, target_player=1, taken={1, 2, 33, 4},
                                     same_ip={1, 2, 33})
        self.assertEqual(n, 3)

    def test_az_keeps_announced_number(self):
        from prolink.remotedb_client import choose_requesting_player
        self.assertEqual(choose_requesting_player(
            5, target_player=1, taken={1, 2, 3, 4, 33}, same_ip={1, 2, 3, 4, 33}), 5)

    def test_monitor_passes_same_ip(self):
        import threading
        import app
        mon = app.Monitor.__new__(app.Monitor)
        mon.local_ip = "169.254.0.9"
        devs = {n: SimpleNamespace(name="XDJ-XZ", ip="169.254.0.2", device_number=n)
                for n in (1, 2, 33)}
        mon.engine = SimpleNamespace(lock=threading.RLock(), devices=devs,
                                     source=SimpleNamespace(device_number=5))
        self.assertEqual(mon._requesting_player_for_db(1), 4)

    def test_claim_stages_are_not_players(self):
        base = bytearray(keepalive("CDJ-3000", 3))
        for stage, off in ((0x00, None), (0x02, 0x2E), (0x04, 0x24)):
            pkt = bytearray(base)
            pkt[10] = stage
            if off:
                pkt[off] = 3
            self.assertIsNone(proto.parse(bytes(pkt)))
            self.assertEqual(proto.claim_stage(bytes(pkt))[0], stage)
        src = SocketSource(NetInfo("169.254.10.99", 16, "en15"), device_number=3)
        two = bytearray(base); two[10] = 0x02; two[0x2E] = 3
        src._note_announce(bytes(two), "169.254.10.20")
        self.assertNotIn(3, src.taken)
        four = bytearray(base); four[10] = 0x04
        src._note_announce(bytes(four), "169.254.10.20")
        self.assertIn(3, src.taken)

    def test_mac_binds_wildcard(self):
        from prolink.link import bind_address
        self.assertEqual(bind_address("169.254.3.3", "darwin"), "0.0.0.0")
        self.assertEqual(bind_address("192.168.1.2", "win32"), "0.0.0.0")

    def test_keepalive_targets_deduplicated(self):
        from prolink.link import keepalive_targets
        t = keepalive_targets("169.254.255.255", ["169.254.0.2", "169.254.0.2", "169.254.0.2"])
        self.assertEqual(t, ["169.254.255.255", "255.255.255.255", "169.254.0.2"])
