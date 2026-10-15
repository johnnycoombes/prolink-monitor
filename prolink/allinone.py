"""All-in-one units (XDJ-XZ, XDJ-AZ, Opus Quad...) and the XZ "USB link".

Everything here is read-only: it classifies what was heard, chooses a device
number nobody else uses, finds the network adapter an all-in-one exposes over
its USB-B cable, maps a deck's slot to an NFS export, and counts packets for
the XZ probe report. Nothing here sends a packet to a player.

Background: https://djl-analysis.deepsymmetry.org/djl-analysis/startup.html#xdj-xz-limitations
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

# Compact name (lower case, no punctuation) -> display name.
ALL_IN_ONE_MODELS = {
    "xdjxz": "XDJ-XZ",
    "xdjaz": "XDJ-AZ",
    "opusquad": "OPUS-QUAD",
    "xdjrx3": "XDJ-RX3",
    "omnisduo": "OMNIS-DUO",
}

# Notes shown on Health next to the model.
MODEL_NOTES = {
    "XDJ-XZ": "2 decks (1, 2) + mixer (33) on one IP; no media-slot broadcasts; "
              "does not defend device numbers on the laptop port",
    "XDJ-AZ": "decks 1-4 + mixer (33) on one IP",
    "OPUS-QUAD": "no dbserver and no beat packets; metadata needs a USB archive (not supported)",
    "XDJ-RX3": "untested",
    "OMNIS-DUO": "decks multiplexed on one player number; untested",
}

# Numbers that are never ours: 17 rekordbox, 25 Kuvo/NXS-GW, 33+ mixers.
_RESERVED = {0, 17, 25} | set(range(33, 48))
# Preference when choosing automatically: the usual virtual-CDJ numbers first.
AUTO_ORDER = (5, 6, 4, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15)


def compact(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def all_in_one_model(name: str | None) -> str | None:
    """``XDJ-XZ`` etc. for a device name that is an all-in-one, else None."""
    c = compact(name)
    for key, display in ALL_IN_ONE_MODELS.items():
        if c == key or c.startswith(key):
            return display
    return None


def is_xdj_xz(name: str | None) -> bool:
    return all_in_one_model(name) == "XDJ-XZ"


def choose_device_number(preferred: int | None, taken) -> tuple[int, str]:
    """A device number nobody on the network uses, and why it was chosen.

    ``preferred`` 0 or None means automatic. A preferred number that is taken
    (an XZ answers "go ahead" even for its own decks 1 and 2 and never
    defends them) is replaced by the first free one in ``AUTO_ORDER``.
    """
    used = {int(n) for n in (taken or ())}
    want = int(preferred or 0)
    if want and want not in _RESERVED and want not in used:
        return want, f"#{want} (as set, free)"
    for n in AUTO_ORDER:
        if n not in used:
            if want:
                return n, f"#{n} (automatic: #{want} is in use)"
            return n, f"#{n} (automatic)"
    raise RuntimeError("no free device number: every number from 3 to 15 is in use")


# ------------------------------------------------------------- USB link adapter

@dataclass
class Adapter:
    name: str
    ip: str
    prefix: int
    description: str = ""
    score: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "ip": self.ip, "prefix": self.prefix,
                "description": self.description, "score": self.score}


_USB_HINTS = ("rndis", "remote ndis", "ncm", "cdc", "usb", "pioneer", "alphatheta",
              "xdj", "ethernet gadget")


def _score(ip: str, name: str, description: str) -> int:
    score = 0
    try:
        if ipaddress.ip_address(ip).is_link_local:
            score += 2
    except ValueError:
        return -1
    text = f"{name} {description}".lower()
    if any(h in text for h in _USB_HINTS):
        score += 2
    if any(h in text for h in ("pioneer", "alphatheta", "xdj")):
        score += 2
    return score


def parse_ifconfig_adapters(text: str) -> list[Adapter]:
    """IPv4 adapters from macOS/BSD ``ifconfig`` output."""
    found: list[Adapter] = []
    iface = ""
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            iface = line.split(":", 1)[0].strip()
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "inet" and parts[2] == "netmask":
            mask = parts[3]
            try:
                bits = (bin(int(mask, 16)).count("1") if mask.startswith("0x")
                        else ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen)
            except ValueError:
                continue
            if parts[1].startswith("127."):
                continue
            found.append(Adapter(iface, parts[1], bits, "", _score(parts[1], iface, "")))
    return found


def parse_windows_adapters(text: str) -> list[Adapter]:
    """JSON from ``Get-NetIPAddress`` joined with ``Get-NetAdapter`` (see below)."""
    try:
        rows = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    found = []
    for r in rows or []:
        ip = str(r.get("IPAddress") or "")
        if not ip or ip.startswith("127."):
            continue
        name = str(r.get("InterfaceAlias") or "")
        desc = str(r.get("InterfaceDescription") or "")
        try:
            prefix = int(r.get("PrefixLength") or 24)
        except (TypeError, ValueError):
            prefix = 24
        found.append(Adapter(name, ip, prefix, desc, _score(ip, name, desc)))
    return found


_WIN_PS = (
    "$a=@{}; Get-NetAdapter | ForEach-Object { $a[$_.ifIndex]=$_.InterfaceDescription }; "
    "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { [pscustomobject]@{"
    "IPAddress=$_.IPAddress; PrefixLength=$_.PrefixLength; InterfaceAlias=$_.InterfaceAlias; "
    "InterfaceDescription=$a[$_.InterfaceIndex] } } | ConvertTo-Json -Compress"
)


def list_adapters() -> list[Adapter]:
    """Local IPv4 adapters, best USB-link candidate first. Read-only."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", _WIN_PS],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            found = parse_windows_adapters(out)
        else:
            exe = "/sbin/ifconfig" if sys.platform == "darwin" else "ifconfig"
            out = subprocess.run([exe], capture_output=True, text=True, timeout=10).stdout
            found = parse_ifconfig_adapters(out)
    except (OSError, subprocess.SubprocessError):
        found = []
    return sorted(found, key=lambda a: -a.score)


