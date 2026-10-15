"""Pro DJ Link model profiles and the "which device am I using" setting.

A profile says what a model can give us: decks, device numbers, whether it is
an all-in-one, whether it runs dbserver, exports media over NFS, sends beat or
precise-position packets, which waveform colours it stores, and how a laptop
connects. ``auto`` picks the profile from the names players announce; an
unknown name gets the conservative ``generic`` profile, so nothing is switched
off that might work. Profiles only ever *skip* requests; they never cause a
packet to be sent to a player.

Sources: djl-analysis (deepsymmetry.org), Beat Link issues, AlphaTheta manuals.
Entries marked ``verified=False`` are from documentation, not tested here.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ModelProfile:
    key: str
    name: str
    kind: str = "player"                 # player | all-in-one | mixer | software
    decks: int = 1
    numbers: tuple = (1, 2, 3, 4)        # device numbers it normally uses
    all_in_one: bool = False
    dbserver: bool = True
    nfs: bool = True
    beat_packets: bool = True
    precise_position: bool = False       # 0x0b absolute position (~30 Hz)
    waveforms: tuple = ("blue",)
    connection: str = "Ethernet (LINK) to a switch or the mixer; Virtual device mode"
    recommended_mode: str = "vcdj"
    notes: str = ""
    verified: bool = False
    aliases: tuple = field(default_factory=tuple)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["numbers"] = list(self.numbers)
        d["waveforms"] = list(self.waveforms)
        d.pop("aliases", None)
        return d


_P = ModelProfile
PROFILES: dict[str, ModelProfile] = {p.key: p for p in (
    _P("xdj-az", "XDJ-AZ", "all-in-one", 4, (1, 2, 3, 4, 33), True,
       precise_position=True, waveforms=("blue", "rgb", "3band"),
       connection="LAN port to the laptop or a switch; Virtual device mode",
       notes="Decks 1-4 and mixer 33 on one IP.", verified=True),
    _P("xdj-xz", "XDJ-XZ", "all-in-one", 2, (1, 2, 33), True,
       waveforms=("blue", "rgb"),
       connection="USB-B cable (Observe only) or the LINK port (Virtual device, automatic number)",
       recommended_mode="observe",
       notes="Decks 1, 2 and mixer 33 on one IP. Does not defend device numbers and sends "
             "no media-slot broadcasts."),
    _P("cdj-3000x", "CDJ-3000X", precise_position=True,
       waveforms=("blue", "rgb", "3band"), aliases=("cdj3000x",)),
    _P("cdj-3000", "CDJ-3000", precise_position=True,
       waveforms=("blue", "rgb", "3band"), verified=True),
    _P("cdj-2000nxs2", "CDJ-2000NXS2", waveforms=("blue", "rgb")),
    _P("cdj-2000nxs", "CDJ-2000NXS", aliases=("cdj-2000nexus",)),
    _P("cdj-2000", "CDJ-2000"),
    _P("cdj-900nxs", "CDJ-900NXS", aliases=("cdj-900nexus",)),
    _P("cdj-900", "CDJ-900"),
    _P("xdj-1000mk2", "XDJ-1000MK2", waveforms=("blue", "rgb")),
    _P("xdj-1000", "XDJ-1000"),
    _P("xdj-700", "XDJ-700"),
    _P("xdj-rx3", "XDJ-RX3", "all-in-one", 2, (1, 2, 33), True, waveforms=("blue", "rgb"),
       connection="LINK/LAN port; Virtual device mode", notes="Untested."),
    _P("xdj-rx2", "XDJ-RX2", "all-in-one", 2, (1, 2, 33), True, waveforms=("blue", "rgb"),
       connection="LINK/LAN port; Virtual device mode", notes="Untested."),
    _P("xdj-rx", "XDJ-RX", "all-in-one", 2, (1, 2, 33), True,
       connection="LINK port; Virtual device mode", notes="Untested."),
    _P("xdj-rr", "XDJ-RR", "all-in-one", 2, (1, 2, 33), True, dbserver=False, nfs=False,
       connection="No Pro DJ Link port for players; monitoring is not expected to work",
       recommended_mode="sniffer", notes="Limited: no media sharing over the link."),
    _P("opus-quad", "OPUS-QUAD", "all-in-one", 4, (1, 2, 3, 4, 33), True,
       dbserver=False, beat_packets=False, waveforms=("blue", "rgb", "3band"),
       connection="LINK port; Observe only", recommended_mode="observe",
       notes="Limited: no dbserver and no beat packets; track metadata is not available."),
    _P("omnis-duo", "OMNIS-DUO", "all-in-one", 2, (1, 2, 33), True,
       waveforms=("blue", "rgb", "3band"), recommended_mode="observe",
       notes="Decks share one player number. Untested."),
    _P("djm", "DJM mixer", "mixer", 0, (33,), dbserver=False, nfs=False,
       waveforms=(), notes="Mixer status and on-air only; no tracks or waveforms."),
    _P("rekordbox", "rekordbox", "software", 0, (17,), dbserver=True, nfs=False,
       beat_packets=False, waveforms=(), recommended_mode="sniffer",
       connection="Same network; rekordbox holds UDP 50000-50002, so use Passive capture",
       notes="Link/Lighting export mode; not a player."),
)}

GENERIC = _P("generic", "Unknown model", notes="Unknown model: every feature is tried "
             "and anything missing is simply left out.")

# Order matters: longer names first so CDJ-3000X is not taken for CDJ-3000.
_MATCH_ORDER = sorted(PROFILES, key=len, reverse=True)

CHOICES = ("auto",) + tuple(PROFILES)


def _compact(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def profile_for_name(name: str | None) -> ModelProfile | None:
    """Profile for an announced device name, or None when unknown."""
    c = _compact(name)
    if not c:
        return None
    for key in _MATCH_ORDER:
        p = PROFILES[key]
        for cand in (key, *p.aliases):
            if c.startswith(_compact(cand)):
                return p
    return None


def resolve(setting: str | None, names) -> dict:
    """The profile in force and where it came from.

    ``setting`` is ``auto`` or a profile key. In auto, the first player
    (not mixer, not rekordbox) wins; then a mixer; unknown names give GENERIC.
    """
    setting = str(setting or "auto").lower()
    detected = []
    for n in names or ():
        p = profile_for_name(n)
        detected.append({"name": str(n), "model": p.name if p else None})
    if setting != "auto" and setting in PROFILES:
        prof, source = PROFILES[setting], "setting"
    else:
        found = [profile_for_name(n) for n in names or ()]
        rank = {"all-in-one": 0, "player": 1, "mixer": 2, "software": 3}
        known = sorted((p for p in found if p), key=lambda p: rank.get(p.kind, 9))
        if known:
            prof, source = known[0], "detected"
        elif names:
            prof, source = GENERIC, "unknown"
        else:
            prof, source = GENERIC, "none"
    return {"setting": setting if setting in CHOICES else "auto", "source": source,
            "profile": prof.as_dict(), "detected": detected}


def capability_line(res: dict) -> str:
    """One Health line: model, where it came from and what it can do."""
    p = res.get("profile") or {}
    src = {"setting": "chosen", "detected": "detected", "unknown": "unknown model",
           "none": "nothing seen yet"}.get(res.get("source"), "")
    yes = [label for flag, label in (("dbserver", "dbserver"), ("nfs", "NFS"),
                                      ("beat_packets", "beats"),
                                      ("precise_position", "precise position"))
           if p.get(flag)]
    waves = "/".join(p.get("waveforms") or []) or "none"
    decks = p.get("decks")
    return (f"{p.get('name', '?')} ({src}) — {decks} deck(s); "
            f"{', '.join(yes) or 'no track data'}; waveforms {waves}")
