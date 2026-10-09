"""Multi-source album artwork resolution (read-only)."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from . import embedded_art
from .art_header import cover_from_ranges
from . import remotedb as wire
from .artwork_util import (
    highres_nfs_art_path,
    map_player_path_to_local,
    normalize_artwork_jpeg,
    looks_like_image,
)
from .pdb import Track
from .remotedb_client import RemoteDbBrowser

_log = logging.getLogger(__name__)

EMBEDDED_READ_LIMIT = 2_000_000  # bytes from start of audio file


def resolve_artwork(
    track: Track | None,
    media: Any,
    *,
    host: str,
    slot: str,
    open_remotedb: Callable[[], RemoteDbBrowser | None] | None = None,
    local_music_root: str = "",
    fetch_bytes: Callable[[str], bytes | None] | None = None,
    size: str = "large",
) -> tuple[bytes | None, str]:
    """Try sources in priority order. Returns (jpeg_bytes, source_label).

    ``size="large"`` is the high-res chain (embedded, dbserver hires, NFS ``_m``).
    ``size="small"`` prefers the rekordbox thumbnail and does not read the audio
    file unless nothing smaller exists. The panel bar uses small; the card uses large.
    """
    if track is None:
        return None, "none"
    if str(size).lower() == "small":
        data, source = _resolve_small(
            track, media, host=host, slot=slot, open_remotedb=open_remotedb,
            fetch_bytes=fetch_bytes,
        )
        if data:
            return normalize_artwork_jpeg(data, max_px=160, quality=80), source
        data, source = _resolve_large(
            track, media, host=host, slot=slot, open_remotedb=open_remotedb,
            local_music_root=local_music_root, fetch_bytes=fetch_bytes,
        )
        if data:
            return normalize_artwork_jpeg(data, max_px=160, quality=80), source
        return None, "none"
    return _resolve_large(
        track, media, host=host, slot=slot, open_remotedb=open_remotedb,
        local_music_root=local_music_root, fetch_bytes=fetch_bytes,
    )


def _fetch_of(media: Any, fetch_bytes: Callable[[str], bytes | None] | None):
    return fetch_bytes or media._fetch  # noqa: SLF001 — library Media API


def _resolve_small(
    track: Track,
    media: Any,
    *,
    host: str,
    slot: str,
    open_remotedb: Callable[[], RemoteDbBrowser | None] | None,
    fetch_bytes: Callable[[str], bytes | None] | None,
) -> tuple[bytes | None, str]:
    """Thumbnail and standard dbserver art. No embedded-audio read."""
    fetch = _fetch_of(media, fetch_bytes)
    artwork_id = int(getattr(track, "artwork_id", 0) or 0)
    slot_byte = _slot_byte(slot)

    if track.artwork_path:
        remote = track.artwork_path.lstrip("/")
        data = fetch(remote, _cache_name("art", remote, ".jpg"))
        if data and looks_like_image(data):
            return data, "thumbnail"

    if artwork_id and open_remotedb is not None:
        browser = open_remotedb()
        if browser is not None:
            browser.slot = slot_byte
            try:
                with browser:
                    raw = browser.album_art(artwork_id, high_res=False)
                    if raw and looks_like_image(raw):
                        return raw, "remotedb"
            except Exception as exc:
                _log.debug("remotedb small art failed id=%s: %s", artwork_id, exc)

    if track.artwork_path:
        hi_path = highres_nfs_art_path(track.artwork_path)
        if hi_path and hi_path != track.artwork_path:
            data = fetch(hi_path.lstrip("/"), _cache_name("arthi", hi_path, ".jpg"))
            if data and looks_like_image(data):
                return data, "nfs-hires"
    return None, "none"


def _resolve_large(
    track: Track | None,
    media: Any,
    *,
    host: str,
    slot: str,
    open_remotedb: Callable[[], RemoteDbBrowser | None] | None = None,
    local_music_root: str = "",
    fetch_bytes: Callable[[str], bytes | None] | None = None,
) -> tuple[bytes | None, str]:
    """High-res chain. Returns (jpeg_bytes, source_label)."""
    if track is None:
        return None, "none"

    fetch = _fetch_of(media, fetch_bytes)

    # 1) Embedded cover from a local library mirror. This does not touch the player.
    if track.file_path and local_music_root:
        local = map_player_path_to_local(local_music_root, track.file_path)
        if local and os.path.isfile(local):
            try:
                with open(local, "rb") as f:
                    head = f.read(EMBEDDED_READ_LIMIT)
                raw = embedded_art.extract_embedded_cover(head, hint=local)
                if raw and looks_like_image(raw):
                    return normalize_artwork_jpeg(raw), "embedded"
            except OSError as exc:
                _log.debug("local embedded art failed %s: %s", local, exc)

    artwork_id = int(getattr(track, "artwork_id", 0) or 0)
    slot_byte = _slot_byte(slot)

    # 2) dbserver high-res album art. Do this before any audio-file read:
    # export.pdb rows have a file path, and pulling the whole track over NFS
    # stalled artwork after that path started resolving again.
    if artwork_id and open_remotedb is not None:
        browser = open_remotedb()
        if browser is not None:
            browser.slot = slot_byte
            try:
                with browser:
                    raw = browser.album_art(artwork_id, high_res=True)
                    if raw and looks_like_image(raw):
                        return normalize_artwork_jpeg(raw), "remotedb-hires"
            except Exception as exc:
                _log.debug("remotedb hires art failed id=%s: %s", artwork_id, exc)

    # 4) NFS rekordbox ``_m`` thumbnail (~240px)
    if track.artwork_path:
        hi_path = highres_nfs_art_path(track.artwork_path)
        if hi_path and hi_path != track.artwork_path:
            data = fetch(hi_path.lstrip("/"), _cache_name("arthi", hi_path, ".jpg"))
            if data and looks_like_image(data):
                return normalize_artwork_jpeg(data), "nfs-hires"

    # 5) dbserver standard album art
    if artwork_id and open_remotedb is not None:
        browser = open_remotedb()
        if browser is not None:
            browser.slot = slot_byte
            try:
                with browser:
                    raw = browser.album_art(artwork_id, high_res=False)
                    if raw and looks_like_image(raw):
                        return normalize_artwork_jpeg(raw), "remotedb"
            except Exception as exc:
                _log.debug("remotedb art failed id=%s: %s", artwork_id, exc)

    # 6) NFS thumbnail (80px class)
    if track.artwork_path:
        remote = track.artwork_path.lstrip("/")
        data = fetch(remote, _cache_name("art", remote, ".jpg"))
        if data and looks_like_image(data):
            return normalize_artwork_jpeg(data), "thumbnail"

    # 7) Embedded cover from the audio file, last. A real NFS medium reads
    # only the ID3/FLAC/MP4 header (partial reads). Older callers that only
    # offer a whole-file fetch still get a capped head, never past the limit.
    # The player and USB stay read-only.
    if track.file_path:
        remote_audio = track.file_path.lstrip("/").replace("\\", "/")
        try:
            raw = _embedded_over_nfs(media, fetch, remote_audio)
            if raw and looks_like_image(raw):
                return normalize_artwork_jpeg(raw), "embedded"
        except Exception as exc:
            _log.debug("nfs embedded art failed %s: %s", remote_audio, exc)

    return None, "none"


def _has_ranged_read(media: Any) -> bool:
    """True for Media (and test doubles), false for a Mock that only stubs ``_fetch``."""
    return callable(getattr(type(media), "read_range", None)) and callable(
        getattr(type(media), "remote_size", None))


def _embedded_over_nfs(media: Any, fetch: Callable, remote: str) -> bytes | None:
    """Cover bytes from the audio file, without downloading the audio."""
    if _has_ranged_read(media):
        try:
            size = int(media.remote_size(remote) or 0)
        except Exception:
            size = 0

        def read(offset: int, length: int) -> bytes | None:
            return media.read_range(remote, offset, length)

        return cover_from_ranges(read, size, remote)
    audio = _fetch_capped(
        fetch, remote, _cache_name("audiohead", remote, ".bin"), EMBEDDED_READ_LIMIT)
    if not audio:
        return None
    return embedded_art.extract_embedded_cover(audio, hint=remote)


def _fetch_capped(fetch: Callable, remote: str, cache_name: str, limit: int) -> bytes | None:
    """Read at most ``limit`` bytes. Older fetch callbacks ignore ``max_bytes``."""
    try:
        data = fetch(remote, cache_name, max_bytes=limit)
    except TypeError:
        data = fetch(remote, cache_name)
    if not data:
        return None
    return data[:limit]


def _slot_byte(slot: str) -> int:
    """Sr byte for this track. Unknown names stay on USB so older callers keep working."""
    byte = wire.slot_byte(slot, 0)
    return byte or wire.SLOT_USB


def _cache_name(prefix: str, remote: str, suffix: str) -> str:
    from .library import _safe_cache_name

    return _safe_cache_name(prefix, remote, suffix)