def usb_link_candidates(adapters: list[Adapter]) -> list[Adapter]:
    """Adapters that look like an all-in-one's USB-B network (link-local or USB)."""
    return [a for a in adapters if a.score >= 2]


# ------------------------------------------------------------ slot -> export

# The usual NFS export names: /B/ = SD, /C/ = USB (djl-analysis). The second
# USB slot on all-in-ones is unconfirmed; names containing "usb2" or "/D/" are
# taken for it. Anything unrecognised falls back to the first export.
_SLOT_EXPORTS = {
    "sd": ("/b/", "sd"),
    "usb": ("/c/", "usb1", "usb"),
    "usb2": ("/d/", "usb2"),
}


def export_for_slot(exports: list[str], slot: str | None) -> str | None:
    """The NFS export that holds the given slot's medium, or None if unknown."""
    if not exports or not slot:
        return None
    if len(exports) == 1:
        return exports[0]
    hints = _SLOT_EXPORTS.get(str(slot).lower())
    if not hints:
        return None
    lowered = [(e, e.lower().rstrip("/") + "/") for e in exports]
    for hint in hints:
        for raw, low in lowered:
            if hint.startswith("/"):
                if low == hint or low.startswith(hint):
                    return raw
            elif hint == "usb":
                if "usb" in low and "usb2" not in low:
                    return raw
            elif hint in low:
                return raw
    return None


# ------------------------------------------------------------- packet census

PACKET_NAMES = {
    0x06: "keep-alive (50000)",
    0x0A: "CDJ status (50002)",
    0x28: "beat (50001)",
    0x29: "mixer status (50002)",
    0x0B: "absolute position (50001)",
    0x03: "channels on-air (50001)",
    0x02: "fader start / misc",
}


@dataclass
class Census:
    """Counts what arrived, per source IP, device number and packet type."""
    started: float = field(default_factory=time.monotonic)
    counts: dict = field(default_factory=dict)       # (ip, dev, type) -> n
    last_seen: dict = field(default_factory=dict)    # type -> monotonic
    names: dict = field(default_factory=dict)        # (ip, dev) -> name
    status: dict = field(default_factory=dict)       # dev -> slot/firmware sample
    lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, data: bytes, src_ip: str, pkt, now: float) -> None:
        if len(data) < 0x0B or not data.startswith(b"Qspt1WmJOL"):
            return
        ptype = data[0x0A]
        dev = int(getattr(pkt, "device_number", 0) or 0) if pkt is not None else 0
        with self.lock:
            key = (src_ip, dev, ptype)
            self.counts[key] = self.counts.get(key, 0) + 1
            self.last_seen[ptype] = now
            name = getattr(pkt, "name", "") if pkt is not None else ""
            if name:
                self.names[(src_ip, dev)] = name
            if pkt is not None and hasattr(pkt, "slot_raw"):
                self.status[dev] = {
                    "slot": getattr(pkt, "slot", ""),
                    "slot_raw": int(getattr(pkt, "slot_raw", 0) or 0),
                    "track_type_raw": int(getattr(pkt, "track_type_raw", 0) or 0),
                    "track_id": int(getattr(pkt, "track_id", 0) or 0),
                    "loaded_from": int(getattr(pkt, "loaded_from", 0) or 0),
                    "firmware": getattr(pkt, "firmware", "") or "",
                    "usb_state": getattr(pkt, "usb_state", "") or "",
                    "sd_state": getattr(pkt, "sd_state", "") or "",
                    "length": len(data),
                }

    def seconds_since(self, ptype: int, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        with self.lock:
            t = self.last_seen.get(ptype)
        return None if t is None else max(0.0, now - t)

    def as_dict(self) -> dict:
        with self.lock:
            rows = [
                {"ip": ip, "device": dev, "type": f"0x{t:02x}",
                 "kind": PACKET_NAMES.get(t, "other"), "count": n,
                 "name": self.names.get((ip, dev), "")}
                for (ip, dev, t), n in sorted(self.counts.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
            ]
            status = {str(k): dict(v) for k, v in sorted(self.status.items())}
        return {"seconds": round(time.monotonic() - self.started, 1),
                "packets": rows, "status_samples": status}


def observe_health(kind: str, census: Census | None, now: float | None = None,
                   grace: float = 6.0) -> dict:
    """Plain-language Health line for the observe-only mode."""
    if kind != "observe" or census is None:
        return {}
    now = time.monotonic() if now is None else now
    age = now - census.started
    status_age = census.seconds_since(0x0A, now)
    keep_age = census.seconds_since(0x06, now)
    if status_age is not None and status_age < grace:
        return {"state": "ok", "text": "Observe only: player status packets are arriving."}
    if age < grace:
        return {"state": "waiting", "text": "Observe only: listening…"}
    if keep_age is None:
        return {"state": "silent", "text": (
            "Observe only: nothing heard on UDP 50000-50002. Check the USB-B cable and "
            "that the adapter has an address (usually 169.254.x.x).")}
    return {"state": "no_status", "text": (
        "Observe only: keep-alives arrive but no player status packets. This link only "
        "carries broadcasts; use Virtual device on the laptop (LINK) port instead.")}
