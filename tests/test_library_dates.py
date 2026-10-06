"""Bad OneLibrary dates must not fail the library query."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from prolink import onelibrary


class ParseLibraryDatetimeTests(unittest.TestCase):
    def test_malformed_and_empty_values_are_blank(self):
        for value in ("2019t00-00-00z", "0000-00-00", "", None):
            self.assertIsNone(onelibrary.parse_library_datetime(value), value)

    def test_valid_iso_still_parses(self):
        parsed = onelibrary.parse_library_datetime("2019-06-01T12:00:00")
        self.assertIsInstance(parsed, datetime)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2019, 6, 1))
        zulu = onelibrary.parse_library_datetime("2019-04-12 19:11:29.274+00:00")
        self.assertEqual(zulu.year, 2019)
        self.assertEqual(zulu.hour, 19)

    def test_patched_column_keeps_every_row(self):
        class DateTime:
            def process_result_value(self, value, dialect):
                return datetime.fromisoformat(value)

        module = SimpleNamespace(
            string_to_datetime=datetime.fromisoformat,
            DateTime=DateTime,
        )
        onelibrary.install_tolerant_datetimes_on(module)
        values = ["2019t00-00-00z", "0000-00-00", "", None, "2019-06-01T12:00:00"]
        column = module.DateTime()
        parsed = [column.process_result_value(value, None) for value in values]
        self.assertEqual(len(parsed), 5)
        self.assertIsNone(parsed[0])
        self.assertIsNone(parsed[1])
        self.assertIsNone(parsed[2])
        self.assertIsNone(parsed[3])
        self.assertEqual(parsed[4].year, 2019)
        self.assertIsNone(module.string_to_datetime("2019t00-00-00z"))
        onelibrary.install_tolerant_datetimes_on(module)
        self.assertTrue(module.DateTime.process_result_value._prolink_safe)


class ListTracksTests(unittest.TestCase):
    def test_orm_skips_one_bad_row_and_returns_the_rest(self):
        good = SimpleNamespace(
            content_id=1, title="Good", artist=SimpleNamespace(name="A"),
            album=None, genre=None, key=None, label=None, length=1000,
            bpmx100=12000, analysisDataFilePath="", image=None, rating=0,
            releaseYear=0, bitrate=0, djComment="",
        )
        bad = SimpleNamespace(content_id="nope")
        db = mock.Mock()
        db.get_content.return_value = [good, bad]
        rows = onelibrary.list_tracks(db, "")
        titles = [row["title"] for row in rows]
        self.assertIn("Good", titles)

    def test_raw_sql_lists_titles_when_the_date_query_fails(self):
        db = mock.Mock()
        db.get_content.side_effect = ValueError(
            "Invalid isoformat string: '2019t00-00-00z'")
        db.session = object()

        def sql_rows(_session, statement, _params=None):
            if statement.startswith("SELECT COUNT"):
                return [(2,)]
            if statement.startswith("SELECT ID"):
                return [
                    (1, "Night Drive", "AZ", "2019t00-00-00z"),
                    (2, "Also", "JC", None),
                ]
            return []

        with mock.patch.object(onelibrary, "_table_names", return_value=["content"]), \
             mock.patch.object(onelibrary, "_columns", return_value=[
                 "ID", "Title", "Artist", "created_at",
             ]), \
             mock.patch.object(onelibrary, "_sql_rows", side_effect=sql_rows):
            rows = onelibrary.list_tracks(db, "")
        self.assertEqual([row["title"] for row in rows], ["Night Drive", "Also"])
        self.assertEqual(rows[0]["artist"], "AZ")
        selected = onelibrary._sql_rows
        self.assertIsNotNone(selected)


class BrowseFallbackTests(unittest.TestCase):
    def test_onelibrary_copy_is_used_only_when_live_and_pdb_are_empty(self):
        import threading
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon._remotedb_fail = {}
        mon.library = mock.Mock()
        mon.library.lock = threading.RLock()
        media = mock.Mock()
        media.db = None
        media.load_database.side_effect = OSError("no export.pdb")
        media.cache_dir = "/tmp/cache"
        media.onelibrary_db = object()
        mon.library.media = {"192.168.1.212": media}
        mon._library_db_slots = lambda: []
        mon._open_remotedb = lambda *_a, **_k: None
        with mock.patch.object(onelibrary, "list_tracks", return_value=[
            {"id": 7, "title": "Copied", "artist": "AZ", "source": "onelibrary"},
        ]) as listed:
            out = mon.browse_tracks("", limit=10)
        listed.assert_called_once()
        self.assertEqual(out["library_source"], "onelibrary")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["tracks"][0]["title"], "Copied")
        media.set_library_source.assert_called()
        args = media.set_library_source.call_args[0]
        self.assertEqual(args[0], "onelibrary")
        self.assertIn("OneLibrary", args[1])


class PhraseToggleTests(unittest.TestCase):
    def test_apply_prefs_hides_the_strip_and_does_not_publish_stale_on(self):
        from PySide6.QtWidgets import QApplication

        from gui.i18n import I18n
        from gui.pages import MonitorPage

        _ = QApplication.instance() or QApplication([])
        page = MonitorPage(I18n(), backend=mock.Mock())
        page.update_state({
            "backend_status": "ok",
            "decks": [{
                "number": 1, "track_id": 0, "playing": False,
                "position_ms": 0, "duration_ms": 0, "speed": 1,
                "bpm": 0, "pitch": 0, "state": "empty",
            }],
        })
        seen = []
        page.on_prefs_changed = lambda prefs: seen.append(bool(prefs["show_phrases"]))
        page.apply_prefs({
            "show_phrases": False,
            "zoom_bars": 4,
            "max_decks": 2,
            "waveform_style": "rgb",
            "playhead_position": "auto",
            "show_empty_decks": True,
        })
        self.assertFalse(page._show_phrases)
        self.assertEqual(seen, [False])
        card = page._cards[1]
        self.assertFalse(card.wave._show_phrases)


if __name__ == "__main__":
    unittest.main()
