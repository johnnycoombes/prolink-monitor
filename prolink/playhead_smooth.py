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


def sync_detail_origin(cache_start: float, true_start: float, px_per_col: float,
                       width: float, dpr: float = 1.0) -> tuple[float, int, bool]:
    """Move a scrolling waveform cache onto the playhead window.

    ``cache_start`` and ``true_start`` are the first visible waveform column.
    The bitmap is shifted by whole device pixels, and the stored origin moves
    by those same pixels. Truncating CSS pixels and then rounding the device
    shift walks the wave away from the loop highlight when the display scale
    is not an integer (125% and 150% on Windows).

    Returns ``(new_cache_start, shift_device_px, full_rebuild)``. A full
    rebuild means the jump is at least one view wide (a seek, or a loop wrap
    that is wider than the window). ``shift_device_px`` is positive when the
    cache is ahead of the playhead and the bitmap must move right.
    """
    if px_per_col <= 0 or width <= 0:
        return float(true_start), 0, True
    scale = float(dpr) if dpr and dpr > 0 else 1.0
    ahead_css = (float(cache_start) - float(true_start)) * float(px_per_col)
    shift_device = int(ahead_css * scale)  # toward zero
    view_device = int(round(float(width) * scale))
    if shift_device == 0:
        return float(cache_start), 0, False
    if abs(shift_device) >= max(1, view_device):
        return float(true_start), 0, True
    moved_css = shift_device / scale
    return float(cache_start) - moved_css / float(px_per_col), shift_device, False


class DisplayPlayhead:
    """Per-frame display position for a 60 Hz paint, between ~5-20 Hz updates.

    Each paint advances the shown position by real elapsed time x speed and
    eases a small share of the remaining error toward the extrapolated target,
    so a new state snapshot never makes the strip step back or jump forward.
    Seeks, loops, pauses and track changes (error over ``snap_ms``) snap.
    """

    def __init__(self, ease: float = 0.2, snap_ms: float = 300.0):
        self.ease = ease
        self.snap_ms = snap_ms
        self._shown: float | None = None
        self._at = 0.0
        self._key = None

    def update(self, target_ms: float, *, playing: bool, speed: float, now: float,
               key=None, looping: bool = False) -> float:
        target = float(target_ms)
        if (self._shown is None or not playing or looping or key != self._key
                or speed <= 0):
            self._shown, self._at, self._key = target, now, key
            return target
        dt = max(0.0, min(0.25, now - self._at))
        expected = self._shown + dt * float(speed) * 1000.0
        err = target - expected
        if abs(err) > self.snap_ms:
            shown = target
        else:
            shown = expected + err * self.ease
        self._shown, self._at = shown, now
        return shown
