"""Cross-platform pieces added for the macOS build."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 3c:22:fb:00:11:22
\tinet6 fe80::1%en0 prefixlen 64 secured scopeid 0xb
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
en7: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.10.3 netmask 0xffff0000 broadcast 169.254.255.255
"""


def _verify_module():
    spec = importlib.util.spec_from_file_location(
        "verify_bundle_mac", ROOT / "packaging" / "verify_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PathsTests(unittest.TestCase):
    def test_mac_uses_application_support_and_caches(self):
        from prolink import paths

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROLINK_CONFIG_DIR", None)
            cfg = paths.config_dir("darwin")
            cache = paths.cache_dir("darwin")
        self.assertTrue(cfg.endswith(os.path.join("Library", "Application Support", "Prolink Listener")))
        self.assertTrue(cache.endswith(os.path.join("Library", "Caches", "Prolink Listener")))

    def test_windows_and_linux_keep_dot_folders(self):
        from prolink import paths

        for plat in ("win32", "linux"):
            self.assertTrue(paths.default_config_dir(plat).endswith(".prolink-monitor"))
            self.assertTrue(paths.cache_dir(plat).endswith(".prolink-cache"))

    def test_env_override_wins(self):
        from prolink import paths

        with mock.patch.dict(os.environ, {"PROLINK_CONFIG_DIR": "/tmp/plx"}):
            self.assertEqual(paths.config_dir("darwin"), "/tmp/plx")


class IfconfigTests(unittest.TestCase):
    def test_parse_prefix_and_interface(self):
        from prolink.link import parse_ifconfig

        self.assertEqual(parse_ifconfig(IFCONFIG, "192.168.1.20"), (24, "en0"))
        self.assertEqual(parse_ifconfig(IFCONFIG, "169.254.10.3"), (16, "en7"))
        self.assertIsNone(parse_ifconfig(IFCONFIG, "10.0.0.1"))


class MacBundleTests(unittest.TestCase):
    def _fake_app(self, tmp: str, plist: dict) -> Path:
        app = Path(tmp) / "ProlinkListener.app"
        contents = app / "Contents"
        res = contents / "Resources"
        fw = contents / "Frameworks"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "MacOS" / "ProlinkListener").write_text("exe")
        for rel in ("web/index.html", "web/overlay.html", "web/wavepalette.json",
                    "assets/icons/music-note.svg", "pyrekordbox/__init__.py",
                    "mutagen/__init__.py", "sqlite3/__init__.py"):
            (res / rel).parent.mkdir(parents=True, exist_ok=True)
            (res / rel).write_text("x")
        helper = fw / "PySide6/Qt/lib/QtWebEngineCore.framework/Helpers/QtWebEngineProcess.app/Contents/MacOS"
        helper.mkdir(parents=True)
        (helper / "QtWebEngineProcess").write_text("proc")
        (fw / "sqlcipher3").mkdir()
        (fw / "sqlcipher3" / "_sqlite3.cpython-312-darwin.so").write_text("so")
        (res / "ProlinkListener.icns").write_bytes(b"icns")
        (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
        return app

    def test_complete_app_passes(self):
        verify = _verify_module()
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fake_app(tmp, {
                "CFBundleIdentifier": "com.johnnycoombes.prolinklistener",
                "CFBundleShortVersionString": "1.0.14",
                "CFBundleIconFile": "ProlinkListener.icns",
                "NSLocalNetworkUsageDescription": "Pro DJ Link",
            })
            self.assertEqual(verify.verify(app), [])

    def test_missing_local_network_text_fails(self):
        verify = _verify_module()
        with tempfile.TemporaryDirectory() as tmp:
            app = self._fake_app(tmp, {
                "CFBundleIdentifier": "com.johnnycoombes.prolinklistener",
                "CFBundleShortVersionString": "1.0.14",
                "CFBundleIconFile": "ProlinkListener.icns",
            })
            errors = verify.verify(app)
            self.assertTrue(any("NSLocalNetworkUsageDescription" in e for e in errors))

    def test_spec_declares_local_network_and_icon(self):
        spec = (ROOT / "packaging" / "prolink_listener.spec").read_text(encoding="utf-8")
        self.assertIn("NSLocalNetworkUsageDescription", spec)
        self.assertIn("BUNDLE(", spec)
        self.assertTrue((ROOT / "packaging" / "macos" / "ProlinkListener.icns").is_file())


class MacUpdatePromptTests(unittest.TestCase):
    def test_frozen_mac_build_never_self_installs(self):
        src = (ROOT / "updater" / "startup.py").read_text(encoding="utf-8")
        self.assertIn('sys.platform == "win32"', src)


if __name__ == "__main__":
    unittest.main()
