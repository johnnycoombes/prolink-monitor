#!/usr/bin/env python3
"""Compare Monitor cost with the library subsystem idle vs a loaded catalog.

This does not talk to a player. It times the in-process paths the Library page
and the live monitor actually call, using stand-in media. Read-only.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import tracemalloc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import Monitor  # noqa: E402
from prolink.remotedb_client import choose_requesting_player  # noqa: E402


class _DummySource:
    kind = "vcdj"
    description = "dummy"
    detail = ""
    device_number = 5

    def start(self, _cb) -> None:
        pass

    def stop(self) -> None:
        pass


class _FakeDb:
    def __init__(self, n: int) -> None:
        self.n = n
        self.rows = [
            {
                "id": i + 1,
                "title": f"Track {i}",
                "artist": f"Artist {i % 400}",
                "album": f"Album {i % 250}",
                "bpm": 120 + (i % 20),
                "key": "8A",
            }
            for i in range(n)
        ]

    def counts(self):
        return {"tracks": self.n, "artists": 400, "albums": 250}

    def search(self, query, limit=100, offset=0):
        rows = self.rows
        q = (query or "").lower()
        if q:
            rows = [r for r in rows if q in r["title"].lower() or q in r["artist"].lower()]
        return [dict(r) for r in rows[offset:offset + limit]], len(rows)


class _FakeMedia:
    def __init__(self, n: int) -> None:
        self.host = "192.0.2.20"
        self.export = "/export/USB"
        self.db = _FakeDb(n)
        self._pdb_fingerprint = (n, 1)
        self._exports_fingerprint = ("/export/USB",)
        self.library_source = "nfs-cache"
        self.library_source_detail = "measure"
        self.onelibrary = type("OL", (), {"present": False})()
        self.cache_dir = "/tmp/prolink-measure"
        self.nfs = type("N", (), {"latency_info": lambda self: {}})()

    def is_stale(self) -> bool:
        return False

    def refresh_if_stale(self) -> None:
        return None

    def set_library_source(self, source: str, detail: str = "") -> None:
        self.library_source = source
        self.library_source_detail = detail

    def close(self) -> None:
        return None


def _time_call(fn, repeats: int) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - start) / repeats


def main() -> int:
    print("dbserver client number when virtual player is 5:",
          choose_requesting_player(5))
    print("dbserver client number when virtual player is 2:",
          choose_requesting_player(2))
    print("threads before monitor:", len(threading.enumerate()))

    idle = Monitor("192.0.2.20", _DummySource(), cache_dir=os.path.join(ROOT, ".cache-measure"))
    print("threads after construct (preload not started):", len(threading.enumerate()))
    idle.start()
    time.sleep(0.05)
    print("threads after start (includes library preload):", len(threading.enumerate()),
          [t.name for t in threading.enumerate() if t is not threading.main_thread()])
    idle.stop()

    tracemalloc.start()
    base_current, base_peak = tracemalloc.get_traced_memory()
    idle_state = _time_call(idle.state, 50)
    idle_current, idle_peak = tracemalloc.get_traced_memory()
    print(f"state() with no media: {idle_state * 1000:.3f} ms/call")
    print(f"tracemalloc after idle state: current={idle_current - base_current} peak+={idle_peak - base_peak}")

    loaded = Monitor("192.0.2.20", _DummySource(), cache_dir=os.path.join(ROOT, ".cache-measure"))
    media = _FakeMedia(5000)
    loaded.library.media["192.0.2.20"] = media
    # Force the snapshot cache to miss once, then stay hot.
    loaded._library_cache_key = None
    first = _time_call(lambda: loaded._library_snapshot(), 1)
    hot = _time_call(loaded.state, 50)
    snap_current, snap_peak = tracemalloc.get_traced_memory()
    print(f"library snapshot first pass (5000-track counts): {first * 1000:.3f} ms")
    print(f"state() with library loaded, snapshot cached: {hot * 1000:.3f} ms/call")

    def browse():
        return loaded.browse_tracks("", limit=200, offset=0)

    # browse_tracks tries remotedb, which needs a device. With no devices it
    # falls through to the in-memory export.pdb search.
    browse_ms = _time_call(browse, 5) * 1000
    result = browse()
    print(f"browse_tracks nfs-cache path, 5000 rows -> {result['total']} "
          f"returned {len(result['tracks'])}: {browse_ms:.1f} ms/call")

    pages = {"calls": 0}

    class _Browser:
        target_player = 1
        requesting_player = 3
        slot = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def track_search_page(self, offset=0, limit=64):
            pages["calls"] += 1
            if offset >= 2000:
                return []
            return [{"id": offset + i + 1, "name": f"T{offset + i}", "subtitle": "A"}
                    for i in range(limit)]

        def playlist_folder(self, _parent):
            return [{"id": 1, "name": "Tonight", "folder": False}]

        def history_entries(self):
            return [{"id": 2, "name": "HISTORY"}]

    loaded._open_remotedb = lambda host, slot=None: _Browser()  # type: ignore[method-assign]
    loaded._library_db_slots = lambda: [1, 2]  # type: ignore[method-assign]
    remote_ms = _time_call(lambda: loaded.browse_tracks("", limit=200, offset=0), 3) * 1000
    print(f"browse_tracks remotedb path (2 slots x up to 2000 rows): {remote_ms:.1f} ms/call, "
          f"page fetches={pages['calls']}")
    play_ms = _time_call(loaded.browse_playlists, 3) * 1000
    print(f"browse_playlists remotedb path: {play_ms:.1f} ms/call")
    print(f"tracemalloc peak bytes={snap_peak}")
    tracemalloc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
