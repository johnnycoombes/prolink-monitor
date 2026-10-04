"""Pro DJ Link monitor: a web server showing what is playing on each deck, live.

Typical use:

    python app.py                       auto-detect the mode and open a browser
    python app.py --mode sniffer        passive capture, coexists with rekordbox
    python app.py --mode vcdj           virtual device (rekordbox closed)
    python app.py --host 192.168.2.100  player address, to read its media
"""

from __future__ import annotations

import argparse
from typing import Any
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
from prolink import remotedb as remotedb_wire             # noqa: E402
from prolink.remotedb_client import RemoteDbBrowser, choose_requesting_player  # noqa: E402
from prolink.mixstatus import MixStatus, MixStatusConfig  # noqa: E402
from prolink.session import SessionRecorder               # noqa: E402
from prolink.audience_deck import AudienceDeckTracker, merge_live_deck  # noqa: E402
from prolink.capture import idle_capture_status  # noqa: E402
from prolink.track_key import keys_match, track_cache_key, track_key_token  # noqa: E402
from prolink.wnp import WnpPublisher  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _az_mode_value(engine) -> str | None:
    """Health-page value. Ignores stand-ins that are not a real mode string."""
    fn = getattr(engine, "az_mode", None)
    mode = fn() if callable(fn) else None
    if mode in ("pro_dj_link", "four_deck"):
        return mode
    return None


