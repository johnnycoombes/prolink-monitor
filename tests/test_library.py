"""Library cache-key and retry helpers (no live NFS required)."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from prolink import library


class CacheNameTests(unittest.TestCase):
    def test_same_path_same_name(self):
        a = library._safe_cache_name("anlz", "PIONEER/USBANLZ/P016/0000875E/ANLZ0000.DAT", ".dat")
        b = library._safe_cache_name("anlz", "PIONEER/USBANLZ/P016/0000875E/ANLZ0000.DAT", ".dat")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("anlz_"))
        self.assertTrue(a.endswith(".dat"))

    def test_different_paths_differ(self):
        a = library._safe_cache_name("anlz", "PIONEER/USBANLZ/P016/0000875E/ANLZ0000.DAT", ".dat")
        b = library._safe_cache_name("anlz", "PIONEER/USBANLZ/P053/0001D21F/ANLZ0000.DAT", ".dat")
        self.assertNotEqual(a, b)


class LibraryRetryTests(unittest.TestCase):
    def test_failed_get_retries_after_ttl(self):
        lib = library.Library(cache_dir=tempfile.mkdtemp())
        with mock.patch.object(library, "Media", side_effect=RuntimeError("no usb")):
            self.assertIsNone(lib.get("192.0.2.1"))
            self.assertIn("192.0.2.1", lib.errors)
            # Within the sticky window, get short-circuits.
            self.assertIsNone(lib.get("192.0.2.1"))

        lib._error_at["192.0.2.1"] = time.time() - library.ERROR_RETRY_SECONDS - 1
        with mock.patch.object(library, "Media") as media_cls:
            media = mock.Mock()
            media_cls.return_value = media
            got = lib.get("192.0.2.1")
            self.assertIs(got, media)
            media.load_database.assert_called_once()
            self.assertNotIn("192.0.2.1", lib.errors)

    def test_explicit_retry_clears_error(self):
        lib = library.Library()
        lib.errors["192.0.2.1"] = "boom"
        lib._error_at["192.0.2.1"] = time.time()
        lib.retry("192.0.2.1")
        self.assertNotIn("192.0.2.1", lib.errors)


if __name__ == "__main__":
    unittest.main()
