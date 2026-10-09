"""Read embedded cover art from the tag header only.

Adapted from chrisle/alphatheta-connect (MIT): NFS artwork comes from the
ID3 / FLAC / MP4 metadata blocks, via ranged reads, not by downloading the
audio. ``read(offset, length)`` must return that slice and nothing past it.
"""

from __future__ import annotations

import struct
from typing import Callable

from .embedded_art import extract_embedded_cover

ReadFn = Callable[[int, int], bytes | None]

# A tag header larger than this is not a cover we will pull in one go.
_MAX_TAG = 512 * 1024
_MAX_STEPS = 64


def _take(read: ReadFn, offset: int, length: int) -> bytes:
    if length <= 0:
        return b""
    data = read(int(offset), int(length))
    return data or b""


def _syncsafe(raw: bytes) -> int:
    if len(raw) != 4:
        return 0
    return (
        ((raw[0] & 0x7F) << 21)
        | ((raw[1] & 0x7F) << 14)
        | ((raw[2] & 0x7F) << 7)
        | (raw[3] & 0x7F)
    )


def cover_from_ranges(read: ReadFn, size: int, hint: str = "") -> bytes | None:
    """Return image bytes, having asked ``read`` only for metadata ranges."""
    size = max(0, int(size or 0))
    head = _take(read, 0, 12)
    if len(head) < 4:
        return None
    low = (hint or "").lower()
    if head.startswith(b"ID3") or low.endswith((".mp3", ".aiff", ".aif", ".wav")):
        found = _id3(read, size, head)
        if found:
            return found
    if head.startswith(b"fLaC") or low.endswith(".flac"):
        found = _flac(read, size)
        if found:
            return found
    if (len(head) >= 8 and head[4:8] == b"ftyp") or low.endswith((".m4a", ".mp4", ".aac")):
        return _mp4(read, size)
    return None


def _id3(read: ReadFn, size: int, head: bytes) -> bytes | None:
    if len(head) < 10 or not head.startswith(b"ID3"):
        head = _take(read, 0, 10)
    if len(head) < 10 or not head.startswith(b"ID3"):
        return None
    tag_size = _syncsafe(head[6:10])
    if tag_size <= 0:
        return None
    total = min(10 + tag_size, _MAX_TAG, size or 10 + tag_size)
    blob = _take(read, 0, total)
    return extract_embedded_cover(blob, hint=".mp3")


def _flac(read: ReadFn, size: int) -> bytes | None:
    """Walk metadata blocks. Skip audio; stop at the picture or the last block."""
    pos = 4
    for _ in range(_MAX_STEPS):
        if size and pos >= size:
            break
        hdr = _take(read, pos, 4)
        if len(hdr) < 4:
            break
        header = struct.unpack(">I", hdr)[0]
        block_type = (header >> 24) & 0x7F
        last = (header >> 24) & 0x80
        length = header & 0x00FFFFFF
        if length > _MAX_TAG:
            break
        if block_type == 6:
            block = _take(read, pos, 4 + length)
            return extract_embedded_cover(b"fLaC" + block, hint=".flac")
        pos += 4 + length
        if last:
            break
    return None


def _mp4(read: ReadFn, size: int) -> bytes | None:
    """Walk atoms from the start. Skip ``mdat`` without reading its payload."""
    pos = 0
    limit = size or _MAX_TAG
    for _ in range(_MAX_STEPS):
        if pos + 8 > limit:
            break
        hdr = _take(read, pos, 8)
        if len(hdr) < 8:
            break
        atom_size = struct.unpack(">I", hdr[:4])[0]
        kind = hdr[4:8]
        header_len = 8
        if atom_size == 1:
            ext = _take(read, pos + 8, 8)
            if len(ext) < 8:
                break
            atom_size = struct.unpack(">Q", ext)[0]
            header_len = 16
        if atom_size < header_len:
            break
        if kind == b"mdat":
            pos += atom_size
            continue
        if kind in (b"moov", b"udta", b"ilst", b"meta"):
            # Descend into the container. ``meta`` has a 4-byte version after the header.
            pos += header_len + (4 if kind == b"meta" else 0)
            continue
        if kind == b"covr":
            blob = _take(read, pos, min(atom_size, _MAX_TAG))
            if len(blob) > 16:
                return blob[16:]
            return None
        pos += atom_size
    return None
