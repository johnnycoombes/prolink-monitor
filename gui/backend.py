"""Background Pro DJ Link session shared by the desktop UI."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import Handler, Monitor, QuietServer, build_source, open_http_server  # noqa: E402
from gui.settings import as_namespace, sessions_dir  # noqa: E402
from prolink import link  # noqa: E402


class Backend(QObject):
    """Owns the Monitor engine and optionally the HTTP panel server."""

    state_changed = Signal(dict)
    paint_tick = Signal()           # high-rate playhead / waveform paint
    status_changed = Signal(str, str)   # status key, detail message
    track_ready = Signal(int)           # track id whose meta/waveform is ready
    port_changed = Signal(int)          # actual bound HTTP port (may differ from settings)
    wnp_test_finished = Signal(bool, str)

    # Full Monitor.state() + Devices/Library refresh; paint runs separately.
    STATE_HZ = 20

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
        self._meta_cache: dict[str, dict] = {}
        self._wave_cache: dict[str, bytes] = {}
        self._art_cache: dict[str, bytes] = {}
        self._fetching: set[str] = set()
        self._meta_miss: dict[str, float] = {}

        self._state_timer = QTimer(self)
        self._state_timer.timeout.connect(self._tick)
        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self._paint_tick)

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

        # State/metadata at a fixed modest rate; playhead paint up to 60 Hz.
        self._state_timer.start(int(1000 / self.STATE_HZ))
        paint_hz = max(5, min(60, int(settings.get("poll_hz", 60))))
        self._paint_timer.start(max(1, int(1000 / paint_hz)))

    def stop(self) -> None:
        self._state_timer.stop()
        self._paint_timer.stop()
        with self._lock:
            monitor = self._monitor
            server = self._server
            self._monitor = None
            self._server = None
            self._bound_port = None
            self._meta_cache.clear()
            self._wave_cache.clear()
            self._art_cache.clear()
            self._fetching.clear()
            self._meta_miss.clear()
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
        from prolink.panel_launch import open_monitor_panel

        open_monitor_panel(f"http://127.0.0.1:{self.port}/")

    def push_display(self, prefs: dict[str, Any]) -> None:
        """Publish the live Monitor view to the web panel and overlay."""
        with self._lock:
            self._settings.update(prefs)
            monitor = self._monitor
        if monitor is not None:
            monitor.configure_display(self._settings)

    def open_overlay(self, url: str | None = None) -> None:
        webbrowser.open(url or f"http://127.0.0.1:{self.port}/overlay?preview=1")

    @staticmethod
    def _track_cache_id(track_id: int, track_key: str | None) -> str:
        key = (track_key or "").strip()
        return key if key else str(int(track_id))

    def meta(self, track_id: int, *, deck: int | None = None,
             track_key: str | None = None) -> dict | None:
        if not track_id or self._monitor is None:
            return None
        cache_id = self._track_cache_id(track_id, track_key)
        with self._lock:
            if cache_id in self._meta_cache:
                return self._meta_cache[cache_id]
        meta = self._monitor.meta(track_id, deck=deck)
        if meta:
            token = meta.get("track_key") or ""
            if track_key and token and token != track_key:
                return None
            with self._lock:
                self._meta_cache[self._track_cache_id(track_id, token or track_key)] = meta
            self.track_ready.emit(track_id)
        return meta

    def waveform(self, track_id: int, *, deck: int | None = None,
                 track_key: str | None = None) -> bytes | None:
        if not track_id or self._monitor is None:
            return None
        cache_id = self._track_cache_id(track_id, track_key)
        with self._lock:
            if cache_id in self._wave_cache:
                return self._wave_cache[cache_id]
        data = self._monitor.waveform(track_id, deck=deck)
        if data:
            meta = self._monitor.meta(track_id, deck=deck, load=False)
            token = (meta or {}).get("track_key") or track_key
            with self._lock:
                self._wave_cache[self._track_cache_id(track_id, token)] = data
            self.track_ready.emit(track_id)
        return data

    def artwork(self, track_id: int, *, deck: int | None = None,
                track_key: str | None = None) -> bytes | None:
        if not track_id or self._monitor is None:
            return None
        cache_id = self._track_cache_id(track_id, track_key)
        with self._lock:
            if cache_id in self._art_cache:
                return self._art_cache[cache_id]
        data = self._monitor.artwork(track_id, deck=deck)
        if data:
            meta = self._monitor.meta(track_id, deck=deck, load=False)
            token = (meta or {}).get("track_key") or track_key
            with self._lock:
                self._art_cache[self._track_cache_id(track_id, token)] = data
        return data

    def browse_tracks(self, query: str = "", *, limit: int = 100,
                      offset: int = 0) -> dict:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return {"tracks": [], "total": 0, "offset": 0, "limit": limit,
                    "query": query, "hosts": [], "multi_player": False}
        return mon.browse_tracks(query, limit=limit, offset=offset)

    def browse_playlists(self) -> dict:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return {"playlists": [], "history": [], "readable": False}
        return mon.browse_playlists()

    def browse_playlist_tracks(self, playlist_id: int, host: str | None = None) -> dict:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return {"id": playlist_id, "host": host, "tracks": []}
        return mon.browse_playlist_tracks(playlist_id, host)

    def loaded_tracks(self) -> list:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return []
        return mon.loaded_tracks()

    def start_link_capture(self, seconds: float) -> str:
        """Start a listen-only packet capture. Returns '' or an i18n key."""
        with self._lock:
            mon = self._monitor
        if mon is None:
            return "health_capture_offline"
        cap = getattr(mon.engine, "capture", None)
        if cap is None:
            return "health_capture_offline"
        try:
            return cap.start(float(seconds)) or ""
        except OSError:
            return "health_capture_offline"

    def start_session(self) -> None:
        with self._lock:
            mon = self._monitor
        if mon is not None:
            mon.session.start()

    def stop_session(self) -> list[str]:
        """Stop recording; auto-save exports when enabled. Returns saved paths."""
        with self._lock:
            mon = self._monitor
            settings = dict(self._settings)
        if mon is None:
            return []
        mon.session.stop()
        if not settings.get("session_autosave"):
            return []
        try:
            return mon.session.save_exports(sessions_dir(settings))
        except OSError:
            return []

    def clear_session(self) -> None:
        with self._lock:
            mon = self._monitor
        if mon is not None:
            mon.session.clear()

    def export_session(self, fmt: str = "json") -> str:
        """Return playlist text for fmt in csv|json|m3u."""
        with self._lock:
            mon = self._monitor
        if mon is None:
            return ""
        fmt = (fmt or "json").lower()
        if fmt == "csv":
            return mon.session.export_csv()
        if fmt == "m3u":
            return mon.session.export_m3u()
        return mon.session.export_json()

    def save_session_exports(self, directory: str | None = None) -> list[str]:
        with self._lock:
            mon = self._monitor
            settings = dict(self._settings)
        if mon is None:
            return []
        path = directory or sessions_dir(settings)
        try:
            return mon.session.save_exports(path)
        except OSError:
            return []

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
            monitor.configure_display(settings)
            if getattr(monitor, "wnp", None) is not None:
                monitor.wnp.configure(settings)
            monitor.start()

            server = None
            bound_port = int(args.port)
            if settings.get("start_web_server", True):
                Handler.monitor = monitor
                server, bound_port = open_http_server(
                    args.port, Handler, host=getattr(args, "http_host", "0.0.0.0") or "0.0.0.0",
                )
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

        # warm metadata for loaded tracks without blocking the UI thread hard.
        # Cap to one in-flight fetch per track_id; remember misses briefly so
        # rekordbox-linked tracks (no NFS metadata) do not spawn 20 threads/s.
        now = time.time()
        for deck in state.get("decks") or []:
            tid = deck.get("track_id") or 0
            if not tid:
                continue
            tkey = deck.get("track_key") or ""
            cache_id = self._track_cache_id(tid, tkey)
            deck_no = int(deck.get("number") or 0) or None
            with self._lock:
                if cache_id in self._meta_cache or cache_id in self._fetching:
                    continue
                miss_at = self._meta_miss.get(cache_id)
                if miss_at is not None and now - miss_at < 10.0:
                    continue
                self._fetching.add(cache_id)

            def _warm(track_id: int = tid, track_key: str = tkey,
                      deck_number: int | None = deck_no, cid: str = cache_id) -> None:
                try:
                    meta = self.meta(track_id, deck=deck_number, track_key=track_key or None)
                    if not meta:
                        with self._lock:
                            self._meta_miss[cid] = time.time()
                    else:
                        with self._lock:
                            self._meta_miss.pop(cid, None)
                finally:
                    with self._lock:
                        self._fetching.discard(cid)

            threading.Thread(target=_warm, daemon=True).start()

    def _paint_tick(self) -> None:
        if self._monitor is None:
            return
        self.paint_tick.emit()

    def test_wnp(self, settings: dict[str, Any]) -> None:
        """Check What's Now Playing without blocking the UI thread."""

        def run() -> None:
            from prolink.wnp import audience_token, test_connection

            deck, meta = self._audience_for_wnp()
            result = test_connection(settings, deck, meta)
            token = audience_token(deck) if result.ok else None
            with self._lock:
                mon = self._monitor
            publisher = getattr(mon, "wnp", None) if mon is not None else None
            if publisher is not None:
                try:
                    # An unsaved host or secret must not overwrite the live
                    # Health line for the endpoint that is actually in use.
                    if publisher.same_saved_target(settings):
                        publisher.note(result, token=token)
                except Exception:
                    pass
            self.wnp_test_finished.emit(bool(result.ok), result.detail)

        threading.Thread(target=run, name="wnp-test", daemon=True).start()

    def _audience_for_wnp(self) -> tuple[dict | None, dict | None]:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return None, None
        try:
            deck = mon.state(notify_wnp=False).get("audience_deck")
        except Exception:
            return None, None
        if not isinstance(deck, dict):
            return None, None
        try:
            meta = mon._audience_meta(deck)
        except Exception:
            meta = None
        return deck, meta

    def _set_status(self, status: str, detail: str) -> None:
        self._status = status
        self._detail = detail or ""
        self.status_changed.emit(status, self._detail)
