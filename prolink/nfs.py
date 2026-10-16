"""Minimal ONC-RPC / MOUNT v1 / NFS v2 client over UDP.

AlphaTheta/Pioneer players (CDJ-3000, XDJ-AZ, XDJ-XZ...) export the inserted
media (USB/SD) over NFS v2. That lets us read the rekordbox database
(export.pdb) and the analysis files (ANLZ*.DAT/.EXT) holding the waveforms,
without needing a free player number for the dbserver protocol.

No external dependencies.
"""

from __future__ import annotations

import os
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass

PROG_PORTMAP = 100000
PROG_NFS = 100003
PROG_MOUNT = 100005

# NFS v2 procedures
NFSPROC_GETATTR = 1
NFSPROC_LOOKUP = 4
NFSPROC_READ = 6
NFSPROC_READDIR = 16

MOUNTPROC_MNT = 1
MOUNTPROC_EXPORT = 5

NFS_OK = 0
FHSIZE = 32


class RpcError(Exception):
    pass


class NfsError(Exception):
    def __init__(self, status: int, path: str = ""):
        self.status = status
        super().__init__(
            f"NFS error {status} ({NFS_ERRORS.get(status, 'unknown')}) {path}".strip())


NFS_ERRORS = {
    1: "NFSERR_PERM", 2: "NFSERR_NOENT", 5: "NFSERR_IO", 13: "NFSERR_ACCES",
    20: "NFSERR_NOTDIR", 21: "NFSERR_ISDIR", 27: "NFSERR_FBIG", 70: "NFSERR_STALE",
}


# --------------------------------------------------------------------------- XDR

class _Unpacker:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def uint(self) -> int:
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def int(self) -> int:
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def raw(self, n: int) -> bytes:
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def var(self) -> bytes:
        n = self.uint()
        v = self.data[self.pos:self.pos + n]
        self.pos += n + (-n % 4)
        return v

    def skip(self, n: int) -> None:
        self.pos += n

    @property
    def left(self) -> int:
        return len(self.data) - self.pos


def _xdr_str(s: bytes | str) -> bytes:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return struct.pack(">I", len(s)) + s + b"\0" * (-len(s) % 4)


def encode_name(name: str, utf16: bool = True) -> bytes:
    """Encode a path or file name the way the player expects it.

    An XDJ-AZ uses UTF-16LE; other models may use plain UTF-8, so the client
    detects it rather than assuming.
    """
    return name.encode("utf-16-le") if utf16 else name.encode("utf-8")


def decode_name(raw: bytes) -> str:
    """Decode a name returned by the player (UTF-16LE, falling back to UTF-8)."""
    if b"\0" in raw:
        try:
            return raw.decode("utf-16-le").rstrip("\0")
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def looks_utf16(raw: bytes) -> bool:
    """UTF-16LE names come back with a NUL after every ASCII character."""
    return b"\0" in raw


@dataclass
class FileAttr:
    type: int
    mode: int
    size: int
    mtime: float

    @property
    def is_dir(self) -> bool:
        return self.type == 2


def _parse_fattr(u: _Unpacker) -> FileAttr:
    ftype = u.uint()
    mode = u.uint()
    u.skip(4 * 3)              # nlink, uid, gid
    size = u.uint()
    u.skip(4 * 2)              # blocksize, rdev
    u.skip(4 * 2)              # blocks, fsid
    u.skip(4)                  # fileid
    u.skip(8)                  # atime
    mtime = u.uint() + u.uint() / 1e6
    u.skip(8)                  # ctime
    return FileAttr(ftype, mode, size, mtime)


# --------------------------------------------------------------------------- RPC

DEFAULT_READ_SIZE = 4096


def clamp_read_size(n: int) -> int:
    return max(2048, min(8192, int(n)))


def read_size() -> int:
    """NFS READ size in bytes (2-8 KB, default 4 KB)."""
    raw = os.environ.get("PROLINK_NFS_READ_SIZE", "")
    try:
        return clamp_read_size(int(raw)) if raw else DEFAULT_READ_SIZE
    except ValueError:
        return DEFAULT_READ_SIZE


