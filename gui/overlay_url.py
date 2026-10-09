"""Build overlay URLs (OBS preview, floating window, shared query params)."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from gui.overlay_now import normalize_now_pos, normalize_now_style
from prolink.bundle import bundle_root

WEB_DIR = os.path.join(bundle_root(), "web")


def overlay_asset_version() -> str:
    """Cache-bust OBS browser sources when overlay.html changes."""
    root = os.path.join(WEB_DIR, "overlay.html")
    try:
        return str(int(os.path.getmtime(root)))
    except OSError:
        return "0"


def _now_playing_query(
    settings: dict[str, Any],
    *,
    embed: bool,
    mock: bool,
    preview: bool = False,
) -> list[tuple[str, str]]:
    style = normalize_now_style(settings.get("overlay_now_style"))
    pos = normalize_now_pos(style, settings.get("overlay_now_pos"))
    qs: list[tuple[str, str]] = [
        ("layout", "nowplaying"),
        ("decks", "1"),
        ("wave", str(settings.get("overlay_waveform_style") or "rgb").lower()),
        ("style", style),
        ("pos", pos),
        ("mix", "0"),
        ("next", "0"),
        ("v", overlay_asset_version()),
    ]
    if embed:
        qs.append(("embed", "1"))
    if mock:
        qs.append(("mock", "1"))
    if preview:
        qs.append(("preview", "1"))
    scale = str(settings.get("overlay_scale") or "1")
    if scale and scale != "1":
        qs.append(("scale", scale))
    accent = str(settings.get("overlay_accent") or "").strip().lstrip("#")
    if len(accent) == 6:
        qs.append(("accent", accent))
    if not settings.get("overlay_show_tags", True):
        qs.append(("tags", "0"))
    if not settings.get("overlay_show_bpm", True):
        qs.append(("bpm", "0"))
    return qs


def build_floating_overlay_url(
    port: int,
    settings: dict[str, Any],
    *,
    mock: bool = False,
) -> str:
    """URL for the desktop floating Now Playing window (live SSE from local HTTP server)."""
    qs = _now_playing_query(settings, embed=True, mock=mock)
    qs.append(("float", "1"))
    return f"http://127.0.0.1:{int(port)}/overlay?{urlencode(qs)}"


def build_parity_screenshot_url(
    port: int,
    settings: dict[str, Any],
) -> str:
    """HTTP URL for parity docs: mock preview with embed (card or panel)."""
    qs = _now_playing_query(settings, embed=True, mock=True, preview=True)
    qs.append(("float", "1"))
    return f"http://127.0.0.1:{int(port)}/overlay?{urlencode(qs)}"


def build_floating_overlay_file_url(settings: dict[str, Any], *, mock: bool = True) -> str:
    """file:// URL for offline screenshots (uses overlay ``mock=1`` data)."""
    from PySide6.QtCore import QUrl

    qs = _now_playing_query(settings, embed=True, mock=mock)
    url = QUrl.fromLocalFile(os.path.join(WEB_DIR, "overlay.html"))
    url.setQuery(urlencode(qs))
    return url.toString()


def build_now_playing_preview_query(settings: dict[str, Any], *, embed: bool = False) -> str:
    """Query string for now playing overlay (used by Overlay page preview)."""
    qs = _now_playing_query(settings, embed=embed, mock=False)
    qs.append(("preview", "1"))
    return urlencode(qs)
