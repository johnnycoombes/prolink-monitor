"""High-level read-only dbserver session for library menus (playlists, history, tracks).

A dropped socket is retried with backoff. The idea — throw away a dead
remotedb connection and open a new one, rather than failing until restart —
follows chrisle/alphatheta-connect (MIT). The retry loop is ours. Nothing
here writes to the player or to a Rekordbox database.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import remotedb as wire
from .db_recovery import RECONNECT_BACKOFF_S, STATS, backoff_delay, is_connection_drop

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
    _dead: bool = field(default=False, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)
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
            closer = getattr(sock, "close", None)
            if callable(closer):
                try:
                    closer()
                except OSError:
                    pass

    def __enter__(self) -> RemoteDbBrowser:
        # Opening the session is itself a request: a player that just rebooted
        # gets the same backoff as a query that dies mid-flight.
        self.call(lambda: None)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def call(self, fn: Callable):
        """Run ``fn``. If the socket drops, wait, handshake again, and retry it.

        A normal reply such as "menu unavailable" is not a drop and is not
        retried. The in-flight request is the whole ``fn``, so a menu render
        that dies halfway starts again from the top.
        """
        last: BaseException | None = None
        for attempt in range(1 + len(RECONNECT_BACKOFF_S)):
            try:
                if self._sock is None or self._dead:
                    if attempt:
                        self.sleeper(backoff_delay(attempt - 1))
                    self.connect()
                    if attempt:
                        STATS.note(str(last) if last else "connection dropped")
                    self._dead = False
                return fn()
            except Exception as exc:
                if not is_connection_drop(exc):
                    raise
                last = exc
                self._dead = True
                self.close()
                self.last_status = RemoteDbStatus(
                    ok=False,
                    error=str(exc),
                    requesting_player=self.requesting_player,
                    target_player=self.target_player,
                    slot=self.slot,
                )
        if last is not None:
            raise last
        raise wire.DbServerError("not connected")

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

    def _render_all(self, count: int, *, track_type: int | None = None) -> list[wire.DbMessage]:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        if count <= 0 or count == 0xFFFFFFFF:
            return []
        items: list[wire.DbMessage] = []
        offset = 0
        tt = wire.TRACK_REKORDBOX if track_type is None else int(track_type)
        while offset < count:
            batch = min(wire.MENU_BATCH, count - offset)
            tx = self._next_tx()
            pkt = wire.encode_render_menu(
                tx, self.requesting_player, self.slot, offset, batch, count,
                track_type=tt)
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
        return self.call(lambda: self._playlist_folder(folder_id))

    def _playlist_folder(self, folder_id: int = 0) -> list[dict]:
        """List playlists/folders under *folder_id* (0 = root). folder flag = 1."""
        avail = self._menu_request(wire.TYPE_PLAYLIST, 0, folder_id, 1)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        return [wire.menu_item_row(m) for m in self._render_all(count)]

    def playlist_tracks(self, playlist_id: int) -> list[dict]:
        return self.call(lambda: self._playlist_tracks(playlist_id))

    def _playlist_tracks(self, playlist_id: int) -> list[dict]:
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
        return self.call(self._history_entries)

    def _history_entries(self) -> list[dict]:
        avail = self._menu_request(wire.TYPE_HISTORY_MENU, 0)
        count = int(avail.args[1]) if len(avail.args) > 1 else 0
        return [wire.menu_item_row(m) for m in self._render_all(count)]

    def track_metadata(self, track_id: int, *, track_type: int = wire.TRACK_REKORDBOX) -> dict:
        """Read one track's title, artist, and related fields. Never writes."""
        return self.call(lambda: self._track_metadata(track_id, track_type=track_type))

    def _track_metadata(self, track_id: int, *, track_type: int = wire.TRACK_REKORDBOX) -> dict:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        if not track_id:
            return {}
        tx = self._next_tx()
        pkt = wire.encode_metadata_request(
            tx, self.requesting_player, self.slot, int(track_type), int(track_id))
        self._sock.sendall(pkt)
        avail = wire.read_message(self._sock)
        if avail.tx_id != tx:
            raise wire.DbServerError("transaction id mismatch")
        if avail.msg_type != wire.TYPE_MENU_AVAILABLE:
            raise wire.DbServerError(f"metadata unavailable type {avail.msg_type:#x}")
        count = int(avail.args[1]) if len(avail.args) > 1 and isinstance(avail.args[1], int) else 0
        if count <= 0 or count == 0xFFFFFFFF:
            return {}
        return wire.metadata_from_items(self._render_all(count, track_type=int(track_type)))

    def album_art(self, artwork_id: int, *, high_res: bool = True) -> bytes | None:
        """Fetch album art bytes via dbserver (high-res when *high_res* and player supports it)."""
        return self.call(lambda: self._album_art(artwork_id, high_res=high_res))

    def _album_art(self, artwork_id: int, *, high_res: bool = True) -> bytes | None:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        if not artwork_id:
            return None
        tx = self._next_tx()
        pkt = wire.encode_album_art_request(
            tx, self.requesting_player, self.slot, int(artwork_id), high_res=high_res)
        self._sock.sendall(pkt)
        resp = wire.read_message(self._sock)
        if resp.tx_id != tx:
            raise wire.DbServerError("transaction id mismatch")
        if resp.msg_type != wire.TYPE_ALBUM_ART_RESP:
            return None
        if len(resp.args) < 4:
            return None
        blob = resp.args[3]
        if isinstance(blob, (bytes, bytearray)) and blob:
            return bytes(blob)
        return None

    def analysis_tag(
        self,
        track_id: int,
        tag: str,
        ext: str,
        *,
        track_type: int = wire.TRACK_REKORDBOX,
    ) -> bytes | None:
        """Read one ANLZ tag (PWV5/EXT, PQTZ/DAT, …). Never writes.

        Used only when no analysis-file path is known. The NFS read of
        ANLZ0000.DAT / .EXT / .2EX is the path that already works.
        """
        return self.call(
            lambda: self._analysis_tag(track_id, tag, ext, track_type=track_type))

    def _analysis_tag(
        self,
        track_id: int,
        tag: str,
        ext: str,
        *,
        track_type: int = wire.TRACK_REKORDBOX,
    ) -> bytes | None:
        if self._sock is None:
            raise wire.DbServerError("not connected")
        if not track_id or not tag or not ext:
            return None
        tx = self._next_tx()
        pkt = wire.encode_anlz_tag_request(
            tx, self.requesting_player, self.slot, int(track_type),
            int(track_id), tag, ext)
        self._sock.sendall(pkt)
        resp = wire.read_message(self._sock)
        if resp.tx_id != tx:
            raise wire.DbServerError("transaction id mismatch")
        if resp.msg_type != wire.TYPE_ANLZ_TAG_RESP:
            return None
        for arg in reversed(resp.args):
            if isinstance(arg, (bytes, bytearray)) and arg:
                return bytes(arg)
        return None

    def track_search_page(self, offset: int = 0, limit: int = 64) -> list[dict]:
        """First page of all tracks (live export.pdb equivalent ordering)."""
        return self.call(lambda: self._track_search_page(offset, limit))

    def _track_search_page(self, offset: int = 0, limit: int = 64) -> list[dict]:
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
