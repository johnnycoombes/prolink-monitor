"""English UI strings for the desktop shell."""

from __future__ import annotations

STRINGS = {
    "app_title": "Engine Room",
    "brand": "ENGINE ROOM",
    "brand_sub": "// pro dj link",
    "nav_monitor": "MONITOR",
    "nav_devices": "DEVICES",
    "nav_library": "LIBRARY",
    "nav_settings": "SETTINGS",
    "nav_about": "ABOUT",
    "monitor_title": "Live decks",
    "monitor_sub": "What each player is doing right now",
    "devices_title": "Network devices",
    "devices_sub": "Players and mixers seen on Pro DJ Link",
    "library_title": "Library",
    "library_sub": "Media loaded from player USB / SD over NFS",
    "settings_title": "Settings",
    "settings_sub": "Connection, display and behaviour",
    "about_title": "About",
    "about_sub": "Pro DJ Link monitor desktop shell",
    "connect": "Connect",
    "disconnect": "Disconnect",
    "reconnect": "Reconnect",
    "save": "Save settings",
    "saved": "Settings saved",
    "apply_reconnect": "Save & reconnect",
    "open_web": "Open web panel",
    "waiting": "Waiting for the player",
    "searching": "Looking for players on the network…",
    "seen_no_status": "Devices are visible but none is sending status yet.",
    "idle": "Not connected",
    "connecting": "Connecting…",
    "connected": "Connected",
    "error": "Error",
    "no_devices": "No devices yet",
    "no_track": "no track",
    "no_track_loaded": "no track loaded",
    "loading": "loading…",
    "elapsed": "elapsed",
    "remaining": "remaining",
    "master": "MASTER",
    "sync": "SYNC",
    "on_air": "ON AIR",
    "channel_closed": "CHANNEL CLOSED",
    "mode": "Mode",
    "host": "Player address",
    "host_hint": "Leave blank to auto-detect",
    "device_number": "Virtual device number",
    "device_name": "Announce name",
    "port": "Web server port",
    "iface": "Capture interface",
    "tshark": "tshark path",
    "cache": "Cache folder",
    "max_decks": "Visible decks",
    "zoom": "Waveform zoom",
    "wave": "Waveform",
    "wave_rgb": "RGB",
    "wave_3band": "3-Band",
    "wave_blue": "Blue",
    "auto_connect": "Connect automatically on launch",
    "start_web": "Start local web server",
    "show_empty": "Show empty decks",
    "poll_hz": "UI refresh rate (Hz)",
    "section_connection": "CONNECTION",
    "section_display": "DISPLAY",
    "section_behaviour": "BEHAVIOUR",
    "col_number": "#",
    "col_name": "NAME",
    "col_kind": "KIND",
    "col_ip": "IP",
    "packets": "packets",
    "tracks": "tracks",
    "artists": "artists",
    "albums": "albums",
    "mode_auto": "Auto",
    "mode_vcdj": "Virtual device",
    "mode_sniffer": "Passive capture",
    "about_body": (
        "Desktop front-end for this prolink-monitor fork.\n"
        "Talks to AlphaTheta / Pioneer DJ gear over Pro DJ Link.\n\n"
        "Based on the original prolink-monitor by @fidow:\n"
        "https://github.com/fidow/prolink-monitor\n\n"
        "Protocol work builds on Deep Symmetry (dysentery, crate-digger)\n"
        "and reverse engineering by @henrybetts and @flesniak.\n\n"
        "Independent project — not affiliated with AlphaTheta / Pioneer DJ."
    ),
}

STATES = {
    "no_track": "no track",
    "loading": "loading",
    "playing": "playing",
    "looping": "looping",
    "paused": "paused",
    "cued": "cued",
    "cue_play": "cue play",
    "cue_scratch": "cue scratch",
    "searching": "searching",
    "unplayable": "unplayable",
    "end_of_track": "end of track",
    "emergency": "emergency",
    "unknown": "unknown",
}


class I18n:
    """Thin English string table. Kept as a class so pages can call `.t()`."""

    def t(self, key: str) -> str:
        return str(STRINGS.get(key, key))

    def state(self, key: str) -> str:
        return str(STATES.get(key, key))
