"""Realtime session playlist recorder.

While recording, every newly played on-air track is appended in chronological
order. Timestamps use a **set clock**: time only advances while at least one
deck is playing and on-air, so dead air between tracks is not counted. The
first logged track is always 00:00:00.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


def format_session_ts(offset_s: float) -> str:
    """Format a non-negative offset as HH:MM:SS."""
    total = max(0, int(offset_s))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def bars_to_seconds(bars: float, bpm: float) -> float:
    """Visible waveform window for N bars in 4/4 at the given BPM."""
    if bpm is None or bpm <= 0:
        bpm = 120.0
    # 4/4 → 4 beats per bar; seconds/beat = 60/bpm
    return max(0.25, float(bars) * 4.0 * 60.0 / float(bpm))


# Waveform zoom options: musical bars in 4/4 (not wall-clock seconds).
ZOOM_BARS = (1, 2, 4, 8, 16)
DEFAULT_ZOOM_BARS = 4


def _snap_live(snap: dict[str, Any]) -> bool:
    """True when a deck should advance the set clock / count as playing out."""
    if not snap.get("playing"):
        return False
    if not int(snap.get("track_id") or 0):
        return False
    if "on_air" in snap and not snap.get("on_air"):
        return False
    return True


@dataclass
class SessionTrack:
    deck: int
    track_id: int
    title: str = ""
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    started_at: float = 0.0       # wall clock
    offset_s: float = 0.0         # set-clock offset from first track


@dataclass
class SessionRecorder:
    """Capture a chronological playlist while the user has Record armed."""

    recording: bool = False
    started_at: float | None = None
    t0: float | None = None          # wall time of first logged track
    tracks: list[SessionTrack] = field(default_factory=list)
    _last_ids: dict[int, int] = field(default_factory=dict)

    # Set clock: only accumulates while something is live on-air.
    _accum_s: float = 0.0
    _clock_running: bool = False
    _run_started: float | None = None
    _paused: bool = False
    last_export_paths: list[str] = field(default_factory=list)

    def start(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.recording = True
        self.started_at = now
        self.t0 = None
        self.tracks.clear()
        self._last_ids.clear()
        self._accum_s = 0.0
        self._clock_running = False
        self._run_started = None
        self._paused = False
        self.last_export_paths.clear()

    def stop(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self._clock_running and self._run_started is not None:
            self._accum_s += max(0.0, now - self._run_started)
        self._clock_running = False
        self._run_started = None
        self._paused = False
        self.recording = False

    def clear(self) -> None:
        self.recording = False
        self.started_at = None
        self.t0 = None
        self.tracks.clear()
        self._last_ids.clear()
        self._accum_s = 0.0
        self._clock_running = False
        self._run_started = None
        self._paused = False
        self.last_export_paths.clear()

    def set_clock_elapsed(self, now: float | None = None) -> float:
        """Elapsed set-clock seconds (excludes dead air)."""
        now = time.time() if now is None else now
        elapsed = self._accum_s
        if self._clock_running and self._run_started is not None:
            elapsed += max(0.0, now - self._run_started)
        return max(0.0, elapsed)

    def _update_clock(self, live: bool, now: float) -> None:
        if live:
            if not self._clock_running:
                self._run_started = now
                self._clock_running = True
                self._paused = False
        else:
            if self._clock_running and self._run_started is not None:
                self._accum_s += max(0.0, now - self._run_started)
                self._run_started = None
                self._clock_running = False
                # Only mark paused once the set has actually started.
                if self.t0 is not None or self.tracks:
                    self._paused = True

    def observe(self, snaps: list[dict[str, Any]], *, now: float | None = None) -> None:
        """Update the set clock from all decks, then log any new live tracks."""
        if not self.recording:
            return
        now = time.time() if now is None else now
        live = any(_snap_live(s) for s in snaps)
        self._update_clock(live, now)
        for snap in snaps:
            self._maybe_log(snap, now)

    def handle(self, snap: dict[str, Any], *, now: float | None = None) -> None:
        """Feed one deck snapshot (updates clock from this snap alone)."""
        self.observe([snap], now=now)

    def _maybe_log(self, snap: dict[str, Any], now: float) -> None:
        if not _snap_live(snap):
            return
        deck = int(snap.get("number") or 0)
        track_id = int(snap.get("track_id") or 0)
        if not deck or not track_id:
            return

        prev = self._last_ids.get(deck)
        if prev == track_id:
            if self.tracks:
                last = self.tracks[-1]
                if last.deck == deck and last.track_id == track_id:
                    title = str(snap.get("title") or "")
                    artist = str(snap.get("artist") or "")
                    if title and not last.title:
                        last.title = title
                    if artist and not last.artist:
                        last.artist = artist
                    bpm = float(snap.get("bpm") or snap.get("track_bpm") or 0.0)
                    if bpm and not last.bpm:
                        last.bpm = bpm
                    key = str(snap.get("key") or "")
                    if key and not last.key:
                        last.key = key
            return

        self._last_ids[deck] = track_id
        if self.t0 is None:
            self.t0 = now
            # First track anchors the set clock at zero.
            self._accum_s = 0.0
            self._run_started = now
            self._clock_running = True
            self._paused = False
            offset = 0.0
        else:
            offset = self.set_clock_elapsed(now)

        self.tracks.append(SessionTrack(
            deck=deck,
            track_id=track_id,
            title=str(snap.get("title") or ""),
            artist=str(snap.get("artist") or ""),
            bpm=float(snap.get("bpm") or snap.get("track_bpm") or 0.0),
            key=str(snap.get("key") or ""),
            started_at=now,
            offset_s=offset,
        ))

    # -- export -------------------------------------------------------------
    def as_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "deck": e.deck,
                "track_id": e.track_id,
                "title": e.title,
                "artist": e.artist,
                "bpm": e.bpm,
                "key": e.key,
                "started_at": e.started_at,
                "offset_s": e.offset_s,
                "timestamp": format_session_ts(e.offset_s),
            }
            for e in self.tracks
        ]

    def export_json(self, *, now: float | None = None) -> str:
        return json.dumps(self.as_state(now=now), indent=2, ensure_ascii=False) + "\n"

    def export_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "deck", "title", "artist", "bpm", "key", "track_id"])
        for e in self.tracks:
            writer.writerow([
                format_session_ts(e.offset_s),
                e.deck,
                e.title,
                e.artist,
                f"{e.bpm:.2f}" if e.bpm else "",
                e.key,
                e.track_id,
            ])
        return buf.getvalue()

    def export_m3u(self) -> str:
        lines = ["#EXTM3U", "#EXTENC:UTF-8"]
        for e in self.tracks:
            label = " - ".join(p for p in (e.artist, e.title) if p) or f"track {e.track_id}"
            lines.append(f"#EXTINF:-1,{label}")
            # No local path available over Pro DJ Link — emit a comment marker.
            lines.append(f"#PROLINK:deck={e.deck};id={e.track_id};t={format_session_ts(e.offset_s)}")
        lines.append("")
        return "\n".join(lines)

    def save_exports(self, directory: str, *, prefix: str | None = None,
                     now: float | None = None) -> list[str]:
        """Write CSV, JSON and M3U into ``directory``. Returns saved paths."""
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
        base = prefix or f"session-{stamp}"
        paths: list[str] = []
        mapping = {
            f"{base}.csv": self.export_csv(),
            f"{base}.json": self.export_json(now=now),
            f"{base}.m3u": self.export_m3u(),
        }
        for name, body in mapping.items():
            path = os.path.join(directory, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            paths.append(path)
        self.last_export_paths = paths
        return paths

    def as_state(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        elapsed = self.set_clock_elapsed(now) if (self.recording or self.tracks) else 0.0
        return {
            "recording": self.recording,
            "paused": bool(self.recording and self._paused and not self._clock_running),
            "started_at": self.started_at,
            "t0": self.t0,
            "elapsed_s": elapsed,
            "elapsed": format_session_ts(elapsed),
            "clock": "set",  # set clock (pauses on dead air), not wall clock
            "tracks": self.as_rows(),
            "exports": list(self.last_export_paths),
        }
