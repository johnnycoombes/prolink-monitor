"""Background Pro DJ Link session shared by the desktop UI."""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import webbrowser
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import Handler, Monitor, QuietServer, build_source, observe_net, open_http_server  # noqa: E402
from prolink.lru import BoundedLRU  # noqa: E402
from gui.settings import as_namespace, sessions_dir  # noqa: E402
from prolink import link  # noqa: E402
from prolink.web_server import close_http_server, start_http_thread  # noqa: E402


class Backend(QObject):
    """Owns the Monitor engine and optionally the HTTP panel server."""

    state_changed = Signal(dict)
    paint_tick = Signal()           # high-rate playhead / waveform paint
    status_changed = Signal(str, str)   # status key, detail message
    track_ready = Signal(int)           # track id whose meta/waveform is ready
    port_changed = Signal(int)          # actual bound HTTP port (may differ from settings)
    wnp_test_finished = Signal(bool, str)
    web_restarted = Signal()            # HTTP is accepting again; reload embedded pages
    web_changed = Signal()              # Health should re-read web_status()

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
        self._http_lock = threading.Lock()
        self._boot_gen = 0
        self._web_error = ""
        self._web_restarting = False
        self._status = "idle"
        self._detail = ""
        # Bounded: the tracks on a deck (hot set) stay; the rest is a ~50-track LRU.
        self._hot: set[str] = set()
        self._meta_cache: dict[str, dict] = BoundedLRU(50, pinned=lambda: self._hot)
        self._wave_cache: dict[str, bytes] = BoundedLRU(50, pinned=lambda: self._hot)
        self._art_cache: dict[str, bytes] = BoundedLRU(
            48, max_bytes=16 * 1024 * 1024, pinned=lambda: self._hot)
        self._fetching: set[str] = set()
        self._meta_miss: dict[str, float] = {}
        # One small worker pool for every cache miss (meta, waveform, artwork),
        # instead of a thread per track. Requests are de-duplicated by key and
        # a miss backs off (1 s, 2 s, 4 s ... 30 s) so a track whose waveform
        # is not readable yet is not asked for 20 times a second.
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prolink-warm")
        self._queued: set[tuple[str, str]] = set()
        self._miss: dict[tuple[str, str], tuple[float, float]] = {}

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
        gen = self._boot_gen
        self._set_status("connecting", "starting…")
        threading.Thread(
            target=self._boot, args=(dict(settings), gen), daemon=True, name="prolink-boot",
        ).start()

        # State/metadata at a fixed modest rate; playhead paint up to 60 Hz.
        self._state_timer.start(int(1000 / self.STATE_HZ))
        paint_hz = max(5, min(60, int(settings.get("poll_hz", 60))))
        self._paint_timer.start(max(1, int(1000 / paint_hz)))

    def stop(self) -> None:
        self._boot_gen += 1
        self._state_timer.stop()
        self._paint_timer.stop()
        with self._lock:
            monitor = self._monitor
            self._monitor = None
            self._bound_port = None
            self._meta_cache.clear()
            self._wave_cache.clear()
            self._art_cache.clear()
            self._fetching.clear()
            self._meta_miss.clear()
            self._queued.clear()
            self._miss.clear()
            self._hot.clear()
        self._stop_http()
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        self._set_status("idle", "")

    def web_status(self) -> dict:
        """Listening, client count, and the last web-server error for Health."""
        with self._http_lock:
            server = self._server
            restarting = self._web_restarting
        err = self._web_error
        if server is None:
            snap = {
                "listening": False,
                "host": "",
                "port": int(self._bound_port or 0),
                "clients": 0,
                "threads": 0,
                "last_error": err,
            }
        else:
            snap = server.snapshot()
            if err and not snap.get("last_error"):
                snap["last_error"] = err
        snap["restarting"] = restarting
        return snap

    def restart_web_server(self) -> None:
        """Stop and bind the panel server again, without tearing down Pro DJ Link."""
        with self._http_lock:
            if self._web_restarting:
                return
            self._web_restarting = True
        threading.Thread(
            target=self._restart_web_thread, name="prolink-http-restart", daemon=True,
        ).start()

    def _take_server(self):
        with self._http_lock:
            server, thread = self._server, self._server_thread
            self._server = None
            self._server_thread = None
            return server, thread

    def _stop_http(self) -> None:
        server, thread = self._take_server()
        close_http_server(server, thread)

    def _open_http(self, monitor, port: int, host: str):
        Handler.monitor = monitor
        server, bound = open_http_server(int(port), Handler, host=host or "0.0.0.0")
        thread = start_http_thread(server)
        return server, thread, int(bound)

    def _restart_web_thread(self) -> None:
        try:
            with self._lock:
                monitor = self._monitor
                settings = dict(self._settings)
            if monitor is None:
                self._web_error = "Connect first"
                return
            if not settings.get("start_web_server", True):
                self._web_error = "Web server is turned off in Settings"
                return
            self._stop_http()
            host = settings.get("http_bind") or "0.0.0.0"
            port = int(settings.get("port") or 8777)
            try:
                server, thread, bound = self._open_http(monitor, port, host)
            except Exception as exc:
                self._web_error = str(exc)
                return
            with self._lock:
                current = self._monitor
            if current is not monitor:
                close_http_server(server, thread)
                return
            with self._http_lock:
                with self._lock:
                    if self._monitor is not monitor:
                        orphan = (server, thread)
                    else:
                        self._server = server
                        self._server_thread = thread
                        self._bound_port = bound
                        self._web_error = ""
                        orphan = None
            if orphan is not None:
                close_http_server(orphan[0], orphan[1])
                return
            if bound != port:
                self.port_changed.emit(bound)
            self.web_restarted.emit()
        except Exception as exc:
            self._web_error = str(exc)
        finally:
            self._web_restarting = False
            self.web_changed.emit()

    def open_web_panel(self) -> None:
        """Open the monitor page in a normal browser tab. Never fullscreen."""
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

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

    MISS_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

    def _backing_off(self, kind: str, cache_id: str, now: float) -> bool:
        hit = self._miss.get((kind, cache_id))
        return hit is not None and now < hit[0]

    def _note_miss(self, kind: str, cache_id: str, now: float) -> None:
        prev = self._miss.get((kind, cache_id))
        step = 0 if prev is None else min(len(self.MISS_BACKOFF_S) - 1, int(prev[1]) + 1)
        self._miss[(kind, cache_id)] = (now + self.MISS_BACKOFF_S[step], step)

    def _request(self, kind: str, track_id: int, deck: int | None,
                 track_key: str | None) -> None:
        """Queue a background read for a cache miss (never blocks the caller)."""
        cache_id = self._track_cache_id(track_id, track_key)
        now = time.monotonic()
        with self._lock:
            if self._monitor is None or (kind, cache_id) in self._queued:
                return
            if self._backing_off(kind, cache_id, now):
                return
            self._queued.add((kind, cache_id))
        fetch = {"meta": self.meta, "waveform": self.waveform, "artwork": self.artwork}[kind]

        def job() -> None:
            got = None
            try:
                got = fetch(track_id, deck=deck, track_key=track_key)
            except Exception:
                got = None
            finally:
                with self._lock:
                    self._queued.discard((kind, cache_id))
                    if got:
                        self._miss.pop((kind, cache_id), None)
                    else:
                        self._note_miss(kind, cache_id, time.monotonic())
            if got:
                self.track_ready.emit(track_id)

        try:
            self._pool.submit(job)
        except RuntimeError:            # pool shut down
            with self._lock:
                self._queued.discard((kind, cache_id))

    def shutdown_workers(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def set_hot_tracks(self, keys) -> None:
        """Track keys on a deck right now; their cache entries are never evicted."""
        with self._lock:
            self._hot = set(keys)

    # UI-thread API: cache only. A miss queues a background read and returns None;
    # track_ready fires when it lands. No NFS, dbserver or file I/O here.
    def meta_cached(self, track_id: int, *, deck: int | None = None,
                    track_key: str | None = None) -> dict | None:
        if not track_id:
            return None
        with self._lock:
            hit = self._meta_cache.get(self._track_cache_id(track_id, track_key))
        if hit is None:
            self._request("meta", track_id, deck, track_key)
        return hit

    def waveform_cached(self, track_id: int, *, deck: int | None = None,
                        track_key: str | None = None) -> bytes | None:
        if not track_id:
            return None
        with self._lock:
            hit = self._wave_cache.get(self._track_cache_id(track_id, track_key))
        if hit is None:
            self._request("waveform", track_id, deck, track_key)
        return hit

    def artwork_cached(self, track_id: int, *, deck: int | None = None,
                       track_key: str | None = None) -> bytes | None:
        if not track_id:
            return None
        with self._lock:
            hit = self._art_cache.get(self._track_cache_id(track_id, track_key))
        if hit is None:
            self._request("artwork", track_id, deck, track_key)
        return hit

    # Blocking versions: worker threads, web handlers and tests only.
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

    def xz_probe_report(self) -> dict:
        with self._lock:
            mon = self._monitor
        if mon is None:
            from prolink import allinone

            return {"error": "not connected",
                    "adapters": [a.as_dict() for a in allinone.list_adapters()]}
        return mon.xz_probe_report()

    def library_diagnostics(self) -> dict:
        with self._lock:
            mon = self._monitor
        if mon is None:
            return {"hosts": [], "error": "not connected"}
        return mon.library_diagnostics()

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
    def _boot(self, settings: dict[str, Any], gen: int) -> None:
        args = as_namespace(settings)
        server = None
        thread = None
        monitor = None
        try:
            if gen != self._boot_gen:
                return
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
            if gen != self._boot_gen:
                return

            net = (observe_net(host, args.iface) if args.mode == "observe" else None) \
                or link.local_net(host or "255.255.255.255")
            source = build_source(args, net)
            monitor = Monitor(host, source, args.cache, local_ip=net.ip)
            monitor.configure_display(settings)
            if getattr(monitor, "wnp", None) is not None:
                monitor.wnp.configure(settings)
            monitor.start()

            server = None
            thread = None
            bound_port = int(args.port)
            if settings.get("start_web_server", True):
                if gen != self._boot_gen:
                    monitor.stop()
                    return
                server, thread, bound_port = self._open_http(
                    monitor, args.port, getattr(args, "http_host", "0.0.0.0") or "0.0.0.0",
                )

            with self._lock:
                if gen != self._boot_gen:
                    orphan = True
                else:
                    self._monitor = monitor
                    self._settings = settings
                    self._bound_port = bound_port if server is not None else None
                    orphan = False
            if server is not None:
                with self._http_lock:
                    if orphan or gen != self._boot_gen or self._monitor is not monitor:
                        orphan = True
                    else:
                        self._server = server
                        self._server_thread = thread
                        self._web_error = ""
            if orphan:
                close_http_server(server, thread)
                with self._lock:
                    if self._monitor is monitor:
                        self._monitor = None
                        self._bound_port = None
                try:
                    monitor.stop()
                except Exception:
                    pass
                return

            if server is not None and bound_port != int(args.port):
                # Tell the UI which port is listening. Leave the saved port
                # alone so the next launch still tries what the user chose.
                self.port_changed.emit(bound_port)

            if gen != self._boot_gen:
                return
            detail = getattr(source, "description", "") or ""
            if host:
                detail = f"{detail} · {host}" if detail else str(host)
            if server is not None and bound_port != int(args.port):
                detail = (f"{detail} · http :{bound_port}" if detail
                          else f"http :{bound_port}")
            self._set_status("connected", detail)
        except Exception as exc:
            close_http_server(server, thread)
            if monitor is not None and self._monitor is not monitor:
                try:
                    monitor.stop()
                except Exception:
                    pass
            if gen == self._boot_gen:
                self._web_error = str(exc)
                self._set_status("error", str(exc))

    def _tick(self) -> None:
        monitor = self._monitor
        if monitor is None:
            if self._status == "connecting":
                self.state_changed.emit(self._with_web({
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
                }))
            return
        try:
            state = monitor.state()
        except Exception as exc:
            self._set_status("error", str(exc))
            return
        state["backend_status"] = self._status
        state["backend_detail"] = self._detail
        self.state_changed.emit(self._with_web(state))

        # Warm metadata for loaded tracks on the bounded worker pool. The same
        # de-duplication and miss backoff apply as for the UI's cache reads.
        hot = set()
        for deck in state.get("decks") or []:
            tid = deck.get("track_id") or 0
            if not tid:
                continue
            tkey = deck.get("track_key") or ""
            hot.add(self._track_cache_id(tid, tkey))
            deck_no = int(deck.get("number") or 0) or None
            with self._lock:
                if self._track_cache_id(tid, tkey) in self._meta_cache:
                    continue
            self._request("meta", tid, deck_no, tkey or None)
        self.set_hot_tracks(hot)

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

    def _with_web(self, state: dict) -> dict:
        try:
            state["web"] = self.web_status()
        except Exception:
            state["web"] = {
                "listening": False, "host": "", "port": 0,
                "clients": 0, "threads": 0, "last_error": self._web_error,
                "restarting": False,
            }
        return state

    def _set_status(self, status: str, detail: str) -> None:
        self._status = status
        self._detail = detail or ""
        self.status_changed.emit(status, self._detail)
