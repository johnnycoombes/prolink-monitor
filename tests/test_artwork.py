"""Artwork path mapping, cache keys, and source fallback order."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from prolink import artwork as artmod
from prolink import embedded_art
from prolink.artwork_util import highres_nfs_art_path, map_player_path_to_local
from prolink.track_key import track_cache_key


class ArtPathTests(unittest.TestCase):
    def test_highres_path_inserts_m(self):
        p = "PIONEER/Artwork/000/a12345.jpg"
        self.assertEqual(highres_nfs_art_path(p), "PIONEER/Artwork/000/a12345_m.jpg")

    def test_local_map_strips_contents_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapped = map_player_path_to_local(tmp, "/contents/House/track.mp3")
            self.assertEqual(mapped, f"{tmp}/House/track.mp3")


class FallbackOrderTests(unittest.TestCase):
    def test_prefers_embedded_over_thumbnail(self):
        media = mock.Mock()
        media._fetch = mock.Mock(return_value=None)

        track = mock.Mock(
            file_path="contents/t.mp3",
            artwork_path="PIONEER/Artwork/a.jpg",
            artwork_id=0,
        )

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"ID3" + b"\x00" * 200)
            local = f.name
        root = tempfile.mkdtemp()
        import os
        import shutil

        rel = "t.mp3"
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(local, dest)

        with mock.patch.object(
            artmod.embedded_art,
            "extract_embedded_cover",
            return_value=b"\xff\xd8\xff" + b"E" * 64,
        ):
            data, source = artmod.resolve_artwork(
                track,
                media,
                host="192.0.2.1",
                slot="usb",
                open_remotedb=None,
                local_music_root=root,
            )
        self.assertEqual(source, "embedded")
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))

    def test_thumbnail_last(self):
        media = mock.Mock()

        def fetch(remote, cache_name):
            if "arthi" in cache_name or "audiohead" in cache_name:
                return None
            if cache_name.startswith("art_"):
                return b"\xff\xd8\xff" + b"t" * 120
            return None

        media._fetch = fetch
        track = mock.Mock(
            file_path="",
            artwork_path="PIONEER/Artwork/a.jpg",
            artwork_id=0,
        )
        with mock.patch.object(artmod.embedded_art, "extract_embedded_cover", return_value=None):
            _data, source = artmod.resolve_artwork(
                track,
                media,
                host="h",
                slot="usb",
                open_remotedb=None,
                local_music_root="",
            )
        self.assertEqual(source, "thumbnail")

    def test_dbserver_hires_before_any_audio_read(self):
        """A resolved file path must not pull the audio file ahead of album art."""
        media = mock.Mock()
        media._fetch = mock.Mock(return_value=b"\xff\xd8\xff" + b"a" * 40)
        browser = mock.Mock()
        browser.album_art.return_value = b"\xff\xd8\xff" + b"H" * 80
        browser.__enter__ = lambda *_a: browser
        browser.__exit__ = lambda *_a: False
        track = mock.Mock(
            file_path="contents/House/long-track.wav",
            artwork_path="PIONEER/Artwork/a.jpg",
            artwork_id=4242,
        )
        data, source = artmod.resolve_artwork(
            track,
            media,
            host="192.168.1.212",
            slot="usb",
            open_remotedb=lambda: browser,
            local_music_root="",
            size="large",
        )
        self.assertEqual(source, "remotedb-hires")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        media._fetch.assert_not_called()
        browser.album_art.assert_called_with(4242, high_res=True)

    def test_audio_head_is_capped_when_nothing_else_matches(self):
        seen = []

        def fetch(remote, cache_name, max_bytes=None):
            seen.append(max_bytes)
            return b"ID3" + b"\x00" * 32

        media = mock.Mock()
        media._fetch = fetch
        track = mock.Mock(file_path="contents/only.mp3", artwork_path="", artwork_id=0)
        with mock.patch.object(artmod.embedded_art, "extract_embedded_cover", return_value=None):
            artmod.resolve_artwork(
                track, media, host="h", slot="usb",
                open_remotedb=None, local_music_root="",
            )
        self.assertEqual(seen, [artmod.EMBEDDED_READ_LIMIT])


class ArtCacheKeyTests(unittest.TestCase):
    def test_track_key_includes_slot(self):
        a = track_cache_key("10.0.0.1", "/e", (100, 200), "usb", 42)
        b = track_cache_key("10.0.0.1", "/e", (100, 200), "sd", 42)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
