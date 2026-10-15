"""Pro DJ Link engine: discovers devices and tracks the live state of each deck.

Two ways of receiving packets:

  SocketSource   Announces us as one more device on the network. The player then
                 starts sending us its status by unicast. It needs UDP ports
                 50000-50002, so rekordbox must be closed (it claims them
                 exclusively).

  SnifferSource  Passive capture through Npcap/tshark. It opens no port and
                 announces nothing, so it coexists with rekordbox: it reads the
                 very packets the player is sending to it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import proto
from .capture import PacketCapture
from .link_timing import LinkTiming
from .playhead_smooth import (
    NOMINAL_INTERVAL_S,
    correction_ms,
    freeze_instant,
    normalize_mode,
)

Handler = Callable[[bytes, str, float], None]


# --------------------------------------------------------------- local network

@dataclass
class NetInfo:
    ip: str
    prefix: int
    alias: str

    @property
    def broadcast(self) -> str:
        net = ipaddress.ip_network(f"{self.ip}/{self.prefix}", strict=False)
        return str(net.broadcast_address)


DISCOVERY_WINDOW = 3.0


def bind_address(interface_ip: str | None, platform: str | None = None) -> str:
    """Address the Pro DJ Link listening sockets bind to.

    Always the wildcard. On macOS a socket bound to the interface address does
    not receive broadcasts, so beat packets (0x28) vanish without an error
    (noted by SuperTimecodeConverter, MIT). Windows and Linux have always used
    the wildcard here and that is what the XDJ-AZ was verified with, so the
    interface is chosen for sending, not for binding.
    """
    return "0.0.0.0"


def keepalive_targets(broadcast: str, unicast_ips=()) -> list[str]:
    """Where keep-alives go: each address once (an all-in-one has 3 devices
    on one IP but must get one copy). Idea from SuperTimecodeConverter (MIT)."""
    seen: list[str] = []
    for ip in (broadcast, "255.255.255.255", *unicast_ips):
        if ip and ip not in seen:
            seen.append(ip)
    return seen


def local_net(target: str) -> NetInfo:
    """Work out which local interface reaches the player, with mask and name."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))
        ip = s.getsockname()[0]
    finally:
        s.close()

    prefix, alias = 24, ""
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-NetIPAddress -IPAddress {ip} -AddressFamily IPv4 | "
                 "Select-Object -First 1 -Property PrefixLength,InterfaceAlias | "
                 "ForEach-Object { \"$($_.PrefixLength)|$($_.InterfaceAlias)\" }"],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
            if "|" in out:
                p, alias = out.split("|", 1)
                prefix = int(p)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.run(["/sbin/ifconfig"], capture_output=True,
                                 text=True, timeout=10).stdout
            found = parse_ifconfig(out, ip)
            if found:
                prefix, alias = found
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    return NetInfo(ip, prefix, alias.strip())


def parse_ifconfig(text: str, ip: str) -> tuple[int, str] | None:
    """Prefix length and interface name for ``ip`` in BSD/macOS ifconfig output.

    macOS prints ``inet 192.168.1.20 netmask 0xffffff00 broadcast ...`` under
    a ``en0: flags=...`` header line.
    """
    iface = ""
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            iface = line.split(":", 1)[0].strip()
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "inet" and parts[1] == ip and parts[2] == "netmask":
            mask = parts[3]
            if mask.startswith("0x"):
                bits = bin(int(mask, 16)).count("1")
            else:
                bits = ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen
            return bits, iface
    return None


def local_mac() -> bytes:
    node = uuid.getnode()
    return node.to_bytes(6, "big")


