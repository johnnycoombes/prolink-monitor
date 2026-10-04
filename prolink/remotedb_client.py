"""High-level read-only dbserver session for library menus (playlists, history, tracks)."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field

from . import remotedb as wire

DEFAULT_TIMEOUT = 4.0


def choose_requesting_player(
    virtual_number: int | None,
    *,
    fallback: int = 3,
) -> int:
    """Pick a dbserver client player number (must often be 1–4 on classic CDJs)."""
    if virtual_number is not None and 1 <= virtual_number <= 6:
        if 1 <= virtual_number <= 4:
            return virtual_number
    return fallback


@dataclass
class RemoteDbStatus:
    ok: bool = False
    error: str = ""
    detail: str = ""
    port: int = 0
    requesting_player: int = 0
    target_player: int = 0
    slot: int = wire.SLOT_USB


@dataclass
class RemoteDbBrowser:
    """One TCP session to a player's dbserver."""

    host: str
    target_player: int
    requesting_player: int
    slot: int = wire.SLOT_USB
    timeout: float = DEFAULT_TIMEOUT
    _sock: socket.socket | None = field(default=None, init=False, repr=False)
    _tx: int = field(default=0, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    last_status: RemoteDbStatus = field(default_factory=RemoteDbStatus, init=False)

    def connect(self) -> None:
        with self._lock:
            self.close()
            port = wire.discover_db_port(self.host, timeout=self.timeout)
            sock = socket.create_connection((self.host, port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            sock.sendall(wire._num_field(wire.GREETING, 4))
            reply_tag, reply_val = wire.decode_field(_SockWrap(sock))
            if reply_tag != wire.TAG_NUM or reply_val != wire.GREETING:
                sock.close()
                raise wire.DbServerError("unexpected dbserver greeting")
            sock.sendall(wire.encode_setup(self.requesting_player))
            setup_resp = wire.read_message(sock)
            if setup_resp.msg_type != wire.TYPE_MENU_AVAILABLE:
                sock.close()
                raise wire.DbServerError(f"setup failed type {setup_resp.msg_type:#x}")
            self._sock = sock
            self._tx = 0
            detail = (
                f"port {port} · player {self.target_player} "
                f"as client #{self.requesting_player} slot {self.slot}"
            )
            self.last_status = RemoteDbStatus(
                ok=True,
                detail=detail,
                port=port,
                requesting_player=self.requesting_player,
                target_player=self.target_player,
                slot=self.slot,
            )

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> RemoteDbBrowser:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _next_tx(self) -> int:
        self._tx += 1
        return self._tx

    def _menu_request(self, req_type: int, *extra: int) -> wire.DbMessage:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        tx = self._next_tx()
        pkt = wire.encode_menu_request(
            tx, req_type, self.requesting_player, self.slot, extra)
        self._sock.sendall(pkt)
        resp = wire.read_message(self._sock)
        if resp.tx_id != tx:
            raise wire.DbServerError("transaction id mismatch")
        if resp.msg_type != wire.TYPE_MENU_AVAILABLE:
            raise wire.DbServerError(f"menu unavailable type {resp.msg_type:#x}")
        return resp

    def _render_all(self, count: int) -> list[wire.DbMessage]:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        if count <= 0 or count == 0xFFFFFFFF:
            return []
        items: list[wire.DbMessage] = []
        offset = 0
        while offset < count:
            batch = min(wire.MENU_BATCH, count - offset)
            tx = self._next_tx()
            pkt = wire.encode_render_menu(
                tx, self.requesting_player, self.slot, offset, batch, count)
            self._sock.sendall(pkt)
            header = wire.read_message(self._sock)
            if header.msg_type != wire.TYPE_MENU_HEADER:
                raise wire.DbServerError("expected menu header")
            while True:
                msg = wire.read_message(self._sock)
                if msg.msg_type == wire.TYPE_MENU_ITEM:
                    items.append(msg)
                    continue
                if msg.msg_type == wire.TYPE_MENU_FOOTER:
                    break
                raise wire.DbServerError(f"unexpected render type {msg.msg_type:#x}")
            offset += batch
        return items

    def playlist_folder(self, folder_id: int = 0) -> list[dict]:
        """List playlists/folders under *folder_id* (0 = root). folder flag = 1."""
        avail = self._menu_request(wire.TYPE_PLAYLIST, 0, folder_id, 1)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        return [wire.menu_item_row(m) for m in self._render_all(count)]

    def playlist_tracks(self, playlist_id: int) -> list[dict]:
        avail = self._menu_request(wire.TYPE_PLAYLIST, 0, playlist_id, 0)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        rows = []
        for m in self._render_all(count):
            row = wire.menu_item_row(m)
            if row:
                row["source"] = "remotedb"
                rows.append(row)
        return rows

    def history_entries(self) -> list[dict]:
        avail = self._menu_request(wire.TYPE_HISTORY_MENU, 0)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        return [wire.menu_item_row(m) for m in self._render_all(count)]

    def track_search_page(self, offset: int = 0, limit: int = 64) -> list[dict]:
        """First page of all tracks (live export.pdb equivalent ordering)."""
        avail = self._menu_request(wire.TYPE_TRACK_MENU, 0)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        if count <= 0:
            return []
        want = min(limit, count - offset)
        if want <= 0:
            return []
        if self._sock is None:
            raise wire.DbServerError("not connected")
        tx = self._next_tx()
        pkt = wire.encode_render_menu(
            tx, self.requesting_player, self.slot, offset, want, count)
        self._sock.sendall(pkt)
        header = wire.read_message(self._sock)
        if header.msg_type != wire.TYPE_MENU_HEADER:
            raise wire.DbServerError("expected menu header")
        items: list[wire.DbMessage] = []
        while True:
            msg = wire.read_message(self._sock)
            if msg.msg_type == wire.TYPE_MENU_ITEM:
                items.append(msg)
                continue
            if msg.msg_type == wire.TYPE_MENU_FOOTER:
                break
        return [wire.menu_item_row(m) for m in items]


class _SockWrap:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, n: int) -> bytes:
        return wire._read_exact(self._sock, n)
