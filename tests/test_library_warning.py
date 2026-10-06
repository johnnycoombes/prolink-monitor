"""pyrekordbox's db6 rename must not show up as a Library error."""

from __future__ import annotations

import threading
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prolink import onelibrary

_WARNING = (
    r"C:\Users\User\AppData\Local\Programs\Python\Python314\Lib\importlib\__init__.py:88: "
    "DeprecationWarning: 'pyrekordbox.db6' is deprecated and will be removed in "
    "version '0.6.0', use 'pyrekordbox.masterdb' instead!\n"
    "The db6 package was renamed to masterdb!\n"
    "  return _bootstrap._gcd_import(name[level:], package, level)"
)


def _models_module():
    return SimpleNamespace(
        string_to_datetime=lambda value: value,
        DateTime=type("DateTime", (), {}),
    )


class MasterDbImportTests(unittest.TestCase):
    def test_imports_via_masterdb_and_skips_db6(self):
        imported: list[str] = []

        def fake_import(name):
            imported.append(name)
            if "db6" in name:
                raise AssertionError("db6 imported even though masterdb is present")
            return _models_module()

        with patch("prolink.onelibrary.importlib.import_module", fake_import):
            onelibrary.install_tolerant_datetimes()
        self.assertIn("pyrekordbox.masterdb.models", imported)
        self.assertNotIn("pyrekordbox.db6.tables", imported)

    def test_db6_is_only_the_fallback_and_its_warning_is_ignored(self):
        imported: list[str] = []

        def fake_import(name):
            imported.append(name)
            if "masterdb" in name or "devicelib" in name:
                raise ImportError(name)
            warnings.warn(
                "'pyrekordbox.db6' is deprecated and will be removed in version "
                "'0.6.0', use 'pyrekordbox.masterdb' instead!\n"
                "The db6 package was renamed to masterdb!",
                DeprecationWarning,
                stacklevel=2,
            )
            return _models_module()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch("prolink.onelibrary.importlib.import_module", fake_import):
                onelibrary.install_tolerant_datetimes()
        self.assertIn("pyrekordbox.db6.tables", imported)
        self.assertFalse(any(issubclass(item.category, DeprecationWarning) for item in caught))


class LibraryErrorWarningTests(unittest.TestCase):
    def _monitor(self, errors, media):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.library = MagicMock()
        mon.library.lock = threading.RLock()
        mon.library.errors = errors
        mon.library.media = media
        mon._library_cache_key = None
        mon._library_cache = (None, None, None)
        return mon

    def test_deprecation_warning_does_not_set_library_error(self):
        from app import Monitor

        mon = self._monitor({"192.168.1.212": _WARNING}, {})
        _totals, err, info = Monitor._library_snapshot(mon)
        self.assertIsNone(err)
        self.assertIsNone(info)
        self.assertIsNone(onelibrary.library_failure_text(DeprecationWarning(_WARNING)))
        self.assertIsNone(onelibrary.library_failure_text(_WARNING))

        summary = onelibrary.summarize(None, present=True, error=_WARNING)
        self.assertIsNone(summary.error)
        self.assertNotIn("DeprecationWarning", summary.detail)
        self.assertNotIn("pyrekordbox.db6", summary.detail)

    def test_warning_is_stripped_from_onelibrary_detail(self):
        from app import Monitor

        warning = _WARNING
        ol = SimpleNamespace(
            present=True, readable=True, tracks=12087, playlists=63, history=28,
            detail=f"12087 tracks · 63 playlists · 28 history · {warning}",
            error=warning, path="/tmp/exportLibrary.db",
        )
        media = SimpleNamespace(
            db=SimpleNamespace(counts=lambda: {"tracks": 12087}),
            onelibrary=ol,
            host="192.168.1.212",
            export="/C/",
            _pdb_fingerprint=(1, 2),
            _exports_fingerprint=(),
            library_source="remotedb",
        )
        mon = self._monitor({}, {"192.168.1.212": media})
        totals, err, info = Monitor._library_snapshot(mon)
        self.assertIsNone(err)
        self.assertEqual(totals["tracks"], 12087)
        self.assertIsNotNone(info)
        self.assertNotIn("DeprecationWarning", info["detail"])
        self.assertNotIn("db6", info["detail"])
        self.assertIn("12087", info["detail"])
        self.assertIsNone(info["error"])

    def test_a_real_library_error_is_still_reported(self):
        from app import Monitor

        mon = self._monitor({"192.168.1.212": "NFS mount failed"}, {})
        _totals, err, _info = Monitor._library_snapshot(mon)
        self.assertEqual(err, "NFS mount failed")


if __name__ == "__main__":
    unittest.main()
