"""1.0.23: per-view fixes after 1.0.21/1.0.22.

- Overlay Panel/Bar style shows elapsed / remaining time again.
- Title-only meta re-asks are bounded (web and desktop) and the desktop
  only nudges the Monitor when the cached meta actually changed.
- SSE reuses the desktop UI tick's state() snapshot.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from prolink import web_server

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(name):
    with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
        return f.read()


def _fn(src, name):
    m = re.search(r"^function %s\(.*?^}" % name, src, re.S | re.M)
    assert m, name
    return m.group(0)


class OverlayTimeTests(unittest.TestCase):
    def test_every_time_layout_has_a_time_element(self):
        src = _src("overlay.html")
        for fn in ("renderNowPanel", "renderNow", "renderDual"):
            self.assertIn("data-time", _fn(src, fn), fn)

    def test_time_refs_cached_with_card(self):
        src = _src("overlay.html")
        self.assertIn("time: Array.from(card.querySelectorAll('[data-time]'))", src)

    def test_lite_stream_carries_time_inputs(self):
        full = {"t": 1, "decks": [{"number": 1, "position_ms": 5.0, "duration_ms": 9.0,
                                   "playing": True, "speed": 1.0, "playhead_age_s": 0.1}],
                "nfs": []}
        deck = web_server.lite_state(full)["decks"][0]
        for k in ("position_ms", "duration_ms", "playing", "speed", "playhead_age_s"):
            self.assertIn(k, deck)


class WebMetaRetryBounded(unittest.TestCase):
    def test_index_and_overlay_cap_retries(self):
        for name in ("index.html", "overlay.html"):
            src = _src(name)
            self.assertIn("META_RETRY_MAX", src, name)
            self.assertIn("metaRetries.delete(cacheId)", src, name)


class _Mon:
    def __init__(self):
        self.calls = 0

    def meta(self, track_id, deck=None, load=True):
        self.calls += 1
        return {"id": track_id, "track_key": "k", "title": "T", "detail_columns": 0}


class DesktopTitleOnlyBounded(unittest.TestCase):
    def _backend(self):
        try:
            from gui.backend import Backend
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Qt unavailable: {exc}")
        from prolink.lru import BoundedLRU
        b = Backend.__new__(Backend)
        b._lock = threading.RLock()
        b._monitor = _Mon()
        b._hot = set()
        b._meta_cache = BoundedLRU(50)
        b._wave_cache = BoundedLRU(50)
        b._queued = set()
        b._miss = {}
        b._meta_final = set()
        b.track_ready = mock.Mock()

        class _Pool:
            def submit(self, fn):
                fn()
        b._pool = _Pool()
        return b

    def test_same_title_only_meta_does_not_reemit(self):
        b = self._backend()
        b.meta(5, deck=1, track_key="k")
        b.meta(5, deck=1, track_key="k")
        self.assertEqual(b.track_ready.emit.call_count, 1)

    def test_title_only_requests_stop_after_backoff(self):
        b = self._backend()
        clock = [1000.0]
        with mock.patch("gui.backend.time.monotonic", side_effect=lambda: clock[0]):
            for _ in range(40):
                b.meta_cached(5, deck=1, track_key="k")
                clock[0] += 60.0
        n = len(b.MISS_BACKOFF_S)
        self.assertLessEqual(b._monitor.calls, n + 1)
        self.assertIn("k", b._meta_final)
        before = b._monitor.calls
        b.meta_cached(5, deck=1, track_key="k")
        self.assertEqual(b._monitor.calls, before)


class RecentStateReuse(unittest.TestCase):
    def test_sse_reuses_ui_snapshot(self):
        import app
        mon = app.Monitor.__new__(app.Monitor)
        built = []
        mon._build_state = lambda notify_wnp=True: built.append(1) or {"t": 1, "decks": []}
        a = mon.state()
        a["backend_status"] = "x"        # caller mutation must not leak
        b = mon.recent_state(0.5)
        self.assertEqual(len(built), 1)
        self.assertNotIn("backend_status", b)
        mon._recent_state = (time.monotonic() - 5, {"old": 1})
        mon.recent_state(0.5)
        self.assertEqual(len(built), 2)


if __name__ == "__main__":
    unittest.main()