class RpcClient:
    """ONC-RPC client over UDP with retries and AUTH_UNIX credentials."""

    def __init__(self, host: str, port: int, prog: int, vers: int, timeout: float = 2.0,
                 retries: int = 5):
        self.host = host
        self.port = port
        self.prog = prog
        self.vers = vers
        self.timeout = timeout
        self.retries = retries
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)
        # Some players only answer requests coming from a privileged port.
        self._bind_reserved()
        self._machine = _xdr_str(socket.gethostname()[:32])
        # Round-trip timing for the Health panel (UDP RPC latency).
        self.last_rtt_ms: float | None = None
        self._rtt_ewma_ms: float | None = None
        self.call_count: int = 0
        self.timeout_count: int = 0

    @property
    def rtt_ms(self) -> float | None:
        """Smoothed RTT in milliseconds, or None before the first success."""
        return self._rtt_ewma_ms

    def _bind_reserved(self) -> None:
        for port in range(1010, 1024):
            try:
                self._sock.bind(("", port))
                return
            except OSError:
                continue
        # No reserved port free: carry on with an ephemeral one.

    def _cred(self) -> bytes:
        body = struct.pack(">I", 0) + self._machine + struct.pack(">III", 0, 0, 0)
        return struct.pack(">II", 1, len(body)) + body      # AUTH_UNIX

    def _record_rtt(self, started: float) -> None:
        rtt = (time.monotonic() - started) * 1000.0
        self.last_rtt_ms = rtt
        if self._rtt_ewma_ms is None:
            self._rtt_ewma_ms = rtt
        else:
            self._rtt_ewma_ms = 0.3 * rtt + 0.7 * self._rtt_ewma_ms

    # Adaptive timeout: 4x the smoothed RTT, clamped to 0.25-0.5 s for the first
    # try, then doubled per retry up to 2 s. Worst case per call with the
    # default 5 tries: 0.5 + 1 + 2 + 2 + 2 = 7.5 s (it was 4 x 2 s = 8 s), while a
    # single lost datagram on a healthy link now costs ~0.25 s instead of 2 s.
    MIN_TIMEOUT_S = 0.25
    BASE_MAX_TIMEOUT_S = 0.5
    MAX_TIMEOUT_S = 2.0

    def attempt_timeouts(self) -> list[float]:
        rtt = self._rtt_ewma_ms
        base = self.BASE_MAX_TIMEOUT_S if rtt is None else 4.0 * rtt / 1000.0
        base = min(self.BASE_MAX_TIMEOUT_S, max(self.MIN_TIMEOUT_S, base))
        cap = max(base, min(self.MAX_TIMEOUT_S, float(self.timeout)))
        return [min(cap, base * (2 ** i)) for i in range(max(1, self.retries))]

    def call(self, proc: int, args: bytes = b"") -> _Unpacker:
        # One request at a time per socket: reads stay strictly serial.
        with self._lock:
            last: Exception | None = None
            sent: set[int] = set()
            for wait in self.attempt_timeouts():
                xid = random.getrandbits(32)
                sent.add(xid)
                msg = (struct.pack(">IIIIII", xid, 0, 2, self.prog, self.vers, proc)
                       + self._cred() + struct.pack(">II", 0, 0) + args)
                started = time.monotonic()
                deadline = started + wait
                try:
                    self._sock.sendto(msg, (self.host, self.port))
                    while True:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise socket.timeout("timed out")
                        self._sock.settimeout(left)
                        data, _ = self._sock.recvfrom(65535)
                        # A late answer to an earlier try of this same call is
                        # just as good (the request is idempotent).
                        if len(data) >= 4 and struct.unpack_from(">I", data, 0)[0] in sent:
                            break
                except socket.timeout as e:
                    self.timeout_count += 1
                    last = e
                    continue
                self._record_rtt(started)
                self.call_count += 1
                u = _Unpacker(data)
                u.skip(4)                       # xid
                if u.uint() != 1:
                    raise RpcError("malformed RPC reply")
                if u.uint() != 0:
                    raise RpcError("RPC rejected by the player")
                u.skip(4)                       # verifier flavor
                vlen = u.uint()
                u.skip(vlen + (-vlen % 4))
                accept = u.uint()
                if accept != 0:
                    raise RpcError(f"RPC accept_stat={accept}")
                return u
            raise RpcError(
                f"no reply from {self.host}:{self.port} (program {self.prog})") from last

    def close(self) -> None:
        self._sock.close()


def portmap_getport(host: str, prog: int, vers: int, proto: int = 17) -> int:
    """Ask the portmapper which port an RPC program lives on."""
    c = RpcClient(host, 111, PROG_PORTMAP, 2, timeout=2.0)
    try:
        u = c.call(3, struct.pack(">IIII", prog, vers, proto, 0))   # PMAPPROC_GETPORT
        return u.uint()
    finally:
        c.close()


def portmap_dump(host: str) -> list[tuple[int, int, int, int]]:
    """List (program, version, protocol, port) for every RPC service on the host."""
    c = RpcClient(host, 111, PROG_PORTMAP, 2, timeout=2.0)
    try:
        u = c.call(4)
        out = []
        while u.uint():
            out.append((u.uint(), u.uint(), u.uint(), u.uint()))
        return out
    finally:
        c.close()


# --------------------------------------------------------------------------- NFS

