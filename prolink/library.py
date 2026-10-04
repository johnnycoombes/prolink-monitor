"""Player library: ties NFS access, export.pdb and the analysis files together.

Downloads the exported database once and, on demand, the analysis and artwork of
each track as it gets loaded on a deck. Everything is cached on disk so that
restarting the program is instant.

Cache keys include the remote path (and for export.pdb, size+mtime) so swapping
USB sticks on the same player cannot serve another stick's files.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict

from . import anlz, onelibrary, pdb
from .nfs import NfsClient

PDB_PATH = "PIONEER/rekordbox/export.pdb"
ONE_LIBRARY_PATH = onelibrary.ONE_LIBRARY_PATH

# How long a failed library open stays sticky before the next get() retries.
ERROR_RETRY_SECONDS = 5.0


def _safe_cache_name(prefix: str, remote: str, suffix: str = "") -> str:
    """Stable, filesystem-safe cache file name derived from the remote path."""
    digest = hashlib.sha1(remote.encode("utf-8", "replace")).hexdigest()[:16]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(remote))[:40] or "file"
    return f"{prefix}_{digest}_{base}{suffix}"


class Media:
    """One medium (USB/SD) mounted in a player, reachable over NFS."""

    def __init__(self, host: str, export: str | None = None, cache_dir: str | None = None,
                 max_analysis: int = 32):
        self.host = host
        base = cache_dir or os.path.join(
            os.path.expanduser("~"), ".prolink-cache", host.replace(":", "_"))
        self.lock = threading.RLock()
        self._analysis: OrderedDict[int, anlz.Analysis] = OrderedDict()
        self._artwork: OrderedDict[int, bytes] = OrderedDict()
        self._max_analysis = max_analysis
        self.db: pdb.PdbDatabase | None = None
        self.export = export
        self.loaded_at = 0.0
        self._pdb_fingerprint: tuple[int, int] | None = None
        self.onelibrary_path: str = ""
        self.onelibrary_db = None
        self.onelibrary: onelibrary.OneLibrarySummary = onelibrary.OneLibrarySummary()
        # How the Library UI reads metadata: remotedb (live TCP) or nfs-cache (USB export files).
        self.library_source: str = "none"
        self.library_source_detail: str = ""
        self._exports_fingerprint: tuple[str, ...] = ()

        self.nfs = NfsClient(host)
        exports = self.nfs.exports()
        if not exports:
            raise RuntimeError(f"{host} exports no media over NFS "
                               "(is a USB drive or SD card inserted?)")
        self.export = export or exports[0]
        self.available_exports = exports
        self.root = self.nfs.mount(self.export)

        # One cache folder per NFS export on this host, so stick swaps that keep
        # the same IP but change the export path do not share anlz/art files.
        export_key = re.sub(r"[^A-Za-z0-9._-]+", "_", self.export.strip("/")) or "export"
        self.cache_dir = os.path.join(base, export_key)
        os.makedirs(self.cache_dir, exist_ok=True)
        self._exports_fingerprint = tuple(sorted(exports))

    def _remote_pdb_fingerprint(self) -> tuple[int, int] | None:
        """Current export.pdb size/mtime on the player (no disk cache)."""
        try:
            _fh, attr = self.nfs.resolve(self.root, PDB_PATH)
            return (attr.size, int(attr.mtime))
        except Exception:
            return None

    def is_stale(self) -> bool:
        """True when NFS export set or export.pdb fingerprint changed."""
        try:
            exports = tuple(sorted(self.nfs.exports()))
        except Exception:
            return True
        if exports != self._exports_fingerprint:
            return True
        fp = self._remote_pdb_fingerprint()
        if fp is None:
            return self.db is not None
        return self._pdb_fingerprint is not None and fp != self._pdb_fingerprint

    def refresh_if_stale(self) -> None:
        """Drop in-memory DB and re-fetch export files when the medium changed."""
        if not self.is_stale():
            return
        with self.lock:
            self._analysis.clear()
            self._artwork.clear()
            self.db = None
            self.onelibrary_db = None
            self.onelibrary = onelibrary.OneLibrarySummary()
            self._pdb_fingerprint = None
        try:
            exports = self.nfs.exports()
            if not exports:
                raise RuntimeError("no exports")
            if self.export not in exports:
                self.export = exports[0]
                export_key = re.sub(r"[^A-Za-z0-9._-]+", "_", self.export.strip("/")) or "export"
                base = os.path.dirname(os.path.dirname(self.cache_dir))
                self.cache_dir = os.path.join(base, export_key)
                os.makedirs(self.cache_dir, exist_ok=True)
            self._exports_fingerprint = tuple(sorted(exports))
            self.root = self.nfs.mount(self.export)
        except Exception:
            pass
        self.load_database(force=True)

    def set_library_source(self, source: str, detail: str = "") -> None:
        self.library_source = source or "none"
        self.library_source_detail = detail or ""

    # -- database -----------------------------------------------------------
    def load_database(self, force: bool = False) -> pdb.PdbDatabase:
        """Download and parse export.pdb. Reuses the cached copy when it matches."""
        with self.lock:
            _fh, attr = self.nfs.resolve(self.root, PDB_PATH)
            fingerprint = (attr.size, int(attr.mtime))
            if (self.db is not None and not force
                    and self._pdb_fingerprint == fingerprint):
                return self.db

            if (self._pdb_fingerprint is not None
                    and self._pdb_fingerprint != fingerprint):
                # USB contents changed under us — drop in-memory track caches.
                self._analysis.clear()
                self._artwork.clear()
                self.db = None

            local = os.path.join(self.cache_dir, "export.pdb")
            stat_path = local + ".stat"
            want = f"{fingerprint[0]}:{fingerprint[1]}"
            reuse = (not force and os.path.exists(local) and os.path.exists(stat_path))
            if reuse:
                try:
                    with open(stat_path, "r", encoding="ascii") as f:
                        reuse = f.read().strip() == want
                except OSError:
                    reuse = False
            if not reuse:
                self.nfs.download(self.root, PDB_PATH, local)
                try:
                    with open(stat_path, "w", encoding="ascii") as f:
                        f.write(want)
                except OSError:
                    pass
            with open(local, "rb") as f:
                self.db = pdb.PdbDatabase(f.read())
            self._pdb_fingerprint = fingerprint
            self.loaded_at = time.time()
            self._load_onelibrary(force=force)
            return self.db

    def _load_onelibrary(self, force: bool = False) -> None:
        """Detect / cache exportLibrary.db and open it when pyrekordbox is available."""
        local = os.path.join(self.cache_dir, "exportLibrary.db")
        present = False
        try:
            _fh, attr = self.nfs.resolve(self.root, ONE_LIBRARY_PATH)
            present = True
            stat_path = local + ".stat"
            want = f"{attr.size}:{int(attr.mtime)}"
            reuse = (not force and os.path.exists(local) and os.path.exists(stat_path))
            if reuse:
                try:
                    with open(stat_path, "r", encoding="ascii") as f:
                        reuse = f.read().strip() == want
                except OSError:
                    reuse = False
            if not reuse:
                self.nfs.download(self.root, ONE_LIBRARY_PATH, local)
                try:
                    with open(stat_path, "w", encoding="ascii") as f:
                        f.write(want)
                except OSError:
                    pass
            self.onelibrary_path = local
        except Exception as exc:
            self.onelibrary_path = local if os.path.exists(local) else ""
            present = bool(self.onelibrary_path)
            if not present:
                self.onelibrary_db = None
                self.onelibrary = onelibrary.summarize(
                    None, present=False, error=str(exc))
                return

        if not present:
            self.onelibrary_db = None
            self.onelibrary = onelibrary.summarize(None, present=False)
            return

        db, err = onelibrary.open_onelibrary(self.onelibrary_path)
        self.onelibrary_db = db
        self.onelibrary = onelibrary.summarize(
            db, present=True, path=self.onelibrary_path, error=err)

    def track(self, track_id: int) -> pdb.Track | None:
        return self.load_database().get(track_id)

    def onelibrary_track(self, track_id: int) -> onelibrary.OneLibraryTrack | None:
        """Look up a content row in OneLibrary (AZ playlists / history IDs)."""
        if self.onelibrary_db is None:
            self._load_onelibrary()
        return onelibrary.find_content(self.onelibrary_db, track_id)

    # -- analysis -----------------------------------------------------------
    def analysis(self, track_id: int) -> anlz.Analysis | None:
        """Beat grid, cues and waveforms for a track."""
        with self.lock:
            cached = self._analysis.get(track_id)
            if cached is not None:
                self._analysis.move_to_end(track_id)
                return cached

        t = self.track(track_id)
        if not t or not t.analyze_path:
            return None

        result: anlz.Analysis | None = None
        base = t.analyze_path.lstrip("/")
        # the .DAT holds the grid and the overview; the .EXT and .2EX the big waveforms
        for ext in (".DAT", ".EXT", ".2EX"):
            path = base[:-4] + ext if base.upper().endswith(".DAT") else base
            data = self._fetch(path, _safe_cache_name("anlz", path, ext.lower()))
            if data is None:
                continue
            try:
                result = anlz.parse(data, result)
            except ValueError:
                continue

        if result is not None:
            with self.lock:
                self._analysis[track_id] = result
                while len(self._analysis) > self._max_analysis:
                    self._analysis.popitem(last=False)
        return result

    def artwork(self, track_id: int) -> bytes | None:
        """Album art as JPEG, or None when the track has none."""
        with self.lock:
            cached = self._artwork.get(track_id)
            if cached is not None:
                self._artwork.move_to_end(track_id)
                return cached
        t = self.track(track_id)
        if not t or not t.artwork_path:
            return None
        remote = t.artwork_path.lstrip("/")
        data = self._fetch(remote, _safe_cache_name("art", remote, ".jpg"))
        if data:
            with self.lock:
                self._artwork[track_id] = data
                while len(self._artwork) > 64:
                    self._artwork.popitem(last=False)
        return data

    # -- helpers ------------------------------------------------------------
    def _fetch(self, remote: str, cache_name: str) -> bytes | None:
        """Read a file off the medium, serving it from the disk cache if present."""
        local = os.path.join(self.cache_dir, cache_name)
        if os.path.exists(local):
            try:
                with open(local, "rb") as f:
                    return f.read()
            except OSError:
                pass
        try:
            fh, attr = self.nfs.resolve(self.root, remote)
            data = self.nfs.read(fh, attr.size)
        except Exception:
            return None
        try:
            with open(local, "wb") as f:
                f.write(data)
        except OSError:
            pass
        return data

    def close(self) -> None:
        self.nfs.close()


class Library:
    """A set of media, indexed by player address.

    An all-in-one such as the XDJ-AZ serves every deck from the same IP, so in
    practice there is usually a single shared medium.
    """

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir
        self.media: dict[str, Media] = {}
        self.errors: dict[str, str] = {}
        self._error_at: dict[str, float] = {}
        self.lock = threading.RLock()

    def get(self, host: str) -> Media | None:
        with self.lock:
            if host in self.media:
                media = self.media[host]
                if media.is_stale():
                    self.drop(host)
                else:
                    return media
            # Sticky errors expire so a late USB insert can succeed without restart.
            err_at = self._error_at.get(host)
            if host in self.errors and err_at is not None:
                if time.time() - err_at < ERROR_RETRY_SECONDS:
                    return None
                self.errors.pop(host, None)
                self._error_at.pop(host, None)
        try:
            m = Media(host, cache_dir=self.cache_dir)
            m.load_database()
        except Exception as e:                    # no medium, no NFS, drive removed
            with self.lock:
                self.errors[host] = str(e)
                self._error_at[host] = time.time()
            return None
        with self.lock:
            self.media[host] = m
            self.errors.pop(host, None)
            self._error_at.pop(host, None)
        return m

    def retry(self, host: str) -> None:
        """Forget a previous failure so the connection is attempted again."""
        with self.lock:
            self.errors.pop(host, None)
            self._error_at.pop(host, None)

    def drop(self, host: str) -> None:
        """Close and forget a medium (e.g. after an unmount / stick swap)."""
        with self.lock:
            media = self.media.pop(host, None)
            self.errors.pop(host, None)
            self._error_at.pop(host, None)
        if media is not None:
            try:
                media.close()
            except Exception:
                pass

    def close(self) -> None:
        with self.lock:
            hosts = list(self.media.keys())
        for host in hosts:
            self.drop(host)
