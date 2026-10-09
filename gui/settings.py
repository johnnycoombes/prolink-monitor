"""Persistent settings for the desktop app."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from gui.overlay_now import normalize_now_pos, normalize_now_style
from prolink.playhead_smooth import normalize_mode as _smoothing_mode
from prolink.proto import normalize_playhead_mode
from prolink.wnp import normalize_target


DEFAULTS: dict[str, Any] = {
    "host": "",
    "mode": "auto",                 # auto | vcdj | sniffer
    "port": 8777,
    "http_bind": "0.0.0.0",        # 0.0.0.0 = reachable on LAN (phone); 127.0.0.1 = local only
    "number": 5,
    "name": "monitor",
    "iface": "",
    "tshark": "",
    "cache": "",
    "local_music_root": "",  # optional mirror of USB library for embedded cover art
    "max_decks": 4,
    "zoom_bars": 4,                # 4/4 bars shown in the detail waveform
    "zoom_bars_by_deck": {},        # optional per-deck overrides { "1": 8, ... }
    "waveform_style": "rgb",       # rgb | 3band | blue
    "show_phrases": True,          # Rekordbox-style phrase strip under overview
    "show_vocals": True,           # vocal lane under the phrase strip
    "show_next_cue": True,         # NEXT cue / phrase countdown
    "playhead_smoothing": "normal",  # direct | normal | strong
    "playhead_position": "auto",   # auto | centre | left — scrolling needle
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
    "overlay_layout": "nowplaying",   # nowplaying | dual | minimal | setlist
    "overlay_now_style": "card",      # card | panel — now playing layout (layout=nowplaying)
    "overlay_now_pos": "bl",          # card: tl|tr|bl|br — panel: bottom|top
    "overlay_corner": "bl",           # bl | br | tl | tr | center
    "floating_now_topmost": True,
    "floating_now_transparent": True,
    "floating_now_x": -1,
    "floating_now_y": -1,
    "floating_now_width": 0,
    "floating_now_height": 0,
    "floating_now_panel_x": -1,
    "floating_now_panel_y": -1,
    "floating_now_panel_width": 0,
    "floating_now_panel_height": 0,
    "overlay_playing_only": True,
    "overlay_mix": True,
    "overlay_decks": 1,
    "overlay_waveform_style": "rgb",  # rgb | 3band | blue — overlay mini-wave only
    "overlay_scale": "1",
    "overlay_accent": "",
    "overlay_show_tags": True,
    "overlay_show_bpm": True,
    "overlay_show_next": True,
    # Panel-bar visualiser. On by default. The audio checkbox only picks the
    # input (waveform vs PC audio); it is not the on/off switch.
    "overlay_show_spectrum": True,
    "overlay_spectrum_audio": False,
    "session_autosave": False,
    "session_autosave_dir": "",       # empty → ~/.prolink-monitor/sessions
    "minimize_to_tray": True,         # when connected, minimize hides to tray
    "close_to_tray": True,            # when connected, window close hides to tray
    "check_for_updates": True,        # look for a signed release on startup
    # What's Now Playing remote input. Off until the user opts in.
    "wnp_enabled": False,
    "wnp_host": "localhost",
    "wnp_port": 8899,
    "wnp_secret": "",
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


def display_prefs_from(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monitor-view prefs the web panel and overlay should follow."""
    src = data or {}
    try:
        zoom = int(src.get("zoom_bars", 4))
    except (TypeError, ValueError):
        zoom = 4
    if zoom not in (1, 2, 4, 8, 16):
        zoom = 4
    by_deck_raw = src.get("zoom_bars_by_deck") or {}
    by_deck: dict[str, int] = {}
    if isinstance(by_deck_raw, dict):
        for key, value in by_deck_raw.items():
            try:
                bars = int(value)
                deck_no = int(key)
            except (TypeError, ValueError):
                continue
            if bars in (1, 2, 4, 8, 16):
                by_deck[str(deck_no)] = bars
    try:
        max_decks = 4 if int(src.get("max_decks", 4)) >= 4 else 2
    except (TypeError, ValueError):
        max_decks = 4
    style = str(src.get("waveform_style") or "rgb").lower()
    if style not in ("rgb", "3band", "blue"):
        style = "rgb"
    audio = bool(src.get("overlay_spectrum_audio", False))
    if str(src.get("spectrum_source") or "") == "loopback":
        audio = True
    return {
        "show_phrases": bool(src.get("show_phrases", True)),
        "show_vocals": bool(src.get("show_vocals", True)),
        "show_next_cue": bool(src.get("show_next_cue", True)),
        "playhead_smoothing": _smoothing_mode(src.get("playhead_smoothing")),
        "show_spectrum": bool(src.get("overlay_show_spectrum", src.get("show_spectrum", True))),
        "zoom_bars": zoom,
        "zoom_bars_by_deck": by_deck,
        "playhead_position": normalize_playhead_mode(src.get("playhead_position")),
        "max_decks": max_decks,
        "waveform_style": style,
        "show_empty_decks": bool(src.get("show_empty_decks", True)),
        "elements": deck_elements_from(src),
        "spectrum_source": "loopback" if audio else "waveform",
    }