def _fields_from_track(track) -> dict:
    """Copy display fields off a pdb or OneLibrary track."""
    if track is None:
        return {}
    return {
        "title": getattr(track, "title", "") or "",
        "artist": getattr(track, "artist", "") or "",
        "album": getattr(track, "album", "") or "",
        "genre": getattr(track, "genre", "") or "",
        "key": getattr(track, "key", "") or "",
        "label": getattr(track, "label", "") or "",
        "comment": getattr(track, "comment", "") or "",
        "year": int(getattr(track, "year", 0) or 0),
        "rating": int(getattr(track, "rating", 0) or 0),
        "tempo": float(getattr(track, "tempo", 0) or 0),
        "duration_s": int(getattr(track, "duration", 0) or 0),
        "bitrate": int(getattr(track, "bitrate", 0) or 0),
        "artwork_id": int(getattr(track, "artwork_id", 0) or 0),
        "has_artwork": bool(
            getattr(track, "artwork_path", "")
            or getattr(track, "artwork_id", 0)
            or getattr(track, "file_path", "")
        ),
    }

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
        # Keyed by device + export + PDB fingerprint + slot + track_id.
        self._waveforms: dict[tuple, bytes] = {}
        self._meta: dict[tuple, dict] = {}
        # Latest resolved medium for each deck (see track_cache_key).
        self._deck_keys: dict[int, tuple] = {}
        self.show_phrases = True
        self._lock = threading.RLock()
        self.mixstatus = MixStatus(MixStatusConfig())
        self.audience = AudienceDeckTracker()
        self.session = SessionRecorder()
        # Library totals are expensive (PDB counts); reuse until media set changes.
        self._library_cache_key: tuple | None = None
        self._library_cache: tuple[dict | None, str | None, dict | None] = (None, None, None)
        self._remotedb_fail: dict[str, str] = {}
        self._art_cache: dict[tuple, tuple[bytes, str]] = {}
        self._art_source_by_deck: dict[int, str] = {}
        self._last_art_source: str = "none"
        self.wnp = WnpPublisher()

    def _device_number_for_host(self, host: str) -> int | None:
        with self.engine.lock:
            for ann in self.engine.devices.values():
                if ann.ip == host:
                    return int(ann.device_number)
        return None

    def _requesting_player_for_db(self) -> int:
        src = self.engine.source
        vn = getattr(src, "device_number", None)
        return choose_requesting_player(vn)

    def _db_slot_for_status(self, status) -> int:
        """Sr byte for this deck's loaded track, not for some other deck."""
        if status is None:
            return 0
        return remotedb_wire.slot_byte(
            getattr(status, "slot", "") or "",
            int(getattr(status, "slot_raw", 0) or 0),
        )

    def _library_db_slots(self) -> list[int]:
        """Every SD/USB slot currently loaded, in deck order. Defaults to USB."""
        found: list[int] = []
        for d in self.engine.active_decks():
            byte = self._db_slot_for_status(getattr(d, "status", None))
            if byte in remotedb_wire.LIBRARY_SLOTS and byte not in found:
                found.append(byte)
        return found or [remotedb_wire.SLOT_USB]

    def _open_remotedb(self, host: str, slot: int | None = None) -> RemoteDbBrowser | None:
        target = self._device_number_for_host(host)
        if target is None:
            return None
        if slot is None:
            slot = self._library_db_slots()[0]
        return RemoteDbBrowser(
            host=host,
            target_player=target,
            requesting_player=self._requesting_player_for_db(),
            slot=int(slot),
        )

    def _cache_key(self, host: str, media, track_id: int, slot: str = "") -> tuple:
        fp = getattr(media, "_pdb_fingerprint", None)
        return track_cache_key(
            host,
            getattr(media, "export", "") or "",
            fp,
            slot,
            track_id,
        )

    def _resolve_key(self, track_id: int, deck: int | None = None) -> tuple | None:
        """Pick the cache key for a loaded track, preferring the deck binding."""
        deck_keys = getattr(self, "_deck_keys", {})
        if deck is not None:
            bound = deck_keys.get(int(deck))
            if keys_match(bound, track_id):
                return bound
        if not track_id:
            return None
        with self._lock:
            for key in deck_keys.values():
                if keys_match(key, track_id):
                    return key
        host, media = self._media_for_legacy(track_id)
        if not host or media is None:
            return None
        return self._cache_key(host, media, track_id)
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

    def _fetch_live_metadata(self, host: str, track_id: int, slot_name: str,
                             slot_raw: int, type_name: str, type_raw: int) -> dict | None:
        """Read-only dbserver metadata for this deck's slot. None on any failure."""
        slot = remotedb_wire.slot_byte(slot_name, slot_raw)
        if slot <= 0 or not host or not track_id:
            return None
        track_type = remotedb_wire.track_type_byte(type_name, type_raw)
        browser = self._open_remotedb(host, slot=slot)
        if browser is None:
            return None
        try:
            with browser:
                meta = browser.track_metadata(track_id, track_type=track_type)
        except Exception:
            return None
        if not meta:
            return None
        if not (str(meta.get("title") or "").strip() or str(meta.get("artist") or "").strip()):
            return None
        meta["library_source"] = "remotedb"
        return meta

    def _on_track_change(self, deck: link.Deck, track_id: int) -> None:
        """A track was loaded: metadata for this deck's slot, then its waveform."""
        if not track_id:
            with self._lock:
                self._deck_keys.pop(deck.number, None)
            return
        status = deck.status
        slot = (status.slot if status else "") or ""
        slot_raw = int(getattr(status, "slot_raw", 0) or 0) if status else 0
        type_name = (status.track_type if status else "") or ""
        type_raw = int(getattr(status, "track_type_raw", 0) or 0) if status else 0
        player_name = (status.name if status else "") or ""
        host = self._host_for(status) or ""

        live = None
        if host:
            live = self._fetch_live_metadata(
                host, track_id, slot, slot_raw, type_name, type_raw)

        remote_only = slot in ("rekordbox", "beatport", "direct_play", "streaming")
        media = None
        copied = None
        copied_src = ""
        if host and not remote_only:
            media = self.library.get(host)
            if media is None:
                self.library.retry(host)
                media = self.library.get(host)
            if media is not None:
                copied, copied_src = media.copied_track(
                    track_id, prefer_onelibrary=proto.is_xdj_az(player_name))

        key = self._key_for(host, media, track_id, slot)
        # The dbserver query above can outlive the track. Only bind the deck
        # when it is still on this id; the cache entry is kept either way.
        if self._deck_still_on(deck, track_id):
            with self._lock:
                self._deck_keys[deck.number] = key

        analysis = None
        if media is not None and copied is not None:
            try:
                analysis = media.analysis(track_id, track=copied)
            except Exception:
                analysis = None
        if analysis is not None and copied is not None and media is not None:
            if self._deck_still_on(deck, track_id):
                length = analysis.duration_ms or ((copied.duration * 1000) if copied else 0)
                deck.set_beat_grid([b.time for b in analysis.beats], length)
            self._build(host, media, track_id, analysis, copied, slot=slot)
            self._finish_meta(
                key, live, slot=slot, track_type=type_name, fallback_source=copied_src)
            return

        fields: dict = {}
        if live:
            fields.update(live)
        elif copied is not None:
            fields.update(_fields_from_track(copied))
            fields["library_source"] = copied_src
        self._publish_basic(
            key, track_id, fields, slot=slot, track_type=type_name,
            fallback_source=copied_src if not live else "remotedb")

    @staticmethod
    def _deck_still_on(deck, track_id: int) -> bool:
        current = getattr(deck, "status", None)
        return current is not None and getattr(current, "track_id", None) == track_id

    def _key_for(self, host: str, media, track_id: int, slot: str) -> tuple:
        if media is not None:
            return self._cache_key(host, media, track_id, slot)
        return track_cache_key(host or "", "", None, slot, track_id)

    def _finish_meta(self, key: tuple, live: dict | None, *, slot: str,
                     track_type: str, fallback_source: str) -> None:
        """Prefer live dbserver fields. Label a stream when the title is blank."""
        with self._lock:
            meta = self._meta.get(key)
            if meta is None:
                return
            if live:
                for field in ("title", "artist", "album", "genre", "key", "label", "comment"):
                    text = str(live.get(field) or "").strip()
                    if text:
                        meta[field] = text
                art_id = int(live.get("artwork_id") or 0)
                if art_id:
                    meta["artwork_id"] = art_id
                    meta["has_artwork"] = True
                if live.get("tempo"):
                    meta["track_bpm"] = float(live["tempo"])
                meta["library_source"] = "remotedb"
            else:
                meta["library_source"] = fallback_source or meta.get("library_source") or ""
            self._meta[key] = proto.apply_source_label(meta, slot, track_type)

    def _publish_basic(self, key: tuple, track_id: int, fields: dict, *,
                       slot: str, track_type: str, fallback_source: str) -> None:
        duration_s = int(fields.get("duration_s") or 0)
        meta = {
            "id": track_id,
            "track_key": track_key_token(key),
            "title": str(fields.get("title") or ""),
            "artist": str(fields.get("artist") or ""),
            "album": str(fields.get("album") or ""),
            "genre": str(fields.get("genre") or ""),
            "key": str(fields.get("key") or ""),
            "label": str(fields.get("label") or ""),
            "comment": str(fields.get("comment") or ""),
            "year": int(fields.get("year") or 0),
            "rating": int(fields.get("rating") or 0),
            "track_bpm": float(fields.get("tempo") or fields.get("track_bpm") or 0),
            "duration_ms": int(fields.get("duration_ms") or (duration_s * 1000)),
            "bitrate": int(fields.get("bitrate") or 0),
            "artwork_id": int(fields.get("artwork_id") or 0),
            "has_artwork": bool(fields.get("artwork_id") or fields.get("has_artwork")),
            "library_source": fields.get("library_source") or fallback_source or "",
            "beats": [],
            "cues": [],
            "phrases": [],
            "detail_columns": 0,
            "overview_columns": 0,
        }
        meta = proto.apply_source_label(meta, slot, track_type)
        with self._lock:
            self._meta[key] = meta

    def _build(self, host: str, media, track_id: int, a: anlz.Analysis, t,
               *, slot: str = "") -> None:
        """Prepare and cache the binary waveform and metadata of a track."""
        key = self._cache_key(host, media, track_id, slot)
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

        token = track_key_token(key)
        meta = {
            "id": track_id,
            "track_key": token,
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
            "has_artwork": bool(
                t and (t.artwork_path or t.artwork_id or t.file_path)),
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

    def _media_for_legacy(self, track_id: int):
        """Best-effort medium lookup when no deck binding exists (library API)."""
        deck_keys = getattr(self, "_deck_keys", {})
        with self._lock:
            for key in deck_keys.values():
                if keys_match(key, track_id):
                    host = key[0]
                    media = self.library.get(host)
                    if media is not None:
                        return host, media
        host = self.host
        return (host, self.library.get(host)) if host else (None, None)

    def _media_for_key(self, key: tuple) -> tuple[str | None, Any]:
        host = key[0] if key else None
        if not host:
            return None, None
        return host, self.library.get(host)

    def ensure(self, track_id: int, *, deck: int | None = None) -> None:
        """Load a track on demand when it has not been prepared yet."""
        key = self._resolve_key(track_id, deck)
        if key is None:
            return
        with self._lock:
            if key in self._meta:
                return
        host, media = self._media_for_key(key)
        if not host or media is None:
            return
        prefer = False
        if deck is not None:
            holder = self.engine.decks.get(int(deck))
            status = holder.status if holder is not None else None
            prefer = proto.is_xdj_az(getattr(status, "name", "") or "")
        copied, _src = media.copied_track(track_id, prefer_onelibrary=prefer)
        a = media.analysis(track_id, track=copied)
        if a is not None:
            slot = str(key[3]) if len(key) > 3 else ""
            self._build(host, media, track_id, a, copied, slot=slot)

    def waveform(self, track_id: int, *, deck: int | None = None) -> bytes | None:
        self.ensure(track_id, deck=deck)
        key = self._resolve_key(track_id, deck)
        if key is None:
            return None
        with self._lock:
            return self._waveforms.get(key)

    def meta(self, track_id: int, *, deck: int | None = None, load: bool = True) -> dict | None:
        """Return cached track metadata. With load=True, may hit NFS once."""
        if load:
            self.ensure(track_id, deck=deck)
        key = self._resolve_key(track_id, deck)
        if key is None:
            return None
        with self._lock:
            return self._meta.get(key)

    def artwork(self, track_id: int, *, deck: int | None = None) -> bytes | None:
        from prolink.artwork import resolve_artwork

        key = self._resolve_key(track_id, deck)
        if key is None:
            return None
        with self._lock:
            cached = self._art_cache.get(key)
            if cached is not None:
                self._last_art_source = cached[1]
                if deck is not None:
                    self._art_source_by_deck[int(deck)] = cached[1]
                return cached[0]
        host, media = self._media_for_key(key)
        slot = str(key[3]) if len(key) > 3 else ""
        status = None
        if deck is not None:
            holder = self.engine.decks.get(int(deck))
            status = holder.status if holder is not None else None
        raw = 0
        if status is not None and (not slot or getattr(status, "slot", "") == slot):
            raw = int(getattr(status, "slot_raw", 0) or 0)
        byte = remotedb_wire.slot_byte(slot, raw) or remotedb_wire.SLOT_USB
        cached_meta = self.meta(track_id, deck=deck, load=False) or {}
        art_id = int(cached_meta.get("artwork_id") or 0)
        if not media:
            data = self._artwork_from_db(host or "", art_id, byte) if host and art_id else None
            if data:
                with self._lock:
                    self._art_cache[key] = (data, "remotedb")
                    self._last_art_source = "remotedb"
                    if deck is not None:
                        self._art_source_by_deck[int(deck)] = "remotedb"
            return data
        prefer = proto.is_xdj_az(getattr(status, "name", "") or "")
        track, _src = media.copied_track(track_id, prefer_onelibrary=prefer)
        if track is not None and art_id and not track.artwork_id:
            track.artwork_id = art_id
        elif track is None and art_id:
            from prolink.pdb import Track
            track = Track(id=track_id, artwork_id=art_id)
        local_root = ""
        try:
            from gui.settings import load_settings

            local_root = str(load_settings().get("local_music_root") or "")
        except Exception:
            pass
        open_db = (lambda h=host, b=byte: self._open_remotedb(h, slot=b)) if host else None
        data, source = resolve_artwork(
            track,
            media,
            host=host or "",
            slot=slot,
            open_remotedb=open_db,
            local_music_root=local_root,
        )
        if data:
            with self._lock:
                self._art_cache[key] = (data, source)
                self._last_art_source = source
                if deck is not None:
                    self._art_source_by_deck[int(deck)] = source
                while len(self._art_cache) > 48:
                    self._art_cache.pop(next(iter(self._art_cache)))
        return data

    def _artwork_from_db(self, host: str, artwork_id: int, slot: int) -> bytes | None:
        """Read-only album-art query when the NFS copy of the track is unavailable."""
        if not host or not artwork_id:
            return None
        browser = self._open_remotedb(host, slot=slot)
        if browser is None:
            return None
        from prolink.artwork_util import looks_like_image, normalize_artwork_jpeg

        try:
            with browser:
                raw = browser.album_art(int(artwork_id), high_res=True)
                if not raw:
                    raw = browser.album_art(int(artwork_id), high_res=False)
        except Exception:
            return None
        if raw and looks_like_image(raw):
            return normalize_artwork_jpeg(raw)
        return None

    def artwork_source(self, *, deck: int | None = None) -> str:
        if deck is not None:
            with self._lock:
                if int(deck) in self._art_source_by_deck:
                    return self._art_source_by_deck[int(deck)]
        with self._lock:
            return self._last_art_source or "none"

    def deck_track_key(self, deck_number: int, track_id: int) -> str:
        if not track_id:
            return ""
        deck_keys = getattr(self, "_deck_keys", {})
        bound = deck_keys.get(int(deck_number))
        if keys_match(bound, track_id):
            return track_key_token(bound)
        return ""

    def browse_tracks(self, query: str = "", *, limit: int = 100,
                      offset: int = 0) -> dict:
        """Search tracks — prefers live dbserver, falls back to NFS export.pdb cache."""
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        q = (query or "").strip().lower()
        with self.library.lock:
            media_list = list(self.library.media.items())
        all_rows: list[dict] = []
        hosts: list[str] = []
        source = "none"
        for host, media in media_list:
            hosts.append(host)
            media.refresh_if_stale()
            live_rows: list[dict] | None = None
            slots_ok: list[int] = []
            player_detail = ""
            for slot in self._library_db_slots():
                browser = self._open_remotedb(host, slot=slot)
                if browser is None:
                    continue
                try:
                    with browser:
                        player_detail = (
                            f"player {browser.target_player} "
                            f"as client #{browser.requesting_player}"
                        )
                        self._remotedb_fail.pop(host, None)
                        chunk: list[dict] = []
                        off = 0
                        while off < 2000:
                            page = browser.track_search_page(offset=off, limit=64)
                            if not page:
                                break
                            chunk.extend(page)
                            if len(page) < 64:
                                break
                            off += 64
                        if live_rows is None:
                            live_rows = []
                        slots_ok.append(int(browser.slot))
                        for row in chunk:
                            tid = int(row.get("id") or 0)
                            if not tid:
                                continue
                            live_rows.append({
                                "id": tid,
                                "title": str(row.get("name") or ""),
                                "artist": str(row.get("subtitle") or ""),
                                "host": host,
                                "slot": int(browser.slot),
                                "source": "remotedb",
                            })
                except Exception as exc:
                    self._remotedb_fail[host] = str(exc)
            if live_rows is not None:
                slot_text = ",".join(str(s) for s in slots_ok) or "?"
                media.set_library_source(
                    "remotedb",
                    f"TCP dbserver · {player_detail} slots {slot_text}".strip(),
                )
                source = "remotedb"
            if live_rows is not None:
                if q:
                    live_rows = [
                        r for r in live_rows
                        if q in (r.get("title") or "").lower()
                        or q in (r.get("artist") or "").lower()
                    ]
                all_rows.extend(live_rows)
                continue
            if media.db is None:
                try:
                    media.load_database()
                except Exception:
                    continue
            media.set_library_source(
                "nfs-cache",
                f"NFS export.pdb cache ({media.cache_dir})",
            )
            source = "nfs-cache"
            rows, _total = media.db.search(query, limit=10_000, offset=0)
            for r in rows:
                r = dict(r)
                r["host"] = host
                r["source"] = "nfs-cache"
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
            "library_source": source,
        }

    def browse_playlists(self) -> dict:
        with self.library.lock:
            media_list = list(self.library.media.items())
        playlists: list[dict] = []
        history: list[dict] = []
        errors: list[dict] = []
        any_present = False
        library_source = "none"
        for host, media in media_list:
            media.refresh_if_stale()
            live_ok = False
            for slot in self._library_db_slots():
                browser = self._open_remotedb(host, slot=slot)
                if browser is None:
                    continue
                try:
                    with browser:
                        detail = (
                            f"TCP dbserver · player {browser.target_player} "
                            f"as client #{browser.requesting_player} slot {browser.slot}"
                        )
                        media.set_library_source("remotedb", detail)
                        library_source = "remotedb"
                        any_present = True
                        live_ok = True
                        for row in browser.playlist_folder(0):
                            if row.get("folder"):
                                continue
                            pid = int(row.get("id") or 0)
                            if not pid:
                                continue
                            playlists.append({
                                "id": pid,
                                "name": row.get("name") or f"#{pid}",
                                "host": host,
                                "slot": int(browser.slot),
                                "source": "remotedb",
                            })
                        for row in browser.history_entries():
                            hid = int(row.get("id") or 0)
                            if not hid:
                                continue
                            history.append({
                                "id": hid,
                                "name": row.get("name") or f"#{hid}",
                                "host": host,
                                "slot": int(browser.slot),
                                "source": "remotedb",
                            })
                        self._remotedb_fail.pop(host, None)
                except Exception as exc:
                    self._remotedb_fail[host] = str(exc)
                    errors.append({"host": host, "error": f"remotedb slot {slot}: {exc}"})
            if live_ok:
                continue
            summary = getattr(media, "onelibrary", None)
            if summary is not None and summary.present:
                any_present = True
                if not summary.readable:
                    errors.append({
                        "host": host,
                        "path": summary.path or onelibrary.ONE_LIBRARY_PATH,
                        "error": summary.error or summary.detail,
                    })
            ol = getattr(media, "onelibrary_db", None)
            if ol is None:
                try:
                    media.load_database()
                    ol = getattr(media, "onelibrary_db", None)
                except Exception:
                    ol = None
            if ol is None:
                continue
            media.set_library_source(
                "nfs-cache",
                f"NFS exportLibrary.db cache ({media.cache_dir})",
            )
            library_source = "nfs-cache"
            for p in onelibrary.list_playlists(ol):
                p = dict(p)
                p["host"] = host
                p["source"] = "nfs-cache"
                playlists.append(p)
            for h in onelibrary.list_history(ol):
                h = dict(h)
                h["host"] = host
                h["source"] = "nfs-cache"
                history.append(h)
        return {
            "playlists": playlists,
            "history": history,
            "readable": bool(playlists or history),
            "present": any_present,
            "errors": errors,
            "pyrekordbox": onelibrary.pyrekordbox_available(),
            "library_source": library_source,
            "remotedb_errors": dict(self._remotedb_fail),
        }

    def browse_playlist_tracks(self, playlist_id: int, host: str | None = None,
                               *, limit: int = 200) -> dict:
        with self.library.lock:
            media_list = list(self.library.media.items())
        for h, media in media_list:
            if host and h != host:
                continue
            media.refresh_if_stale()
            for slot in self._library_db_slots():
                browser = self._open_remotedb(h, slot=slot)
                if browser is None:
                    continue
                try:
                    with browser:
                        rows = browser.playlist_tracks(playlist_id)[:limit]
                        if not rows:
                            continue
                        tracks = []
                        for row in rows:
                            tid = int(row.get("id") or 0)
                            if not tid:
                                continue
                            tracks.append({
                                "id": tid,
                                "title": row.get("name") or "",
                                "artist": row.get("subtitle") or "",
                                "host": h,
                                "slot": int(browser.slot),
                                "source": "remotedb",
                            })
                        if not tracks:
                            continue
                        media.set_library_source("remotedb", browser.last_status.detail or "")
                        return {
                            "id": playlist_id,
                            "host": h,
                            "tracks": tracks,
                            "library_source": "remotedb",
                        }
                except Exception:
                    pass
            ol = getattr(media, "onelibrary_db", None)
            if ol is None:
                continue
            tracks = onelibrary.playlist_tracks(ol, playlist_id, limit=limit)
            for t in tracks:
                t["host"] = h
                t["source"] = "nfs-cache"
            return {
                "id": playlist_id,
                "host": h,
                "tracks": tracks,
                "library_source": "nfs-cache",
            }
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
            meta = self.meta(s.track_id, deck=d.number) or {}
            rows.append({
                "id": s.track_id,
                "deck": d.number,
                "track_key": meta.get("track_key") or self.deck_track_key(d.number, s.track_id),
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
             getattr(m, "_exports_fingerprint", None),
             getattr(m, "library_source", None),
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
                    "path": ol.path or onelibrary.ONE_LIBRARY_PATH,
                    "pyrekordbox": onelibrary.pyrekordbox_available(),
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
    @staticmethod
    def _waveform_report(status) -> dict:
        """Player-reported waveform settings, or null when absent/unknown.

        MagicMock-style stand-ins and unexpected strings stay null so a
        missing field cannot leak into the SSE payload.
        """
        pos = getattr(status, "waveform_position", None)
        color = getattr(status, "waveform_color", None)
        return {
            "waveform_position": pos if pos in ("centre", "left") else None,
            "waveform_color": color if color in ("blue", "rgb", "3band") else None,
        }

    def _display_prefs(self) -> dict:
        """GUI display prefs pushed to web clients (overlay + panel)."""
        show_phrases = bool(getattr(self, "show_phrases", True))
        zoom_bars = 4
        playhead_position = "auto"
        try:
            from gui.settings import load_settings

            prefs = load_settings()
            zoom_bars = int(prefs.get("zoom_bars") or 4)
            if zoom_bars not in (1, 2, 4, 8, 16):
                zoom_bars = 4
            playhead_position = str(prefs.get("playhead_position") or "auto")
        except Exception:
            pass
        return {
            "show_phrases": show_phrases,
            "zoom_bars": zoom_bars,
            "playhead_position": playhead_position,
        }

    def paint_state(self) -> dict:
        """Lightweight playhead snapshot for high-rate SSE / paint clients."""
        decks = []
        for d in self.engine.active_decks():
            s = d.status
            if s is None:
                continue
            tid = s.track_id
            decks.append({
                "number": d.number,
                "track_id": tid,
                "track_key": self.deck_track_key(d.number, tid) if tid else "",
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
                **self._waveform_report(s),
            })
        tracker = getattr(self, "audience", None) or AudienceDeckTracker()
        self.audience = tracker
        tracker.observe(decks)
        audience_deck = merge_live_deck(tracker.pick(decks), decks)
        return {
            "t": time.time(),
            "paint": True,
            "decks": decks,
            "audience_deck": audience_deck,
        }

    def state(self, *, notify_wnp: bool = True) -> dict:
        decks = []
        for d in self.engine.active_decks():
            s = d.status
            if s is None:
                continue
            tid = s.track_id
            decks.append({
                "number": d.number,
                "name": s.name,
                "firmware": s.firmware,
                "track_id": tid,
                "track_key": self.deck_track_key(d.number, tid) if tid else "",
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
                **self._waveform_report(s),
            })
        # Enrich from cache only — never NFS on the hot path (ensure runs on track change).
        for snap in decks:
            tid = snap.get("track_id") or 0
            deck_no = int(snap.get("number") or 0)
            meta = self.meta(tid, deck=deck_no, load=False) if tid else None
            if meta and meta.get("track_key") == snap.get("track_key"):
                snap["title"] = meta.get("title") or ""
                snap["artist"] = meta.get("artist") or ""
                snap["key"] = meta.get("key") or ""
            elif tid:
                snap.pop("title", None)
                snap.pop("artist", None)
                snap.pop("key", None)
            self.mixstatus.handle(snap)
        tracker = getattr(self, "audience", None) or AudienceDeckTracker()
        self.audience = tracker
        tracker.observe(decks)
        audience_deck = merge_live_deck(tracker.pick(decks), decks)
        # One observe pass: set-clock pause/resume + track logging.
        self.session.observe(decks)
        with self.engine.lock:
            devices = [{"number": a.device_number, "name": a.name,
                        "kind": a.kind, "ip": a.ip}
                       for a in sorted(self.engine.devices.values(),
                                       key=lambda x: x.device_number)]
        totals, library_error, onelibrary_info = self._library_snapshot()
        library_source = {"source": "none", "detail": ""}
        with self.library.lock:
            for _h, m in self.library.media.items():
                library_source = {
                    "source": getattr(m, "library_source", "none") or "none",
                    "detail": getattr(m, "library_source_detail", "") or "",
                }
                break
        remotedb_fail = getattr(self, "_remotedb_fail", None) or {}
        if remotedb_fail and library_source.get("source") != "remotedb":
            library_source["remotedb_error"] = next(iter(remotedb_fail.values()))
        art_sources: dict[str, str] = {}
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                for dnum, src in getattr(self, "_art_source_by_deck", {}).items():
                    art_sources[str(dnum)] = src
                last_art = getattr(self, "_last_art_source", "none") or "none"
        else:
            last_art = "none"
        mix = self.mixstatus.as_state()
        timing = getattr(self.engine, "link_timing", None)
        if timing is not None:
            link_timing = timing.snapshot(time.monotonic())
        else:
            link_timing = []
        cap = getattr(self.engine, "capture", None)
        capture = cap.status() if cap is not None else idle_capture_status()
        wnp_state = self._wnp_state(audience_deck) if notify_wnp else self._wnp_health_only()
        nfs_health: list[dict] = []
        with self.library.lock:
            for host, media in self.library.media.items():
                try:
                    info = media.nfs.latency_info()
                except Exception:
                    info = {}
                nfs_health.append({
                    "host": host,
                    "export": getattr(media, "export", "") or "",
                    "rtt_ms": info.get("nfs_rtt_ms"),
                    "last_rtt_ms": info.get("nfs_last_rtt_ms"),
                    "mount_rtt_ms": info.get("mount_rtt_ms"),
                    "calls": int(info.get("nfs_calls") or 0),
                    "timeouts": int(info.get("nfs_timeouts") or 0),
                })
        return {
            "t": time.time(),
            "paint": False,
            "decks": decks,
            "display": self._display_prefs(),
            "devices": devices,
            "packets": self.engine.packets,
            "mode": getattr(self.engine.source, "description", ""),
            "mode_kind": getattr(self.engine.source, "kind", ""),
            "mode_detail": getattr(self.engine.source, "detail", ""),
            "az_mode": _az_mode_value(self.engine),
            "host": self.host or "",
            "library": totals,
            "library_error": library_error,
            "library_source": library_source,
            "artwork_sources": art_sources,
            "artwork_source_last": last_art,
            "onelibrary": onelibrary_info,
            "nfs": nfs_health,
            "link_timing": link_timing,
            "capture": capture,
            "mix": mix,
            "audience_deck": audience_deck,
            "wnp": wnp_state,
            "now_playing": mix.get("now_playing"),
            "pending": mix.get("pending"),
            "setlist": mix.get("setlist") or [],
            "session": self.session.as_state(),
        }

    def _audience_meta(self, audience_deck: dict | None) -> dict | None:
        """Cached library fields for the audience deck. Never touches NFS."""
        if not audience_deck:
            return None
        try:
            track_id = int(audience_deck.get("track_id") or 0)
        except (TypeError, ValueError):
            return None
        if not track_id:
            return None
        try:
            deck_no = int(audience_deck.get("number") or 0) or None
        except (TypeError, ValueError):
            deck_no = None
        try:
            meta = self.meta(track_id, deck=deck_no, load=False)
        except Exception:
            return None
        if not meta:
            return None
        deck_key = str(audience_deck.get("track_key") or "")
        meta_key = str(meta.get("track_key") or "")
        if deck_key and meta_key and deck_key != meta_key:
            return None
        return meta

    def _wnp_health_only(self) -> dict:
        publisher = getattr(self, "wnp", None)
        if publisher is None:
            return {
                "enabled": False,
                "status": "disabled",
                "detail": "Off",
                "host": "",
                "port": 0,
                "track": "",
            }
        try:
            return publisher.health()
        except Exception:
            return {
                "enabled": False,
                "status": "error",
                "detail": "What's Now Playing update failed inside the listener.",
                "host": "",
                "port": 0,
                "track": "",
            }

    def _wnp_state(self, audience_deck: dict | None) -> dict:
        """Queue a What's Now Playing update and return Health-page status."""
        publisher = getattr(self, "wnp", None)
        if publisher is None:
            return {
                "enabled": False,
                "status": "disabled",
                "detail": "Off",
                "host": "",
                "port": 0,
                "track": "",
            }
        try:
            publisher.refresh_from_disk()
            publisher.observe(audience_deck, self._audience_meta(audience_deck))
            return publisher.health()
        except Exception:
            return {
                "enabled": False,
                "status": "error",
                "detail": "What's Now Playing update failed inside the listener.",
                "host": "",
                "port": 0,
                "track": "",
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
                track_id, deck, want_key = self._parse_track_request(parsed)
                meta = self.monitor.meta(track_id, deck=deck)
                if meta and want_key and meta.get("track_key") != want_key:
                    meta = None
                return self._json(meta or {"error": "no_analysis"}, 200 if meta else 404)
            if path.startswith("/api/waveform/"):
                track_id, deck, want_key = self._parse_track_request(parsed)
                meta = self.monitor.meta(track_id, deck=deck, load=False)
                if want_key and (not meta or meta.get("track_key") != want_key):
                    return self._json({"error": "no_waveform"}, 404)
                data = self.monitor.waveform(track_id, deck=deck)
                if not data:
                    return self._json({"error": "no_waveform"}, 404)
                return self._send(200, data, "application/octet-stream", "no-store")
            if path.startswith("/api/artwork/"):
                track_id, deck, want_key = self._parse_track_request(parsed)
                meta = self.monitor.meta(track_id, deck=deck, load=False)
                if want_key and (not meta or meta.get("track_key") != want_key):
                    return self._json({"error": "no_artwork"}, 404)
                art = self.monitor.artwork(track_id, deck=deck)
                if not art:
                    return self._json({"error": "no_artwork"}, 404)
                return self._send(200, art, "image/jpeg", "no-store")
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
        path = os.path.join(WEB_DIR, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "not_found"}, 404)
        extra: dict[str, str] = {}
        if name.endswith(".html"):
            extra["Pragma"] = "no-cache"
            try:
                extra["X-Asset-Version"] = str(int(os.path.getmtime(path)))
            except OSError:
                pass
        self._send(200, body, ctype, "no-store", extra)

    @staticmethod
    def _parse_track_request(parsed) -> tuple[int, int | None, str | None]:
        from urllib.parse import parse_qs

        track_id = int(parsed.path.rsplit("/", 1)[1])
        qs = parse_qs(parsed.query)
        raw_deck = (qs.get("deck") or [None])[0]
        deck: int | None
        try:
            deck = int(raw_deck) if raw_deck not in (None, "") else None
        except (TypeError, ValueError):
            deck = None
        want_key = (qs.get("key") or [None])[0]
        want_key = str(want_key).strip() if want_key else None
        return track_id, deck, want_key

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


def open_http_server(port: int, handler=None, host: str = "0.0.0.0"):
    """Bind the panel HTTP server, trying alternate ports if ``port`` is blocked.

    Default ``host`` is ``0.0.0.0`` so phones on the same LAN can open the panel.
    Pass ``127.0.0.1`` to keep it local-only. Returns ``(server, actual_port)``.
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


def lan_urls(port: int, local_ip: str | None = None) -> list[str]:
    """Useful URLs for opening the panel from this machine or a phone on the LAN."""
    urls = [f"http://127.0.0.1:{port}/"]
    ip = (local_ip or "").strip()
    if ip and not ip.startswith("127.") and ip != "0.0.0.0":
        urls.append(f"http://{ip}:{port}/")
    return urls


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
    p.add_argument("--http-host", default="0.0.0.0",
                   help="HTTP bind address (0.0.0.0 = LAN; 127.0.0.1 = local only)")
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
    try:
        from gui.settings import load_settings

        monitor.show_phrases = bool(load_settings().get("show_phrases", True))
    except Exception:
        pass
    Handler.monitor = monitor
    try:
        monitor.start()
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        return 1
    print(f"  mode: {source.description}")

    try:
        server, bound_port = open_http_server(args.port, Handler, host=args.http_host)
    except OSError as e:
        print(f"\nERROR: {e}")
        print("  On Windows, WinError 10013 usually means the port is reserved\n"
              "  (Hyper-V / excluded range) or in use. Try e.g. --port 18777")
        monitor.stop()
        return 1
    if bound_port != args.port:
        print(f"  ! port {args.port} was blocked; using {bound_port} instead "
              f"(pass --port to pick one)")
    urls = lan_urls(bound_port, net.ip)
    url = urls[0]
    print(f"  panel: {url}")
    if len(urls) > 1:
        print(f"  phone / LAN: {urls[1]}   (same Wi‑Fi; optional ?readonly=1)")
    print(f"  overlay: http://127.0.0.1:{bound_port}/overlay")
    if len(urls) > 1:
        print(f"           {urls[1].rstrip('/')}/overlay\n")
    else:
        print()

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
