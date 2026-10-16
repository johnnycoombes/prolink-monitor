"""HTTP panel server: reconnect, SSE clients, and a hard cap on threads.

A GUI reconnect used to call ``ThreadingHTTPServer.shutdown()`` and leave the
listening socket open. The port stayed in LISTEN with nobody calling accept, so
a browser or OBS got "unable to connect" while the socket still looked live.
Each SSE client also ran forever with no write timeout, so a closed page left
a CLOSE_WAIT socket and a thread. Those threads are what piled up past 200.

This server closes the listen socket and every client socket on the way down,
so the same port can be bound again. SSE writes time out, a dropped client is
closed, and a comment line goes out once a second so a dead reader is noticed
even when it never sends a FIN the handler would otherwise miss.
"""

from __future__ import annotations

import hashlib
import json
import re
import select
import socket
import struct
import threading
import time
from http.server import ThreadingHTTPServer

# Short requests need a free thread even while several overlays are streaming.
MAX_REQUEST_THREADS = 32
MAX_SSE_CLIENTS = 12
SSE_WRITE_TIMEOUT_S = 2.0
SSE_HEARTBEAT_S = 1.0


def _close_socket(sock) -> None:
    """Close a socket without waiting out CLOSE_WAIT or TIME_WAIT."""
    if sock is None:
        return
    try:
        linger = struct.pack("ii", 1, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
    except OSError:
        pass
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def peer_closed(sock) -> bool:
    """True when the client has gone away.

    A half-closed browser stays readable and ``recv`` returns ``b''``. A client
    that merely stopped reading is not readable; the write timeout catches that.
    """
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    try:
        data = sock.recv(1, socket.MSG_PEEK)
    except (BlockingIOError, InterruptedError, TimeoutError, socket.timeout):
        return False
    except OSError:
        return True
    return data == b""


class PanelServer(ThreadingHTTPServer):
    """Threaded panel server that can be stopped and bound again on the same port."""

    daemon_threads = True
    # Never join SSE threads from server_close(); we close their sockets instead.
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, server_address, RequestHandlerClass):
        self.stopping = False
        self._serving = threading.Event()
        self._clients_lock = threading.Lock()
        self._clients: set = set()
        self._inflight_lock = threading.Lock()
        self._inflight = 0
        self.last_error = ""
        self.sse_write_timeout = SSE_WRITE_TIMEOUT_S
        self.sse_heartbeat_s = SSE_HEARTBEAT_S
        self.sse_sndbuf = 0
        super().__init__(server_address, RequestHandlerClass)

    def server_bind(self) -> None:
        """Bind the listen socket.

        On Windows ``SO_REUSEADDR`` means another socket may share the port.
        Setting it before the first bind makes a free 8777 come back as
        WinError 10013, so the process falls through to 8787 and 8777 stays
        unbound. Bind plainly first. Use the share flag only when the port
        is still in TIME_WAIT from the previous process.
        """
        if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            super().server_bind()
            return
        try:
            self.socket.close()
        except OSError:
            pass
        last: OSError | None = None
        for reuse in (False, True):
            sock = socket.socket(self.address_family, self.socket_type)
            try:
                if reuse:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(self.server_address)
            except OSError as exc:
                last = exc
                try:
                    sock.close()
                except OSError:
                    pass
                continue
            self.socket = sock
            self.server_address = sock.getsockname()
            return
        if last is not None:
            raise last
        raise OSError("bind failed")

    def serve_forever(self, poll_interval=0.2):
        self._serving.set()
        try:
            super().serve_forever(poll_interval)
        finally:
            self._serving.clear()

    def process_request(self, request, client_address):
        if self.stopping:
            _close_socket(request)
            return
        with self._inflight_lock:
            if self._inflight >= MAX_REQUEST_THREADS:
                self.last_error = "too many web clients"
                _close_socket(request)
                return
            self._inflight += 1
        try:
            request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        thread = threading.Thread(
            target=self._run_request,
            args=(request, client_address),
            daemon=True,
            name="prolink-http",
        )
        thread.start()

    def _run_request(self, request, client_address) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            try:
                self.shutdown_request(request)
            except Exception:
                _close_socket(request)
            with self._inflight_lock:
                self._inflight = max(0, self._inflight - 1)

    def register_sse(self, sock) -> bool:
        with self._clients_lock:
            if self.stopping or len(self._clients) >= MAX_SSE_CLIENTS:
                if not self.stopping:
                    self.last_error = "too many event streams"
                return False
            self._clients.add(sock)
            return True

    def unregister_sse(self, sock) -> None:
        with self._clients_lock:
            self._clients.discard(sock)

    def drop_clients(self) -> None:
        with self._clients_lock:
            socks = list(self._clients)
            self._clients.clear()
        for sock in socks:
            _close_socket(sock)

    def snapshot(self) -> dict:
        host, port = self.server_address[:2]
        with self._clients_lock:
            clients = len(self._clients)
        with self._inflight_lock:
            threads = self._inflight
        listening = False
        try:
            listening = (not self.stopping) and self.socket.fileno() >= 0
        except Exception:
            listening = False
        return {
            "listening": listening,
            "host": host,
            "port": int(port),
            "clients": clients,
            "threads": threads,
            "last_error": self.last_error or "",
        }


