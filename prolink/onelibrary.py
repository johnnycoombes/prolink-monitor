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

from dataclasses import dataclass, field
from typing import Any

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
    try:
        contents = list(db.get_content())
        out.tracks = len(contents)
    except Exception:
        out.tracks = 0
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
    return out


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


def find_content(db: Any, content_id: int) -> OneLibraryTrack | None:
    if db is None or not content_id:
        return None
    try:
        for content in db.get_content():
            cid = getattr(content, "content_id", None)
            if cid == content_id:
                return track_from_content(content)
    except Exception:
        return None
    return None
