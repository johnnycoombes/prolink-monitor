"""Pick a newer final release. No network and no Qt."""

from __future__ import annotations

from packaging.version import InvalidVersion, Version


def parse_version(value: str) -> Version | None:
    try:
        return Version(str(value).strip())
    except InvalidVersion:
        return None


def is_newer(current: str, candidate: str) -> bool:
    left = parse_version(current)
    right = parse_version(candidate)
    if left is None or right is None:
        return False
    return right > left


def select_release(current: str, candidates: list[str]) -> str | None:
    """Newest final release newer than ``current``.

    Pre-releases (``1.2.0a1``, ``1.2.0rc1``) are ignored. Invalid strings are
    ignored. Returns the version text the caller supplied, not a normalised
    form, so the archive name stays unchanged.
    """
    current_v = parse_version(current)
    if current_v is None:
        return None
    best: tuple[Version, str] | None = None
    for raw in candidates:
        parsed = parse_version(raw)
        if parsed is None or parsed.is_prerelease:
            continue
        if parsed <= current_v:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, raw)
    return None if best is None else best[1]
