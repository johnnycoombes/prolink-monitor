"""Persistent settings for the desktop app."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any


DEFAULTS: dict[str, Any] = {
    "host": "",
    "mode": "auto",                 # auto | vcdj | sniffer
    "port": 8777,
    "number": 5,
    "name": "monitor",
    "iface": "",
    "tshark": "",
    "cache": "",
    "max_decks": 4,
    "zoom_seconds": 8,
    "waveform_style": "rgb",       # rgb | 3band | blue
    "auto_connect": True,
    "start_web_server": True,
    "window_width": 1360,
    "window_height": 860,
    "sidebar_compact": False,
    "sidebar_visible": True,
    "poll_hz": 60,
    "show_empty_decks": True,
    # Deck card elements (Monitor page)
    "deck_show_artwork": True,
    "deck_show_title": True,
    "deck_show_artist": True,
    "deck_show_meta": True,
    "deck_show_tags": True,
    "deck_show_waveform": True,
    "deck_show_bpm": True,
    "deck_show_tempo": True,
    "deck_show_time": True,
    "deck_show_key": True,
    "deck_show_state": True,
    "overlay_layout": "nowplaying",   # nowplaying | dual | minimal
    "overlay_corner": "bl",           # bl | br | tl | tr | center
    "overlay_playing_only": True,
    "overlay_mix": True,
    "overlay_decks": 1,
}

# Keys that toggle individual pieces of each Monitor deck card.
DECK_ELEMENT_KEYS = (
    "deck_show_artwork",
    "deck_show_title",
    "deck_show_artist",
    "deck_show_meta",
    "deck_show_tags",
    "deck_show_waveform",
    "deck_show_bpm",
    "deck_show_tempo",
    "deck_show_time",
    "deck_show_key",
    "deck_show_state",
)


def deck_elements_from(data: dict[str, Any] | None = None) -> dict[str, bool]:
    """Return the deck-element visibility map, defaulting missing keys to on."""
    src = data or {}
    return {key: bool(src.get(key, True)) for key in DECK_ELEMENT_KEYS}



def settings_path() -> str:
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".prolink-monitor")
    return os.path.join(base, "settings.json")


def load_settings() -> dict[str, Any]:
    path = settings_path()
    data = deepcopy(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in DEFAULTS:
                    data[key] = value
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return _sanitize(data)


def save_settings(data: dict[str, Any]) -> None:
    clean = _sanitize({**DEFAULTS, **data})
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(DEFAULTS)
    out["host"] = str(data.get("host") or "").strip()
    mode = str(data.get("mode") or "auto").lower()
    out["mode"] = mode if mode in ("auto", "vcdj", "sniffer") else "auto"
    try:
        out["port"] = max(1, min(65535, int(data.get("port", 8777))))
    except (TypeError, ValueError):
        out["port"] = 8777
    try:
        out["number"] = max(1, min(6, int(data.get("number", 5))))
    except (TypeError, ValueError):
        out["number"] = 5
    out["name"] = (str(data.get("name") or "monitor").strip() or "monitor")[:20]
    out["iface"] = str(data.get("iface") or "").strip()
    out["tshark"] = str(data.get("tshark") or "").strip()
    out["cache"] = str(data.get("cache") or "").strip()
    try:
        out["max_decks"] = 4 if int(data.get("max_decks", 4)) >= 4 else 2
    except (TypeError, ValueError):
        out["max_decks"] = 4
    try:
        zoom = int(data.get("zoom_seconds", 8))
        out["zoom_seconds"] = zoom if zoom in (4, 8, 16, 32, 64) else 8
    except (TypeError, ValueError):
        out["zoom_seconds"] = 8
    style = str(data.get("waveform_style") or "rgb").lower()
    out["waveform_style"] = style if style in ("rgb", "3band", "blue") else "rgb"
    out["auto_connect"] = bool(data.get("auto_connect", True))
    out["start_web_server"] = bool(data.get("start_web_server", True))
    try:
        out["window_width"] = max(960, min(3840, int(data.get("window_width", 1360))))
    except (TypeError, ValueError):
        out["window_width"] = 1360
    try:
        out["window_height"] = max(640, min(2160, int(data.get("window_height", 860))))
    except (TypeError, ValueError):
        out["window_height"] = 860
    out["sidebar_compact"] = bool(data.get("sidebar_compact", False))
    out["sidebar_visible"] = bool(data.get("sidebar_visible", True))
    try:
        out["poll_hz"] = max(5, min(60, int(data.get("poll_hz", 60))))
    except (TypeError, ValueError):
        out["poll_hz"] = 60
    out["show_empty_decks"] = bool(data.get("show_empty_decks", True))
    for key in DECK_ELEMENT_KEYS:
        out[key] = bool(data.get(key, True))
    layout = str(data.get("overlay_layout") or "nowplaying").lower()
    out["overlay_layout"] = layout if layout in ("nowplaying", "dual", "minimal") else "nowplaying"
    corner = str(data.get("overlay_corner") or "bl").lower()
    out["overlay_corner"] = corner if corner in ("bl", "br", "tl", "tr", "center") else "bl"
    out["overlay_playing_only"] = bool(data.get("overlay_playing_only", True))
    out["overlay_mix"] = bool(data.get("overlay_mix", True))
    try:
        out["overlay_decks"] = max(1, min(4, int(data.get("overlay_decks", 1))))
    except (TypeError, ValueError):
        out["overlay_decks"] = 1
    return out


def as_namespace(data: dict[str, Any]):
    """Shape settings like the argparse namespace expected by build_source()."""
    from types import SimpleNamespace
    return SimpleNamespace(
        host=data["host"] or None,
        mode=data["mode"],
        port=data["port"],
        number=data["number"],
        name=data["name"],
        tshark=data["tshark"] or None,
        iface=data["iface"] or None,
        cache=data["cache"] or None,
        no_open=True,
    )
