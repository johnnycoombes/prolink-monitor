"""RemoteDB wire encode/decode (no live player required)."""

from __future__ import annotations

import struct
import unittest

from prolink import remotedb as wire


class RemoteDbWireTests(unittest.TestCase):
    def test_build_rmst_packs_bytes(self):
        val = wire.build_rmst(3, wire.MENU_MAIN, wire.SLOT_USB, wire.TRACK_REKORDBOX)
        self.assertEqual(val, (3 << 24) | (1 << 16) | (3 << 8) | 1)

    def test_encode_decode_setup_roundtrip(self):
        raw = wire.encode_setup(3)
        msg = wire.decode_message(raw)
        self.assertEqual(msg.tx_id, wire.SETUP_TX)
        self.assertEqual(msg.msg_type, wire.TYPE_SETUP)
        self.assertEqual(msg.args, [3])

    def test_encode_menu_request_has_magic(self):
        tx = 1
        pkt = wire.encode_menu_request(
            tx, wire.TYPE_TRACK_MENU, 3, wire.SLOT_USB, [0])
        self.assertIn(struct.pack(">I", wire.MAGIC), pkt)
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.tx_id, tx)
        self.assertEqual(msg.msg_type, wire.TYPE_TRACK_MENU)
        self.assertEqual(len(msg.args), 2)
        self.assertEqual(msg.args[0], wire.build_rmst(3, wire.MENU_MAIN, wire.SLOT_USB))
        self.assertEqual(msg.args[1], 0)

    def test_menu_item_row(self):
        msg = wire.DbMessage(
            5,
            wire.TYPE_MENU_ITEM,
            [0, 42, 0, "Title", 0, "Artist", wire.ITEM_PLAYLIST, 0, 0, 0, 0, 0],
        )
        row = wire.menu_item_row(msg)
        self.assertEqual(row["id"], 42)
        self.assertEqual(row["name"], "Title")
        self.assertTrue(row["playlist"])


if __name__ == "__main__":
    unittest.main()
