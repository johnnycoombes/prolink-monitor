"""Pro DJ Link packet encoding and decoding.

Three UDP ports carry everything:

    50000  keep-alive / device announcements (broadcast)
    50001  beats and position (broadcast)
    50002  detailed player status (unicast, only to announced devices)

Field offsets are verified against real captures from an XDJ-AZ (firmware 1.30).
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field

MAGIC = b"Qspt1WmJOL"

PORT_ANNOUNCE = 50000
PORT_BEAT = 50001
PORT_STATUS = 50002
PORT_MIXER = 50004

# Packet types (byte 0x0a)
TYPE_KEEPALIVE = 0x06
TYPE_BEAT = 0x28
TYPE_ABSOLUTE_POSITION = 0x0B
TYPE_MIXER_STATUS = 0x03
TYPE_CDJ_STATUS = 0x0A
TYPE_DJM_STATUS = 0x29
TYPE_MIXER_CLOCK = 0x20

# Absolute / Precise Position packets (CDJ-3000 and newer, port 50001).
# Layout from Deep Symmetry / Beat Link ``PrecisePosition``.
ABS_POS_MIN_LEN = 0x3C
ABS_POS_DEVICE = 0x21
ABS_POS_TRACK_LEN = 0x24   # seconds on CDJ-3000; some AZ builds send milliseconds
ABS_POS_PLAYHEAD = 0x28    # milliseconds
ABS_POS_PITCH = 0x2C       # signed, pitch% × 100
ABS_POS_BPM = 0x38         # effective BPM × 10

# These are stable keys, not user-facing text: they travel through the API as-is
# and each interface translates them (see web/index.html).
SLOTS = {0: "empty", 1: "cd", 2: "sd", 3: "usb", 4: "rekordbox"}
TRACK_TYPES = {0: "unknown", 1: "rekordbox", 2: "file", 5: "cd"}
STORAGE = {0: "loaded", 2: "stopping", 3: "unmounting", 4: "no_media"}

PLAY_STATES = {
    0x00: "no_track", 0x02: "loading", 0x03: "playing", 0x04: "looping",
    0x05: "paused", 0x06: "cued", 0x07: "cue_play", 0x08: "cue_scratch",
    0x09: "searching", 0x0E: "unplayable", 0x11: "end_of_track",
    0x12: "emergency",
}
PLAYING_STATES = {0x03, 0x04, 0x07}

# CDJ status "settings block 1" (documented CDJ status layout).
# When present it starts at 0xD0 with 12 34 56 78 (then 00 00 00 01 on
# current players). Older packets are shorter or leave these bytes zero.
# Whether the XDJ-AZ includes the block is unverified — a missing or
# unrecognised block yields None and callers fall back by model.
SETTINGS_BLOCK_OFFSET = 0xD0
SETTINGS_BLOCK_MARKER = bytes.fromhex("12345678")
WAVEFORM_COLOR_OFFSET = 0xDA      # 01 blue, 03 RGB, 04 3-band
WAVEFORM_POSITION_OFFSET = 0xDD   # 01 centre, 02 left

_WAVEFORM_COLORS = {0x01: "blue", 0x03: "rgb", 0x04: "3band"}
_WAVEFORM_POSITIONS = {0x01: "centre", 0x02: "left"}

# Scrolling-waveform needle, as a fraction of the strip width.
# Centre is the historical middle. Left is the left quarter: the needle
# stays near the left edge so more of the upcoming track stays visible,
# matching WAVEFORM CURRENT POSITION = Left on the players.
PLAYHEAD_CENTRE_FRACTION = 0.5
PLAYHEAD_LEFT_FRACTION = 0.25

# Factory default is Left on these models. CDJ-3000 defaults to Centre.
# CDJ-2000NXS2 and XDJ-XZ have no such setting and are always centred.
_LEFT_DEFAULT_MODELS = ("xdjaz", "opusquad")


def _name(data: bytes, off: int, length: int = 20) -> str:
    return data[off:off + length].split(b"\0")[0].decode("ascii", "replace")


def _pitch(raw: int) -> float:
    """0x100000 means normal speed; returns the playback rate multiplier."""
    return raw / 0x100000


@dataclass
class Announce:
    """Keep-alive: a device announcing its presence."""
    name: str
    device_number: int
    device_type: int
    mac: str
    ip: str

    @property
    def kind(self) -> str:
        """The type byte reads 3 on every device seen, so classify by number.

        An all-in-one such as the XDJ-AZ announces itself five times: once per
        deck (1-4) plus the mixer section (33).
        """
        n = self.device_number
        if 1 <= n <= 6:
            return "player"
        if n == 17:
            return "rekordbox"
        if 33 <= n <= 47:
            return "mixer"
        return "device"


@dataclass
class Beat:
    """Beat packet, sent exactly on the beat."""
    device_number: int
    name: str
    next_beat: int         # ms until the next beat
    next_bar: int          # ms until the next bar
    pitch: float           # rate multiplier (1.0 = 0 %)
    bpm: float             # track tempo at this point
    beat_in_bar: int       # 1..4

    @property
    def effective_bpm(self) -> float:
        return self.bpm * self.pitch


@dataclass
class AbsolutePosition:
    """Precise playhead from a CDJ-3000-class player (~every 30 ms on port 50001).

    XDJ-AZ support is unconfirmed — counters in the live state show whether any
    arrived. When present, these replace beat-grid interpolation for position.
    """
    device_number: int
    name: str
    track_length_s: int    # whole seconds
    position_ms: int       # absolute playhead
    pitch_percent: float   # tempo fader as shown on the player (±%)
    effective_bpm: float   # on-screen BPM (track × pitch)

    @property
    def speed(self) -> float:
        """Playback rate multiplier implied by the pitch slider."""
        return 1.0 + self.pitch_percent / 100.0


@dataclass
class Status:
    """Full player status (port 50002)."""
    device_number: int = 0
    name: str = ""
    firmware: str = ""

    track_id: int = 0
    track_number: int = 0
    slot: str = ""
    track_type: str = ""
    loaded_from: int = 0          # device number the track was loaded from

    play_state: str = ""
    play_state_raw: int = 0
    bpm: float = 0.0              # track tempo (0 when no track)
    pitch: float = 1.0            # actual playback rate
    physical_pitch: float = 1.0   # tempo fader position
    beat_count: int = 0           # absolute beat within the track
    beat_in_bar: int = 0          # 1..4
    cue_distance: int = 0         # beats to the next cue (0x1ff = none)

    # From settings block 1 when the marker is present; None if absent,
    # the packet is short, or the byte is not a known value.
    waveform_color: str | None = None       # blue | rgb | 3band
    waveform_position: str | None = None    # centre | left

    on_air: bool = False
    sync: bool = False
    master: bool = False
    playing: bool = False

    usb_state: str = ""
    sd_state: str = ""
    raw: bytes = field(default=b"", repr=False)

    @property
    def effective_bpm(self) -> float:
        """Tempo the deck is set to play at: track tempo times the fader."""
        return self.bpm * self.physical_pitch

    @property
    def pitch_percent(self) -> float:
        """Tempo fader position. This is the number a DJ expects to read."""
        return (self.physical_pitch - 1.0) * 100.0

    @property
    def speed(self) -> float:
        """Real advance rate: 0 when stopped. Used to move the playhead."""
        return self.pitch

    @property
    def has_track(self) -> bool:
        return self.track_id != 0


def parse(data: bytes) -> Announce | Beat | AbsolutePosition | Status | None:
    """Decode any Pro DJ Link packet; returns None if it is not one."""
    if len(data) < 0x24 or not data.startswith(MAGIC):
        return None
    packet_type = data[10]
    if packet_type == TYPE_KEEPALIVE:
        return _parse_announce(data)
    if packet_type == TYPE_BEAT:
        return _parse_beat(data)
    if packet_type == TYPE_ABSOLUTE_POSITION:
        return _parse_absolute_position(data)
    if packet_type == TYPE_CDJ_STATUS:
        return _parse_status(data)
    return None


def _parse_announce(data: bytes) -> Announce | None:
    if len(data) < 0x36:
        return None
    return Announce(
        name=_name(data, 0x0C),
        device_number=data[0x24],
        device_type=data[0x21],
        mac=":".join(f"{b:02x}" for b in data[0x26:0x2C]),
        ip=".".join(str(b) for b in data[0x2C:0x30]),
    )


def _parse_beat(data: bytes) -> Beat | None:
    if len(data) < 0x60:
        return None
    next_beat, _second, next_bar = struct.unpack_from(">III", data, 0x24)
    pitch = struct.unpack_from(">I", data, 0x54)[0]
    bpm = struct.unpack_from(">H", data, 0x5A)[0]
    return Beat(
        device_number=data[0x21],
        name=_name(data, 0x0B),
        next_beat=next_beat,
        next_bar=next_bar,
        pitch=_pitch(pitch),
        bpm=bpm / 100.0 if bpm != 0xFFFF else 0.0,
        beat_in_bar=data[0x5C],
    )


def _parse_absolute_position(data: bytes) -> AbsolutePosition | None:
    """CDJ-3000-class Precise Position (type 0x0b), ~30 Hz on port 50001."""
    if len(data) < ABS_POS_MIN_LEN:
        return None
    track_len = struct.unpack_from(">I", data, ABS_POS_TRACK_LEN)[0]
    playhead = struct.unpack_from(">I", data, ABS_POS_PLAYHEAD)[0]
    raw_pitch = struct.unpack_from(">i", data, ABS_POS_PITCH)[0]
    raw_bpm = struct.unpack_from(">I", data, ABS_POS_BPM)[0]
    return AbsolutePosition(
        device_number=data[ABS_POS_DEVICE],
        name=_name(data, 0x0B),
        track_length_s=track_len,
        position_ms=playhead,
        pitch_percent=raw_pitch / 100.0,
        effective_bpm=(raw_bpm / 10.0) if raw_bpm != 0xFFFFFFFF else 0.0,
    )


def _parse_status(data: bytes) -> Status | None:
    if len(data) < 0xA7:
        return None
    s = Status(raw=data)
    s.device_number = data[0x21]
    s.name = _name(data, 0x0B)
    s.firmware = _name(data, 0x7C, 4)

    s.loaded_from = data[0x28]
    s.slot = SLOTS.get(data[0x29], "unknown")
    s.track_type = TRACK_TYPES.get(data[0x2A], "unknown")
    s.track_id = struct.unpack_from(">I", data, 0x2C)[0]
    s.track_number = struct.unpack_from(">I", data, 0x30)[0]

    s.usb_state = STORAGE.get(struct.unpack_from(">I", data, 0x6C)[0], "unknown")
    s.sd_state = STORAGE.get(struct.unpack_from(">I", data, 0x70)[0], "unknown")

    s.play_state_raw = struct.unpack_from(">I", data, 0x78)[0]
    s.play_state = PLAY_STATES.get(s.play_state_raw, "unknown")

    flags = struct.unpack_from(">H", data, 0x88)[0]
    s.on_air = bool(flags & 0x08)
    s.sync = bool(flags & 0x10)
    s.master = bool(flags & 0x20)
    s.playing = bool(flags & 0x40)

    s.physical_pitch = _pitch(struct.unpack_from(">I", data, 0x8C)[0])
    bpm = struct.unpack_from(">H", data, 0x92)[0]
    s.bpm = bpm / 100.0 if bpm != 0xFFFF else 0.0
    s.pitch = _pitch(struct.unpack_from(">I", data, 0x98)[0])

    beat_count = struct.unpack_from(">I", data, 0xA0)[0]
    s.beat_count = 0 if beat_count == 0xFFFFFFFF else beat_count   # -1 = no track
    s.cue_distance = struct.unpack_from(">H", data, 0xA4)[0]
    s.beat_in_bar = data[0xA6]
    s.waveform_color, s.waveform_position = _parse_waveform_settings(data)
    return s


def _parse_waveform_settings(data: bytes) -> tuple[str | None, str | None]:
    """Waveform colour and current position from settings block 1.

    Requires the 12 34 56 78 marker at 0xD0 and enough bytes to read each
    field. Unknown colour or position values are None (not a guess).
    """
    off = SETTINGS_BLOCK_OFFSET
    marker = SETTINGS_BLOCK_MARKER
    if len(data) < off + len(marker):
        return None, None
    if data[off:off + len(marker)] != marker:
        return None, None
    color = None
    position = None
    if len(data) > WAVEFORM_COLOR_OFFSET:
        color = _WAVEFORM_COLORS.get(data[WAVEFORM_COLOR_OFFSET])
    if len(data) > WAVEFORM_POSITION_OFFSET:
        position = _WAVEFORM_POSITIONS.get(data[WAVEFORM_POSITION_OFFSET])
    return color, position


def normalize_playhead_mode(value: str | None) -> str:
    """User override: auto (default), centre, or left. Unknown -> auto."""
    mode = str(value or "auto").strip().lower()
    if mode == "center":
        mode = "centre"
    return mode if mode in ("auto", "centre", "left") else "auto"


def _normalize_reported_position(value: str | None) -> str | None:
    if value is None:
        return None
    pos = str(value).strip().lower()
    if pos == "center":
        pos = "centre"
    return pos if pos in ("centre", "left") else None


def _model_defaults_left(player_name: str) -> bool:
    compact = "".join(ch for ch in (player_name or "").lower() if ch.isalnum())
    return any(token in compact for token in _LEFT_DEFAULT_MODELS)


def resolve_playhead_position(mode: str | None, reported: str | None,
                              player_name: str = "") -> str:
    """Centre or left for one deck.

    A manual Centre/Left override wins. Auto uses the player's reported
    Waveform Current Position, then a model fallback (Left for XDJ-AZ and
    Opus Quad, Centre otherwise) when the packet does not say.
    """
    chosen = normalize_playhead_mode(mode)
    if chosen in ("centre", "left"):
        return chosen
    reported_pos = _normalize_reported_position(reported)
    if reported_pos:
        return reported_pos
    if _model_defaults_left(player_name):
        return "left"
    return "centre"


def playhead_fraction(position: str | None) -> float:
    """Horizontal anchor for the scrolling-waveform needle (0 = left edge)."""
    if _normalize_reported_position(position) == "left":
        return PLAYHEAD_LEFT_FRACTION
    return PLAYHEAD_CENTRE_FRACTION


def build_keepalive(name: str, device_number: int, mac: bytes, ip: str,
                    device_count: int = 1) -> bytes:
    """Build the keep-alive that announces us as a virtual device.

    Sending it is mandatory: players send their detailed status by unicast, and
    only to addresses that have announced themselves on the network.

    The template is copied byte for byte from the keep-alive rekordbox emits,
    captured on the same network: a client the player accepts and does send
    status to. Only the name, device number, MAC and IP differ.
    """
    pkt = bytearray(0x36)
    pkt[0:10] = MAGIC
    pkt[10] = TYPE_KEEPALIVE
    pkt[11] = 0x00
    encoded = name.encode("ascii", "replace")[:19]
    pkt[0x0C:0x0C + len(encoded)] = encoded
    pkt[0x20] = 0x01
    pkt[0x21] = 0x03          # every device seen so far uses 3 here
    struct.pack_into(">H", pkt, 0x22, 0x36)
    pkt[0x24] = device_number
    pkt[0x25] = 0x01
    pkt[0x26:0x2C] = mac
    pkt[0x2C:0x30] = socket.inet_aton(ip)
    pkt[0x30] = device_count & 0xFF   # devices we know about
    pkt[0x31] = 0x01
    pkt[0x32] = 0x00
    pkt[0x33] = 0x00
    pkt[0x34] = 0x04          # capability flags, same as rekordbox
    pkt[0x35] = 0x08
    return bytes(pkt)
