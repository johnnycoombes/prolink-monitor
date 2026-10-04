"""Track cache keying and deck binding helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from prolink.track_key import keys_match, track_cache_key, track_key_token


class TrackCacheKeyTests(unittest.TestCase):
    def test_same_medium_same_id_same_key(self):
        fp = (12345, 99)
        a = track_cache_key("192.168.1.10", "/export", fp, "usb", 42)
        b = track_cache_key("192.168.1.10", "/export", fp, "usb", 42)
        self.assertEqual(a, b)
        self.assertEqual(track_key_token(a), track_key_token(b))

    def test_slot_or_fingerprint_change_differs(self):
        fp = (12345, 99)
        base = track_cache_key("192.168.1.10", "/export", fp, "usb", 42)
        other_slot = track_cache_key("192.168.1.10", "/export", fp, "sd", 42)
        other_fp = track_cache_key("192.168.1.10", "/export", (999, 1), "usb", 42)
        self.assertNotEqual(base, other_slot)
        self.assertNotEqual(base, other_fp)
        self.assertNotEqual(track_key_token(base), track_key_token(other_slot))

    def test_keys_match_requires_track_id(self):
        key = track_cache_key("h", "e", (1, 2), "s", 7)
        self.assertTrue(keys_match(key, 7))
        self.assertFalse(keys_match(key, 8))
        self.assertFalse(keys_match(None, 7))


class MonitorBindingTests(unittest.TestCase):
    def test_resolve_key_prefers_deck_binding(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.host = "192.168.1.10"
        mon.library = MagicMock()
        fp = (100, 1)
        bound = track_cache_key("192.168.1.10", "/a", fp, "usb", 5)
        stale = track_cache_key("192.168.1.10", "/b", fp, "usb", 5)
        mon._deck_keys = {1: bound, 2: stale}
        mon._lock = __import__("threading").RLock()

        self.assertEqual(mon._resolve_key(5, deck=1), bound)
        self.assertEqual(mon._resolve_key(5, deck=2), stale)

    def test_meta_not_returned_for_wrong_deck_binding(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.host = "192.168.1.10"
        mon.library = MagicMock()
        fp = (1, 1)
        key = track_cache_key("192.168.1.10", "/export", fp, "usb", 9)
        mon._deck_keys = {1: key}
        mon._meta = {key: {"id": 9, "track_key": track_key_token(key), "title": "A"}}
        mon._waveforms = {}
        mon._lock = __import__("threading").RLock()

        got = mon.meta(9, deck=1, load=False)
        self.assertEqual(got["title"], "A")
        mon._deck_keys[1] = track_cache_key("192.168.1.10", "/export", fp, "sd", 9)
        self.assertIsNone(mon.meta(9, deck=1, load=False))


if __name__ == "__main__":
    unittest.main()
