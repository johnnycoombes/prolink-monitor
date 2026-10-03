"""Pro DJ Link monitor: a web server showing what is playing on each deck, live.

Typical use:

    python app.py                       auto-detect the mode and open a browser
    python app.py --mode sniffer        passive capture, coexists with rekordbox
    python app.py --mode vcdj           virtual device (rekordbox closed)
    python app.py --host 192.168.2.100  player address, to read its media
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prolink import anlz, link, proto                     # noqa: E402
from prolink.library import Library                       # noqa: E402
from prolink import onelibrary                            # noqa: E402
from prolink.mixstatus import MixStatus, MixStatusConfig  # noqa: E402
from prolink.session import SessionRecorder               # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

OVERVIEW_COLUMNS = 1600          # resolution of the track overview we send


class Monitor:
    """Joins the network engine with the library and exposes resolved state."""

    def __init__(self, host: str | None, source, cache_dir: str | None = None,
                 local_ip: str | None = None):
        self.host = host
        self.local_ip = local_ip      # to ignore our own announcement
        self.engine = link.ProLink(source)
        self.library = Library(cache_dir)
        self.engine.on_track_change = self._on_track_change
        # Keyed by (host, export, pdb fingerprint, track_id) so a stick swap
        # that reuses player track IDs cannot serve the previous USB's waves.
        self._waveforms: dict[tuple, bytes] = {}
        self._meta: dict[tuple, dict] = {}
        # which player each track was read from: with separate CDJs the track
        # loaded on one deck often lives on another deck's USB drive
        self._track_host: dict[int, str] = {}
        self._lock = threading.RLock()
        self.mixstatus = MixStatus(MixStatusConfig())
        self.session = SessionRecorder()
        # Library totals are expensive (PDB counts); reuse until media set changes.
        self._library_cache_key: tuple | None = None
        self._library_cache: tuple[dict | None, str | None, dict | None] = (None, None, None)

    def _cache_key(self, host: str, media, track_id: int) -> tuple:
        fp = getattr(media, "_pdb_fingerprint", None) or (0, 0)
        return (host, getattr(media, "export", "") or "", fp, track_id)
    def start(self) -> None:
        self.engine.start()
        threading.Thread(target=self._preload, daemon=True).start()

    def stop(self) -> None:
        self.engine.stop()
        self.library.close()

    def _preload(self) -> None:
        """Open the NFS connection in the background so the first track is instant."""
        for _ in range(30):
            if self.host:
                break
            players = self.engine.players(exclude_ip=self.local_ip)
            if players:
                self.host = players[0].ip
                print(f"  player found at {self.host}")
                break
            time.sleep(0.5)
        if not self.host:
            return
        # USB may not be mounted yet — retry rather than stick on the first miss.
        for _ in range(12):
            if self.library.get(self.host) is not None:
                return
            self.library.retry(self.host)
            time.sleep(2.0)

    # -- tracks -------------------------------------------------------------
    def _host_for(self, status) -> str | None:
        """Address of the player holding the medium a track was loaded from.

        On an all-in-one every deck reports itself, but with separate players
        `loaded_from` points at whichever one has the USB drive in it.
        """
        if status is not None and status.loaded_from:
            with self.engine.lock:
                device = self.engine.devices.get(status.loaded_from)
            if device is not None:
                return device.ip
        return self.host

    def _on_track_change(self, deck: link.Deck, track_id: int) -> None:
        """A track was loaded: fetch its beat grid for the playhead and its waveform."""
        if not track_id:
            return
        status = deck.status
        if status is not None and status.slot == "rekordbox":
            return                       # streamed from rekordbox, not on any medium
        host = self._host_for(status)
        if not host:
            return
        media = self.library.get(host)
        if media is None:
            self.library.retry(host)
            media = self.library.get(host)
        if media is None:
            return
        with self._lock:
            self._track_host[track_id] = host
        try:
            a = media.analysis(track_id)
        except Exception:
            return
        if a is None:
            return
        # Fast loads can finish after the deck already moved on — never attach
        # another track's grid to the current one.
        current = deck.status
        if current is None or current.track_id != track_id:
            self._build(host, media, track_id, a, media.track(track_id))
            return
        t = media.track(track_id)
        length = a.duration_ms or ((t.duration * 1000) if t else 0)
        deck.set_beat_grid([b.time for b in a.beats], length)
        self._build(host, media, track_id, a, t)

    def _build(self, host: str, media, track_id: int, a: anlz.Analysis, t) -> None:
        """Prepare and cache the binary waveform and metadata of a track."""
        key = self._cache_key(host, media, track_id)
        with self._lock:
            if key in self._waveforms:
                return

        heights, rgb, lows, mids, highs, blue_h, blue_rgb = anlz.waveform_levels(a)

        if heights:
            ov_h, ov_rgb = anlz.downsample(heights, rgb, OVERVIEW_COLUMNS)
            ov_l, ov_m, ov_hi = anlz.downsample_bands(lows, mids, highs, OVERVIEW_COLUMNS)
        else:
            ov_h, ov_rgb = bytearray(), bytearray()
            ov_l, ov_m, ov_hi = bytearray(), bytearray(), bytearray()
        if blue_h:
            ov_blue_h, ov_blue_rgb = anlz.downsample(blue_h, blue_rgb, OVERVIEW_COLUMNS)
        else:
            ov_blue_h, ov_blue_rgb = bytearray(), bytearray()

        length = a.duration_ms or ((t.duration * 1000) if t else 0)
        cps = anlz.DETAIL_COLUMNS_PER_SECOND
        # PLWF is the RGB waveform. PLWB is the 3-band heights (Beat Link's
        # per-band scale). PLBC is the blue waveform and its COLOR_MAP shades.
        payload = (_pack_wave(heights, rgb, cps, length)
                   + _pack_wave(ov_h, ov_rgb, 0.0, length)
                   + _pack_bands(lows, mids, highs, cps, length)
                   + _pack_bands(ov_l, ov_m, ov_hi, 0.0, length)
                   + _pack_blue(blue_h, blue_rgb, cps, length)
                   + _pack_blue(ov_blue_h, ov_blue_rgb, 0.0, length))

        meta = {
            "id": track_id,
            "title": (t.title if t else "") or "",
            "artist": t.artist if t else "",
            "album": t.album if t else "",
            "genre": t.genre if t else "",
            "key": t.key if t else "",
            "label": t.label if t else "",
            "comment": t.comment if t else "",
            "year": t.year if t else 0,
            "rating": t.rating if t else 0,
            "track_bpm": t.tempo if t else 0.0,
            "duration_ms": length,
            "bitrate": t.bitrate if t else 0,
            "has_artwork": bool(t and t.artwork_path),
            "beats": [[b.time, b.number] for b in a.beats],
            "cues": [{"hot": c.hot_cue, "type": c.type, "t": c.time,
                      "end": c.loop_time, "color": c.color, "text": c.comment}
                     for c in a.cues],
            "phrases": [{"beat": p.beat, "kind": p.kind, "text": p.label}
                        for p in a.phrases],
            "detail_columns": len(heights),
            "overview_columns": len(ov_h),
        }
        with self._lock:
            self._waveforms[key] = payload
            self._meta[key] = meta
            while len(self._waveforms) > 12:
                oldest = next(iter(self._waveforms))
                self._waveforms.pop(oldest, None)
                self._meta.pop(oldest, None)

    def _media_for(self, track_id: int):
        """The medium a track lives on, from whichever player supplied it."""
        with self._lock:
            host = self._track_host.get(track_id) or self.host
        return (host, self.library.get(host)) if host else (None, None)

    def ensure(self, track_id: int) -> None:
        """Load a track on demand when it has not been prepared yet."""
        host, media = self._media_for(track_id)
        if not host or media is None:
            return
        key = self._cache_key(host, media, track_id)
        with self._lock:
            if key in self._meta:
                return
        a = media.analysis(track_id)
        if a is not None:
            self._build(host, media, track_id, a, media.track(track_id))

    def waveform(self, track_id: int) -> bytes | None:
        self.ensure(track_id)
        host, media = self._media_for(track_id)
        if not host or media is None:
            return None
        key = self._cache_key(host, media, track_id)
        with self._lock:
            return self._waveforms.get(key)

    def meta(self, track_id: int, *, load: bool = True) -> dict | None:
        """Return cached track metadata. With load=True, may hit NFS once."""
        if load:
            self.ensure(track_id)
        host, media = self._media_for(track_id)
        if not host or media is None:
            return None
        key = self._cache_key(host, media, track_id)
        with self._lock:
            return self._meta.get(key)

    def artwork(self, track_id: int) -> bytes | None:
        _host, media = self._media_for(track_id)
        return media.artwork(track_id) if media else None

    def browse_tracks(self, query: str = "", *, limit: int = 100,
                      offset: int = 0) -> dict:
        """Search classic export.pdb tracks across mounted media."""
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        with self.library.lock:
            media_list = list(self.library.media.items())
        all_rows: list[dict] = []
        hosts: list[str] = []
        for host, media in media_list:
            if media.db is None:
                continue
            hosts.append(host)
            rows, _total = media.db.search(query, limit=10_000, offset=0)
            for r in rows:
                r = dict(r)
                r["host"] = host
                r["source"] = "pdb"
                all_rows.append(r)
        all_rows.sort(key=lambda r: ((r.get("title") or "").lower(), r.get("id") or 0))
        total = len(all_rows)
        return {
            "tracks": all_rows[offset:offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "query": query or "",
            "hosts": hosts,
            "multi_player": len(hosts) > 1,
        }

    def browse_playlists(self) -> dict:
        with self.library.lock:
            media_list = list(self.library.media.items())
        playlists: list[dict] = []
        history: list[dict] = []
        for host, media in media_list:
            ol = getattr(media, "onelibrary_db", None)
            if ol is None:
                continue
            for p in onelibrary.list_playlists(ol):
                p = dict(p)
                p["host"] = host
                playlists.append(p)
            for h in onelibrary.list_history(ol):
                h = dict(h)
                h["host"] = host
                history.append(h)
        return {
            "playlists": playlists,
            "history": history,
            "readable": bool(playlists or history),
        }

    def browse_playlist_tracks(self, playlist_id: int, host: str | None = None,
                               *, limit: int = 200) -> dict:
        with self.library.lock:
            media_list = list(self.library.media.items())
        for h, media in media_list:
            if host and h != host:
                continue
            ol = getattr(media, "onelibrary_db", None)
            if ol is None:
                continue
            tracks = onelibrary.playlist_tracks(ol, playlist_id, limit=limit)
            for t in tracks:
                t["host"] = h
            return {"id": playlist_id, "host": h, "tracks": tracks}
        return {"id": playlist_id, "host": host, "tracks": []}

    def loaded_tracks(self) -> list[dict]:
        """Tracks currently loaded on decks (for artwork grid / quick filter)."""
        rows = []
        seen: set[int] = set()
        for d in self.engine.active_decks():
            s = d.status
            if s is None or not s.track_id or s.track_id in seen:
                continue
            seen.add(s.track_id)
            meta = self.meta(s.track_id) or {}
            rows.append({
                "id": s.track_id,
                "deck": d.number,
                "title": meta.get("title") or "",
                "artist": meta.get("artist") or "",
                "key": meta.get("key") or "",
                "bpm": meta.get("track_bpm") or 0,
                "has_artwork": bool(meta.get("has_artwork")),
                "source": "deck",
            })
        return rows

    def _library_snapshot(self) -> tuple[dict | None, str | None, dict | None]:
        """Cached library totals / OneLibrary info for the hot state() path."""
        with self.library.lock:
            media_list = list(self.library.media.values())
            first_error = next(iter(self.library.errors.values()), None)
        key = tuple(
            (getattr(m, "host", None), getattr(m, "export", None),
             getattr(m, "_pdb_fingerprint", None),
             bool(getattr(m, "db", None)),
             id(getattr(m, "onelibrary", None)))
            for m in media_list
        )
        if key == self._library_cache_key:
            return self._library_cache
        totals: dict[str, int | str] = {}
        onelibrary_info = None
        for m in media_list:
            if m.db is None:
                continue
            for k, v in m.db.counts().items():
                totals[k] = totals.get(k, 0) + v
            ol = getattr(m, "onelibrary", None)
            if ol is not None and ol.present:
                onelibrary_info = {
                    "present": True,
                    "readable": ol.readable,
                    "tracks": ol.tracks,
                    "playlists": ol.playlists,
                    "history": ol.history,
                    "detail": ol.detail,
                    "error": ol.error,
                }
                if ol.readable:
                    totals["onelibrary_playlists"] = (
                        int(totals.get("onelibrary_playlists", 0)) + ol.playlists)
                    totals["onelibrary_history"] = (
                        int(totals.get("onelibrary_history", 0)) + ol.history)
        snap = (totals or None, None if totals else first_error, onelibrary_info)
        self._library_cache_key = key
        self._library_cache = snap
        return snap

    # -- state --------------------------------------------------------------
    def paint_state(self) -> dict:
        """Lightweight playhead snapshot for high-rate SSE / paint clients."""
        decks = []
        for d in self.engine.active_decks():
            s = d.status
            if s is None:
                continue
            decks.append({
                "number": d.number,
                "track_id": s.track_id,
                "playing": d.is_playing,
                "bpm": round(s.effective_bpm, 2),
                "track_bpm": round(s.bpm, 2),
                "pitch": round(s.pitch_percent, 2),
                "speed": round(s.speed, 6),
                "position_ms": round(d.position_ms, 1),
                "duration_ms": d.track_length_ms,
                "beat": s.beat_count,
                "bar": s.beat_in_bar,
                "state": s.play_state,
                "master": s.master,
                "sync": s.sync,
                "on_air": s.on_air,
                "position_source": d.position_source,
            })
        return {"t": time.time(), "paint": True, "decks": decks}

    def state(self) -> dict:
        decks = []
        for d in self.engine.active_decks():
            s = d.status
            if s is None:
                continue
            decks.append({
                "number": d.number,
                "name": s.name,
                "firmware": s.firmware,
                "track_id": s.track_id,
                "slot": s.slot,
                "track_type": s.track_type,
                "loaded_from": s.loaded_from,
                "state": s.play_state,
                "playing": d.is_playing,
                "bpm": round(s.effective_bpm, 2),
                "track_bpm": round(s.bpm, 2),
                "pitch": round(s.pitch_percent, 2),
                "speed": round(s.speed, 6),
                "position_ms": round(d.position_ms, 1),
                "duration_ms": d.track_length_ms,
                "beat": s.beat_count,
                "bar": s.beat_in_bar,
                "beat_packets": d.beat_packets,
                "absolute_packets": d.absolute_packets,
                "position_source": d.position_source,
                "cue_in": s.cue_distance if s.cue_distance != 0x1FF else None,
                "master": s.master,
                "sync": s.sync,
                "on_air": s.on_air,
            })
        # Enrich from cache only — never NFS on the hot path (ensure runs on track change).
        for snap in decks:
            tid = snap.get("track_id") or 0
            meta = self.meta(tid, load=False) if tid else None
            if meta:
                snap["title"] = meta.get("title") or ""
                snap["artist"] = meta.get("artist") or ""
                snap["key"] = meta.get("key") or ""
            self.mixstatus.handle(snap)
        # One observe pass: set-clock pause/resume + track logging.
        self.session.observe(decks)
        with self.engine.lock:
            devices = [{"number": a.device_number, "name": a.name,
                        "kind": a.kind, "ip": a.ip}
                       for a in sorted(self.engine.devices.values(),
                                       key=lambda x: x.device_number)]
        totals, library_error, onelibrary_info = self._library_snapshot()
        mix = self.mixstatus.as_state()
        return {
            "t": time.time(),
            "paint": False,
            "decks": decks,
            "devices": devices,
            "packets": self.engine.packets,
            "mode": getattr(self.engine.source, "description", ""),
            "mode_kind": getattr(self.engine.source, "kind", ""),
            "mode_detail": getattr(self.engine.source, "detail", ""),
            "host": self.host or "",
            "library": totals,
            "library_error": library_error,
            "onelibrary": onelibrary_info,
            "mix": mix,
            "now_playing": mix.get("now_playing"),
            "pending": mix.get("pending"),
            "setlist": mix.get("setlist") or [],
            "session": self.session.as_state(),
        }


def _pack_wave(heights, rgb, columns_per_second: float, duration_ms: int) -> bytes:
    """Pack a waveform: a 24-byte header, then the heights, then the RGB."""
    n = len(heights)
    header = struct.pack("<4sIIfII", b"PLWF", 1, n, columns_per_second, duration_ms, 0)
    return header + bytes(heights) + bytes(rgb)


def _pack_bands(lows, mids, highs, columns_per_second: float, duration_ms: int) -> bytes:
    """Pack low/mid/high lanes. Same 24-byte header as PLWF, magic PLWB."""
    n = len(lows)
    header = struct.pack("<4sIIfII", b"PLWB", 1, n, columns_per_second, duration_ms, 0)
    return header + bytes(lows) + bytes(mids) + bytes(highs)


def _pack_blue(heights, rgb, columns_per_second: float, duration_ms: int) -> bytes:
    """Pack the blue waveform: heights, then COLOR_MAP RGB. Magic PLBC."""
    n = len(heights)
    header = struct.pack("<4sIIfII", b"PLBC", 1, n, columns_per_second, duration_ms, 0)
    return header + bytes(heights) + bytes(rgb)


# ---------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    monitor: Monitor = None            # injected at start-up
    protocol_version = "HTTP/1.1"

    TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".js": "text/javascript; charset=utf-8", ".png": "image/png",
             ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".woff2": "font/woff2"}

    def log_message(self, fmt, *args):    # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store",
              headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                return self._file("index.html", "text/html; charset=utf-8")
            if path in ("/overlay", "/overlay.html"):
                return self._file("overlay.html", "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(self.monitor.state())
            if path == "/api/setlist":
                return self._json(self.monitor.mixstatus.as_state())
            if path == "/api/session":
                return self._json(self.monitor.session.as_state())
            if path == "/api/session/start":
                self.monitor.session.start()
                return self._json(self.monitor.session.as_state())
            if path == "/api/session/stop":
                self.monitor.session.stop()
                return self._json(self.monitor.session.as_state())
            if path == "/api/session/clear":
                self.monitor.session.clear()
                return self._json(self.monitor.session.as_state())
            if path == "/api/session/export":
                from urllib.parse import parse_qs
                fmt = (parse_qs(parsed.query).get("fmt") or ["json"])[0].lower()
                session = self.monitor.session
                if fmt == "csv":
                    body = session.export_csv()
                    ctype = "text/csv; charset=utf-8"
                    name = "session.csv"
                elif fmt == "m3u":
                    body = session.export_m3u()
                    ctype = "audio/x-mpegurl; charset=utf-8"
                    name = "session.m3u"
                else:
                    body = session.export_json()
                    ctype = "application/json; charset=utf-8"
                    name = "session.json"
                return self._send(
                    200, body.encode("utf-8"), ctype,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'},
                )
            if path == "/api/library/tracks":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                q = (qs.get("q") or [""])[0]
                try:
                    limit = int((qs.get("limit") or ["100"])[0])
                except ValueError:
                    limit = 100
                try:
                    offset = int((qs.get("offset") or ["0"])[0])
                except ValueError:
                    offset = 0
                return self._json(self.monitor.browse_tracks(q, limit=limit, offset=offset))
            if path == "/api/library/playlists":
                return self._json(self.monitor.browse_playlists())
            if path.startswith("/api/library/playlist/"):
                from urllib.parse import parse_qs
                pid = int(path.rsplit("/", 1)[1])
                qs = parse_qs(parsed.query)
                host = (qs.get("host") or [None])[0] or None
                try:
                    limit = int((qs.get("limit") or ["200"])[0])
                except ValueError:
                    limit = 200
                return self._json(self.monitor.browse_playlist_tracks(pid, host, limit=limit))
            if path == "/api/library/loaded":
                return self._json({"tracks": self.monitor.loaded_tracks()})
            if path == "/api/events":
                return self._events()
            if path.startswith("/api/track/"):
                track_id = int(path.rsplit("/", 1)[1])
                meta = self.monitor.meta(track_id)
                return self._json(meta or {"error": "no_analysis"}, 200 if meta else 404)
            if path.startswith("/api/waveform/"):
                track_id = int(path.rsplit("/", 1)[1])
                data = self.monitor.waveform(track_id)
                if not data:
                    return self._json({"error": "no_waveform"}, 404)
                return self._send(200, data, "application/octet-stream", "max-age=3600")
            if path.startswith("/api/artwork/"):
                track_id = int(path.rsplit("/", 1)[1])
                art = self.monitor.artwork(track_id)
                if not art:
                    return self._json({"error": "no_artwork"}, 404)
                return self._send(200, art, "image/jpeg", "max-age=3600")
            if not path.startswith("/api/"):
                return self._static(path)
            return self._json({"error": "not_found"}, 404)
        except (ValueError, KeyError):
            self._json({"error": "bad_request"}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _static(self, path: str) -> None:
        """Serve files out of web/, without letting anyone escape that folder."""
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return self._json({"error": "not_found"}, 404)
        ext = os.path.splitext(full)[1].lower()
        self._file(os.path.relpath(full, WEB_DIR),
                   self.TYPES.get(ext, "application/octet-stream"))

    def _file(self, name: str, ctype: str) -> None:
        try:
            with open(os.path.join(WEB_DIR, name), "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._json({"error": "not_found"}, 404)

    def _events(self) -> None:
        """SSE: fat state ~15 Hz, lightweight playhead paint at 60 Hz.

        Matches the desktop split (20 Hz metadata / 60 Hz paint). Clients that
        only need smooth playheads merge ``paint: true`` frames into the last
        full state.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        paint_hz = 60.0
        full_hz = 15.0
        full_every = max(1, int(round(paint_hz / full_hz)))
        tick = 0
        try:
            while True:
                if tick % full_every == 0:
                    payload = json.dumps(self.monitor.state(), ensure_ascii=False)
                else:
                    payload = json.dumps(self.monitor.paint_state(),
                                        ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                tick += 1
                time.sleep(1.0 / paint_hz)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class QuietServer(ThreadingHTTPServer):
    """A browser closing an SSE stream is normal, not something to dump a stack for."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# Fallback ports when the requested one is blocked (common on Windows when Hyper-V
# or another service has reserved the default range — WinError 10013).
_HTTP_PORT_FALLBACKS = (8787, 8877, 9777, 18777, 0)


def open_http_server(port: int, handler=None, host: str = "127.0.0.1"):
    """Bind the panel HTTP server, trying alternate ports if ``port`` is blocked.

    Returns ``(server, actual_port)``. Raises ``OSError`` if nothing will bind.
    Port ``0`` asks the OS for any free port.
    """
    handler = handler or Handler
    wanted = int(port)
    tried: list[int] = []
    errors: list[tuple[int, OSError]] = []

    candidates = [wanted]
    for alt in _HTTP_PORT_FALLBACKS:
        if alt not in candidates:
            candidates.append(alt)

    for candidate in candidates:
        tried.append(candidate)
        try:
            server = QuietServer((host, candidate), handler)
        except OSError as exc:
            errors.append((candidate, exc))
            continue
        actual = int(server.server_address[1])
        return server, actual

    # Prefer the error from the port the user asked for.
    primary = next((e for p, e in errors if p == wanted), errors[-1][1])
    detail = "; ".join(f"{p}: {e}" for p, e in errors[:3])
    raise OSError(
        f"could not bind HTTP port (tried {tried}). "
        f"Pass --port with a free number. Last errors: {detail}"
    ) from primary


# -------------------------------------------------------------------- start-up

def build_source(args, net: link.NetInfo):
    """Pick the packet source according to the requested mode."""
    if args.mode == "sniffer":
        return link.SnifferSource(net, args.tshark, args.iface)
    if args.mode == "vcdj":
        return link.SocketSource(net, args.number, args.name)
    # auto: prefer the virtual device; fall back to passive capture when the
    # ports are taken
    src = link.SocketSource(net, args.number, args.name)
    probe = link.socket.socket(link.socket.AF_INET, link.socket.SOCK_DGRAM)
    probe.setsockopt(link.socket.SOL_SOCKET, link.socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", proto.PORT_STATUS))
        probe.close()
        return src
    except OSError:
        probe.close()
        print("  ! UDP ports 50000-50002 are busy (rekordbox running?),\n"
              "    switching to passive capture. Close rekordbox if you want the "
              "virtual device mode.")
        return link.SnifferSource(net, args.tshark, args.iface)


def main() -> int:
    p = argparse.ArgumentParser(description="Pro DJ Link monitor for AlphaTheta gear")
    p.add_argument("--host", default=None,
                   help="player address to read the library from "
                        "(auto-detected from the network when omitted)")
    p.add_argument("--mode", choices=["auto", "vcdj", "sniffer"], default="auto")
    p.add_argument("--port", type=int, default=8777, help="web server port")
    p.add_argument("--number", type=int, default=5,
                   help="virtual device number (1-6, avoid the ones in use)")
    p.add_argument("--name", default="monitor", help="name to announce ourselves with")
    p.add_argument("--tshark", default=None, help="path to tshark")
    p.add_argument("--iface", default=None,
                   help="capture interface for sniffer mode (see \"tshark -D\")")
    p.add_argument("--cache", default=None, help="cache folder")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = p.parse_args()

    print("Pro DJ Link monitor")
    host = args.host
    iface = args.iface
    if not host:
        print("  looking for a player on the network...")
        host = link.discover_player(4.0)
        if not host and args.mode != "vcdj":
            # port 50000 is taken (rekordbox), so listen through the capture driver
            # instead: it also tells us which interface the traffic is really on
            print("  port 50000 is busy, looking through the capture driver...")
            try:
                host, found_iface = link.discover_by_capture(args.tshark)
            except RuntimeError:
                host, found_iface = None, None
            if found_iface and not iface:
                iface = found_iface
        if host:
            print(f"  player found at {host}")
        else:
            print("  ! no player answered. Pass --host if auto-detection misses it.")
    args.iface = iface
    net = link.local_net(host or "255.255.255.255")
    print(f"  local network: {net.ip}/{net.prefix} via \"{net.alias}\" "
          f"(broadcast {net.broadcast})")

    try:
        source = build_source(args, net)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        return 1

    monitor = Monitor(host, source, args.cache, local_ip=net.ip)
    Handler.monitor = monitor
    try:
        monitor.start()
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        return 1
    print(f"  mode: {source.description}")

    try:
        server, bound_port = open_http_server(args.port, Handler)
    except OSError as e:
        print(f"\nERROR: {e}")
        print("  On Windows, WinError 10013 usually means the port is reserved\n"
              "  (Hyper-V / excluded range) or in use. Try e.g. --port 18777")
        monitor.stop()
        return 1
    if bound_port != args.port:
        print(f"  ! port {args.port} was blocked; using {bound_port} instead "
              f"(pass --port to pick one)")
    url = f"http://127.0.0.1:{bound_port}/"
    print(f"  panel: {url}")
    print(f"  overlay: http://127.0.0.1:{bound_port}/overlay\n")

    if monitor.engine.wait_for_devices(6.0):
        for d in monitor.engine.active_decks():
            s = d.status
            print(f"  deck {d.number}: {s.play_state}, track {s.track_id or '-'}, "
                  f"{s.effective_bpm:.2f} BPM")
    else:
        print("  ! no deck is reporting status yet.")
        if isinstance(source, link.SocketSource):
            print("    Check the player is powered on and on the same network.")
        else:
            print("    In passive mode status is only visible while another program\n"
                  "    (rekordbox) is linked to the player.")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down...")
    finally:
        monitor.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
