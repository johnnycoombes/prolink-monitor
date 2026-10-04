"""Track-source bytes, per-deck dbserver slot, and XDJ-AZ link mode."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from prolink import link, onelibrary, proto, remotedb as wire
from prolink.library import Media
from prolink.pdb import Track


def _status(slot: int, track_type: int = 1, name: bytes = b"XDJ-AZ") -> bytes:
    pkt = bytearray(0xA8)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_CDJ_STATUS
    pkt[0x0B:0x0B + len(name)] = name[:20]
    pkt[0x21] = 1
    pkt[0x29] = slot
    pkt[0x2A] = track_type
    return bytes(pkt)


def _on_air(name: bytes = b"XDJ-AZ") -> bytes:
    pkt = bytearray(0x2D)
    pkt[0:10] = proto.MAGIC
    pkt[10] = proto.TYPE_CHANNELS_ON_AIR
    pkt[0x0B:0x0B + len(name)] = name[:20]
    pkt[0x21] = 33
    return bytes(pkt)


class SlotMapTests(unittest.TestCase):
    def test_known_source_bytes(self):
        expected = {
            0x00: "empty",
            0x01: "cd",
            0x02: "sd",
            0x03: "usb",
            0x04: "rekordbox",
            0x05: "streaming",
            0x06: "direct_play",
            0x07: "usb2",
            0x08: "streaming",
            0x09: "beatport",
        }
        for raw, name in expected.items():
            parsed = proto.parse(_status(raw))
            self.assertIsInstance(parsed, proto.Status)
            assert isinstance(parsed, proto.Status)
            self.assertEqual(parsed.slot, name, hex(raw))
            self.assertEqual(parsed.slot_raw, raw)

    def test_unconfirmed_byte_stays_addressable(self):
        parsed = proto.parse(_status(0x0A))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.slot, "unknown")
        self.assertEqual(wire.slot_byte(parsed.slot, parsed.slot_raw), 0x0A)

    def test_streaming_track_type(self):
        parsed = proto.parse(_status(0x09, track_type=0x06, name=b"CDJ-3000"))
        assert isinstance(parsed, proto.Status)
        self.assertEqual(parsed.track_type, "streaming")
        self.assertEqual(parsed.track_type_raw, 0x06)
        self.assertEqual(parsed.slot, "beatport")

    def test_streaming_and_usb2_are_different_slots(self):
        self.assertEqual(wire.slot_byte("usb", 0), wire.SLOT_USB)
        self.assertEqual(wire.slot_byte("usb2", 0), wire.SLOT_USB2)
        self.assertEqual(wire.slot_byte("direct_play", 0), wire.SLOT_DIRECT_PLAY)
        self.assertEqual(wire.slot_byte("beatport", 0), wire.SLOT_BEATPORT)
        self.assertEqual(wire.slot_byte("streaming", 5), 5)
        self.assertEqual(wire.slot_byte("streaming", 8), 8)
        self.assertNotEqual(wire.slot_byte("usb", 0), wire.slot_byte("usb2", 0))

    def test_service_labels_do_not_invent_apple_music(self):
        self.assertEqual(proto.stream_service_label("beatport", "streaming"), "Beatport")
        self.assertEqual(
            proto.stream_service_label("direct_play", "streaming"),
            "Streaming Direct Play",
        )
        self.assertEqual(proto.stream_service_label("streaming", "streaming"), "Streaming")
        self.assertEqual(proto.stream_service_label("usb2", "rekordbox"), "")
        labelled = proto.apply_source_label(
            {"title": "", "artist": "", "label": ""}, "beatport", "streaming")
        self.assertEqual(labelled["title"], "Beatport")
        with_title = proto.apply_source_label(
            {"title": "Rave", "artist": "", "label": ""}, "beatport", "streaming")
        self.assertEqual(with_title["title"], "Rave")
        self.assertEqual(with_title["artist"], "Beatport")


class MetadataQueryTests(unittest.TestCase):
    def test_usb2_rekordbox_query_uses_slot_7(self):
        pkt = wire.encode_metadata_request(1, 5, wire.SLOT_USB2, wire.TRACK_REKORDBOX, 99)
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.msg_type, wire.TYPE_REKORDBOX_METADATA)
        self.assertEqual(msg.args[0], wire.build_rmst(5, wire.MENU_MAIN, wire.SLOT_USB2, 1))
        self.assertEqual(msg.args[1], 99)

    def test_beatport_uses_standard_metadata_query(self):
        pkt = wire.encode_metadata_request(
            2, 5, wire.SLOT_BEATPORT, wire.TRACK_STREAMING, 12)
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.msg_type, wire.TYPE_REKORDBOX_METADATA)
        self.assertNotEqual(msg.msg_type, wire.TYPE_GENERIC_METADATA)
        self.assertEqual(msg.args[0] & 0xFFFF, (wire.SLOT_BEATPORT << 8) | wire.TRACK_STREAMING)

    def test_unanalyzed_uses_generic_query(self):
        pkt = wire.encode_metadata_request(3, 5, wire.SLOT_USB, wire.TRACK_UNANALYZED, 4)
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.msg_type, wire.TYPE_GENERIC_METADATA)

    def test_metadata_items_mask_high_bytes(self):
        items = [
            wire.DbMessage(1, wire.TYPE_MENU_ITEM,
                           [7, 99, 0, "Night", 0, "", 0x00010004, 0, 4321, 0, 0, 0]),
            wire.DbMessage(1, wire.TYPE_MENU_ITEM,
                           [0, 7, 0, "DJ", 0, "", 0x07, 0, 0, 0, 0, 0]),
            wire.DbMessage(1, wire.TYPE_MENU_ITEM,
                           [0, 12800, 0, "", 0, "", 0x0D, 0, 0, 0, 0, 0]),
        ]
        meta = wire.metadata_from_items(items)
        self.assertEqual(meta["title"], "Night")
        self.assertEqual(meta["artist"], "DJ")
        self.assertEqual(meta["artwork_id"], 4321)
        self.assertEqual(meta["tempo"], 128.0)


class PerDeckSlotTests(unittest.TestCase):
    def _monitor(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._lock = threading.RLock()
        mon._deck_keys = {}
        mon._meta = {}
        mon._waveforms = {}
        mon.library = MagicMock()
        mon.library.get.return_value = None
        return mon

    def test_each_deck_keeps_its_own_slot(self):
        from app import Monitor

        mon = self._monitor()
        usb = proto.Status(slot="usb", slot_raw=3)
        usb2 = proto.Status(slot="usb2", slot_raw=7)
        self.assertEqual(mon._db_slot_for_status(usb), wire.SLOT_USB)
        self.assertEqual(mon._db_slot_for_status(usb2), wire.SLOT_USB2)

        d1 = MagicMock()
        d1.status = usb
        d2 = MagicMock()
        d2.status = usb2
        mon.engine = MagicMock()
        mon.engine.active_decks.return_value = [d1, d2]
        self.assertEqual(mon._library_db_slots(), [wire.SLOT_USB, wire.SLOT_USB2])
        # The helper used to return whichever deck it saw first.
        self.assertNotEqual(mon._library_db_slots(), [wire.SLOT_USB])

    def test_track_change_queries_that_decks_slot(self):
        mon = self._monitor()
        seen = []

        def fetch(host, track_id, slot_name, slot_raw, type_name, type_raw):
            seen.append((host, track_id, slot_name, slot_raw, type_name, type_raw))
            return {"title": "From USB 2", "artist": "AZ"}

        mon._fetch_live_metadata = fetch
        mon._host_for = lambda status: "10.0.0.8"
        deck = MagicMock()
        deck.number = 4
        deck.status = proto.Status(
            slot="usb2", slot_raw=7, track_type="rekordbox", track_type_raw=1,
            track_id=42, name="XDJ-AZ",
        )
        mon._on_track_change(deck, 42)
        self.assertEqual(seen, [("10.0.0.8", 42, "usb2", 7, "rekordbox", 1)])
        meta = next(iter(mon._meta.values()))
        self.assertEqual(meta["title"], "From USB 2")
        self.assertEqual(meta["artist"], "AZ")
        self.assertEqual(meta["library_source"], "remotedb")

    def test_stale_load_does_not_rebind_the_deck(self):
        mon = self._monitor()
        mon._fetch_live_metadata = lambda *args, **kwargs: {"title": "Old", "artist": "X"}
        mon._host_for = lambda status: "10.0.0.8"
        deck = MagicMock()
        deck.number = 1
        deck.status = proto.Status(
            slot="usb2", slot_raw=7, track_id=2,
            track_type="rekordbox", track_type_raw=1, name="XDJ-AZ",
        )
        mon._on_track_change(deck, 1)
        self.assertEqual(mon._deck_keys, {})
        self.assertEqual(next(iter(mon._meta.values()))["title"], "Old")

    def test_stream_without_metadata_is_labelled(self):
        mon = self._monitor()
        mon._fetch_live_metadata = lambda *args, **kwargs: None
        mon._host_for = lambda status: "10.0.0.8"
        deck = MagicMock()
        deck.number = 1
        deck.status = proto.Status(
            slot="direct_play", slot_raw=6, track_type="streaming", track_type_raw=6,
            name="CDJ-3000",
        )
        mon._on_track_change(deck, 8)
        meta = next(iter(mon._meta.values()))
        self.assertEqual(meta["title"], "Streaming Direct Play")
        # Not a file slot, so the USB library is not consulted for it.
        mon.library.get.assert_not_called()

    def test_unconfirmed_streaming_byte_is_not_named_as_a_shop(self):
        mon = self._monitor()
        mon._fetch_live_metadata = lambda *args, **kwargs: None
        mon._host_for = lambda status: "10.0.0.8"
        deck = MagicMock()
        deck.number = 2
        deck.status = proto.Status(slot="streaming", slot_raw=5, track_type="streaming",
                                   track_type_raw=6, name="CDJ-3000")
        mon._on_track_change(deck, 3)
        meta = next(iter(mon._meta.values()))
        self.assertEqual(meta["title"], "Streaming")
        self.assertNotIn("Apple", meta["title"])
        self.assertNotIn("TIDAL", meta["title"])


class CopiedDbFallbackTests(unittest.TestCase):
    def _media(self, pdb_track, ol_track, *, present=True, readable=True):
        media = Media.__new__(Media)
        media.track = lambda track_id: pdb_track
        media.onelibrary_track = lambda track_id: ol_track
        media.onelibrary = onelibrary.OneLibrarySummary(present=present, readable=readable)
        return media

    def test_az_prefers_onelibrary_id(self):
        media = self._media(
            Track(id=5, title="Wrong PDB"),
            onelibrary.OneLibraryTrack(id=5, title="Right OL", artist="AZ"),
        )
        track, source = media.copied_track(5, prefer_onelibrary=True)
        self.assertEqual(source, "onelibrary")
        self.assertEqual(track.title, "Right OL")
        self.assertEqual(track.artist, "AZ")

    def test_az_does_not_use_pdb_when_onelibrary_is_readable(self):
        media = self._media(
            Track(id=5, title="Wrong PDB"),
            None,
            present=True,
            readable=True,
        )
        track, source = media.copied_track(5, prefer_onelibrary=True)
        self.assertIsNone(track)
        self.assertEqual(source, "")

    def test_pdb_then_onelibrary_when_not_az(self):
        media = self._media(None, onelibrary.OneLibraryTrack(id=9, title="Only OL"))
        track, source = media.copied_track(9, prefer_onelibrary=False)
        self.assertEqual(source, "onelibrary")
        self.assertEqual(track.title, "Only OL")

    def test_pdb_wins_for_other_players_when_it_has_a_title(self):
        media = self._media(
            Track(id=9, title="PDB"),
            onelibrary.OneLibraryTrack(id=9, title="OL"),
        )
        track, source = media.copied_track(9, prefer_onelibrary=False)
        self.assertEqual(source, "nfs-cache")
        self.assertEqual(track.title, "PDB")


class AzModeTests(unittest.TestCase):
    def test_channels_on_air_packet(self):
        parsed = proto.parse(_on_air())
        self.assertIsInstance(parsed, proto.ChannelsOnAir)
        assert isinstance(parsed, proto.ChannelsOnAir)
        self.assertTrue(proto.is_xdj_az(parsed.name))
        self.assertEqual(parsed.device_number, 33)

    def test_recent_on_air_is_pro_dj_link(self):
        engine = link.ProLink(source=MagicMock())
        self.assertIsNone(engine.az_mode(now=0))
        engine._note_player_name("XDJ-AZ")
        self.assertEqual(engine.az_mode(now=5), "four_deck")
        engine._on_packet(_on_air(), "10.0.0.8", 10.0)
        self.assertEqual(engine.az_mode(now=10.9), "pro_dj_link")
        self.assertEqual(engine.az_mode(now=11.0), "four_deck")

    def test_other_mixer_on_air_does_not_count(self):
        engine = link.ProLink(source=MagicMock())
        engine._on_packet(_on_air(b"DJM-V10"), "10.0.0.9", 10.0)
        self.assertIsNone(engine.az_mode(now=10.1))

    def test_health_label(self):
        from gui.i18n import I18n
        from gui.pages import az_mode_label

        i18n = I18n()
        self.assertEqual(az_mode_label(i18n, "pro_dj_link"), "Pro DJ Link")
        self.assertEqual(az_mode_label(i18n, "four_deck"), "4-deck")
        self.assertEqual(az_mode_label(i18n, None), "—")


if __name__ == "__main__":
    unittest.main()
