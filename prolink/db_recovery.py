"""Reconnect a dropped dbserver session and retry the request that was in flight.

The idea follows chrisle/alphatheta-connect (MIT): a player that closes its
end of the remotedb socket must not leave every later query failing until the
process restarts. This module is our own retry loop — backoff, then a fresh
handshake, then the same read again. Nothing here writes to the player.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# First retry is quick; later ones back off so a rebooting player is not hammered.
RECONNECT_BACKOFF_S = (0.05, 0.2, 0.5, 1.0, 2.0)


@dataclass
class RecoveryStats:
    """Process-wide counts for the Health page."""

    reconnects: int = 0
    last_error: str = ""
    last_at: float = 0.0

    def note(self, error: str) -> None:
        self.reconnects += 1
        self.last_error = error
        self.last_at = time.time()

    def snapshot(self) -> dict:
        return {
            "reconnects": self.reconnects,
            "last_error": self.last_error,
            "last_at": self.last_at,
        }


STATS = RecoveryStats()


def is_connection_drop(exc: BaseException) -> bool:
    """True when the socket died, as opposed to a normal 'menu empty' reply."""
    if isinstance(exc, (OSError, TimeoutError)):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in ("eof", "not connected", "timed out", "timeout", "reset", "broken pipe")
    )


def backoff_delay(attempt: int) -> float:
    """Seconds to wait before retry number ``attempt`` (0 = first retry)."""
    if attempt < 0:
        return 0.0
    if attempt >= len(RECONNECT_BACKOFF_S):
        return RECONNECT_BACKOFF_S[-1]
    return RECONNECT_BACKOFF_S[attempt]
