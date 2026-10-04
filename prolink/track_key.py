"""Stable cache keys for loaded tracks (device + medium + slot + id)."""

from __future__ import annotations

import hashlib
from typing import Any


def track_cache_key(
    host: str,
    export: str,
    pdb_fingerprint: tuple[int, int] | None,
    slot: str,
    track_id: int,
) -> tuple[str, str, tuple[int, int], str, int]:
    """Tuple key for in-memory waveform/metadata caches."""
    fp = pdb_fingerprint or (0, 0)
    return (
        str(host or ""),
        str(export or ""),
        (int(fp[0]), int(fp[1])),
        str(slot or ""),
        int(track_id or 0),
    )


def track_key_token(key: tuple[Any, ...]) -> str:
    """URL-safe token for clients (artwork / waveform cache busting)."""
    raw = "|".join(str(part) for part in key)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def keys_match(stored: tuple[Any, ...] | None, track_id: int) -> bool:
    if not stored or len(stored) < 5:
        return False
    try:
        return int(stored[4]) == int(track_id or 0)
    except (TypeError, ValueError):
        return False
