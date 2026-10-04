"""Mix / setlist status — port of prolink-connect's SmartTiming rules.

A track counts as played (now-playing / setlist entry) after it has been
playing and on-air for ``beats_until_reported`` consecutive beats (default
128 ≈ two phrases). Brief drop-outs of up to ``allowed_interrupt_beats``
(default 8) are ignored. After ``time_between_sets`` seconds with nothing
live, the current set ends and the next promotion starts a new one.

Reference: https://github.com/evanpurkhiser/prolink-connect (MIT)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


def _beat_ms(bpm: float, pitch_percent: float = 0.0) -> float:
    """Milliseconds per beat at the effective tempo."""
    if bpm <= 0:
        bpm = 120.0
    effective = bpm * (1.0 + pitch_percent / 100.0)
    if effective <= 0:
        effective = 120.0
    return 60_000.0 / effective


@dataclass
class MixStatusConfig:
    time_between_sets: float = 30.0
    allowed_interrupt_beats: int = 8
    beats_until_reported: int = 128
    use_on_air: bool = True


@dataclass
class SetlistEntry:
    deck: int
    track_id: int
    title: str = ""
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    started_at: float = 0.0
    reported_at: float = 0.0


@dataclass
class MixStatus:
    """Tracks which deck is 'now playing' for the audience and the setlist."""

    config: MixStatusConfig = field(default_factory=MixStatusConfig)
    on_now_playing: Callable[[dict[str, Any]], None] | None = None
    on_set_started: Callable[[], None] | None = None
    on_set_ended: Callable[[], None] | None = None

    now_playing: dict[str, Any] | None = None
    setlist: list[SetlistEntry] = field(default_factory=list)
    set_active: bool = False

    _last: dict[int, dict[str, Any]] = field(default_factory=dict)
    _started_at: dict[int, float] = field(default_factory=dict)
    _may_stop_at: dict[int, float] = field(default_factory=dict)
    _live: set[int] = field(default_factory=set)
    _set_end_deadline: float | None = None

    def reset(self) -> None:
        self.now_playing = None
        self.setlist.clear()
        self.set_active = False
        self._last.clear()
        self._started_at.clear()
        self._may_stop_at.clear()
        self._live.clear()
        self._set_end_deadline = None

    def _on_air(self, snap: dict[str, Any]) -> bool:
        if not self.config.use_on_air:
            return True
        return bool(snap.get("on_air"))

    def _playing(self, snap: dict[str, Any]) -> bool:
        return bool(snap.get("playing"))

    def _promote(self, snap: dict[str, Any], now: float) -> None:
        deck = int(snap["number"])
        if deck in self._live and self.now_playing and self.now_playing.get("number") == deck:
            old_tid = int(self.now_playing.get("track_id") or 0)
            new_tid = int(snap.get("track_id") or 0)
            if old_tid == new_tid:
                return
            # Same live deck, different track — refresh snapshot; restart SmartTiming.
            self._started_at[deck] = now
            self.now_playing = dict(snap)
            self.now_playing["reported_at"] = now
            if self.on_now_playing:
                self.on_now_playing(self.now_playing)
            return
        if not self._on_air(snap) or not self._playing(snap):
            return
        if not self.set_active:
            self.set_active = True
            if self.on_set_started:
                self.on_set_started()
        self._set_end_deadline = None
        self._live.add(deck)
        entry = SetlistEntry(
            deck=deck,
            track_id=int(snap.get("track_id") or 0),
            title=str(snap.get("title") or ""),
            artist=str(snap.get("artist") or ""),
            bpm=float(snap.get("bpm") or 0.0),
            key=str(snap.get("key") or ""),
            started_at=self._started_at.get(deck, now),
            reported_at=now,
        )
        # Avoid duplicate consecutive entries for the same track on the same deck.
        if (not self.setlist
                or self.setlist[-1].track_id != entry.track_id
                or self.setlist[-1].deck != entry.deck):
            self.setlist.append(entry)
        self.now_playing = dict(snap)
        self.now_playing["reported_at"] = now
        if self.on_now_playing:
            self.on_now_playing(self.now_playing)

    def _mark_stopped(self, snap: dict[str, Any], now: float) -> None:
        deck = int(snap["number"])
        self._may_stop_at.pop(deck, None)
        self._started_at.pop(deck, None)
        self._live.discard(deck)
        if self.now_playing and self.now_playing.get("number") == deck:
            self.now_playing = None
        if not self._live and self.set_active:
            self._set_end_deadline = now + self.config.time_between_sets

    def handle(self, snap: dict[str, Any], *, now: float | None = None) -> None:
        """Feed one deck snapshot (from Monitor.state decks[])."""
        now = time.time() if now is None else now
        deck = int(snap.get("number") or 0)
        if not deck:
            return
        # Enrich title/artist later via caller; mix logic only needs play/on-air/bpm.
        last = self._last.get(deck)
        self._last[deck] = snap

        playing = self._playing(snap)
        on_air = self._on_air(snap)

        if last is None:
            if playing and on_air and snap.get("track_id"):
                self._started_at[deck] = now
                self._maybe_first(snap, now)
            return

        was_playing = self._playing(last)
        was_on_air = self._on_air(last)

        if (
            deck in self._live
            and self.now_playing
            and self.now_playing.get("number") == deck
            and int(snap.get("track_id") or 0)
            and int(snap.get("track_id") or 0) != int(last.get("track_id") or 0)
        ):
            self._started_at[deck] = now
            self.now_playing = dict(snap)
            self.now_playing["reported_at"] = now

        if playing and not was_playing:
            if deck in self._may_stop_at and on_air:
                self._may_stop_at.pop(deck, None)
            else:
                self._started_at[deck] = now
                self._maybe_first(snap, now)

        if was_playing and not playing:
            self._may_stop_at[deck] = now

        if was_on_air != on_air:
            if not on_air and deck in self._live:
                self._may_stop_at[deck] = now
            elif on_air and playing:
                self._may_stop_at.pop(deck, None)
                self._maybe_first(snap, now)

        # SmartTiming: promote after enough consecutive play time.
        started = self._started_at.get(deck)
        if started is not None and playing and on_air and snap.get("track_id"):
            need = self.config.beats_until_reported * _beat_ms(
                float(snap.get("track_bpm") or snap.get("bpm") or 0.0),
                float(snap.get("pitch") or 0.0),
            )
            if (now - started) * 1000.0 >= need:
                self._promote(snap, now)

        stopped_at = self._may_stop_at.get(deck)
        if stopped_at is not None:
            need = self.config.allowed_interrupt_beats * _beat_ms(
                float(snap.get("track_bpm") or snap.get("bpm") or 0.0),
                float(snap.get("pitch") or 0.0),
            )
            if (now - stopped_at) * 1000.0 >= need:
                self._mark_stopped(snap, now)

        if (self._set_end_deadline is not None
                and now >= self._set_end_deadline
                and self.set_active
                and not self._live):
            self.set_active = False
            self._set_end_deadline = None
            if self.on_set_ended:
                self.on_set_ended()

    def _maybe_first(self, snap: dict[str, Any], now: float) -> None:
        """If nothing else is live, promote this deck immediately."""
        others = [
            s for n, s in self._last.items()
            if n != snap["number"] and self._playing(s) and self._on_air(s)
        ]
        if others:
            return
        # First into a quiet booth — still respect SmartTiming unless already live.
        if not self._live:
            # For an empty booth, promote as soon as play+on-air; setlist logging
            # still waits for beats_until_reported via the normal path unless we
            # choose to promote now. Match prolink-connect: promote immediately
            # when alone.
            self._promote(snap, now)

    def as_state(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        pending = self._pending_promotion(now)
        return {
            "set_active": self.set_active,
            "now_playing": self.now_playing,
            "pending": pending,
            "setlist": [
                {
                    "deck": e.deck,
                    "track_id": e.track_id,
                    "title": e.title,
                    "artist": e.artist,
                    "bpm": e.bpm,
                    "key": e.key,
                    "started_at": e.started_at,
                    "reported_at": e.reported_at,
                }
                for e in self.setlist
            ],
            "config": {
                "time_between_sets": self.config.time_between_sets,
                "allowed_interrupt_beats": self.config.allowed_interrupt_beats,
                "beats_until_reported": self.config.beats_until_reported,
                "use_on_air": self.config.use_on_air,
            },
        }

    def _pending_promotion(self, now: float) -> dict[str, Any] | None:
        """Deck that is accumulating SmartTiming beats toward becoming now-playing.

        Used by the overlay lower-third “next track” teaser. Returns None when
        nothing is approaching promotion, or when the live deck is already NP.
        """
        need_beats = max(1, int(self.config.beats_until_reported))
        best: dict[str, Any] | None = None
        best_prog = -1.0
        np_deck = int((self.now_playing or {}).get("number") or 0)
        for deck, snap in self._last.items():
            if not snap.get("track_id"):
                continue
            if not self._playing(snap) or not self._on_air(snap):
                continue
            if deck == np_deck and deck in self._live:
                continue
            started = self._started_at.get(deck)
            if started is None:
                continue
            # Alone-in-booth promotes immediately — no pending teaser needed.
            if not self._live and deck not in self._live:
                others = [
                    s for n, s in self._last.items()
                    if n != deck and self._playing(s) and self._on_air(s)
                ]
                if not others:
                    continue
            beat_ms = _beat_ms(
                float(snap.get("track_bpm") or snap.get("bpm") or 0.0),
                float(snap.get("pitch") or 0.0),
            )
            elapsed_beats = max(0.0, (now - started) * 1000.0 / beat_ms)
            if elapsed_beats >= need_beats:
                continue
            progress = min(1.0, elapsed_beats / need_beats)
            if progress < 0.15:
                # Too early — avoid flashing a lower-third for every cue-play.
                continue
            if progress > best_prog:
                best_prog = progress
                best = {
                    "deck": deck,
                    "track_id": int(snap.get("track_id") or 0),
                    "title": str(snap.get("title") or ""),
                    "artist": str(snap.get("artist") or ""),
                    "bpm": float(snap.get("bpm") or 0.0),
                    "key": str(snap.get("key") or ""),
                    "beats": round(elapsed_beats, 1),
                    "need_beats": need_beats,
                    "progress": round(progress, 3),
                }
        return best
