"""Listen-only capture of Pro DJ Link packets this process already received.

A capture is a single 5–60 s window written to a local ``.plc`` file. It does
not open a socket and does not transmit. ``probe.py --replay`` and
``read_capture`` both read the file back.

File layout, little-endian:

    Header (24 bytes):
        magic          4s   b"PLC1"
        version        u16  1
        header_len     u16  24
        started_unix   u64  milliseconds
        duration_s     u16  requested length
        flags          u16  bit 0 = truncated (hit the memory cap)
        reserved       u32

    Records, until EOF:
        offset_us      u64  microseconds since the capture started
        ipv4           u32  source address, network order
        src_port       u16  0 (the listener does not record it)
        dst_port       u16  Pro DJ Link port implied by the packet type
        length         u32
        payload        bytes
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

from . import proto

MAGIC = b"PLC1"
VERSION = 1
HEADER = struct.Struct("<4sHHQHHI")
RECORD = struct.Struct("<QIHHI")
HEADER_LEN = HEADER.size  # 24

MIN_DURATION_S = 5.0
MAX_DURATION_S = 60.0
MAX_PACKETS = 16_000
MAX_BYTES = 4_000_000
MAX_PAYLOAD = 4096

_FLAG_TRUNCATED = 0x0001


def clamp_duration(seconds: float) -> float:
    """Force a capture length into the supported 5–60 s range."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 10.0
    if value < MIN_DURATION_S:
        return MIN_DURATION_S
    if value > MAX_DURATION_S:
        return MAX_DURATION_S
    return value


def captures_dir() -> str:
    """Directory for diagnostic captures. Created on demand."""
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".prolink-monitor")
    path = os.path.join(base, "captures")
    os.makedirs(path, exist_ok=True)
    return path


def idle_capture_status() -> dict:
    return {
        "active": False,
        "saving": False,
        "seconds": 0,
        "remaining_s": 0.0,
        "packets": 0,
        "last_path": "",
        "last_packets": 0,
        "last_truncated": False,
        "error": "",
    }


def implied_port(data: bytes) -> int:
    """UDP port a Pro DJ Link packet of this type is carried on, or 0."""
    if len(data) < 11 or not data.startswith(proto.MAGIC):
        return 0
    kind = data[10]
    if kind == proto.TYPE_KEEPALIVE:
        return proto.PORT_ANNOUNCE
    if kind in (proto.TYPE_BEAT, proto.TYPE_ABSOLUTE_POSITION):
        return proto.PORT_BEAT
    if kind in (proto.TYPE_CDJ_STATUS, proto.TYPE_DJM_STATUS, proto.TYPE_MIXER_STATUS):
        return proto.PORT_STATUS
    return 0


@dataclass(frozen=True)
class CapturedPacket:
    offset_s: float
    ip: str
    src_port: int
    dst_port: int
    data: bytes


@dataclass
class CaptureFile:
    started_unix_ms: int
    duration_s: int
    truncated: bool
    packets: list[CapturedPacket] = field(default_factory=list)


