"""Pick the live deck the audience hears (Now Playing), independent of SmartTiming."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AudienceDeckTracker:
    """Tracks on-air timing and selects the audience deck each tick."""

    on_air_ever: bool = False
    on_air_since: dict[int, float] = field(default_factory=dict)

    def observe(self, decks: list[dict[str, Any]], *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        seen = set()
        for raw in decks or []:
            n = int(raw.get("number") or 0)
            if not n:
                continue
            seen.add(n)
            if raw.get("on_air"):
                self.on_air_ever = True
                self.on_air_since.setdefault(n, now)
            else:
                self.on_air_since.pop(n, None)
        for n in list(self.on_air_since):
            if n not in seen:
                self.on_air_since.pop(n, None)

    def pick(self, decks: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Selection rule (documented in README / PR):

        1. Prefer tempo **master**, **playing**, and **on-air** (when on-air is in use).
        2. Else a **playing** deck that is **on-air**; if several, prefer **master**, else
           the deck that went on-air most recently.
        3. If on-air never reported on this rig, fall back to **master + playing**, then
           any playing deck with a loaded track.
        """
        playing = [
            d for d in (decks or [])
            if d.get("track_id") and d.get("playing")
        ]
        if not playing:
            return None

        use_on_air = self.on_air_ever

        def on_air_ok(d: dict[str, Any]) -> bool:
            return bool(d.get("on_air")) if use_on_air else True

        def master_on_air(d: dict[str, Any]) -> bool:
            return bool(d.get("master")) and on_air_ok(d)

        tier1 = [d for d in playing if master_on_air(d)]
        if tier1:
            return tier1[0]

        if use_on_air:
            pool = [d for d in playing if d.get("on_air")]
            if pool:
                masters = [d for d in pool if d.get("master")]
                if masters:
                    return masters[0]
                return max(
                    pool,
                    key=lambda d: self.on_air_since.get(int(d.get("number") or 0), 0.0),
                )

        masters = [d for d in playing if d.get("master")]
        if masters:
            return masters[0]

        return playing[0]


def merge_live_deck(
    audience: dict[str, Any] | None,
    decks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Overlay paint fields from ``decks[]`` onto the audience snapshot."""
    if not audience:
        return None
    n = int(audience.get("number") or 0)
    live = next((d for d in (decks or []) if int(d.get("number") or 0) == n), None)
    if not live:
        return dict(audience)
    merged = {**audience, **live}
    # Drop stale mix/SmartTiming title fields when the live track id changed.
    if int(live.get("track_id") or 0) != int(audience.get("track_id") or 0):
        for key in ("title", "artist", "key", "reported_at"):
            merged.pop(key, None)
    return merged
