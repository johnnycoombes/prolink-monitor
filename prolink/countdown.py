"""Next cue or phrase, and how long until the playhead reaches it.

Seek-aware: the mark is always the next one strictly after the playhead, so
a jump forward or backward picks a new target immediately.
"""

from __future__ import annotations


def _phrase_time(phrase: dict, beats: list) -> float | None:
    if phrase.get("t") is not None:
        try:
            return float(phrase["t"])
        except (TypeError, ValueError):
            return None
    try:
        beat = int(phrase.get("beat") or 0)
    except (TypeError, ValueError):
        return None
    if beat < 1 or beat > len(beats):
        return None
    row = beats[beat - 1]
    try:
        return float(row[0] if isinstance(row, (list, tuple)) else row)
    except (TypeError, ValueError, IndexError):
        return None


def _cue_name(cue: dict) -> str:
    text = str(cue.get("text") or cue.get("comment") or "").strip()
    if text:
        return text
    hot = int(cue.get("hot") or cue.get("hot_cue") or 0)
    if hot > 0:
        return f"CUE {chr(64 + hot)}" if hot <= 26 else f"CUE {hot}"
    return "MEMORY"


def next_mark(pos_ms: float, *, cues: list | None = None,
              phrases: list | None = None, beats: list | None = None) -> dict | None:
    """The soonest cue or phrase change after ``pos_ms``.

    Returns ``{name, t, kind, remain_ms, color}`` or None.
    """
    pos = float(pos_ms or 0)
    best: dict | None = None
    for cue in cues or []:
        if cue.get("type") == "loop":
            continue
        try:
            t = float(cue.get("t") if cue.get("t") is not None else cue.get("time"))
        except (TypeError, ValueError):
            continue
        if t <= pos + 1:
            continue
        row = {
            "name": _cue_name(cue),
            "t": t,
            "kind": "hot" if int(cue.get("hot") or cue.get("hot_cue") or 0) else "memory",
            "color": cue.get("color"),
            "remain_ms": t - pos,
        }
        if best is None or t < best["t"]:
            best = row
    for phrase in phrases or []:
        t = _phrase_time(phrase, beats or [])
        if t is None or t <= pos + 1:
            continue
        name = str(phrase.get("text") or phrase.get("label") or "PHRASE").strip() or "PHRASE"
        row = {
            "name": name,
            "t": t,
            "kind": "phrase",
            "color": None,
            "remain_ms": t - pos,
        }
        if best is None or t < best["t"]:
            best = row
    return best


def urgency_color(remain_ms: float, base: str = "#e8eaf0") -> str:
    """Calm, then amber inside 10s, then red inside 3s."""
    remain = float(remain_ms or 0)
    if remain <= 3000:
        return "#ff3b30"
    if remain <= 10000:
        return "#ff9f45"
    return base


def format_countdown(mark: dict | None) -> str:
    if not mark:
        return ""
    remain = max(0.0, float(mark.get("remain_ms") or 0))
    total = int(remain // 1000)
    clock = f"{total // 60}:{total % 60:02d}"
    return f"NEXT: {mark.get('name') or 'CUE'} in {clock}"
