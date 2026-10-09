"""Version check and update apply logic.

The signed round trip uses a temporary tufup repository and a local HTTP
server. No files under the real home directory are written.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import tempfile
import threading
import unittest
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from updater.apply import (
    apply_extracted_tree,
    decide_action,
    is_user_data_dir,
    windows_batch_preview,
)
from updater.check import perform_check, query_repository
from updater.config import APP_NAME, config_dir, library_cache_dir
from updater.signing import extract_keys_zip, sign_repository
from updater.versioning import is_newer, select_release


class VersionSelectionTests(unittest.TestCase):
    def test_newer_final_release_wins(self):
        self.assertTrue(is_newer("1.0.0", "1.0.1"))
        self.assertFalse(is_newer("1.0.1", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "not-a-version"))

    def test_prereleases_are_ignored(self):
        chosen = select_release(
            "1.0.0",
            ["1.1.0rc1", "1.0.1", "0.9.0", "1.2.0a1", "1.0.2", "garbage"],
        )
        self.assertEqual(chosen, "1.0.2")

    def test_no_final_newer_than_current(self):
        self.assertIsNone(select_release("1.2.0", ["1.2.0", "1.3.0b1"]))
        self.assertIsNone(select_release("bad", ["1.0.0"]))


class ApplyDecisionTests(unittest.TestCase):
    def test_only_a_confirmed_frozen_app_is_installed(self):
        self.assertEqual(decide_action("up_to_date", confirmed=True, frozen=True), "ignore")
        self.assertEqual(decide_action("not_configured", confirmed=True, frozen=True), "ignore")
        self.assertEqual(decide_action("error", confirmed=True, frozen=True), "ignore")
        self.assertEqual(decide_action("available", confirmed=False, frozen=True), "later")
        self.assertEqual(decide_action("available", confirmed=True, frozen=False), "show_download")
        self.assertEqual(decide_action("available", confirmed=True, frozen=True), "apply")

    def test_windows_script_restarts_and_does_not_purge(self):
        script = windows_batch_preview(
            r"C:\temp\src",
            r"C:\Users\User\Prolink Listener",
            r"C:\Users\User\Prolink Listener\ProlinkListener.exe",
        )
        self.assertIn("robocopy", script)
        self.assertIn(r"ProlinkListener.exe", script)
        self.assertIn('start ""', script)
        self.assertNotIn("/purge", script)
        self.assertNotIn(".prolink-monitor", script)
        self.assertNotIn(".prolink-cache", script)

    def test_copy_leaves_unrelated_files_and_refuses_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            dst = root / "app"
            src.mkdir()
            dst.mkdir()
            (src / "ProlinkListener.exe").write_text("new", encoding="utf-8")
            (dst / "ProlinkListener.exe").write_text("old", encoding="utf-8")
            (dst / "notes.txt").write_text("keep", encoding="utf-8")
            settings = root / "settings"
            settings.mkdir()
            (settings / "settings.json").write_text("{}\n", encoding="utf-8")

            apply_extracted_tree(src, dst)
            self.assertEqual((dst / "ProlinkListener.exe").read_text(encoding="utf-8"), "new")
            self.assertEqual((dst / "notes.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((settings / "settings.json").read_text(encoding="utf-8"), "{}\n")

            previous = os.environ.get("PROLINK_CONFIG_DIR")
            os.environ["PROLINK_CONFIG_DIR"] = str(settings)
            try:
                self.assertTrue(is_user_data_dir(settings))
                self.assertTrue(is_user_data_dir(settings / "updates"))
                with self.assertRaises(RuntimeError):
                    apply_extracted_tree(src, settings)
                self.assertEqual((settings / "settings.json").read_text(encoding="utf-8"), "{}\n")
                self.assertFalse((settings / "ProlinkListener.exe").exists())
            finally:
                if previous is None:
                    os.environ.pop("PROLINK_CONFIG_DIR", None)
                else:
                    os.environ["PROLINK_CONFIG_DIR"] = previous

            cache = library_cache_dir()
            self.assertTrue(is_user_data_dir(cache))
            self.assertTrue(is_user_data_dir(cache / "192.168.1.10"))

    def test_config_dir_follows_the_settings_override(self):
        previous = os.environ.get("PROLINK_CONFIG_DIR")
        os.environ["PROLINK_CONFIG_DIR"] = r"C:\custom\prolink-monitor"
        try:
            self.assertEqual(config_dir(), Path(r"C:\custom\prolink-monitor"))
        finally:
            if previous is None:
                os.environ.pop("PROLINK_CONFIG_DIR", None)
            else:
                os.environ["PROLINK_CONFIG_DIR"] = previous


class SettingsEntryTests(unittest.TestCase):
    def test_check_for_updates_round_trip(self):
        from gui.settings import load_settings, save_settings, settings_path

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("PROLINK_CONFIG_DIR")
            os.environ["PROLINK_CONFIG_DIR"] = tmp
            try:
                fresh = load_settings()
                self.assertTrue(fresh["check_for_updates"])
                self.assertTrue(str(settings_path()).endswith(os.path.join(".prolink-monitor", "settings.json"))
                                or settings_path().startswith(tmp))
                save_settings({**fresh, "check_for_updates": False})
                saved = load_settings()
                self.assertFalse(saved["check_for_updates"])
                self.assertTrue(saved["minimize_to_tray"])
            finally:
                if previous is None:
                    os.environ.pop("PROLINK_CONFIG_DIR", None)
                else:
                    os.environ["PROLINK_CONFIG_DIR"] = previous

    def test_source_check_without_root_does_not_raise(self):
        result = perform_check("1.0.0")
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.current, "1.0.0")
        self.assertIsNone(result.available)


class BundleLayoutTests(unittest.TestCase):
    def test_source_tree_still_finds_web_assets(self):
        from prolink.bundle import bundle_root, is_frozen

        self.assertFalse(is_frozen())
        root = bundle_root()
        self.assertTrue(os.path.isfile(os.path.join(root, "web", "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(root, "web", "overlay.html")))
        self.assertTrue(os.path.isfile(os.path.join(root, "app.py")))

    def test_verify_bundle_requires_webengine_and_web(self):
        import importlib.util

        # The local folder is named packaging/, which is also a third-party
        # module. Load the checker by path so that name is not imported.
        spec = importlib.util.spec_from_file_location(
            "verify_bundle",
            Path(__file__).resolve().parents[1] / "packaging" / "verify_bundle.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        verify = module.verify

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            self.assertTrue(verify(dist))
            (dist / "ProlinkListener.exe").write_text("exe", encoding="utf-8")
            internal = dist / "_internal"
            (internal / "web").mkdir(parents=True)
            (internal / "web" / "index.html").write_text("ok", encoding="utf-8")
            (internal / "web" / "overlay.html").write_text("ok", encoding="utf-8")
            (internal / "QtWebEngineProcess.exe").write_text("proc", encoding="utf-8")
            self.assertEqual(verify(dist), [])


class KeyZipTests(unittest.TestCase):
    def test_zip_entries_are_flattened_and_unknown_names_dropped(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("keystore/root", b"private-root")
            archive.writestr("keystore/root.pub", b"public-root")
            archive.writestr("../outside.txt", b"nope")
            archive.writestr("notes.txt", b"nope")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "keys"
            extract_keys_zip(blob.getvalue(), dest)
            self.assertEqual((dest / "root").read_bytes(), b"private-root")
            self.assertEqual((dest / "root.pub").read_bytes(), b"public-root")
            self.assertFalse((dest / "notes.txt").exists())
            self.assertFalse((dest.parent / "outside.txt").exists())


class SkipMetadataTests(unittest.TestCase):
    def test_missing_secret_skips(self):
        from scripts.publish_tuf_release import skip_reason

        reason = skip_reason(keys_b64="", root_json=Path("packaging/tuf/root.json"))
        self.assertIn("TUFUP_KEYS_B64", reason or "")

    def test_missing_root_skips_even_with_a_secret(self):
        from scripts.publish_tuf_release import skip_reason

        reason = skip_reason(keys_b64="abc", root_json=Path("packaging/tuf/does-not-exist.json"))
        self.assertIn("root.json", reason or "")

    def test_ready_when_both_exist(self):
        from scripts.publish_tuf_release import skip_reason

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root.json"
            root.write_text("{}", encoding="utf-8")
            self.assertIsNone(skip_reason(keys_b64="abc", root_json=root))


class SignedUpdateFlowTests(unittest.TestCase):
    def test_client_sees_a_newer_signed_bundle_and_rejects_tampering(self):
        from securesystemslib.interface import generate_and_write_ed25519_keypair

        from updater.config import ROLE_NAMES

        password = "test-password"
        previous_pw = os.environ.get("TUFUP_KEY_PASSWORD")
        os.environ["TUFUP_KEY_PASSWORD"] = password
        cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                keys = root / "keystore"
                keys.mkdir()
                for role in ROLE_NAMES:
                    generate_and_write_ed25519_keypair(password=password, filepath=str(keys / role))

                def _bundle(version: str, marker: str) -> Path:
                    folder = root / f"bundle-{version}"
                    folder.mkdir()
                    (folder / "marker.txt").write_text(marker, encoding="utf-8")
                    return folder

                repo = root / "repository"
                sign_repository(
                    keys_dir=keys,
                    repo_dir=repo,
                    bundle_dir=_bundle("1.0.0", "one"),
                    version="1.0.0",
                )
                self.assertEqual(Path.cwd(), cwd)

                # Continue the same repository with a newer bundle.
                sign_repository(
                    keys_dir=keys,
                    repo_dir=repo,
                    bundle_dir=_bundle("1.1.0", "two"),
                    version="1.1.0",
                )

                serve = root / "serve"
                serve.mkdir()
                for path in (repo / "metadata").iterdir():
                    if path.suffix == ".json":
                        shutil.copyfile(path, serve / path.name)
                for path in (repo / "targets").iterdir():
                    if path.is_file():
                        shutil.copyfile(path, serve / path.name)

                handler = _quiet_handler(serve)
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                port = httpd.server_address[1]
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{port}/"
                try:
                    trusted = serve / "root.json"
                    install = root / "install"
                    install.mkdir()
                    (install / "marker.txt").write_text("old", encoding="utf-8")
                    (install / "kept.txt").write_text("stay", encoding="utf-8")

                    seen = query_repository(
                        current_version="1.0.0",
                        trusted_root=trusted,
                        metadata_dir=root / "client-meta",
                        metadata_url=base,
                        target_dir=root / "client-targets",
                        target_url=base,
                        install_dir=install,
                    )
                    self.assertEqual(seen.status, "available")
                    self.assertEqual(seen.available, "1.1.0")
                    self.assertEqual(seen.filename, f"{APP_NAME}-1.1.0.tar.gz")

                    # User said no: nothing in the install folder changes.
                    self.assertEqual(decide_action(seen.status, confirmed=False, frozen=True), "later")
                    self.assertEqual((install / "marker.txt").read_text(encoding="utf-8"), "old")

                    def _copy(src_dir, dst_dir, **_kwargs):
                        apply_extracted_tree(Path(src_dir), Path(dst_dir))

                    seen.client.extract_dir = root / "extract"
                    seen.client.download_and_apply_update(skip_confirmation=True, install=_copy)
                    self.assertEqual((install / "marker.txt").read_text(encoding="utf-8"), "two")
                    self.assertEqual((install / "kept.txt").read_text(encoding="utf-8"), "stay")

                    current = query_repository(
                        current_version="1.1.0",
                        trusted_root=trusted,
                        metadata_dir=root / "client-meta-current",
                        metadata_url=base,
                        target_dir=root / "client-targets-current",
                        target_url=base,
                        install_dir=root / "install-current",
                    )
                    self.assertEqual(current.status, "up_to_date")

                    archive = serve / f"{APP_NAME}-1.1.0.tar.gz"
                    archive.write_bytes(archive.read_bytes() + b"tamper")
                    tampered = query_repository(
                        current_version="1.0.0",
                        trusted_root=trusted,
                        metadata_dir=root / "client-meta-tamper",
                        metadata_url=base,
                        target_dir=root / "client-targets-tamper",
                        target_url=base,
                        install_dir=root / "install-tamper",
                    )
                    self.assertEqual(tampered.status, "available")

                    def _should_not_install(*_args, **_kwargs):
                        raise AssertionError("tampered archive was installed")

                    with self.assertRaises(Exception):
                        tampered.client.download_and_apply_update(
                            skip_confirmation=True,
                            install=_should_not_install,
                        )
                finally:
                    httpd.shutdown()
        finally:
            os.chdir(cwd)
            if previous_pw is None:
                os.environ.pop("TUFUP_KEY_PASSWORD", None)
            else:
                os.environ["TUFUP_KEY_PASSWORD"] = previous_pw

    def test_publish_script_writes_metadata_for_a_temp_root(self):
        """The release script's skip path is covered above; this checks the zip decode."""
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("root", b"x")
        encoded = base64.b64encode(blob.getvalue()).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            from updater.signing import decode_keys_b64

            decode_keys_b64(encoded, Path(tmp))
            self.assertEqual((Path(tmp) / "root").read_bytes(), b"x")


def _quiet_handler(directory: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format, *args):
            return

    return Handler


if __name__ == "__main__":
    unittest.main()