def _sleep_until(deadline: float, server, sock) -> bool:
    """Sleep until ``deadline``. Return True when the stream should stop."""
    while True:
        if getattr(server, "stopping", False) or peer_closed(sock):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


_FRAMES: dict = {}
_FRAMES_LOCK = threading.Lock()

# Keys an overlay reads. Health fields (nfs, dbserver, link_timing, anlz, ...)
# stay on the full stream for the main panel only.
LITE_KEYS = frozenset({
    "t", "paint", "decks", "display", "spectrum", "audience_deck",
    "now_playing", "pending", "setlist", "mix",
})
# Fields that change every frame without changing what a client shows.
# An unchanged frame is still re-sent at least this often.
UNCHANGED_RESEND_S = 1.0
DEFAULT_PAINT_HZ = 30.0
DEFAULT_FULL_HZ = 15.0


def lite_state(state: dict) -> dict:
    """The overlay subset of a full ``state()`` snapshot."""
    return {k: v for k, v in state.items() if k in LITE_KEYS}


_VOLATILE_RE = re.compile(rb'"(?:t|playhead_age_s)": -?[0-9.eE+-]+')


def _digest(state) -> str:
    """Hash of a snapshot without its per-frame timestamp fields."""
    raw = json.dumps(state, ensure_ascii=False, default=str).encode("utf-8")
    return _frame_digest(raw)


def _frame_digest(raw: bytes) -> str:
    # One regex pass over the encoded frame, not a second json.dumps.
    return hashlib.blake2b(_VOLATILE_RE.sub(b"", raw), digest_size=12).hexdigest()


def _any_playing(state) -> bool:
    try:
        return any(bool(d.get("playing")) for d in state.get("decks") or [])
    except AttributeError:
        return False


def shared_frame_info(monitor, kind: str, ttl: float, *, lite: bool = False):
    """``(frame, digest, playing)`` for one tick, shared by every client.

    With several browsers and an OBS overlay open, each stream used to call
    ``monitor.state()`` and ``json.dumps`` on its own. A frame younger than
    ``ttl`` is reused. The lite variant is cut from the same full snapshot.
    """
    now = time.monotonic()
    key = (id(monitor), kind, bool(lite))
    with _FRAMES_LOCK:
        hit = _FRAMES.get(key)
        if hit is not None and hit[0] is monitor and now - hit[1] < ttl:
            if hit[2] is None:
                # Raw snapshot stored by a lite client; encode on first use.
                hit = (monitor, hit[1], *_encode(hit[5]), hit[5])
                _FRAMES[key] = hit
            return hit[2], hit[3], hit[4]
        if kind == "state":
            full_key = (id(monitor), kind, False)
            base = None
            if lite:
                fh = _FRAMES.get(full_key)
                if fh is not None and fh[0] is monitor and now - fh[1] < ttl:
                    base = fh[5]
            if base is None:
                base = monitor.state()
                if lite:
                    # Keep the full snapshot so a main panel on the same tick reuses it.
                    _FRAMES[full_key] = (monitor, now, None, None, None, base)
            state = lite_state(base) if lite else base
        else:
            state = monitor.paint_state()
        frame, digest, playing = _encode(state)
        _FRAMES[key] = (monitor, now, frame, digest, playing, state)
        if len(_FRAMES) > 12:
            for k in list(_FRAMES)[:-6]:
                _FRAMES.pop(k, None)
        return frame, digest, playing


def _encode(state):
    frame = f"data: {json.dumps(state, ensure_ascii=False)}\n\n".encode("utf-8")
    return frame, _frame_digest(frame), _any_playing(state)


def shared_frame(monitor, kind: str, ttl: float, *, lite: bool = False) -> bytes:
    """One encoded SSE frame per tick (see ``shared_frame_info``)."""
    return shared_frame_info(monitor, kind, ttl, lite=lite)[0]


