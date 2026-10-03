"""Realtime session playlist recorder.

While recording, every newly played track is appended in chronological order.
The first logged track is treated as 00:00:00; later entries carry an HH:MM:SS
offset from that first track's wall-clock start.
"""

from __future__ import annotations

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


@dataclass
class SessionTrack:
    deck: int
    track_id: int
    title: str = ""
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    started_at: float = 0.0
    offset_s: float = 0.0


@dataclass
class SessionRecorder:
    """Capture a chronological playlist while the user has Record armed."""

    recording: bool = False
    started_at: float | None = None
    t0: float | None = None
    tracks: list[SessionTrack] = field(default_factory=list)
    _last_ids: dict[int, int] = field(default_factory=dict)

    def start(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.recording = True
        self.started_at = now
        self.t0 = None
        self.tracks.clear()
        self._last_ids.clear()

    def stop(self) -> None:
        self.recording = False

    def clear(self) -> None:
        self.recording = False
        self.started_at = None
        self.t0 = None
        self.tracks.clear()
        self._last_ids.clear()

    def handle(self, snap: dict[str, Any], *, now: float | None = None) -> None:
        """Feed one deck snapshot. Logs a row when a new track starts playing."""
        if not self.recording:
            return
        now = time.time() if now is None else now
        deck = int(snap.get("number") or 0)
        track_id = int(snap.get("track_id") or 0)
        if not deck or not track_id:
            return
        if not snap.get("playing"):
            return
        # Prefer on-air when the mixer reports it; still log if the flag is absent.
        if "on_air" in snap and not snap.get("on_air"):
            return

        prev = self._last_ids.get(deck)
        if prev == track_id:
            # Enrich title/artist/bpm if metadata arrived after the first sighting.
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
            offset = 0.0
        else:
            offset = max(0.0, now - self.t0)

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

    def as_state(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        elapsed = 0.0
        if self.t0 is not None:
            elapsed = max(0.0, now - self.t0)
        elif self.recording and self.started_at is not None:
            elapsed = max(0.0, now - self.started_at)
        return {
            "recording": self.recording,
            "started_at": self.started_at,
            "t0": self.t0,
            "elapsed_s": elapsed,
            "elapsed": format_session_ts(elapsed),
            "tracks": [
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
            ],
        }
