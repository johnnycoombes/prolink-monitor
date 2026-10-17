"""On-demand hi-res cover art read from the audio file itself (read-only).

The player's dbserver art and the rekordbox ``_m`` thumbnail top out around
240-500 px. The cover embedded in the audio file is usually the original
(1000 px or more). This module pulls only the tag/metadata region over NFS with
ranged reads (never the audio), parses it with mutagen, and caches the result
in memory and on disk. Loads run in a small background pool so the UI keeps the
player art until the larger image is ready.

Nothing is ever written to the player or the USB.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import struct
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_log = logging.getLogger(__name__)

ReadFn = Callable[[int, int], "bytes | None"]

DEFAULT_CAP_MB = 16
MIN_CAP_MB = 1
MAX_CAP_MB = 64
_MAX_STEPS = 256


def clamp_cap_mb(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = DEFAULT_CAP_MB
    return max(MIN_CAP_MB, min(MAX_CAP_MB, v))


def _take(read: ReadFn, offset: int, length: int) -> bytes:
    if length <= 0:
        return b""
    return read(int(offset), int(length)) or b""


def _syncsafe(raw: bytes) -> int:
    if len(raw) != 4:
        return 0
    return ((raw[0] & 0x7F) << 21) | ((raw[1] & 0x7F) << 14) | ((raw[2] & 0x7F) << 7) | (raw[3] & 0x7F)


# -- gather the metadata region with ranged reads ----------------------------

def gather_metadata(read: ReadFn, size: int, cap: int, hint: str = "") -> tuple[str, bytes] | None:
    """Return (kind, bytes) holding just the tags, or None. Never reads past ``cap``."""
    size = max(0, int(size or 0))
    head = _take(read, 0, 12)
    if len(head) < 12:
        return None
    if head.startswith(b"ID3"):
        total = 10 + _syncsafe(head[6:10])
        if head[5] & 0x10:
            total += 10  # footer
        if total > cap:
            return None
        return "id3", _take(read, 0, total)
    if head.startswith(b"fLaC"):
        return _flac(read, size, cap)
    if head[4:8] == b"ftyp":
        return _mp4(read, size, cap)
    if head.startswith((b"RIFF", b"FORM")):
        return _chunked(read, size, cap, big=head.startswith(b"FORM"))
    return None


def _flac(read: ReadFn, size: int, cap: int):
    pos, used = 4, 4
    for _ in range(_MAX_STEPS):
        hdr = _take(read, pos, 4)
        if len(hdr) < 4:
            break
        last = hdr[0] & 0x80
        length = int.from_bytes(hdr[1:4], "big")
        pos += 4 + length
        used = pos
        if used > cap or (size and pos > size):
            return None
        if last:
            break
    return "flac", _take(read, 0, used)


def _mp4(read: ReadFn, size: int, cap: int):
    """Top-level atoms: keep ftyp + moov, skip mdat (wherever moov sits)."""
    pos = 0
    keep: list[bytes] = []
    limit = size or (1 << 62)
    for _ in range(_MAX_STEPS):
        if pos + 8 > limit:
            break
        hdr = _take(read, pos, 16)
        if len(hdr) < 8:
            break
        atom = struct.unpack(">I", hdr[:4])[0]
        kind = hdr[4:8]
        if atom == 1 and len(hdr) >= 16:
            atom = struct.unpack(">Q", hdr[8:16])[0]
        elif atom == 0:
            atom = limit - pos
        if atom < 8:
            break
        if kind in (b"ftyp", b"moov"):
            if atom > cap:
                return None
            keep.append(_take(read, pos, atom))
            if kind == b"moov":
                return "mp4", b"".join(keep)
        pos += atom
    return None


def _chunked(read: ReadFn, size: int, cap: int, *, big: bool):
    """WAV (RIFF) / AIFF (FORM): find the ``id3 `` chunk."""
    fmt = ">I" if big else "<I"
    pos = 12
    limit = size or (1 << 62)
    for _ in range(_MAX_STEPS):
        if pos + 8 > limit:
            break
        hdr = _take(read, pos, 8)
        if len(hdr) < 8:
            break
        cid = hdr[:4]
        length = struct.unpack(fmt, hdr[4:8])[0]
        if cid.lower() == b"id3 ":
            if length > cap:
                return None
            return "id3", _take(read, pos + 8, length)
        pos += 8 + length + (length & 1)
    return None


# -- parse with mutagen --------------------------------------------------------

def _best(pictures: list[tuple[int, bytes]]) -> bytes | None:
    """Front cover (type 3) first, then the largest image."""
    pictures = [(t, d) for t, d in pictures if d]
    if not pictures:
        return None
    pictures.sort(key=lambda p: (p[0] == 3, len(p[1])), reverse=True)
    return bytes(pictures[0][1])


def cover_from_metadata(kind: str, blob: bytes) -> bytes | None:
    if not blob:
        return None
    try:
        if kind == "id3":
            from mutagen.id3 import ID3

            tags = ID3(io.BytesIO(blob))
            return _best([(int(getattr(f, "type", 0)), f.data) for f in tags.getall("APIC")])
        if kind == "flac":
            from mutagen.flac import Picture

            # Parse PICTURE blocks directly; no audio frames are needed.
            pics, pos = [], 4
            while pos + 4 <= len(blob):
                last = blob[pos] & 0x80
                btype = blob[pos] & 0x7F
                length = int.from_bytes(blob[pos + 1:pos + 4], "big")
                if btype == 6:
                    pic = Picture(blob[pos + 4:pos + 4 + length])
                    pics.append((int(pic.type), pic.data))
                pos += 4 + length
                if last:
                    break
            return _best(pics)
        if kind == "mp4":
            from mutagen.mp4 import MP4

            audio = MP4(io.BytesIO(blob))
            covers = (audio.tags or {}).get("covr") or []
            return _best([(3, bytes(c)) for c in covers])
    except Exception as exc:
        _log.debug("mutagen cover parse failed (%s): %s", kind, exc)
    return None


def read_cover(read: ReadFn, size: int, *, cap_mb: int = DEFAULT_CAP_MB, hint: str = "") -> bytes | None:
    found = gather_metadata(read, size, clamp_cap_mb(cap_mb) * 1024 * 1024, hint)
    if not found:
        return None
    return cover_from_metadata(*found)


# -- cache + async loader -------------------------------------------------------

class HiResArtCache:
    """Memory LRU + disk cache with a small background loader."""

    def __init__(self, cache_dir: str | None, *, memory_items: int = 48, workers: int = 2):
        self.cache_dir = os.path.join(cache_dir, "hires-art") if cache_dir else ""
        self._mem: OrderedDict[str, bytes] = OrderedDict()
        self._miss: set[str] = set()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._max = int(memory_items)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hires-art")

    @staticmethod
    def key(host: str, remote: str, size: int = 0) -> str:
        return hashlib.sha1(f"{host}|{remote}|{int(size)}".encode("utf-8", "replace")).hexdigest()

    def _disk(self, key: str) -> str:
        return os.path.join(self.cache_dir, key + ".jpg") if self.cache_dir else ""

    def get(self, key: str) -> bytes | None:
        with self._lock:
            hit = self._mem.get(key)
            if hit is not None:
                self._mem.move_to_end(key)
                return hit
        path = self._disk(key)
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                return None
            if data:
                self._remember(key, data)
                return data
        return None

    def known_missing(self, key: str) -> bool:
        with self._lock:
            return key in self._miss

    def _remember(self, key: str, data: bytes) -> None:
        with self._lock:
            self._mem[key] = data
            self._mem.move_to_end(key)
            while len(self._mem) > self._max:
                self._mem.popitem(last=False)

    def put(self, key: str, data: bytes) -> None:
        self._remember(key, data)
        path = self._disk(key)
        if not path:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            pass

    def request(self, key: str, loader: Callable[[], "bytes | None"],
                on_done: Callable[[bytes], None] | None = None) -> bool:
        """Start a background load unless cached, failed before, or in flight."""
        with self._lock:
            if key in self._mem or key in self._miss or key in self._pending:
                return False
            self._pending.add(key)

        def job() -> None:
            data = None
            try:
                data = loader()
            except Exception as exc:
                _log.debug("hi-res art load failed: %s", exc)
            with self._lock:
                self._pending.discard(key)
                if not data:
                    self._miss.add(key)
            if data:
                self.put(key, data)
                if on_done is not None:
                    try:
                        on_done(data)
                    except Exception:
                        pass

        self._pool.submit(job)
        return True

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
