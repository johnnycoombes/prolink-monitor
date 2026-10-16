"""1.0.22: hot cues survive a title published before the ANLZ read.

On the XDJ-AZ the dbserver title often lands before NFS has read the ANLZ
files (export.pdb still loading, or a slow first NFS pass). That title-only
entry has ``cues: []``. The web panel, overlays and desktop Monitor all cached
it as final, so the waveform appeared a moment later but the hot cue markers
never did. These tests replay that order.
"""

from __future__ import annotations

import os
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink import anlz
from prolink.web_server import lite_state
from tests import test_az_anlz as _az
_analysis = _az._analysis
from prolink.pdb import Track

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _az_cues():
    """Real XDJ-AZ layout: memory cues in PCOB, hot cues A-H in PCO2."""
    a = _analysis()
    a.cues = [anlz.Cue(0, "cue", 141, 0xFFFFFFFF)] + [
        anlz.Cue(n, "cue", 1000 * n, 0xFFFFFFFF, "CUE(Auto)", (255, 0, 23))
        for n in range(1, 9)]
    return a


class TitleFirstThenAnlz(unittest.TestCase):
    def _setup(self):
        h = _az.AzFourDeckAnlzTests()
        analysis = _az_cues()
        track = Track(id=30638, title="Night Drive", artist="AZ", duration=501,
                      analyze_path="/PIONEER/USBANLZ/P016/000077AE/ANLZ0000.DAT",
                      tempo=128.0)
        media = h._media(track, analysis)
        # First pass: NFS read fails, so only the dbserver title is published.
        media.analysis = mock.Mock(side_effect=[None, analysis, analysis, analysis])
        mon = h._monitor(media)
        mon._schedule_late_retry = lambda *a, **k: None
        deck = h._deck()
        mon.engine.decks = {1: deck}
        mon._on_track_change(deck, 30638)
        placeholder = mon.meta(30638, deck=1, load=False)
        self.assertEqual(placeholder["cues"], [])
        self.assertEqual(int(placeholder.get("detail_columns") or 0), 0)
        return mon

    def test_api_track_returns_cues_not_the_placeholder(self):
        mon = self._setup()
        meta = mon.meta(30638, deck=1)          # what /api/track serves
        hot = [c for c in meta["cues"] if c["hot"]]
        self.assertEqual([c["hot"] for c in hot], list(range(1, 9)))
        self.assertGreater(meta["detail_columns"], 0)

    def test_desktop_backend_replaces_title_only_meta(self):
        try:
            from gui.backend import Backend, meta_is_analysed
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Qt unavailable: {exc}")
        mon = self._setup()
        b = Backend.__new__(Backend)
        from prolink.lru import BoundedLRU
        b._lock = threading.RLock()
        b._monitor = mon
        b._hot = set()
        b._meta_cache = BoundedLRU(50)
        b._wave_cache = BoundedLRU(50)
        b.track_ready = mock.Mock()
        key = mon.meta(30638, deck=1, load=False)["track_key"]
        # Cache the title-only entry, as the UI did before the wave landed.
        b._meta_cache[b._track_cache_id(30638, key)] = mon.meta(30638, deck=1, load=False)
        self.assertFalse(meta_is_analysed(b._meta_cache[b._track_cache_id(30638, key)]))
        meta = b.meta(30638, deck=1, track_key=key)
        self.assertTrue(meta_is_analysed(meta))
        self.assertEqual(len([c for c in meta["cues"] if c["hot"]]), 8)
        token = meta["track_key"]
        cached = b._meta_cache[b._track_cache_id(30638, token)]
        self.assertEqual(len([c for c in cached["cues"] if c["hot"]]), 8)


class StreamsKeepTrackKeys(unittest.TestCase):
    def test_lite_stream_keeps_decks(self):
        full = {"t": 1, "decks": [{"number": 1, "track_id": 7, "track_key": "k"}],
                "nfs": {"big": 1}, "anlz": [1]}
        lite = lite_state(full)
        self.assertEqual(lite["decks"], full["decks"])   # clients still find the meta
        self.assertNotIn("nfs", lite)


class WebClientsRefetchTitleOnlyMeta(unittest.TestCase):
    def test_index_and_overlay_refetch(self):
        for name in ("index.html", "overlay.html"):
            with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("detail_columns > 0", src, name)
            self.assertIn("meta: full", src, name)


if __name__ == "__main__":
    unittest.main()
