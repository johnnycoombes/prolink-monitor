"""Album art request wire encoding."""

from __future__ import annotations

import unittest

from prolink import remotedb as wire


class AlbumArtEncodeTests(unittest.TestCase):
    def test_album_art_request_high_res(self):
        pkt = wire.encode_album_art_request(7, 3, wire.SLOT_USB, 12345, high_res=True)
        msg = wire.decode_message(pkt)
        self.assertEqual(msg.tx_id, 7)
        self.assertEqual(msg.msg_type, wire.TYPE_ALBUM_ART)
        self.assertEqual(len(msg.args), 3)
        rmst = wire.build_rmst(3, wire.MENU_DATA, wire.SLOT_USB)
        self.assertEqual(msg.args[0], rmst)
        self.assertEqual(msg.args[1], 12345)
        self.assertEqual(msg.args[2], 1)


if __name__ == "__main__":
    unittest.main()
