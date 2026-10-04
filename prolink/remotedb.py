"""Pioneer dbserver (RemoteDB) wire format — read-only TCP client helpers.

Protocol described at https://djl-analysis.deepsymmetry.org/djl-analysis/track_metadata.html
Independent Python implementation. Queries only; nothing here writes to the player
or to a rekordbox database.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO, Iterable

MAGIC = 0x872349AE
SETUP_TX = 0xFFFFFFFE
GREETING = 1

REMOTE_DB_DISCOVERY_PORT = 12523
REMOTE_DB_QUERY = b"\x00\x00\x00\x0fRemoteDBServer\x00"

# Message types (subset used for library browse)
TYPE_SETUP = 0x0000
TYPE_ROOT_MENU = 0x1000
TYPE_HISTORY_MENU = 0x1012
TYPE_PLAYLIST = 0x1105
TYPE_TRACK_MENU = 0x1004
TYPE_REKORDBOX_METADATA = 0x2002
TYPE_GENERIC_METADATA = 0x2202   # unanalyzed files and audio CDs
TYPE_ALBUM_ART = 0x2003
TYPE_RENDER_MENU = 0x3000
TYPE_ALBUM_ART_RESP = 0x4002
TYPE_MENU_AVAILABLE = 0x4000
TYPE_MENU_HEADER = 0x4001
TYPE_MENU_ITEM = 0x4101
TYPE_MENU_FOOTER = 0x4201

MENU_MAIN = 1
MENU_DATA = 8
TRACK_REKORDBOX = 1
TRACK_UNANALYZED = 2
TRACK_CD = 5
TRACK_STREAMING = 6
SLOT_CD = 1
SLOT_SD = 2
SLOT_USB = 3
SLOT_REKORDBOX = 4
SLOT_DIRECT_PLAY = 6
SLOT_USB2 = 7
SLOT_BEATPORT = 9

# Sr values that address a mounted library (not a stream or a laptop collection).
LIBRARY_SLOTS = (SLOT_SD, SLOT_USB, SLOT_USB2)

_SLOT_BY_NAME = {
    "cd": SLOT_CD,
    "sd": SLOT_SD,
    "usb": SLOT_USB,
    "device": SLOT_USB,
    "rekordbox": SLOT_REKORDBOX,
    "direct_play": SLOT_DIRECT_PLAY,
    "usb2": SLOT_USB2,
    "beatport": SLOT_BEATPORT,
}

_TRACK_TYPE_BY_NAME = {
    "rekordbox": TRACK_REKORDBOX,
    "file": TRACK_UNANALYZED,
    "cd": TRACK_CD,
    "streaming": TRACK_STREAMING,
}

MENU_BATCH = 64

# Menu item types (7th argument of 0x4101)
ITEM_FOLDER = 0x0001
ITEM_PLAYLIST = 0x0008
ITEM_HISTORY = 0x0024


class DbServerError(Exception):
    """Remote dbserver returned an unexpected message or failed to connect."""


class KnownType(IntEnum):
    SETUP_REQ = TYPE_SETUP
    ROOT_MENU_REQ = TYPE_ROOT_MENU
    HISTORY_MENU_REQ = TYPE_HISTORY_MENU
    PLAYLIST_REQ = TYPE_PLAYLIST
    TRACK_MENU_REQ = TYPE_TRACK_MENU
    RENDER_MENU_REQ = TYPE_RENDER_MENU
    MENU_AVAILABLE = TYPE_MENU_AVAILABLE
    MENU_HEADER = TYPE_MENU_HEADER
    MENU_ITEM = TYPE_MENU_ITEM
    MENU_FOOTER = TYPE_MENU_FOOTER


TAG_NUM = 0x06
TAG_STR = 0x02
TAG_BLOB = 0x03


def discover_db_port(host: str, *, timeout: float = 3.0) -> int:
    """Query TCP 12523 for the player's dbserver port (usually 1051)."""
    with socket.create_connection((host, REMOTE_DB_DISCOVERY_PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(REMOTE_DB_QUERY)
        reply = _read_exact(sock, 2)
    port = (reply[0] << 8) | reply[1]
    if port <= 0 or port >= 65535:
        raise DbServerError(f"invalid dbserver port {port:#x} from {host}")
    return port


def slot_byte(name: str, raw: int = 0) -> int:
    """Dbserver Sr byte for a parsed slot name, falling back to the raw status byte."""
    if name in _SLOT_BY_NAME:
        return _SLOT_BY_NAME[name]
    raw_b = int(raw or 0) & 0xFF
    if name == "streaming" and raw_b:
        return raw_b
    if name in ("unknown", "") and raw_b:
        return raw_b
    return 0


def track_type_byte(name: str, raw: int = 0) -> int:
    """Dbserver Tr byte. Unknown with no raw byte stays the rekordbox type."""
    if name in _TRACK_TYPE_BY_NAME:
        return _TRACK_TYPE_BY_NAME[name]
    raw_b = int(raw or 0) & 0xFF
    if raw_b:
        return raw_b
    return TRACK_REKORDBOX


def metadata_message_type(track_type: int) -> int:
    """0x2002 for rekordbox and streaming; 0x2202 for unanalyzed files and CDs.

    Streaming (Tr 0x06, including Beatport) answers the standard metadata query.
    """
    if int(track_type) in (TRACK_UNANALYZED, TRACK_CD):
        return TYPE_GENERIC_METADATA
    return TYPE_REKORDBOX_METADATA


def build_rmst(
    requesting_player: int,
    menu: int,
    slot: int,
    track_type: int = TRACK_REKORDBOX,
) -> int:
    """Pack R:M:S:T into a 32-bit big-endian integer (first menu argument)."""
    return (
        ((requesting_player & 0xFF) << 24)
        | ((menu & 0xFF) << 16)
        | ((slot & 0xFF) << 8)
        | (track_type & 0xFF)
    )


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise DbServerError("unexpected EOF on dbserver socket")
        buf.extend(chunk)
    return bytes(buf)


def _num_field(value: int, size: int) -> bytes:
    if size == 1:
        tag = 0x0F
        payload = struct.pack(">B", value & 0xFF)
    elif size == 2:
        tag = 0x10
        payload = struct.pack(">H", value & 0xFFFF)
    elif size == 4:
        tag = 0x11
        payload = struct.pack(">I", value & 0xFFFFFFFF)
    else:
        raise ValueError(f"unsupported number field size {size}")
    return bytes([tag]) + payload


def _blob_field(data: bytes) -> bytes:
    return bytes([0x14]) + struct.pack(">I", len(data)) + data


def _str_field(text: str) -> bytes:
    encoded = text.encode("utf-16-be") + b"\x00\x00"
    return bytes([0x26]) + struct.pack(">I", len(encoded) // 2) + encoded


def encode_number_arg(value: int) -> bytes:
    return _num_field(value, 4)


def encode_message(
    tx_id: int,
    msg_type: int,
    args: Iterable[tuple[int, bytes]],
) -> bytes:
    """Build a full dbserver message. *args* is (argument_tag, encoded_field) pairs."""
    arg_list = list(args)
    if len(arg_list) > 12:
        raise ValueError("dbserver messages allow at most 12 arguments")
    tags = bytes(a[0] for a in arg_list) + bytes(12 - len(arg_list))
    parts = [
        _num_field(MAGIC, 4),
        _num_field(tx_id, 4),
        _num_field(msg_type, 2),
        _num_field(len(arg_list), 1),
        _blob_field(tags),
    ]
    for _, field_bytes in arg_list:
        parts.append(field_bytes)
    return b"".join(parts)


def decode_field(stream: BinaryIO) -> tuple[int, object]:
    """Read one typed field; return (argument_tag, value)."""
    tag = stream.read(1)
    if not tag:
        raise DbServerError("EOF reading field tag")
    t = tag[0]
    if t == 0x0F:
        val = struct.unpack(">B", _read_stream(stream, 1))[0]
        return TAG_NUM, val
    if t == 0x10:
        val = struct.unpack(">H", _read_stream(stream, 2))[0]
        return TAG_NUM, val
    if t == 0x11:
        val = struct.unpack(">I", _read_stream(stream, 4))[0]
        return TAG_NUM, val
    if t == 0x14:
        length = struct.unpack(">I", _read_stream(stream, 4))[0]
        data = _read_stream(stream, length) if length else b""
        return TAG_BLOB, data
    if t == 0x26:
        char_len = struct.unpack(">I", _read_stream(stream, 4))[0]
        raw = _read_stream(stream, char_len * 2) if char_len else b""
        text = raw.decode("utf-16-be", errors="replace").rstrip("\x00")
        return TAG_STR, text
    raise DbServerError(f"unknown field tag {t:#x}")


def _read_stream(stream: BinaryIO, n: int) -> bytes:
    read = getattr(stream, "read")
    data = read(n)
    if len(data) != n:
        raise DbServerError("truncated dbserver field")
    return data


@dataclass
class DbMessage:
    tx_id: int
    msg_type: int
    args: list[object]

    @property
    def known_type(self) -> KnownType | None:
        try:
            return KnownType(self.msg_type)
        except ValueError:
            return None


class _SocketReader:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, n: int) -> bytes:
        return _read_exact(self._sock, n)


def read_message(sock: socket.socket) -> DbMessage:
    """Read exactly one dbserver message from *sock*."""
    return _decode_from_stream(_SocketReader(sock))


def decode_message(data: bytes) -> DbMessage:
    """Parse one message from an in-memory buffer (for unit tests)."""
    import io

    return _decode_from_stream(io.BytesIO(data))


def _decode_from_stream(reader: BinaryIO) -> DbMessage:
    start_tag, start_val = decode_field(reader)
    if start_tag != TAG_NUM or start_val != MAGIC:
        raise DbServerError("missing message magic")
    _, tx_id = decode_field(reader)
    _, msg_type = decode_field(reader)
    _, arg_count = decode_field(reader)
    tag_blob_tag, tag_blob = decode_field(reader)
    if tag_blob_tag != TAG_BLOB or len(tag_blob) < arg_count:
        raise DbServerError("bad argument tag blob")
    arg_tags = list(tag_blob[:arg_count])
    args: list[object] = []
    last_num = 0
    for atag in arg_tags:
        if atag == TAG_BLOB and last_num == 0:
            args.append(b"")
            continue
        ftag, val = decode_field(reader)
        if ftag != atag:
            raise DbServerError(f"argument tag mismatch expected {atag} got {ftag}")
        args.append(val)
        last_num = int(val) if ftag == TAG_NUM else 0
    return DbMessage(int(tx_id), int(msg_type), args)


def menu_item_row(msg: DbMessage) -> dict[str, object]:
    """Turn a 0x4101 menu item into a plain dict."""
    if msg.msg_type != TYPE_MENU_ITEM or len(msg.args) < 7:
        return {}
    parent_id = int(msg.args[0]) if isinstance(msg.args[0], int) else 0
    item_id = int(msg.args[1]) if isinstance(msg.args[1], int) else 0
    label1 = msg.args[3] if len(msg.args) > 3 and isinstance(msg.args[3], str) else ""
    label2 = msg.args[5] if len(msg.args) > 5 and isinstance(msg.args[5], str) else ""
    item_type = int(msg.args[6]) if isinstance(msg.args[6], int) else 0
    folder = item_type == ITEM_FOLDER
    playlist = item_type == ITEM_PLAYLIST
    history = item_type == ITEM_HISTORY
    name = label1 or label2
    subtitle = label2 if label1 else ""
    return {
        "id": item_id,
        "parent_id": parent_id,
        "name": name,
        "subtitle": subtitle,
        "type": item_type,
        "folder": folder,
        "playlist": playlist,
        "history": history,
    }


def encode_setup(requesting_player: int) -> bytes:
    return encode_message(
        SETUP_TX,
        TYPE_SETUP,
        [(TAG_NUM, encode_number_arg(requesting_player))],
    )


def encode_menu_request(
    tx_id: int,
    req_type: int,
    requesting_player: int,
    slot: int,
    extra_nums: Iterable[int] = (),
) -> bytes:
    rmst = encode_number_arg(build_rmst(requesting_player, MENU_MAIN, slot))
    args: list[tuple[int, bytes]] = [(TAG_NUM, rmst)]
    for n in extra_nums:
        args.append((TAG_NUM, encode_number_arg(n)))
    return encode_message(tx_id, req_type, args)


def encode_album_art_request(
    tx_id: int,
    requesting_player: int,
    slot: int,
    artwork_id: int,
    *,
    high_res: bool = False,
) -> bytes:
    rmst = encode_number_arg(build_rmst(requesting_player, MENU_DATA, slot))
    args: list[tuple[int, bytes]] = [
        (TAG_NUM, rmst),
        (TAG_NUM, encode_number_arg(artwork_id)),
    ]
    if high_res:
        args.append((TAG_NUM, encode_number_arg(1)))
    return encode_message(tx_id, TYPE_ALBUM_ART, args)


def encode_metadata_request(
    tx_id: int,
    requesting_player: int,
    slot: int,
    track_type: int,
    track_id: int,
) -> bytes:
    """Read-only track-metadata query (menu of title, artist, and so on)."""
    rmst = encode_number_arg(build_rmst(requesting_player, MENU_MAIN, slot, track_type))
    return encode_message(
        tx_id,
        metadata_message_type(track_type),
        [
            (TAG_NUM, rmst),
            (TAG_NUM, encode_number_arg(track_id)),
        ],
    )


def metadata_from_items(items: Iterable[DbMessage]) -> dict[str, object]:
    """Collapse a metadata render (menu items) into title / artist / artwork id."""
    out: dict[str, object] = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "key": "",
        "label": "",
        "comment": "",
        "duration_s": 0,
        "tempo": 0.0,
        "artwork_id": 0,
        "year": 0,
        "bitrate": 0,
        "rating": 0,
    }
    for msg in items:
        if msg.msg_type != TYPE_MENU_ITEM or len(msg.args) < 7:
            continue
        kind = int(msg.args[6]) & 0xFFFF
        text = msg.args[3] if len(msg.args) > 3 and isinstance(msg.args[3], str) else ""
        number = int(msg.args[1]) if isinstance(msg.args[1], int) else 0
        if kind == 0x04:
            out["title"] = text
            if len(msg.args) > 8 and isinstance(msg.args[8], int):
                out["artwork_id"] = int(msg.args[8])
        elif kind == 0x07:
            out["artist"] = text
        elif kind == 0x02:
            out["album"] = text
        elif kind == 0x06:
            out["genre"] = text
        elif kind == 0x0F:
            out["key"] = text
        elif kind == 0x0E:
            out["label"] = text
        elif kind == 0x23:
            out["comment"] = text
        elif kind == 0x0B:
            out["duration_s"] = number
        elif kind == 0x0D:
            out["tempo"] = (number / 100.0) if number else 0.0
        elif kind == 0x0A:
            out["rating"] = number
        elif kind == 0x10:
            out["bitrate"] = number
        elif kind == 0x11:
            if text.strip().isdigit():
                out["year"] = int(text.strip())
            elif number and number < 10000:
                out["year"] = number
    return out


def encode_render_menu(
    tx_id: int,
    requesting_player: int,
    slot: int,
    offset: int,
    limit: int,
    total: int,
    track_type: int = TRACK_REKORDBOX,
) -> bytes:
    rmst = encode_number_arg(build_rmst(requesting_player, MENU_MAIN, slot, track_type))
    return encode_message(
        tx_id,
        TYPE_RENDER_MENU,
        [
            (TAG_NUM, rmst),
            (TAG_NUM, encode_number_arg(offset)),
            (TAG_NUM, encode_number_arg(limit)),
            (TAG_NUM, encode_number_arg(0)),
            (TAG_NUM, encode_number_arg(total)),
            (TAG_NUM, encode_number_arg(0)),
        ],
    )
