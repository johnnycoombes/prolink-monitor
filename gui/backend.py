"""Background Pro DJ Link session shared by the desktop UI."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import Handler, Monitor, QuietServer, build_source, open_http_server  # noqa: E402
from gui.settings import as_namespace  # noqa: E402
from prolink import link  # noqa: E402


class Backend(QObject):
    """Owns the Monitor engine and optionally the HTTP panel server."""

    state_changed = Signal(dict)
    status_changed = Signal(str, str)   # status key, detail message
    track_ready = Signal(int)           # track id whose meta/waveform is ready
    port_changed = Signal(int)          # actual bound HTTP port (may differ from settings)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monitor: Monitor | None = None
        self._server: QuietServer | None = None
        self._server_thread: threading.Thread | None = None
        self._settings: dict[str, Any] = {}
        self._bound_port: int | None = None
        self._lock = threading.RLock()
        self._status = "idle"
        self._detail = ""
        self._meta_cache: dict[int, dict] = {}
        self._wave_cache: dict[int, bytes] = {}
        self._art_cache: dict[int, bytes] = {}

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- public API ---------------------------------------------------------
    @property
    def status(self) -> str:
        return self._status

    @property
    def monitor(self) -> Monitor | None:
        return self._monitor

    @property
    def port(self) -> int:
        if self._bound_port is not None:
            return int(self._bound_port)
        return int(self._settings.get("port", 8777))

    def start(self, settings: dict[str, Any]) -> None:
        self.stop()
        self._settings = dict(settings)
        self._set_status("connecting", "starting…")
        threading.Thread(target=self._boot, args=(dict(settings),), daemon=True).start()

        hz = max(5, min(30, int(settings.get("poll_hz", 20))))
        self._timer.start(int(1000 / hz))

    def stop(self) -> None:
        self._timer.stop()
        with self._lock:
            monitor = self._monitor
            server = self._server
            self._monitor = None
            self._server = None
            self._bound_port = None
            self._meta_cache.clear()
            self._wave_cache.clear()
            self._art_cache.clear()
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        self._set_status("idle", "")

    def open_web_panel(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def open_overlay(self, url: str | None = None) -> None:
        webbrowser.open(url or f"http://127.0.0.1:{self.port}/overlay?preview=1")

    def meta(self, track_id: int) -> dict | None:
        if not track_id or self._monitor is None:
            return None
        with self._lock:
            if track_id in self._meta_cache:
                return self._meta_cache[track_id]
        meta = self._monitor.meta(track_id)
        if meta:
            with self._lock:
                self._meta_cache[track_id] = meta
            self.track_ready.emit(track_id)
        return meta

    def waveform(self, track_id: int) -> bytes | None:
        if not track_id or self._monitor is None:
            return None
        with self._lock:
            if track_id in self._wave_cache:
                return self._wave_cache[track_id]
        data = self._monitor.waveform(track_id)
        if data:
            with self._lock:
                self._wave_cache[track_id] = data
            self.track_ready.emit(track_id)
        return data

    def artwork(self, track_id: int) -> bytes | None:
        if not track_id or self._monitor is None:
            return None
        with self._lock:
            if track_id in self._art_cache:
                return self._art_cache[track_id]
        data = self._monitor.artwork(track_id)
        if data:
            with self._lock:
                self._art_cache[track_id] = data
        return data

    # -- boot / poll --------------------------------------------------------
    def _boot(self, settings: dict[str, Any]) -> None:
        args = as_namespace(settings)
        try:
            host = args.host
            iface = args.iface
            if not host:
                host = link.discover_player(4.0)
                if not host and args.mode != "vcdj":
                    try:
                        host, found_iface = link.discover_by_capture(args.tshark)
                    except RuntimeError:
                        host, found_iface = None, None
                    if found_iface and not iface:
                        iface = found_iface
                args.iface = iface

            net = link.local_net(host or "255.255.255.255")
            source = build_source(args, net)
            monitor = Monitor(host, source, args.cache, local_ip=net.ip)
            monitor.start()

            server = None
            bound_port = int(args.port)
            if settings.get("start_web_server", True):
                Handler.monitor = monitor
                server, bound_port = open_http_server(args.port, Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self._server_thread = thread

            with self._lock:
                self._monitor = monitor
                self._server = server
                self._settings = settings
                self._bound_port = bound_port if server is not None else None

            if server is not None and bound_port != int(args.port):
                # Keep overlay URL / Open web panel in sync with the real port.
                self._settings["port"] = bound_port
                self.port_changed.emit(bound_port)

            detail = getattr(source, "description", "") or ""
            if host:
                detail = f"{detail} · {host}" if detail else str(host)
            if server is not None and bound_port != int(args.port):
                detail = (f"{detail} · http :{bound_port}" if detail
                          else f"http :{bound_port}")
            self._set_status("connected", detail)
        except Exception as exc:
            self._set_status("error", str(exc))

    def _tick(self) -> None:
        monitor = self._monitor
        if monitor is None:
            if self._status == "connecting":
                self.state_changed.emit({
                    "t": time.time(),
                    "decks": [],
                    "devices": [],
                    "packets": 0,
                    "mode": "",
                    "mode_kind": "",
                    "mode_detail": "",
                    "host": "",
                    "library": None,
                    "library_error": None,
                    "backend_status": self._status,
                    "backend_detail": self._detail,
                })
            return
        try:
            state = monitor.state()
        except Exception as exc:
            self._set_status("error", str(exc))
            return
        state["backend_status"] = self._status
        state["backend_detail"] = self._detail
        self.state_changed.emit(state)

        # warm metadata for loaded tracks without blocking the UI thread hard
        for deck in state.get("decks") or []:
            tid = deck.get("track_id") or 0
            if tid and tid not in self._meta_cache:
                threading.Thread(target=self.meta, args=(tid,), daemon=True).start()

    def _set_status(self, status: str, detail: str) -> None:
        self._status = status
        self._detail = detail or ""
        self.status_changed.emit(status, self._detail)
