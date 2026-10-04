"""Rolling status-packet timing for the Health page.

Tracks each device that sends CDJ status (type 0x0A) over a fixed window:
how long since the last packet, the average gap, jitter, the largest gap,
and how many gaps were late. Memory is capped per device and across devices.

Sequence holes use the 4-byte counter at bytes 0xC8–0xCB. Public captures
show that field incrementing on older Nexus players and staying at zero on
CDJ-3000-class players. It is treated as a real counter only after it steps
by one several times in a row; otherwise holes stay unknown.
"""

from __future__ import annotations

import struct
import threading
from collections import deque
from dataclasses import dataclass, field

from . import proto

# Classic CDJ status layout: a big-endian packet counter sits just after the
# fourth pitch field. Shorter packets do not carry it.
STATUS_COUNTER_OFFSET = 0xC8
STATUS_COUNTER_END = 0xCC

# Status is about 5–6 Hz (~170–200 ms). A gap past this is late: normal
# arrival jitter on this protocol stays under ~150 ms, so 400 ms is a stall
# or a missed packet rather than ordinary delay.
LATE_GAP_S = 0.400

WINDOW_S = 30.0
MAX_SAMPLES = 192
MAX_DEVICES = 16
STALE_S = 60.0

# +1 steps required before the 0xC8 field is trusted. A live counter does
# this continuously; a stuck zero or an unrelated field does not.
STEPS_TO_TRUST = 3
# Identical values in a row retire a counter that has stopped moving.
FROZEN_LIMIT = 8
# Larger jumps are a reset (or not a counter), not a pile of holes.
HOLE_MAX_JUMP = 256


def status_counter(data: bytes) -> int | None:
    """Return the status-packet counter, or None when the packet has none."""
    if len(data) < STATUS_COUNTER_END or not data.startswith(proto.MAGIC):
        return None
    if data[10] != proto.TYPE_CDJ_STATUS:
        return None
    return struct.unpack_from(">I", data, STATUS_COUNTER_OFFSET)[0]


def _delta(prev: int, seq: int) -> int:
    return (seq - prev) & 0xFFFFFFFF


@dataclass
class _Device:
    number: int
    name: str = ""
    ip: str = ""
    last_t: float = 0.0
    samples: deque = field(default_factory=deque)
    last_seq: int | None = None
    step_run: int = 0
    frozen: int = 0
    seq_usable: bool = False


class LinkTiming:
    """Bounded per-device status timing. Safe to call from the packet thread."""

    def __init__(self, window_s: float = WINDOW_S, max_samples: int = MAX_SAMPLES,
                 max_devices: int = MAX_DEVICES, stale_s: float = STALE_S,
                 late_gap_s: float = LATE_GAP_S):
        self.window_s = float(window_s)
        self.max_samples = int(max_samples)
        self.max_devices = int(max_devices)
        self.stale_s = float(stale_s)
        self.late_gap_s = float(late_gap_s)
        self._lock = threading.Lock()
        self._devices: dict[int, _Device] = {}

    def observe(self, device: int, now: float, data: bytes,
                ip: str = "", name: str = "") -> None:
        """Record one received status packet. `now` is the arrival time."""
        seq = status_counter(data)
        with self._lock:
            dev = self._devices.get(int(device))
            if dev is None:
                dev = _Device(number=int(device))
                self._devices[int(device)] = dev
            if name:
                dev.name = name
            if ip:
                dev.ip = ip
            dev.last_t = float(now)
            self._note_seq(dev, seq)
            stored = seq if dev.seq_usable else None
            dev.samples.append((float(now), stored))
            self._trim(dev, float(now))
            self._evict(float(now))

    def snapshot(self, now: float) -> list[dict]:
        """One row per device heard inside the stale horizon, oldest gap last."""
        with self._lock:
            self._evict(float(now))
            rows = [self._row(dev, float(now)) for dev in self._devices.values()]
        rows.sort(key=lambda row: row["device"])
        return rows

    def _note_seq(self, dev: _Device, seq: int | None) -> None:
        if seq is None:
            return
        if dev.last_seq is None:
            dev.last_seq = seq
            return
        delta = _delta(dev.last_seq, seq)
        if delta == 1:
            dev.step_run += 1
            dev.frozen = 0
            if dev.step_run >= STEPS_TO_TRUST:
                dev.seq_usable = True
        elif delta == 0:
            dev.frozen += 1
            dev.step_run = 0
            if dev.frozen >= FROZEN_LIMIT:
                dev.seq_usable = False
        else:
            dev.step_run = 0
            dev.frozen = 0
        dev.last_seq = seq

    def _trim(self, dev: _Device, now: float) -> None:
        cutoff = now - self.window_s
        samples = dev.samples
        while samples and samples[0][0] < cutoff:
            samples.popleft()
        while len(samples) > self.max_samples:
            samples.popleft()

    def _evict(self, now: float) -> None:
        stale = [num for num, dev in self._devices.items()
                 if now - dev.last_t > self.stale_s]
        for num in stale:
            del self._devices[num]
        while len(self._devices) > self.max_devices:
            oldest = min(self._devices.values(), key=lambda dev: dev.last_t)
            del self._devices[oldest.number]

    def _row(self, dev: _Device, now: float) -> dict:
        self._trim(dev, now)
        times = [sample[0] for sample in dev.samples]
        intervals: list[float] = []
        for earlier, later in zip(times, times[1:]):
            gap = later - earlier
            if gap > 0:
                intervals.append(gap)
        age = max(0.0, now - dev.last_t) if dev.last_t else None
        interval = (sum(intervals) / len(intervals)) if intervals else None
        jitter = _pstdev(intervals) if len(intervals) >= 2 else None
        max_gap = max(intervals) if intervals else None
        late = sum(1 for gap in intervals if gap > self.late_gap_s)
        holes: int | None = None
        if dev.seq_usable:
            holes = 0
            prev: int | None = None
            for _t, seq in dev.samples:
                if seq is None:
                    prev = None
                    continue
                if prev is not None:
                    delta = _delta(prev, seq)
                    if 1 < delta <= HOLE_MAX_JUMP:
                        holes += delta - 1
                prev = seq
        return {
            "device": dev.number,
            "name": dev.name,
            "ip": dev.ip,
            "age_ms": _ms(age),
            "interval_ms": _ms(interval),
            "jitter_ms": _ms(jitter),
            "max_gap_ms": _ms(max_gap),
            "late": late,
            "seq_holes": holes,
            "seq_counter": "live" if dev.seq_usable else "unused",
            "samples": len(dev.samples),
        }


def _ms(seconds: float | None) -> float | None:
    if seconds is None:
        return None
    return round(float(seconds) * 1000.0, 1)


def _pstdev(values: list[float]) -> float:
    n = len(values)
    mean = sum(values) / n
    var = sum((value - mean) ** 2 for value in values) / n
    return var ** 0.5