def settings_path() -> str:
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".prolink-monitor")
    return os.path.join(base, "settings.json")


def sessions_dir(data: dict[str, Any] | None = None) -> str:
    """Directory for auto-saved / exported session playlists."""
    src = data or {}
    custom = str(src.get("session_autosave_dir") or "").strip()
    if custom:
        return os.path.expanduser(custom)
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".prolink-monitor")
    return os.path.join(base, "sessions")


def load_settings() -> dict[str, Any]:
    path = settings_path()
    data = deepcopy(DEFAULTS)
    legacy: dict[str, Any] = {}
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in DEFAULTS:
                    data[key] = value
            # Migrate pre-bars zoom_seconds → zoom_bars (≈ seconds/2 at 120 BPM).
            if "zoom_bars" not in saved and "zoom_seconds" in saved:
                legacy["zoom_seconds"] = saved["zoom_seconds"]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return _sanitize({**data, **legacy})


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
    bind = str(data.get("http_bind") or "0.0.0.0").strip() or "0.0.0.0"
    out["http_bind"] = "127.0.0.1" if bind in ("127.0.0.1", "localhost") else "0.0.0.0"
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
        if "zoom_bars" in data and data.get("zoom_bars") is not None:
            zoom = int(data.get("zoom_bars", 4))
        else:
            # Legacy seconds → bars at 120 BPM (1 bar ≈ 2 s).
            sec = int(data.get("zoom_seconds", 8))
            zoom = {4: 2, 8: 4, 16: 8, 32: 16, 64: 16}.get(sec, 4)
        out["zoom_bars"] = zoom if zoom in (1, 2, 4, 8, 16) else 4
    except (TypeError, ValueError):
        out["zoom_bars"] = 4
    by_deck = data.get("zoom_bars_by_deck") or {}
    clean_deck: dict[str, int] = {}
    if isinstance(by_deck, dict):
        for k, v in by_deck.items():
            try:
                bars = int(v)
            except (TypeError, ValueError):
                continue
            if bars in (1, 2, 4, 8, 16):
                clean_deck[str(int(k))] = bars
    out["zoom_bars_by_deck"] = clean_deck
    style = str(data.get("waveform_style") or "rgb").lower()
    out["waveform_style"] = style if style in ("rgb", "3band", "blue") else "rgb"
    out["show_phrases"] = bool(data.get("show_phrases", True))
    out["show_vocals"] = bool(data.get("show_vocals", True))
    out["show_next_cue"] = bool(data.get("show_next_cue", True))
    out["playhead_smoothing"] = _smoothing_mode(data.get("playhead_smoothing"))
    out["playhead_position"] = normalize_playhead_mode(data.get("playhead_position"))
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
    out["overlay_layout"] = (
        layout if layout in ("nowplaying", "dual", "minimal", "setlist") else "nowplaying"
    )
    out["overlay_now_style"] = normalize_now_style(data.get("overlay_now_style"))
    now_style = out["overlay_now_style"]
    out["overlay_now_pos"] = normalize_now_pos(now_style, data.get("overlay_now_pos"))
    corner = str(data.get("overlay_corner") or "bl").lower()
    out["overlay_corner"] = corner if corner in ("bl", "br", "tl", "tr", "center") else "bl"
    out["overlay_playing_only"] = bool(data.get("overlay_playing_only", True))
    out["overlay_mix"] = bool(data.get("overlay_mix", True))
    try:
        out["overlay_decks"] = max(1, min(4, int(data.get("overlay_decks", 1))))
    except (TypeError, ValueError):
        out["overlay_decks"] = 1
    owave = str(data.get("overlay_waveform_style") or "rgb").lower()
    out["overlay_waveform_style"] = owave if owave in ("rgb", "3band", "blue") else "rgb"
    scale = str(data.get("overlay_scale") or "1").strip()
    try:
        s = float(scale)
        out["overlay_scale"] = scale if 0.5 <= s <= 2.5 else "1"
    except (TypeError, ValueError):
        out["overlay_scale"] = "1"
    accent = str(data.get("overlay_accent") or "").strip().lstrip("#")
    out["overlay_accent"] = accent if len(accent) == 6 and all(
        c in "0123456789abcdefABCDEF" for c in accent
    ) else ""
    out["overlay_show_tags"] = bool(data.get("overlay_show_tags", True))
    out["overlay_show_bpm"] = bool(data.get("overlay_show_bpm", True))
    out["overlay_show_next"] = bool(data.get("overlay_show_next", True))
    out["overlay_show_spectrum"] = bool(data.get("overlay_show_spectrum", True))
    out["overlay_spectrum_audio"] = bool(data.get("overlay_spectrum_audio", False))
    out["floating_now_topmost"] = bool(data.get("floating_now_topmost", True))
    out["floating_now_transparent"] = bool(data.get("floating_now_transparent", True))
    for key, lo, hi in (
        ("floating_now_x", -1, 10000),
        ("floating_now_y", -1, 10000),
        ("floating_now_width", 0, 4000),
        ("floating_now_height", 0, 4000),
        ("floating_now_panel_x", -1, 10000),
        ("floating_now_panel_y", -1, 10000),
        ("floating_now_panel_width", 0, 4000),
        ("floating_now_panel_height", 0, 4000),
    ):
        try:
            out[key] = max(lo, min(hi, int(data.get(key, lo))))
        except (TypeError, ValueError):
            out[key] = lo if "x" in key or "y" in key else 0
    out["session_autosave"] = bool(data.get("session_autosave", False))
    out["session_autosave_dir"] = str(data.get("session_autosave_dir") or "").strip()
    out["minimize_to_tray"] = bool(data.get("minimize_to_tray", True))
    out["close_to_tray"] = bool(data.get("close_to_tray", True))
    out["check_for_updates"] = bool(data.get("check_for_updates", True))
    out["local_music_root"] = str(data.get("local_music_root") or "").strip()
    out["wnp_enabled"] = bool(data.get("wnp_enabled", False))
    host, port = normalize_target(data.get("wnp_host", "localhost"), data.get("wnp_port", 8899))
    out["wnp_host"] = host
    out["wnp_port"] = port
    out["wnp_secret"] = str(data.get("wnp_secret") or "").strip()
    return out


def as_namespace(data: dict[str, Any]):
    """Shape settings like the argparse namespace expected by build_source()."""
    from types import SimpleNamespace
    return SimpleNamespace(
        host=data["host"] or None,
        mode=data["mode"],
        port=data["port"],
        http_host=data.get("http_bind") or "0.0.0.0",
        number=data["number"],
        name=data["name"],
        tshark=data["tshark"] or None,
        iface=data["iface"] or None,
        cache=data["cache"] or None,
        no_open=True,
    )
