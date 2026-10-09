"""Rate-limited playhead corrections between status packets.

Own implementation. The behaviour (ease toward the packet, cap the catch-up,
snap on a real jump) is the idea; the numbers and the code are ours.

Direct snaps. Normal allows ±15% of one packet's travel on a large error and
±8% on a small one. Strong is tighter (±8% / ±4%). Loop wraps, seeks, pauses,
reverses and track changes are not passed through this helper — the caller
snaps those itself.
"""

from __future__ import annotations

MODES = ("direct", "normal", "strong")
# Nominal gap between CDJ status packets, used when two samples share a clock.
NOMINAL_INTERVAL_S = 1.0 / 6.0
# Under this, the tighter cap applies so small jitter is not chased hard.
SMALL_ERROR_MS = 80.0
# Packets quieter than this: keep moving for a second, then hold.
FREEZE_AFTER_S = 1.0

_CAPS = {
    "normal": (0.15, 0.08),
    "strong": (0.08, 0.04),
}


def normalize_mode(value: str | None) -> str:
    mode = str(value or "normal").lower().strip()
    return mode if mode in MODES else "normal"


def correction_ms(error_ms: float, speed: float, mode: str,
                  interval_s: float = NOMINAL_INTERVAL_S) -> float:
    """Milliseconds to add this step. Direct returns the whole error."""
    mode = normalize_mode(mode)
    if mode == "direct":
        return float(error_ms)
    large, small = _CAPS[mode]
    frac = large if abs(error_ms) >= SMALL_ERROR_MS else small
    interval = interval_s if interval_s > 0 else NOMINAL_INTERVAL_S
    travel = max(abs(float(speed)), 0.05) * 1000.0 * interval
    cap = frac * travel
    if error_ms > 0:
        return min(float(error_ms), cap)
    return max(float(error_ms), -cap)


def freeze_instant(now: float, last_packet: float, freeze_after: float = FREEZE_AFTER_S) -> float:
    """Clock to project to. After ``freeze_after`` with no packet, stop there."""
    if last_packet <= 0:
        return now
    if now - last_packet > freeze_after:
        return last_packet + freeze_after
    return now
