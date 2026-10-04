"""Optional OneLibrary helpers (no encrypted fixture required)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from prolink import onelibrary


class OneLibraryHelperTests(unittest.TestCase):
    def test_summarize_absent(self):
        s = onelibrary.summarize(None, present=False)
        self.assertFalse(s.present)
        self.assertFalse(s.readable)

    def test_summarize_present_unreadable(self):
        s = onelibrary.summarize(None, present=True, error="no pyrekordbox")
        self.assertTrue(s.present)
        self.assertFalse(s.readable)
        self.assertIn("pyrekordbox", s.detail)

    def test_summarize_readable(self):
        db = mock.Mock()
        db.get_content.return_value = [1, 2, 3]
        db.get_playlist.return_value = ["a"]
        db.get_history.return_value = ["h1", "h2"]
        s = onelibrary.summarize(db, present=True, path="/tmp/exportLibrary.db")
        self.assertTrue(s.readable)
        self.assertEqual(s.tracks, 3)
        self.assertEqual(s.playlists, 1)
        self.assertEqual(s.history, 2)

    def test_track_from_content(self):
        content = SimpleNamespace(
            content_id=42,
            title="Cola",
            artist=SimpleNamespace(name="CamelPhat"),
            album=SimpleNamespace(name="Club"),
            genre=SimpleNamespace(name="House"),
            key=SimpleNamespace(name="3A"),
            label=None,
            length=415000,
            bpmx100=12232,
            analysisDataFilePath="/PIONEER/USBANLZ/x/ANLZ0000.DAT",
            image=SimpleNamespace(path="/PIONEER/Artwork/a.jpg"),
            rating=5,
            releaseYear=2017,
            bitrate=320,
            djComment="",
        )
        t = onelibrary.track_from_content(content)
        self.assertEqual(t.id, 42)
        self.assertEqual(t.title, "Cola")
        self.assertEqual(t.artist, "CamelPhat")
        self.assertAlmostEqual(t.tempo, 122.32)
        self.assertEqual(t.duration_ms, 415000)

    def test_playlist_tracks_uses_get_playlist_content(self):
        db = mock.Mock()
        row = SimpleNamespace(
            content=SimpleNamespace(
                content_id=7,
                title="Track",
                artist=SimpleNamespace(name="Artist"),
                album=None,
                genre=None,
                key=None,
                label=None,
                length=180000,
                bpmx100=12800,
                analysisDataFilePath="",
                image=None,
                rating=0,
                releaseYear=0,
                bitrate=0,
                djComment="",
            )
        )
        db.get_playlist_content.return_value = [row]
        rows = onelibrary.playlist_tracks(db, 99)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 7)
        self.assertEqual(rows[0]["title"], "Track")
        db.get_playlist_content.assert_called_with(99)


if __name__ == "__main__":
    unittest.main()