class FrameGate:
    """Per-client decision: send a frame, or skip one identical to the last.

    A frame equal to the last one sent of its kind is skipped, but something
    goes out at least every ``resend_s`` so a client never looks stale.
    Paint frames are only needed while a deck is playing; when every deck is
    stopped they follow the same unchanged-skip rule.
    """

    def __init__(self, resend_s: float = UNCHANGED_RESEND_S):
        self.resend_s = resend_s
        self._last: dict[str, tuple[str, float]] = {}

    def should_send(self, kind: str, digest: str, now: float, playing: bool = False) -> bool:
        if kind == "paint" and playing:
            self._last[kind] = (digest, now)
            return True
        prev = self._last.get(kind)
        if prev is not None and prev[0] == digest and now - prev[1] < self.resend_s:
            return False
        self._last[kind] = (digest, now)
        return True


def serve_sse(handler, *, paint_hz: float = DEFAULT_PAINT_HZ,
              full_hz: float = DEFAULT_FULL_HZ, lite: bool = False) -> None:
    """Stream state until the client leaves, the socket times out, or we stop.

    A comment line (``: ping``) goes out about once a second. EventSource
    ignores it. The write is what notices a client that stopped reading.
    ``lite`` streams only the keys an overlay uses (``LITE_KEYS``).
    """
    server = handler.server
    sock = handler.connection
    register = getattr(server, "register_sse", None)
    if register is not None and not register(sock):
        try:
            handler.send_error(503, "too many streams")
        except OSError:
            pass
        return

    timeout = float(getattr(server, "sse_write_timeout", SSE_WRITE_TIMEOUT_S) or SSE_WRITE_TIMEOUT_S)
    heartbeat_s = float(getattr(server, "sse_heartbeat_s", SSE_HEARTBEAT_S) or SSE_HEARTBEAT_S)
    sndbuf = int(getattr(server, "sse_sndbuf", 0) or 0)
    gate = FrameGate()
    try:
        if sndbuf > 0:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
        sock.settimeout(timeout)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        full_every = max(1, int(round(paint_hz / full_hz)))
        tick = 0
        last_heartbeat = time.monotonic()
        while not getattr(server, "stopping", False):
            if peer_closed(sock):
                return
            now = time.monotonic()
            monitor = handler.monitor
            kind = "state" if tick % full_every == 0 else "paint"
            ttl = (1.0 / full_hz if kind == "state" else 1.0 / paint_hz) * 0.9
            frame, digest, playing = shared_frame_info(
                monitor, kind, ttl, lite=lite and kind == "state")
            wrote = False
            if gate.should_send(kind, digest, now, playing):
                handler.wfile.write(frame)
                wrote = True
            if now - last_heartbeat >= heartbeat_s:
                handler.wfile.write(b": ping\n\n")
                last_heartbeat = now
                wrote = True
            if wrote:
                handler.wfile.flush()
            tick += 1
            if _sleep_until(time.monotonic() + (1.0 / paint_hz), server, sock):
                return
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
            TimeoutError, socket.timeout, OSError):
        return
    except Exception as exc:
        try:
            server.last_error = str(exc)
        except Exception:
            pass
        return
    finally:
        unregister = getattr(server, "unregister_sse", None)
        if unregister is not None:
            unregister(sock)
        _close_socket(sock)


def _call_shutdown(server: PanelServer) -> None:
    if not server._serving.wait(0.5):
        return
    try:
        server.shutdown()
    except Exception as exc:
        server.last_error = str(exc)


def _wake_accept(server: PanelServer) -> None:
    """Unblock ``select`` on the listening socket so shutdown does not wait."""
    try:
        host, port = server.server_address[:2]
    except Exception:
        return
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    try:
        sock = socket.create_connection((host, int(port)), timeout=0.3)
    except OSError:
        return
    try:
        sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0),
        )
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def close_http_server(server, thread: threading.Thread | None = None,
                      timeout: float = 2.0) -> None:
    """Stop accept, close every SSE client, and release the listen port."""
    if server is None:
        return
    server.stopping = True
    drop = getattr(server, "drop_clients", None)
    if drop is not None:
        drop()
    helper = threading.Thread(
        target=_call_shutdown, args=(server,), daemon=True, name="prolink-http-shutdown",
    )
    helper.start()
    _wake_accept(server)
    helper.join(timeout)
    try:
        server.server_close()
    except Exception as exc:
        try:
            server.last_error = str(exc)
        except Exception:
            pass
    if thread is not None and thread.is_alive():
        thread.join(timeout)


def start_http_thread(server) -> threading.Thread:
    """Run ``serve_forever`` and return once the accept loop is inside it."""
    thread = threading.Thread(
        target=server.serve_forever, name="prolink-http-accept", daemon=True,
    )
    thread.start()
    serving = getattr(server, "_serving", None)
    if serving is not None:
        serving.wait(2.0)
    return thread


def http_thread_count() -> int:
    """Live threads this module started. Tests use this to catch leaks."""
    return sum(
        1 for thread in threading.enumerate()
        if thread.is_alive() and thread.name.startswith("prolink-http")
    )