def write_capture(path: str, packets: list[CapturedPacket], *,
                  started_unix_ms: int, duration_s: int, truncated: bool = False) -> None:
    """Write a capture atomically (temp file, then replace)."""
    flags = _FLAG_TRUNCATED if truncated else 0
    parts = [HEADER.pack(MAGIC, VERSION, HEADER_LEN, int(started_unix_ms),
                         int(duration_s) & 0xFFFF, flags, 0)]
    for pkt in packets:
        payload = pkt.data
        if len(payload) > MAX_PAYLOAD:
            payload = payload[:MAX_PAYLOAD]
        try:
            ip_raw = struct.unpack("!I", socket.inet_aton(pkt.ip or "0.0.0.0"))[0]
        except OSError:
            ip_raw = 0
        offset_us = max(0, int(round(pkt.offset_s * 1_000_000)))
        parts.append(RECORD.pack(offset_us, ip_raw, pkt.src_port & 0xFFFF,
                                 pkt.dst_port & 0xFFFF, len(payload)))
        parts.append(payload)
    blob = b"".join(parts)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_capture(path: str) -> CaptureFile:
    """Load a ``.plc`` file. Raises ValueError when the bytes are not one."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER_LEN:
        raise ValueError("capture file is too short")
    magic, version, header_len, started_ms, duration_s, flags, _reserved = HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise ValueError("not a prolink capture")
    if version != VERSION:
        raise ValueError(f"unsupported capture version {version}")
    if header_len < HEADER_LEN or header_len > len(blob):
        raise ValueError("bad capture header")
    off = header_len
    packets: list[CapturedPacket] = []
    while off < len(blob):
        if off + RECORD.size > len(blob):
            raise ValueError("truncated capture record")
        offset_us, ip_raw, src_port, dst_port, length = RECORD.unpack_from(blob, off)
        off += RECORD.size
        if length > MAX_PAYLOAD or off + length > len(blob):
            raise ValueError("truncated capture payload")
        payload = blob[off:off + length]
        off += length
        packets.append(CapturedPacket(
            offset_s=offset_us / 1_000_000,
            ip=socket.inet_ntoa(struct.pack("!I", ip_raw)),
            src_port=src_port,
            dst_port=dst_port,
            data=payload,
        ))
    return CaptureFile(
        started_unix_ms=int(started_ms),
        duration_s=int(duration_s),
        truncated=bool(flags & _FLAG_TRUNCATED),
        packets=packets,
    )


class ReplaySource:
    """Feed a capture into the same callback a live listener uses.

    With ``pace`` the packets arrive on a background thread at their original
    spacing, and nothing is put on the network. ``stop`` ends playback.
    """

    def __init__(self, path: str, pace: bool = True):
        self.path = path
        self.pace = pace
        self.capture = read_capture(path)
        self.kind = "replay"
        self.detail = os.path.basename(path)
        self.description = f"replay of {self.detail}"
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self, on_packet) -> None:
        self._running = True
        if self.pace:
            self._thread = threading.Thread(target=self._run, args=(on_packet,), daemon=True)
            self._thread.start()
        else:
            self._run(on_packet)

    def _run(self, on_packet) -> None:
        origin = time.monotonic()
        for pkt in self.capture.packets:
            if not self._running:
                return
            if self.pace and pkt.offset_s > 0:
                self._wait_until(origin + pkt.offset_s)
                if not self._running:
                    return
            on_packet(pkt.data, pkt.ip, time.monotonic())

    def _wait_until(self, deadline: float) -> None:
        while self._running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


@dataclass
class _Session:
    seconds: float
    directory: str
    t0: float
    started_unix: float
    seq: int
    records: list[CapturedPacket] = field(default_factory=list)
    nbytes: int = 0
    truncated: bool = False
    path: str = ""

    @property
    def deadline(self) -> float:
        return self.t0 + self.seconds


class PacketCapture:
    """Optional recorder hooked to packets the listener has already accepted.

    Inactive captures do nothing beyond a pointer check. While one is running,
    packets are kept in a fixed-size buffer and written once, when the window
    ends. This object never sends.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._session: _Session | None = None
        self._writes = 0
        self._seq = 0
        self._published = 0
        self._pending_packets = 0
        self._last = idle_capture_status()

    def start(self, seconds: float, directory: str | None = None) -> str:
        """Begin a capture. Returns '' or an i18n key when one is already running."""
        length = clamp_duration(seconds)
        with self._lock:
            if self._session is not None:
                return "health_capture_busy"
            self._seq += 1
            self._session = _Session(
                seconds=length,
                directory=directory or captures_dir(),
                t0=time.monotonic(),
                started_unix=time.time(),
                seq=self._seq,
            )
            self._last["error"] = ""
        return ""

    def observe(self, data: bytes, ip: str, now: float) -> None:
        """Keep one received datagram if a capture is open and the magic matches."""
        if self._session is None:
            return
        if len(data) < 11 or not data.startswith(proto.MAGIC):
            return
        finished = self._accept(data, ip, now)
        if finished is not None:
            self._write_async(finished)

    def status(self) -> dict:
        """Current capture, finishing the window if its clock has run out."""
        session = self._session
        if session is not None and time.monotonic() >= session.deadline:
            finished = self._expire(time.monotonic())
            if finished is not None:
                self._write_async(finished)
        return self._view()

    def close(self) -> dict:
        """End an open capture and write it on this thread. Used by tests."""
        with self._lock:
            session = self._session
            finished = self._detach(session) if session is not None else None
        if finished is not None:
            self._write(finished)
        return self._view()

    def _accept(self, data: bytes, ip: str, now: float) -> _Session | None:
        with self._lock:
            session = self._session
            if session is None:
                return None
            if now >= session.deadline:
                return self._detach(session)
            payload = bytes(data)
            if len(payload) > MAX_PAYLOAD:
                payload = payload[:MAX_PAYLOAD]
                session.truncated = True
            if (session.nbytes + len(payload) > MAX_BYTES
                    or len(session.records) >= MAX_PACKETS):
                session.truncated = True
                return self._detach(session)
            session.records.append(CapturedPacket(
                offset_s=max(0.0, now - session.t0),
                ip=ip or "0.0.0.0",
                src_port=0,
                dst_port=implied_port(payload),
                data=payload,
            ))
            session.nbytes += len(payload)
            return None

    def _expire(self, now: float) -> _Session | None:
        with self._lock:
            session = self._session
            if session is None or now < session.deadline:
                return None
            return self._detach(session)

    def _view(self) -> dict:
        with self._lock:
            out = dict(self._last)
            out["saving"] = self._writes > 0
            current = self._session
            if current is None:
                out["active"] = False
                out["remaining_s"] = 0.0
                if self._writes > 0:
                    out["packets"] = self._pending_packets
                else:
                    out["seconds"] = 0
                    out["packets"] = out["last_packets"] if out["last_path"] else 0
                return out
            remaining = max(0.0, current.deadline - time.monotonic())
            out.update({
                "active": True,
                "seconds": current.seconds,
                "remaining_s": round(remaining, 1),
                "packets": len(current.records),
            })
            return out

    def _detach(self, session: _Session) -> _Session:
        """Caller holds ``_lock``. Unhooks the session and marks a save in progress."""
        self._session = None
        self._writes += 1
        self._pending_packets = len(session.records)
        if not session.path:
            session.path = _capture_path(session.directory, session.started_unix)
        return session

    def _write_async(self, session: _Session) -> None:
        threading.Thread(target=self._write, args=(session,), daemon=True).start()

    def _write(self, session: _Session) -> None:
        result = {
            "last_path": "",
            "last_packets": 0,
            "last_truncated": False,
            "error": "",
        }
        try:
            write_capture(
                session.path,
                session.records,
                started_unix_ms=int(session.started_unix * 1000),
                duration_s=int(round(session.seconds)),
                truncated=session.truncated,
            )
            result = {
                "last_path": session.path,
                "last_packets": len(session.records),
                "last_truncated": session.truncated,
                "error": "",
            }
        except OSError as exc:
            result["error"] = str(exc)
        finally:
            with self._lock:
                self._writes = max(0, self._writes - 1)
                if session.seq >= self._published:
                    self._last.update(result)
                    self._published = session.seq


def _capture_path(directory: str, started_unix: float) -> str:
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_unix))
    base = os.path.join(directory, f"prolink-{stamp}.plc")
    if not os.path.exists(base):
        return base
    n = 2
    while True:
        cand = os.path.join(directory, f"prolink-{stamp}-{n}.plc")
        if not os.path.exists(cand):
            return cand
        n += 1
