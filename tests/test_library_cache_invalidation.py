"""NFS library cache invalidation when export fingerprint changes."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from prolink import library


class MediaStaleTests(unittest.TestCase):
    def _make_media(self):
        with mock.patch.object(library, "NfsClient") as nfs_cls:
            nfs = mock.Mock()
            nfs.exports.return_value = ["/export/USB"]
            nfs.mount.return_value = mock.Mock()
            nfs_cls.return_value = nfs
            m = library.Media("192.0.2.1", cache_dir=tempfile.mkdtemp())
            m._exports_fingerprint = ("/export/USB",)
            m._pdb_fingerprint = (1000, 2000)
            m.db = mock.Mock()
            return m, nfs

    def test_not_stale_when_fingerprint_matches(self):
        media, nfs = self._make_media()
        nfs.exports.return_value = ["/export/USB"]
        with mock.patch.object(media, "_remote_pdb_fingerprint", return_value=(1000, 2000)):
            self.assertFalse(media.is_stale())

    def test_stale_when_pdb_fingerprint_changes(self):
        media, nfs = self._make_media()
        nfs.exports.return_value = ["/export/USB"]
        with mock.patch.object(media, "_remote_pdb_fingerprint", return_value=(1001, 2000)):
            self.assertTrue(media.is_stale())

    def test_stale_when_export_path_changes(self):
        media, nfs = self._make_media()
        nfs.exports.return_value = ["/export/OTHER"]
        with mock.patch.object(media, "_remote_pdb_fingerprint", return_value=(1000, 2000)):
            self.assertTrue(media.is_stale())

    def test_library_get_drops_stale_media(self):
        lib = library.Library(cache_dir=tempfile.mkdtemp())
        media, _nfs = self._make_media()
        lib.media["192.0.2.1"] = media
        with mock.patch.object(media, "is_stale", return_value=True):
            with mock.patch.object(lib, "drop") as drop:
                with mock.patch.object(library, "Media") as media_cls:
                    media_cls.side_effect = RuntimeError("stop")
                    try:
                        lib.get("192.0.2.1")
                    except RuntimeError:
                        pass
                    drop.assert_called_once_with("192.0.2.1")


if __name__ == "__main__":
    unittest.main()