def discover_player(timeout: float = 4.0) -> str | None:
    """Listen for keep-alive broadcasts and return the first player's address.

    Worth doing before anything else when no host was given: picking an
    interface by routing towards the broadcast address lands on a virtual
    adapter on machines with VMware or Hyper-V installed. Knowing the player's
    real address first makes the choice unambiguous.

    Returns None when the port is busy (rekordbox running) or nothing answers.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        s.bind(("0.0.0.0", proto.PORT_ANNOUNCE))
    except OSError:
        s.close()
        return None
    s.settimeout(0.5)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                data, _addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            pkt = proto.parse(data)
            if isinstance(pkt, proto.Announce) and pkt.kind == "player":
                return pkt.ip
    finally:
        s.close()
    return None


def discover_by_capture(tshark: str | None = None,
                        seconds: int = 5) -> tuple[str | None, str | None]:
    """Find the player *and* the right capture interface by listening on all of them.

    This is the fallback for when rekordbox holds port 50000 and we cannot listen
    with a socket. Guessing the interface by routing towards the broadcast address
    lands on a VMware or Hyper-V adapter on many machines, and then we capture
    where nothing is happening. Listening everywhere at once and seeing which
    interface actually carries Pro DJ Link removes the guesswork.

    Returns (player address, capture interface), either of which may be None.
    """
    exe = tshark or SnifferSource._find_tshark()
    interfaces: list[str] = []
    try:
        listing = subprocess.run([exe, "-D"], capture_output=True, text=True,
                                 timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return None, None
    for line in listing.splitlines():
        m = re.match(r"\s*(\d+)\.\s+(\S+)", line)
        if not m:
            continue
        name = m.group(2)
        if "Loopback" in name or "etwdump" in name:
            continue                      # never carries player traffic
        interfaces.append(name)
    if not interfaces:
        return None, None

    fields = ["-T", "fields", "-e", "frame.interface_name", "-e", "udp.payload"]
    attempts = [interfaces]                     # all at once...
    attempts += [[i] for i in interfaces]       # ...and one by one if that fails
    for group in attempts:
        cmd = [exe]
        for i in group:
            cmd += ["-i", i]
        cmd += ["-a", f"duration:{seconds}", "-n", "-Q",
                "-f", f"udp port {proto.PORT_ANNOUNCE}"] + fields
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=seconds + 25).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        for line in out.splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[1]:
                continue
            try:
                data = bytes.fromhex(parts[1].replace(":", ""))
            except ValueError:
                continue
            pkt = proto.parse(data)
            if isinstance(pkt, proto.Announce) and pkt.kind == "player":
                return pkt.ip, (parts[0] or None)
    return None, None


# --------------------------------------------------------------- packet sources

class SocketSource:
    """Virtual device: opens the ports and announces itself on the network.

    It listens for ``listen_first`` seconds before the first keep-alive and
    never announces a number another device already uses. An XDJ-XZ on its
    laptop port says "go ahead" to any number, even its own decks 1 and 2,
    and never defends them, so the check has to happen on our side.
    ``device_number`` 0 means automatic.
    """

    announce = True

    def __init__(self, net: NetInfo, device_number: int = 5,
                 device_name: str = "monitor", announce_interval: float = 1.5,
                 listen_first: float | None = None):
        self.net = net
        self.requested_number = int(device_number or 0)
        self.device_number = self.requested_number or 5
        self.device_name = device_name
        self.announce_interval = announce_interval
        # A full discovery window: CDJs send a keep-alive every ~2.0 s
        # (dysentery #75) and mixers every 1.5 s, so 3 s hears everyone once.
        self.listen_first = max(0.0, float(
            DISCOVERY_WINDOW if listen_first is None else listen_first))
        self._announcing = False
        self._conflict = threading.Event()
        self.number_note = ""
        self.taken: set[int] = set()
        self._socks: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._running = False
        self._stop = threading.Event()
        self.kind = "vcdj"
        self._describe()

    def _describe(self) -> None:
        self.detail = f"#{self.device_number} \"{self.device_name}\""
        self.description = f"virtual device {self.detail}"

    def _note_announce(self, data: bytes, src_ip: str) -> None:
        """Remember the numbers other devices announce (not our own packets).

        A final claim (stage 0x04) also reserves its number, so we never pick
        one a device is about to take. Stage 0x00/0x02 are ignored.
        """
        if src_ip == self.net.ip:
            return              # our own broadcast coming back
        claim = proto.claim_stage(data)
        if claim is not None:
            stage, number = claim
            if stage == 0x04 and number:
                self.taken.add(int(number))
                self._check_conflict(int(number))
            return
        if len(data) > 0x24 and data.startswith(proto.MAGIC) \
                and data[0x0A] == proto.TYPE_NUMBER_IN_USE:
            # Another device defends a number (beat-link #91 claim race).
            number = int(data[0x24])
            if number:
                self.taken.add(number)
                self._check_conflict(number)
            return
        if len(data) < 0x25 or data[0x0A] != proto.TYPE_KEEPALIVE:
            return
        number = int(data[0x24])
        self.taken.add(number)
        self._check_conflict(number)

    def _check_conflict(self, number: int) -> None:
        if self._announcing and number == self.device_number:
            self._conflict.set()

    def start(self, on_packet: Handler) -> None:
        user_handler = on_packet

        def on_packet(data: bytes, src: str, now: float) -> None:  # noqa: F811
            try:
                self._note_announce(data, src)
            except Exception:
                pass
            user_handler(data, src, now)

        self._running = True
        for port in (proto.PORT_ANNOUNCE, proto.PORT_BEAT, proto.PORT_STATUS):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            try:
                s.bind((bind_address(self.net.ip), port))
            except OSError as e:
                for done in self._socks:
                    done.close()
                self._socks.clear()
                raise RuntimeError(
                    f"could not open UDP port {port}: {e}\n"
                    "This is almost always rekordbox, which claims 50000-50002 "
                    "exclusively. Close it, or start in passive mode with "
                    "--mode sniffer."
                ) from e
            s.settimeout(0.5)
            self._socks.append(s)
            t = threading.Thread(
                target=self._recv_loop, args=(s, on_packet),
                daemon=True, name="prolink-link",
            )
            t.start()
            self._threads.append(t)

        if not self.announce:
            return
        t = threading.Thread(target=self._announce_loop, daemon=True, name="prolink-announce")
        t.start()
        self._threads.append(t)

    def _pick_number(self) -> None:
        from .allinone import choose_device_number

        number, note = choose_device_number(self.requested_number, self.taken)
        self.device_number = number
        self.number_note = note
        self._describe()

    def _recv_loop(self, sock: socket.socket, on_packet: Handler) -> None:
        while self._running:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            on_packet(data, addr[0], time.monotonic())

    def _announce_loop(self) -> None:
        # Listen first, then claim a number nobody announced.
        if self.listen_first and self._stop.wait(self.listen_first):
            return
        try:
            self._pick_number()
        except RuntimeError:
            self.number_note = "no free device number; not announcing"
            return
        pkt = proto.build_keepalive(self.device_name, self.device_number,
                                    local_mac(), self.net.ip)
        self._announcing = True
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        targets = keepalive_targets(self.net.broadcast)
        while self._running:
            if self._conflict.is_set():
                # Someone claimed or defended our number: stop using it.
                self._conflict.clear()
                self.taken.add(self.device_number)
                old = self.device_number
                try:
                    self._pick_number()
                except RuntimeError:
                    self.number_note = "no free device number; stopped announcing"
                    break
                self.number_note += f" (moved from #{old}: another device claimed it)"
                pkt = proto.build_keepalive(self.device_name, self.device_number,
                                            local_mac(), self.net.ip)
            for dst in targets:
                try:
                    out.sendto(pkt, (dst, proto.PORT_ANNOUNCE))
                except OSError:
                    pass
            if self._stop.wait(self.announce_interval):
                break
        out.close()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        self._socks.clear()
        for thread in list(self._threads):
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(0.8)
        self._threads.clear()


class ObserveSource(SocketSource):
    """Observe only: opens UDP 50000-50002 and never sends a packet.

    Meant for the network adapter an XDJ-XZ (or Opus Quad) exposes over its
    USB-B cable, which carries the unit's own Pro DJ Link traffic. Whether
    per-player status packets reach a silent listener there is what the Health
    line and the XZ probe report show.
    """

    announce = False

    def __init__(self, net: NetInfo, device_name: str = "monitor"):
        super().__init__(net, 0, device_name, listen_first=0.0)
        self.kind = "observe"
        self.device_number = 0
        self.detail = f"{net.alias or net.ip}"
        self.description = f"observe only on \"{self.detail}\" (not announcing)"

    def _describe(self) -> None:
        pass


class SnifferSource:
    """Passive capture through tshark (Npcap). Does not interfere with rekordbox."""

    TSHARK_PATHS = [
        r"C:\Program Files\Wireshark\tshark.exe",
        r"C:\Program Files (x86)\Wireshark\tshark.exe",
        "/usr/bin/tshark",
        "/usr/local/bin/tshark",
        "tshark",
    ]

    def __init__(self, net: NetInfo, tshark: str | None = None, iface: str | None = None):
        self.net = net
        self.tshark = tshark or self._find_tshark()
        self.iface = iface
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.kind = "sniffer"
        self.detail = iface or net.alias or net.ip
        self.description = f"passive capture on \"{self.detail}\" via Npcap"

    @classmethod
    def _find_tshark(cls) -> str:
        import os
        import shutil
        for p in cls.TSHARK_PATHS:
            if p == "tshark":
                found = shutil.which(p)
                if found:
                    return found
            elif os.path.exists(p):
                return p
        raise RuntimeError(
            "tshark not found. Passive mode needs Wireshark/Npcap installed "
            "(https://www.wireshark.org). Otherwise close rekordbox and use the "
            "virtual device mode."
        )

    def _interface(self) -> str:
        """Find the capture index matching our interface."""
        if self.iface:
            return self.iface
        out = subprocess.run([self.tshark, "-D"], capture_output=True, text=True,
                             timeout=30).stdout
        alias = self.net.alias
        for line in out.splitlines():
            m = re.match(r"\s*(\d+)\.\s+(\S+)\s*(?:\((.*)\))?", line)
            if not m:
                continue
            number, _device, friendly = m.groups()
            if alias and friendly and friendly.strip().lower() == alias.lower():
                return number
            # macOS and Linux list the BSD name (en15) as the device itself.
            if alias and _device.strip().lower() == alias.lower():
                return number
        raise RuntimeError(
            f"could not find interface \"{alias}\" among the ones tshark lists. "
            f"Pass it by hand with --iface. Available:\n{out}"
        )

    def start(self, on_packet: Handler) -> None:
        iface = self._interface()
        # frame.time_epoch is essential: tshark delivers its output in batches, so
        # the moment we read a line says nothing about when the packet arrived.
        # Without it the playhead jumps on every beat.
        cmd = [self.tshark, "-i", iface, "-l", "-n", "-Q",
               "-f", "udp portrange 50000-50002",
               "-T", "fields", "-e", "frame.time_epoch", "-e", "ip.src",
               "-e", "udp.payload"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      text=True, bufsize=1)
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop, args=(on_packet,), daemon=True, name="prolink-sniffer",
        )
        self._thread.start()

    def _read_loop(self, on_packet: Handler) -> None:
        assert self._proc and self._proc.stdout
        offset: float | None = None       # wall clock to monotonic
        for line in self._proc.stdout:
            if not self._running:
                return
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[2]:
                continue
            try:
                data = bytes.fromhex(parts[2].replace(":", ""))
                epoch = float(parts[0])
            except ValueError:
                continue
            if offset is None:
                offset = time.monotonic() - epoch
            on_packet(data, parts[1], epoch + offset)

    def stop(self) -> None:
        self._running = False
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(0.8)


# ----------------------------------------------------------------------- state

# Correction applied on every beat. The observed instant arrives with a variable
# delay (status only comes ~6 times a second), so correcting it all at once makes
# the playhead jump back and forth. Correcting a fraction of the error absorbs
# that noise without losing track.
SMOOTHING = 0.2
# Above this error it is not noise: it is a real jump (cue, scratch, seek).
JUMP_MS = 400.0
# A loop wrap is a real jump even when it is shorter than JUMP_MS. Smaller
# than this is packet jitter, not the playhead coming back to the loop start.
LOOP_WRAP_MIN_MS = 20.0


def wrap_loop_position(pos_ms: float, looping: bool,
                       start_ms: float | None, end_ms: float | None) -> float:
    """Keep a playhead inside the active loop.

    Extrapolation between packets runs past the loop end. Folding it back
    is what stops the highlight walking out of the loop. Outside a loop, or
    without bounds, the position is unchanged.
    """
    if not looping:
        return pos_ms
    try:
        start = float(start_ms)  # type: ignore[arg-type]
        end = float(end_ms)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return pos_ms
    length = end - start
    if length <= 0:
        return pos_ms
    if pos_ms >= end:
        return start + (pos_ms - start) % length
    # Just before the window while the loop is active (a sample that wrapped
    # the other way, or jitter). A seek well before the loop is left alone.
    if start - length < pos_ms < start:
        return start
    return pos_ms

# Tracks longer than this (as seconds) are not plausible DJ tracks; Absolute
# Position "seconds" fields that big are almost certainly already milliseconds
# (seen on some XDJ-AZ firmwares) and must not be multiplied by 1000 again.
_ABS_LENGTH_SECONDS_CAP = 12 * 3600


def _abs_track_length_ms(raw: int) -> int:
    """Normalize Absolute Position track-length field to milliseconds."""
    if raw <= 0:
        return 0
    if raw > _ABS_LENGTH_SECONDS_CAP:
        return int(raw)
    return int(raw) * 1000


def length_exceeds_known(length_ms: int, known_ms: int) -> bool:
    """True when a packet length sits well above a length we already trust.

    Some XDJ-AZ Absolute Position packets put a track length in this field
    that is not the loaded track (655861 ms against a real 8:21). Once
    dbserver metadata or the beat grid has a length, a later value more
    than 5% and 2 seconds above it is ignored.
    """
    known = int(known_ms or 0)
    length = int(length_ms or 0)
    if known <= 0 or length <= 0:
        return False
    return length > known + int(known * 0.05) + 2000


@dataclass
class Deck:
    """Live state of one deck, with the playhead interpolated between beats.

    Position is kept as a continuous model (base position, base time, speed)
    that is only nudged, instead of being re-anchored on every beat. When the
    player sends Absolute Position packets (CDJ-3000 class), those become the
    source of truth and beat-grid estimation is skipped.
    """
    number: int
    status: proto.Status | None = None
    last_seen: float = 0.0
    last_beat_packet: float = 0.0
    beat_in_bar: int = 0
    beat_packets: int = 0
    absolute_packets: int = 0
    position_source: str = "none"   # none | beat_grid | exact

    # (position in ms, monotonic instant, speed) -- replaced as a tuple so the
    # web server threads always read a consistent model
    _model: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _last_beat: int = -1
    _beat_times: list[int] = field(default_factory=list, repr=False)
    track_length_ms: int = 0
    # Length from dbserver metadata or the ANLZ beat grid. Absolute Position
    # must not replace this with a longer, unrelated packet value.
    metadata_duration_ms: int = 0
    _exact: bool = False
    # Active loop, learned from a backward wrap or from the status packet.
    # Cleared as soon as the player is not in play state "looping".
    _loop_start_ms: float | None = None
    _loop_end_ms: float | None = None
    _loop_from_packet: bool = False
    # direct | normal | strong. Snaps (seek, pause, reverse, loop wrap) ignore it.
    smoothing: str = "normal"

    def set_beat_grid(self, times: list[int], length_ms: int = 0) -> None:
        self._beat_times = times
        length = length_ms or (times[-1] if times else 0)
        self.track_length_ms = length
        if length > 0:
            self.metadata_duration_ms = length
        self._last_beat = -1          # force a re-fix now the grid is loaded

    def note_metadata_duration(self, duration_ms: int) -> None:
        """Trust a library/dbserver length over an inflated Absolute Position."""
        ms = int(duration_ms or 0)
        if ms <= 0:
            return
        self.metadata_duration_ms = ms
        if self.track_length_ms <= 0 or length_exceeds_known(self.track_length_ms, ms):
            self.track_length_ms = ms

    def _beat_time(self, beat: int) -> int:
        """Millisecond a beat starts at, according to the analysed grid."""
        if beat < 1:
            return 0            # the deck has not crossed the first beat yet
        if self._beat_times:
            if beat <= len(self._beat_times):
                return self._beat_times[beat - 1]
            return self._beat_times[-1]
        s = self.status
        if s and s.bpm:                      # no grid: estimate from the tempo
            return int((beat - 1) * 60000.0 / s.bpm)
        return 0

    def _project(self, now: float) -> float:
        """Position of the continuous model at a given instant."""
        pos, t, speed = self._model
        return pos + (now - t) * 1000.0 * speed

    def on_status(self, s: proto.Status, now: float) -> None:
        prev = self.status
        self.status = s
        prev_seen = self.last_seen
        prev_speed = self._model[2]
        self.last_seen = now
        if s.beat_in_bar:
            self.beat_in_bar = s.beat_in_bar

        new_track = prev is None or prev.track_id != s.track_id
        if new_track:
            self._beat_times = []
            self.track_length_ms = 0
            self.metadata_duration_ms = 0
            self._last_beat = -1
            self._exact = False
            self.position_source = "none"
            self._model = (0.0, now, 0.0)
            self._clear_loop_window()

        # advance the model up to now at the speed it carried, then continue at
        # the new one
        self._model = (self._project(now), now, s.speed if s.has_track else 0.0)
        self._sync_loop_window(s)

        # Exact Absolute Position packets already own the playhead. A loop
        # wrap can still show up first in the beat grid (the absolute clock
        # keeps counting on some players), so notice that jump too.
        if self._exact:
            if s.play_state == "looping":
                self._observe_beat_wrap(s, now)
            self._fold_model()
            return

        if s.beat_count == self._last_beat:
            self._fold_model()
            return
        self._last_beat = s.beat_count

        # when the beat happened: the beat packet marks it precisely, while the
        # status arrives with up to ~150 ms of variable delay
        delay = now - self.last_beat_packet
        beat_time = self.last_beat_packet if delay < 0.25 else now
        target = self._beat_time(s.beat_count) + (now - beat_time) * 1000.0 * s.speed

        pos, _, speed = self._model
        error = target - pos
        # A loop wrap is a jump back to the loop start. Smoothing that (the
        # usual treatment for an error under JUMP_MS) walks the playhead out
        # of the loop a little further on every cycle.
        # A beat time of 0 with no grid is "unknown", not the loop start.
        beat_known = bool(self._beat_times or s.bpm)
        reversed_play = bool(prev_speed and speed and (prev_speed > 0) != (speed > 0))
        if s.play_state == "looping" and error < -LOOP_WRAP_MIN_MS and beat_known:
            self._learn_loop_from_wrap(pos, target)
            self._model = (target, now, speed)
        elif new_track or not s.speed or abs(error) > JUMP_MS or reversed_play:
            self._model = (target, now, speed)          # real jump: re-place it
        else:
            interval = (now - prev_seen) if prev_seen > 0 and now > prev_seen else NOMINAL_INTERVAL_S
            step = correction_ms(error, speed, self.smoothing, interval)
            self._model = (pos + step, now, speed)
        if self.position_source == "none":
            self.position_source = "beat_grid"
        self._fold_model()

    def on_beat(self, b: proto.Beat, now: float) -> None:
        self.last_beat_packet = now
        self.beat_packets += 1
        self.beat_in_bar = b.beat_in_bar

    def on_absolute_position(self, ap: proto.AbsolutePosition, now: float) -> None:
        """CDJ-3000-class Precise Position — trust playhead over beat-grid guess."""
        self.absolute_packets += 1
        self._exact = True
        self.position_source = "exact"
        self.last_seen = now
        if ap.track_length_s:
            length_ms = _abs_track_length_ms(ap.track_length_s)
            known = self.metadata_duration_ms
            if known and length_exceeds_known(length_ms, known):
                # Packet length is not the loaded track. Keep the metadata length.
                if (self.track_length_ms <= 0
                        or length_exceeds_known(self.track_length_ms, known)):
                    self.track_length_ms = known
            elif self.track_length_ms <= 0:
                self.track_length_ms = length_ms
            elif length_ms <= max(self.track_length_ms * 2, self.track_length_ms + 2000):
                # Agree with beat-grid / prior length — take the longer within reason.
                # Ignore absurd AP values that would inflate remain time (seen when
                # some firmwares put milliseconds in the "seconds" field).
                self.track_length_ms = max(self.track_length_ms, length_ms)
        s = self.status
        # Prefer status for whether we are actually advancing; use the packet's
        # pitch slider when status says we are moving.
        if s and s.has_track and s.speed:
            speed = ap.speed
        else:
            speed = 0.0
        before = self._project(now)
        pos = float(ap.position_ms)
        if s and s.play_state == "looping" and pos + LOOP_WRAP_MIN_MS < before:
            self._learn_loop_from_wrap(before, pos)
        self._model = (pos, now, speed)
        self._fold_model()

    def loop_window(self) -> tuple[float, float] | None:
        """Active loop bounds, or None when the player is not looping."""
        s = self.status
        if s is None or s.play_state != "looping":
            return None
        start, end = self._loop_start_ms, self._loop_end_ms
        if start is None or end is None or end <= start:
            return None
        return float(start), float(end)

    def loop_payload(self) -> dict:
        window = self.loop_window()
        if window is None:
            return {"loop_start_ms": None, "loop_end_ms": None}
        return {
            "loop_start_ms": round(window[0], 1),
            "loop_end_ms": round(window[1], 1),
        }

    def _clear_loop_window(self) -> None:
        self._loop_start_ms = None
        self._loop_end_ms = None
        self._loop_from_packet = False

    def _sync_loop_window(self, s: proto.Status) -> None:
        """Take loop in/out from the status packet, or forget it when not looping."""
        if s.play_state != "looping":
            self._clear_loop_window()
            return
        start = int(getattr(s, "loop_start_ms", 0) or 0)
        end = int(getattr(s, "loop_end_ms", 0) or 0)
        if end <= start:
            return
        pos = self._model[0]
        length = end - start
        # Ignore a decoded window the playhead is nowhere near. A short packet
        # or a different layout must not yank the highlight into the wrong loop.
        # A little past the end is normal: extrapolation runs on until this packet.
        if pos < start - length or pos > end + max(length * 4, 2000):
            return
        self._loop_start_ms = float(start)
        self._loop_end_ms = float(end)
        self._loop_from_packet = True

    def _learn_loop_from_wrap(self, before: float, after: float) -> None:
        """Remember the loop from a playhead that jumped backward while looping."""
        if self._loop_from_packet:
            return
        if after >= before - LOOP_WRAP_MIN_MS:
            return
        length = before - after
        if length < LOOP_WRAP_MIN_MS:
            return
        if self.track_length_ms and length > self.track_length_ms:
            return
        self._loop_start_ms = float(after)
        self._loop_end_ms = float(before)

    def _observe_beat_wrap(self, s: proto.Status, now: float) -> None:
        """While Absolute Position owns the playhead, still notice a loop wrap.

        On some players the absolute clock keeps counting through a loop and
        the beat number is what jumps back to the loop start. That jump is the
        position the highlight should follow.
        """
        if s.beat_count == self._last_beat or not s.speed:
            return
        self._last_beat = s.beat_count
        # No grid and no tempo: beat 0 is "unknown", not a wrap back to the start.
        if not self._beat_times and not s.bpm:
            return
        delay = now - self.last_beat_packet
        beat_time = self.last_beat_packet if 0 <= delay < 0.25 else now
        target = self._beat_time(s.beat_count) + (now - beat_time) * 1000.0 * s.speed
        pos, t, speed = self._model
        if target >= pos - LOOP_WRAP_MIN_MS:
            return
        self._learn_loop_from_wrap(pos, target)
        window = self.loop_window()
        if window and not (window[0] <= pos < window[1]):
            folded = wrap_loop_position(pos, True, window[0], window[1])
            self._model = (folded, t, speed)

    def _fold_model(self) -> None:
        """Pull an anchor that ran past the loop end back inside it."""
        window = self.loop_window()
        if window is None:
            return
        pos, t, speed = self._model
        folded = wrap_loop_position(pos, True, window[0], window[1])
        if folded != pos:
            self._model = (folded, t, speed)

    def _bounded(self, pos: float) -> float:
        window = self.loop_window()
        if window is not None:
            pos = wrap_loop_position(pos, True, window[0], window[1])
        elif self.track_length_ms:
            pos = min(pos, self.track_length_ms)
        return max(0.0, pos)

    @property
    def position_ms(self) -> float:
        """Estimated playhead position, in milliseconds.

        Between packets the model keeps moving. If packets stop, that coast
        lasts at most a second and then the playhead holds.
        """
        s = self.status
        when = freeze_instant(time.monotonic(), self.last_seen)
        if not s or not s.has_track:
            # Absolute packets can arrive before status on some firmwares.
            if self._exact:
                return self._bounded(self._project(when))
            return 0.0
        return self._bounded(self._project(when))

    @property
    def is_playing(self) -> bool:
        s = self.status
        return bool(s and s.play_state_raw in proto.PLAYING_STATES)

class ProLink:
    """Discovers the Pro DJ Link network and keeps every deck's state."""

    def __init__(self, source):
        self.source = source
        self.devices: dict[int, proto.Announce] = {}
        self.decks: dict[int, Deck] = {}
        self.lock = threading.RLock()
        self.on_track_change: Callable[[Deck, int], None] | None = None
        self._last_track: dict[int, int] = {}
        self.packets = 0
        self.started_at = 0.0
        # Health diagnostics. Both only look at packets already received.
        self.link_timing = LinkTiming()
        self.capture = PacketCapture()
        from .allinone import Census

        self.census = Census()
        self.playhead_smoothing = "normal"
        self._saw_az = False
        self._az_first_seen: float | None = None
        self._az_status_at: float | None = None
        self._az_on_air_at: float | None = None

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.source.start(self._on_packet)

    def stop(self) -> None:
        self.source.stop()

    def _deck(self, number: int) -> Deck:
        d = self.decks.get(number)
        if d is None:
            d = Deck(number)
            d.smoothing = normalize_mode(getattr(self, "playhead_smoothing", "normal"))
            self.decks[number] = d
        return d

    def _on_packet(self, data: bytes, src_ip: str, now: float) -> None:
        """`now` is when the packet arrived, not when we got round to it."""
        # Capture is listen-only: it records this datagram and sends nothing.
        self.capture.observe(data, src_ip, now)
        pkt = proto.parse(data)
        try:
            self.census.observe(data, src_ip, pkt, now)
        except Exception:
            pass
        if pkt is None:
            return
        if proto.is_ignored_device(getattr(pkt, "name", "")):
            return      # ShowKontrol (TCS) shares the ports but is not DJ gear
        with self.lock:
            self.packets += 1
            if isinstance(pkt, proto.Announce):
                self.devices[pkt.device_number] = pkt
                self._note_player_name(pkt.name)
            elif isinstance(pkt, proto.Beat):
                self._deck(pkt.device_number).on_beat(pkt, now)
            elif isinstance(pkt, proto.AbsolutePosition):
                self._deck(pkt.device_number).on_absolute_position(pkt, now)
            elif isinstance(pkt, proto.ChannelsOnAir):
                self._note_channels_on_air(pkt, now)
            elif isinstance(pkt, proto.Status):
                self._note_player_name(pkt.name, now)
                if proto.is_xdj_az(pkt.name) and 1 <= pkt.device_number <= 4:
                    self._az_status_at = now
                deck = self._deck(pkt.device_number)
                deck.on_status(pkt, now)
                self.link_timing.observe(
                    pkt.device_number, now, data, ip=src_ip, name=pkt.name)
                before = self._last_track.get(pkt.device_number)
                if before != pkt.track_id:
                    self._last_track[pkt.device_number] = pkt.track_id
                    if self.on_track_change:
                        threading.Thread(
                            target=self.on_track_change,
                            args=(deck, pkt.track_id),
                            daemon=True,
                            name="prolink-track",
                        ).start()

    def _note_player_name(self, name: str, now: float | None = None) -> None:
        if proto.is_xdj_az(name):
            self._saw_az = True
            if self._az_first_seen is None:
                self._az_first_seen = time.monotonic() if now is None else now

    def _note_channels_on_air(self, pkt: proto.ChannelsOnAir, now: float) -> None:
        """An XDJ-AZ only emits this while it is in Pro DJ Link mode."""
        if not proto.is_xdj_az(pkt.name):
            return
        self._saw_az = True
        if self._az_first_seen is None:
            self._az_first_seen = now
        self._az_on_air_at = now

    def az_mode(self, now: float | None = None) -> str | None:
        """``pro_dj_link``, ``four_deck``, ``unknown``, or None (no XDJ-AZ)."""
        when = time.monotonic() if now is None else now
        with self.lock:
            return proto.az_link_mode(self._saw_az, self._az_on_air_at, when,
                                      self._az_status_at, self._az_first_seen)

    def active_decks(self, timeout: float = 5.0) -> list[Deck]:
        """Decks we have heard from recently, ordered by number."""
        now = time.monotonic()
        with self.lock:
            return sorted((d for d in self.decks.values()
                           if d.status and now - d.last_seen < timeout),
                          key=lambda d: d.number)

    def players(self, exclude_ip: str | None = None) -> list[proto.Announce]:
        """Announced devices that are actual players (device numbers 1-6).

        `exclude_ip` drops our own announcement: in virtual device mode we take a
        number in that same range and hear our own broadcast back.
        """
        with self.lock:
            return sorted((a for a in self.devices.values()
                           if a.kind == "player" and a.ip != exclude_ip),
                          key=lambda a: a.device_number)

    def wait_for_devices(self, seconds: float = 5.0) -> bool:
        """Wait until some deck reports status. True if anything arrived."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.active_decks():
                return True
            time.sleep(0.2)
        return False
