"""Extract embedded cover art from audio files (MIT — no GPL dependencies).

Supports common DJ formats: ID3 APIC (MP3/AIFF), FLAC METADATA_BLOCK_PICTURE.
"""

from __future__ import annotations

import struct


def extract_embedded_cover(data: bytes, *, hint: str = "") -> bytes | None:
    """Return raw image bytes from *data*, or None."""
    if len(data) < 12:
        return None
    head = data[:12]
    if head.startswith(b"ID3"):
        return _id3_apic(data)
    if head.startswith(b"fLaC"):
        return _flac_picture(data)
    if len(data) > 8 and data[4:8] == b"ftyp":
        return _mp4_covr(data)
    low = (hint or "").lower()
    if low.endswith((".mp3", ".aiff", ".aif", ".wav")):
        found = _id3_apic(data)
        if found:
            return found
    if low.endswith(".flac"):
        return _flac_picture(data)
    if low.endswith((".m4a", ".mp4", ".aac")):
        return _mp4_covr(data)
    return None


def _id3_apic(data: bytes) -> bytes | None:
    if len(data) < 10 or not data.startswith(b"ID3"):
        return None
    ver = data[3]
    flags = data[5]
    tag_size = _syncsafe(data[6:10])
    pos = 10
    end = min(len(data), 10 + tag_size)
    while pos + 10 <= end:
        frame_id = data[pos:pos + 4].decode("ascii", errors="replace")
        if ver == 4:
            frame_size = _syncsafe(data[pos + 4:pos + 8])
            header = 10
        else:
            frame_size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            header = 10
        frame_start = pos + header
        frame_end = frame_start + frame_size
        if frame_end > len(data):
            break
        if frame_id in ("APIC", "PIC "):
            payload = data[frame_start:frame_end]
            img = _parse_apic_payload(payload, v24=(ver != 2))
            if img:
                return img
        pos = frame_end
        if flags & 0x40 and ver == 3:
            break
    return None


def _parse_apic_payload(payload: bytes, *, v24: bool) -> bytes | None:
    if len(payload) < 5:
        return None
    i = 0
    enc = payload[i]
    i += 1
    while i < len(payload) and payload[i] != 0:
        i += 1
    i += 1  # mime / image format NUL
    if not v24:
        while i < len(payload) and payload[i] != 0:
            i += 1
        i += 1
    i += 1  # picture type
    if enc == 0:
        while i < len(payload) and payload[i] != 0:
            i += 1
        i += 1
    elif enc in (1, 2):
        while i + 1 < len(payload):
            if payload[i] == 0 and payload[i + 1] == 0:
                i += 2
                break
            i += 2
    if i >= len(payload):
        return None
    return payload[i:]


def _flac_picture(data: bytes) -> bytes | None:
    if not data.startswith(b"fLaC") or len(data) < 8:
        return None
    pos = 4
    while pos + 4 <= len(data):
        header = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        block_type = (header >> 24) & 0x7F
        length = header & 0x00FFFFFF
        block = data[pos:pos + length]
        pos += length
        if block_type == 6 and len(block) > 32:
            # picture type(4) + mime len(4) + mime + desc len(4) + desc + dims + data len(4)
            p = 4
            mime_len = struct.unpack(">I", block[p:p + 4])[0]
            p += 4 + mime_len
            desc_len = struct.unpack(">I", block[p:p + 4])[0]
            p += 4 + desc_len + 16
            if p + 4 <= len(block):
                data_len = struct.unpack(">I", block[p:p + 4])[0]
                p += 4
                if data_len > 0 and p + data_len <= len(block):
                    return block[p:p + data_len]
        if block_type == 127:
            break
    return None


def _mp4_covr(data: bytes) -> bytes | None:
    """Best-effort covr atom scan (MP4/M4A)."""
    needle = b"covr"
    idx = 0
    while True:
        j = data.find(needle, idx)
        if j < 0 or j + 8 > len(data):
            return None
        # atom size is 4 bytes before type at j-4
        if j < 4:
            idx = j + 4
            continue
        size = struct.unpack(">I", data[j - 4:j])[0]
        if size < 16 or j - 4 + size > len(data):
            idx = j + 4
            continue
        atom = data[j - 4:j - 4 + size]
        # skip size(4) type(4) version+flags(4)
        payload = atom[12:]
        if len(payload) > 8:
            return payload
        idx = j + 4


def _syncsafe(raw: bytes) -> int:
    if len(raw) != 4:
        return 0
    return (
        ((raw[0] & 0x7F) << 21)
        | ((raw[1] & 0x7F) << 14)
        | ((raw[2] & 0x7F) << 7)
        | (raw[3] & 0x7F)
    )
