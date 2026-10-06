"""Choose where a loaded track's ANLZ files come from.

On an XDJ-AZ the status packet's track id is the rekordbox id dbserver
already uses for the title. The waveform is not in that reply. It is in
ANLZ files on the USB stick, and ``export.pdb`` is what names those files.

OneLibrary (``exportLibrary.db``) is a second database. When it is present
but its content table is empty or unreadable, it must not hide the
``export.pdb`` row. That is what stopped every waveform fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _analyze_path(track: Any) -> str:
    return str(getattr(track, "analyze_path", "") or "")


def _useful(track: Any) -> bool:
    if track is None:
        return False
    return bool(
        getattr(track, "title", "")
        or getattr(track, "artist", "")
        or _analyze_path(track)
        or getattr(track, "file_path", "")
    )


def _duration_ms(track: Any) -> int:
    raw_ms = int(getattr(track, "duration_ms", 0) or 0)
    if raw_ms > 0:
        return raw_ms
    seconds = int(getattr(track, "duration", 0) or 0)
    if seconds <= 0:
        return 0
    # pdb.Track.duration is whole seconds. A value already in ms is much larger.
    if seconds >= 100_000:
        return seconds
    return seconds * 1000


@dataclass
class AnlzChoice:
    track: Any | None
    source: str
    note: str


def choose_by_id(
    pdb_track: Any,
    ol_track: Any,
    *,
    live_title: str = "",
    live_artist: str = "",
) -> AnlzChoice:
    """Pick a library row for this status id.

    ``export.pdb`` comes first: that id is the one the player announces, and
    it is the row that carries the ANLZ path. OneLibrary is used when it
    actually has this id. A live title picks between the two when they disagree.
    """
    options: list[tuple[str, Any]] = []
    if _useful(pdb_track):
        options.append(("pdb-id", pdb_track))
    if _useful(ol_track):
        options.append(("onelibrary-id", ol_track))
    if not options:
        return AnlzChoice(
            None, "none",
            "no export.pdb or OneLibrary row for this track id",
        )

    title = _norm(live_title)
    artist = _norm(live_artist)
    if title:
        titled = [item for item in options if _norm(getattr(item[1], "title", "")) == title]
        if artist:
            both = [
                item for item in titled
                if _norm(getattr(item[1], "artist", "")) in ("", artist)
            ]
            if both:
                titled = both
        if titled:
            titled.sort(key=lambda item: (
                not _analyze_path(item[1]),
                0 if item[0] == "pdb-id" else 1,
            ))
            source, track = titled[0]
            return AnlzChoice(track, source, "title matches the loaded track")

    options.sort(key=lambda item: (
        not _analyze_path(item[1]),
        0 if item[0] == "pdb-id" else 1,
    ))
    source, track = options[0]
    if _analyze_path(track):
        note = "analysis path from " + (
            "export.pdb" if source == "pdb-id" else "OneLibrary"
        )
    else:
        note = (
            "export.pdb row has no analysis path"
            if source == "pdb-id"
            else "OneLibrary row has no analysis path"
        )
    return AnlzChoice(track, source, note)


def match_by_title(
    tracks: Iterable[Any],
    *,
    title: str,
    artist: str = "",
    duration_ms: int = 0,
) -> Any | None:
    """Find a row with the same title (and artist, when we have one).

    Duration, when both sides have it, must be within eight seconds so a
    shared title does not attach another track's waveform.
    """
    want_title = _norm(title)
    want_artist = _norm(artist)
    if not want_title:
        return None
    best = None
    best_gap = 10 ** 12
    for track in tracks:
        if not _useful(track) or not _analyze_path(track):
            continue
        if _norm(getattr(track, "title", "")) != want_title:
            continue
        got_artist = _norm(getattr(track, "artist", ""))
        if want_artist and got_artist and got_artist != want_artist:
            continue
        gap = 0
        got_ms = _duration_ms(track)
        if duration_ms > 0 and got_ms > 0:
            gap = abs(got_ms - int(duration_ms))
            if gap > 8000:
                continue
        if best is None or gap < best_gap:
            best = track
            best_gap = gap
    return best
