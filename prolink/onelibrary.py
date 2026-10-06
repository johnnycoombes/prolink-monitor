"""Optional OneLibrary / Device Library Plus reader (exportLibrary.db).

XDJ-AZ, OPUS-QUAD and OMNIS-DUO use AlphaTheta's newer SQLCipher library on USB
(``PIONEER/rekordbox/exportLibrary.db``). Rekordbox still writes classic
``export.pdb`` alongside it, which this project already reads. Playlists and
history created on the player itself only land in the OneLibrary file.

Opening the encrypted DB needs ``pyrekordbox`` from GitHub master (Device
Library Plus landed after 0.4.4) plus its SQLCipher stack::

    pip install "pyrekordbox @ git+https://github.com/dylanljones/pyrekordbox.git@master"

When that package is missing, we still detect and cache the file so the UI can
show that OneLibrary is present but not readable yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import pdb

ONE_LIBRARY_PATH = "PIONEER/rekordbox/exportLibrary.db"


@dataclass
class OneLibrarySummary:
    """Lightweight counts / status for the live Library panel."""
    present: bool = False
    readable: bool = False
    path: str = ""
    tracks: int = 0
    playlists: int = 0
    history: int = 0
    error: str | None = None
    detail: str = ""


@dataclass
class OneLibraryTrack:
    id: int
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    key: str = ""
    label: str = ""
    duration_ms: int = 0
    tempo: float = 0.0
    analyze_path: str = ""
    artwork_path: str = ""
    rating: int = 0
    year: int = 0
    bitrate: int = 0
    comment: str = ""


def pyrekordbox_available() -> bool:
    try:
        from pyrekordbox import DeviceLibraryPlus  # noqa: F401
        return True
    except Exception:
        return False


def open_onelibrary(path: str) -> tuple[Any | None, str | None]:
    """Open ``exportLibrary.db``. Returns ``(db, error)``."""
    try:
        from pyrekordbox import DeviceLibraryPlus
    except Exception as exc:
        return None, f"pyrekordbox missing or incomplete ({exc})"
    try:
        db = DeviceLibraryPlus(path)
        return db, None
    except Exception as exc:
        return None, str(exc)


def summarize(db: Any | None, *, present: bool, path: str = "",
              error: str | None = None) -> OneLibrarySummary:
    out = OneLibrarySummary(present=present, path=path, error=error)
    if not present:
        out.detail = "not on medium"
        return out
    if db is None:
        out.detail = error or "present (install pyrekordbox master to read)"
        return out
    out.readable = True
    tracks, content_error = content_count(db)
    out.tracks = tracks
    if content_error:
        out.error = content_error
    try:
        playlists = list(db.get_playlist())
        out.playlists = len(playlists)
    except Exception:
        out.playlists = 0
    try:
        history = list(db.get_history())
        out.history = len(history)
    except Exception:
        # Some pyrekordbox builds name this differently.
        try:
            history = list(getattr(db, "get_histories", lambda: [])())
            out.history = len(history)
        except Exception:
            out.history = 0
    out.detail = (
        f"{out.tracks} tracks · {out.playlists} playlists · {out.history} history"
    )
    if out.error:
        out.detail = f"{out.detail} · {out.error}"
    return out


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ID_COLUMNS = ("content_id", "ContentID", "ID", "id")
_PATH_COLUMNS = (
    "analysisDataFilePath", "AnalysisDataFilePath", "analysis_data_file_path",
)
_TITLE_COLUMNS = ("title", "Title")
_ARTIST_COLUMNS = ("artist", "Artist")
_LENGTH_COLUMNS = ("length", "Length")


def content_count(db: Any) -> tuple[int, str | None]:
    """How many content rows we can see, and why the ORM count may be zero.

    Firmware 1.30 still serves playlists from this file while ``get_content()``
    can return nothing (empty mapped table, or a query that raises). A raw
    count of the real content table is what the Health page should show.
    """
    orm_error: str | None = None
    try:
        rows = list(db.get_content())
        if rows:
            return len(rows), None
    except Exception as exc:
        orm_error = f"get_content failed: {exc}"
    counted, note = _raw_content_count(db)
    if orm_error and note:
        return counted, f"{orm_error}; {note}"
    if orm_error:
        return counted, orm_error
    if counted and note:
        return counted, note
    if note:
        return 0, note
    return 0, None


def _session(db: Any) -> Any:
    return getattr(db, "session", None)


def _sql_rows(session: Any, statement: str, params: dict | None = None) -> list[Any]:
    from sqlalchemy import text

    result = session.execute(text(statement), params or {})
    if hasattr(result, "fetchall"):
        return list(result.fetchall())
    return list(result)


def _rollback(session: Any) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            pass


def _table_names(session: Any) -> list[str]:
    try:
        rows = _sql_rows(
            session, "SELECT name FROM sqlite_master WHERE type='table'")
    except Exception:
        _rollback(session)
        return []
    names = []
    for row in rows:
        name = row[0] if not isinstance(row, str) else row
        if isinstance(name, str) and _SAFE_IDENT.match(name):
            names.append(name)
    return names


def _columns(session: Any, table: str) -> list[str]:
    try:
        rows = _sql_rows(session, f"PRAGMA table_info({table})")
    except Exception:
        _rollback(session)
        return []
    cols = []
    for row in rows:
        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        name = row[1] if len(row) > 1 else None
        if isinstance(name, str):
            cols.append(name)
    return cols


def _content_tables(names: list[str]) -> list[str]:
    exact = [n for n in names if n.lower() == "content"]
    others = [
        n for n in names
        if "content" in n.lower() and n not in exact
        and "playlist" not in n.lower() and "history" not in n.lower()
    ]
    return exact + others


def _raw_content_count(db: Any) -> tuple[int, str | None]:
    session = _session(db)
    if session is None:
        return 0, None
    notes = []
    best = 0
    best_table = ""
    for table in _content_tables(_table_names(session)):
        try:
            rows = _sql_rows(session, f"SELECT COUNT(*) FROM {table}")
            n = int(rows[0][0]) if rows else 0
        except Exception:
            _rollback(session)
            continue
        notes.append(f"{table}={n}")
        if n > best:
            best = n
            best_table = table
    if not notes:
        return 0, None
    if best and best_table and best_table.lower() != "content":
        return best, "ORM content empty; " + ", ".join(notes)
    if best:
        return best, "get_content returned no rows; " + ", ".join(notes)
    return 0, "content tables empty (" + ", ".join(notes) + ")"


def raw_content_track(db: Any, content_id: int) -> OneLibraryTrack | None:
    """Read one content row when the ORM query cannot see it."""
    session = _session(db)
    if session is None or not content_id:
        return None
    for table in _content_tables(_table_names(session)):
        cols = _columns(session, table)
        if not cols:
            continue
        id_col = next((c for c in _ID_COLUMNS if c in cols), None)
        if id_col is None:
            continue
        title_col = next((c for c in _TITLE_COLUMNS if c in cols), None)
        artist_col = next((c for c in _ARTIST_COLUMNS if c in cols), None)
        path_col = next((c for c in _PATH_COLUMNS if c in cols), None)
        length_col = next((c for c in _LENGTH_COLUMNS if c in cols), None)
        picked = [id_col]
        for col in (title_col, artist_col, path_col, length_col):
            if col and col not in picked:
                picked.append(col)
        sql = (
            f"SELECT {', '.join(picked)} FROM {table} "
            f"WHERE {id_col} = :id LIMIT 1"
        )
        try:
            rows = _sql_rows(session, sql, {"id": int(content_id)})
        except Exception:
            _rollback(session)
            continue
        if not rows:
            continue
        row = rows[0]
        values = {picked[i]: row[i] for i in range(len(picked))}
        title = str(values.get(title_col) or "") if title_col else ""
        artist = str(values.get(artist_col) or "") if artist_col else ""
        path = str(values.get(path_col) or "") if path_col else ""
        length = 0
        if length_col and values.get(length_col):
            try:
                length = int(values[length_col])
            except (TypeError, ValueError):
                length = 0
        if not title and not path:
            continue
        return OneLibraryTrack(
            id=int(content_id),
            title=title,
            artist=artist,
            duration_ms=length,
            analyze_path=path,
        )
    return None


def track_from_content(content: Any) -> OneLibraryTrack:
    """Map a pyrekordbox content row to our track-shaped dataclass."""
    def _name(obj: Any) -> str:
        if obj is None:
            return ""
        return getattr(obj, "name", None) or str(obj) or ""

    bpm = getattr(content, "bpmx100", None) or getattr(content, "bpm", 0) or 0
    if isinstance(bpm, int) and bpm > 500:  # bpm×100
        tempo = bpm / 100.0
    else:
        tempo = float(bpm or 0)
    image = getattr(content, "image", None)
    art = ""
    if image is not None:
        art = getattr(image, "path", "") or ""
    return OneLibraryTrack(
        id=int(getattr(content, "content_id", 0) or getattr(content, "ID", 0) or 0),
        title=getattr(content, "title", "") or "",
        artist=_name(getattr(content, "artist", None)),
        album=_name(getattr(content, "album", None)),
        genre=_name(getattr(content, "genre", None)),
        key=_name(getattr(content, "key", None)),
        label=_name(getattr(content, "label", None)),
        duration_ms=int(getattr(content, "length", 0) or 0),
        tempo=tempo,
        analyze_path=getattr(content, "analysisDataFilePath", "") or "",
        artwork_path=art,
        rating=int(getattr(content, "rating", 0) or 0),
        year=int(getattr(content, "releaseYear", 0) or 0),
        bitrate=int(getattr(content, "bitrate", 0) or 0),
        comment=getattr(content, "djComment", "") or "",
    )


def as_pdb_track(track: OneLibraryTrack) -> pdb.Track:
    """Same shape as an export.pdb row so callers can share one code path."""
    return pdb.Track(
        id=int(track.id or 0),
        title=track.title or "",
        artist=track.artist or "",
        album=track.album or "",
        genre=track.genre or "",
        key=track.key or "",
        label=track.label or "",
        comment=track.comment or "",
        duration=int((track.duration_ms or 0) / 1000),
        tempo=float(track.tempo or 0),
        rating=int(track.rating or 0),
        year=int(track.year or 0),
        bitrate=int(track.bitrate or 0),
        analyze_path=track.analyze_path or "",
        artwork_path=track.artwork_path or "",
    )


def find_content(db: Any, content_id: int) -> OneLibraryTrack | None:
    if db is None or not content_id:
        return None
    getter = getattr(db, "get_content", None)
    if callable(getter):
        for kwargs in ({"content_id": int(content_id)}, {"ID": int(content_id)}, {}):
            try:
                rows = list(getter(**kwargs))
            except Exception:
                continue
            for content in rows:
                cid = getattr(content, "content_id", None)
                if cid is None:
                    cid = getattr(content, "ID", None)
                try:
                    if int(cid or 0) != int(content_id):
                        continue
                except (TypeError, ValueError):
                    continue
                return track_from_content(content)
            if kwargs:
                # Filtered query ran. Still try SQL in case the ORM table is
                # empty while another content table has the row.
                break
    try:
        return raw_content_track(db, int(content_id))
    except Exception:
        return None


def _playlist_row(item: Any) -> dict[str, Any]:
    pid = int(getattr(item, "ID", 0) or getattr(item, "id", 0)
              or getattr(item, "playlist_id", 0) or 0)
    name = (getattr(item, "name", None) or getattr(item, "title", None)
            or getattr(item, "Name", None) or f"Playlist {pid}")
    parent = getattr(item, "parent_id", None)
    if parent is None:
        parent = getattr(item, "ParentID", None)
    try:
        parent_id = int(parent) if parent is not None else 0
    except (TypeError, ValueError):
        parent_id = 0
    is_folder = bool(getattr(item, "is_folder", False)
                     or getattr(item, "attribute", 0) in (1, 2))
    return {
        "id": pid,
        "name": str(name),
        "parent_id": parent_id,
        "folder": is_folder,
    }


def list_playlists(db: Any) -> list[dict[str, Any]]:
    if db is None:
        return []
    try:
        items = list(db.get_playlist())
    except Exception:
        return []
    return [_playlist_row(p) for p in items]


def list_history(db: Any) -> list[dict[str, Any]]:
    if db is None:
        return []
    items = []
    for getter in ("get_history", "get_histories"):
        fn = getattr(db, getter, None)
        if not callable(fn):
            continue
        try:
            items = list(fn())
            break
        except Exception:
            continue
    out = []
    for item in items:
        row = _playlist_row(item)
        row["kind"] = "history"
        out.append(row)
    return out


def playlist_tracks(db: Any, playlist_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    """Best-effort track list for a OneLibrary playlist / history entry."""
    if db is None or not playlist_id:
        return []
    limit = max(1, min(500, int(limit)))
    candidates: list[Any] = []
    for name in ("get_playlist_content", "get_history_content",
                 "get_playlist_songs", "get_songs"):
        fn = getattr(db, name, None)
        if not callable(fn):
            continue
        try:
            candidates = list(fn(playlist_id))
            if candidates:
                break
        except TypeError:
            try:
                candidates = list(fn())
                if candidates:
                    break
            except Exception:
                continue
        except Exception:
            continue
    if not candidates:
        return []
    rows = []
    for item in candidates[:limit]:
        content = getattr(item, "content", None) or item
        try:
            track = track_from_content(content)
        except Exception:
            continue
        rows.append({
            "id": track.id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "genre": track.genre,
            "key": track.key,
            "bpm": track.tempo,
            "duration_s": int((track.duration_ms or 0) / 1000),
            "has_artwork": bool(track.artwork_path),
            "source": "onelibrary",
        })
    return rows