class NfsClient:
    """Read-only access to the file system exported by the player."""

    def __init__(self, host: str, timeout: float = 2.0):
        self.host = host
        mount_port = portmap_getport(host, PROG_MOUNT, 1)
        nfs_port = portmap_getport(host, PROG_NFS, 2)
        if not mount_port or not nfs_port:
            raise RpcError(
                f"{host} does not export NFS (mountd={mount_port}, nfs={nfs_port})")
        self.mount_port = mount_port
        self.nfs_port = nfs_port
        self.utf16 = True          # detected from the first names the player sends
        self._mount = RpcClient(host, mount_port, PROG_MOUNT, 1, timeout)
        self._nfs = RpcClient(host, nfs_port, PROG_NFS, 2, timeout)

    def latency_info(self) -> dict:
        """NFS / mount RPC latency snapshot for the connection Health panel."""
        return {
            "nfs_rtt_ms": self._nfs.rtt_ms,
            "nfs_last_rtt_ms": self._nfs.last_rtt_ms,
            "mount_rtt_ms": self._mount.rtt_ms,
            "nfs_calls": self._nfs.call_count,
            "nfs_timeouts": self._nfs.timeout_count,
            "mount_calls": self._mount.call_count,
            "mount_timeouts": self._mount.timeout_count,
        }

    # -- mounting -----------------------------------------------------------
    def exports(self) -> list[str]:
        u = self._mount.call(MOUNTPROC_EXPORT)
        out = []
        first = True
        while u.left >= 4 and u.uint():
            raw = u.var()
            if first:                              # learn the player's encoding
                self.utf16 = looks_utf16(raw)
                first = False
            out.append(decode_name(raw))
            while u.left >= 4 and u.uint():        # group list
                u.var()
        return out

    def mount(self, path: str) -> bytes:
        u = self._mount.call(MOUNTPROC_MNT, _xdr_str(encode_name(path, self.utf16)))
        status = u.uint()
        if status != NFS_OK:
            raise NfsError(status, path)
        return u.raw(FHSIZE)

    # -- operations ---------------------------------------------------------
    def lookup(self, dir_fh: bytes, name: str) -> tuple[bytes, FileAttr]:
        """Look up one entry, retrying with the other encoding if it is missing.

        Gets the encoding right on players that were never tested, and remembers
        whichever one worked.
        """
        for utf16 in (self.utf16, not self.utf16):
            u = self._nfs.call(NFSPROC_LOOKUP, dir_fh + _xdr_str(encode_name(name, utf16)))
            status = u.uint()
            if status == NFS_OK:
                self.utf16 = utf16
                return u.raw(FHSIZE), _parse_fattr(u)
            if status != 2:                        # only NFSERR_NOENT is worth retrying
                raise NfsError(status, name)
        raise NfsError(2, name)

    def getattr(self, fh: bytes) -> FileAttr:
        u = self._nfs.call(NFSPROC_GETATTR, fh)
        status = u.uint()
        if status != NFS_OK:
            raise NfsError(status)
        return _parse_fattr(u)

    def readdir(self, fh: bytes) -> list[str]:
        names: list[str] = []
        cookie = 0
        while True:
            u = self._nfs.call(NFSPROC_READDIR, fh + struct.pack(">II", cookie, 8192))
            status = u.uint()
            if status != NFS_OK:
                raise NfsError(status)
            eof = False
            while True:
                if not u.uint():                   # is there another entry?
                    eof = bool(u.uint())
                    break
                u.uint()                           # fileid
                names.append(decode_name(u.var()))
                cookie = u.uint()
            if eof or not names:
                break
        return [n for n in names if n not in (".", "..")]

    def read(self, fh: bytes, size: int | None = None, chunk: int | None = None) -> bytes:
        if size is None:
            size = self.getattr(fh).size
        return self.read_at(fh, 0, int(size), chunk=chunk)

    def read_at(self, fh: bytes, offset: int, size: int, chunk: int | None = None) -> bytes:
        """Read ``size`` bytes starting at ``offset``. Does not read the rest of the file.

        Reads are strictly serial (one outstanding request, no pipelining) in
        ``read_size()`` pieces: 4 KB by default, ``PROLINK_NFS_READ_SIZE`` 2048-8192.
        Smaller replies are not IP-fragmented as badly, so one lost fragment
        costs less on a busy player.
        """
        chunk = read_size() if chunk is None else clamp_read_size(chunk)
        buf = bytearray()
        start = max(0, int(offset))
        want = max(0, int(size))
        while len(buf) < want:
            n = min(chunk, want - len(buf))
            u = self._nfs.call(
                NFSPROC_READ, fh + struct.pack(">III", start + len(buf), n, 0))
            status = u.uint()
            if status != NFS_OK:
                raise NfsError(status)
            _parse_fattr(u)
            data = u.var()
            if not data:
                break
            buf += data
        return bytes(buf)

    # -- helpers ------------------------------------------------------------
    def resolve(self, root_fh: bytes, path: str) -> tuple[bytes, FileAttr]:
        """Walk a path like 'PIONEER/rekordbox/export.pdb' from a file handle."""
        fh = root_fh
        attr = self.getattr(fh)
        for part in path.replace("\\", "/").split("/"):
            if not part:
                continue
            fh, attr = self.lookup(fh, part)
        return fh, attr

    def download(self, root_fh: bytes, path: str, dest: str) -> int:
        fh, attr = self.resolve(root_fh, path)
        data = self.read(fh, attr.size)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)

    def close(self) -> None:
        self._mount.close()
        self._nfs.close()
