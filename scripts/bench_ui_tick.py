"""Benchmark: desktop Monitor tick (20 Hz) while NFS is slow.

A fake Monitor sleeps ``--delay`` seconds on every meta/waveform/artwork read
that is not cached (a slow or lossy NFS link) and never finds the waveform.
Prints the median and worst tick time of MonitorPage.update_state.

    QT_QPA_PLATFORM=offscreen python scripts/bench_ui_tick.py
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SlowMonitor:
    def __init__(self, delay: float):
        self.delay = delay
        self.calls = {"meta": 0, "waveform": 0, "artwork": 0}

    def meta(self, track_id, deck=None, load=True):
        if load:
            self.calls["meta"] += 1
            time.sleep(self.delay)
        return {"title": f"T{track_id}", "track_key": f"k{track_id}", "has_artwork": True,
                "duration_ms": 200000, "bpm": 120}

    def waveform(self, track_id, deck=None):
        self.calls["waveform"] += 1
        time.sleep(self.delay)
        return None                      # still not readable

    def artwork(self, track_id, deck=None, size="large"):
        self.calls["artwork"] += 1
        time.sleep(self.delay)
        return None


def deck(n: int) -> dict:
    return {"number": n, "name": "XDJ-AZ", "track_id": 100 + n, "track_key": f"k{100 + n}",
            "playing": True, "position_ms": 1000.0, "bpm": 120.0, "track_bpm": 120.0,
            "speed": 1.0, "state": "playing", "beat": 1, "bar": 1, "duration_ms": 200000}


def run(delay: float, ticks: int) -> dict:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.backend import Backend
    from gui.i18n import I18n
    from gui.pages import MonitorPage

    backend = Backend()
    mon = SlowMonitor(delay)
    backend._monitor = mon
    page = MonitorPage(I18n(), backend)
    state = {"decks": [deck(1), deck(2)]}
    # Let the meta warm-up land (it is allowed to be slow off the UI thread).
    for _ in range(3):
        backend.meta(101, deck=1, track_key="k101")
        backend.meta(102, deck=2, track_key="k102")
    times = []
    for _ in range(ticks):
        t0 = time.perf_counter()
        page.update_state(state)
        app.processEvents()
        times.append((time.perf_counter() - t0) * 1000.0)
    page.deleteLater()
    stop = getattr(backend, "shutdown_workers", None)
    if callable(stop):
        stop()
    return {"median_ms": statistics.median(times), "max_ms": max(times),
            "calls": dict(mon.calls), "ticks": ticks}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--ticks", type=int, default=20)
    a = ap.parse_args()
    r = run(a.delay, a.ticks)
    print(f"ticks={r['ticks']} delay={a.delay*1000:.0f}ms median={r['median_ms']:.2f}ms "
          f"max={r['max_ms']:.2f}ms backend calls={r['calls']}")
