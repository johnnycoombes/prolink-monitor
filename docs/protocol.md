# Pro DJ Link, as implemented

What prolink-monitor sends, listens for and decodes. Offsets are byte offsets into the UDP payload, as used in `prolink/proto.py` (multi-byte fields are big-endian). This is a description of this code, not a full protocol reference.

**Reference.** The field reference for Pro DJ Link, dbserver, `export.pdb` and the ANLZ files is Deep Symmetry's [DJ Link Ecosystem Analysis](https://djl-analysis.deepsymmetry.org/) (from the dysentery and crate-digger projects). Beat Link and its siblings are Eclipse Public License; they were read for ideas and to check offsets. No EPL or GPL source or text is copied into this project. See the README's Credits for the other references (SuperTimecodeConverter, prodj-beats, prolink-connect, alphatheta-connect, rbxport, OneLibraryCompanion).

**Read-only.** The only packets this app originates on the link are keep-alives (virtual device mode). Everything else is a read-only query: dbserver menu/metadata/art/ANLZ-tag requests and NFS v2 reads. It never sends remote-control, load, sync or fader commands, and never writes to media or databases.

## Ports

| Port | Proto | Direction | Used for |
|---|---|---|---|
| 50000 | UDP | in / out (broadcast) | Keep-alives (`0x06`), number claims (`0x00`, `0x02`, `0x04`), number-in-use (`0x08`) |
| 50001 | UDP | in | Beat (`0x28`), Absolute Position (`0x0b`), Channels On-Air (`0x03`) |
| 50002 | UDP | in (unicast to announced devices) | CDJ status (`0x0a`), mixer status (`0x29`) |
| 12523 | TCP | out | dbserver port discovery (`RemoteDBServer` query) |
| *returned* | TCP | out | dbserver session (often 1051, but not always) |
| 111 | UDP | out | ONC-RPC portmapper |
| *returned* | UDP | out | MOUNT v1 and NFS v2 |

Listening sockets bind to `0.0.0.0` on every platform (on macOS an interface-bound socket misses broadcasts). Port 50004 (mixer clock, type `0x20`) is not bound or parsed.

## Packet header

Every packet starts with the 10-byte magic `Qspt1WmJOL`; byte `0x0a` is the packet type. Packets shorter than `0x24` bytes or without the magic are ignored. Devices named `TCS-SHOWKONTROL…` are ignored.

## Keep-alive (type `0x06`, UDP 50000)

Parsed (`_parse_announce`, ≥ `0x36` bytes):

| Offset | Field |
|---|---|
| `0x0c` | Device name (20 bytes, ASCII) |
| `0x21` | Device type |
| `0x24` | Device number |
| `0x26`–`0x2b` | MAC |
| `0x2c`–`0x2f` | IPv4 |
| `0x30` | Peer count (devices it sees, itself included) |

Built (`build_keepalive`, `0x36` bytes): name at `0x0c`, `0x20 = 0x01`, `0x21 = 0x03`, length `0x0036` at `0x22`, number at `0x24`, `0x25 = 0x01`, MAC, IP, known-device count at `0x30`, `0x31 = 0x01`, `0x34 = 0x04`, `0x35 = 0x08`. Sent every 1.5 s to the subnet broadcast and `255.255.255.255`, each address once.

## Device-number claim

```mermaid
sequenceDiagram
  participant A as prolink-monitor
  participant L as LAN (UDP 50000)
  A->>L: listen 3 s (DISCOVERY_WINDOW)
  L-->>A: keep-alives (number @0x24), claims (0x04 @0x24), in-use (0x08 @0x24)
  A->>A: taken = numbers heard; choose_device_number()
  loop every 1.5 s
    A->>L: keep-alive with our number
    L-->>A: 0x04 claim or 0x08 in-use naming our number?
    A->>A: if so, mark taken, pick another, note "moved from #n"
  end
```

- Claim stages are recognised but never counted as devices (no ghost decks): `0x00` (first stage), `0x02` (number at `0x2e`), `0x04` (final, number at `0x24`). Only a final claim reserves its number.
- `choose_device_number`: a set number (default 5) is kept if free and not reserved; otherwise the first free of `AUTO_ORDER = 5, 6, 4, 3, 7, 8, 13, 14, 15`.
- **XDJ-XZ**: if an XZ has announced, `XZ_AUTO_ORDER = 3, 4, 5, 6, 7, 8, 13, 14, 15`. The XZ answers "go ahead" to any number and is reported (unconfirmed) not to defend its own, so the check is on our side.
- **Reserved**: 0, 17 (rekordbox), 25, 9–12 (Opus Quad deck numbers), 33–47 (mixers).
- **Opus Quad**: decks reported as 9–12 are mapped to 1–4 (`raw & 0x07`) wherever a device number is read.
- **XDJ-AZ**: uses 1–4 and 33; the default 5 is free, so behaviour matches a CDJ setup apart from the listen window.
- **Observe only** never announces, so it never claims.
- dbserver client number: a number not used by any deck or mixer at the target IP, taken from 4 down to 1 when ours would collide (an XZ ignores requests claiming to be its own deck).

## CDJ status (type `0x0a`, UDP 50002)

Parsed when ≥ `0xa7` bytes (`_parse_status`):

| Offset | Size | Field |
|---|---|---|
| `0x0b` | 20 | Device name |
| `0x21` | 1 | Device number |
| `0x28` | 1 | Loaded-from device |
| `0x29` | 1 | Slot (table below) |
| `0x2a` | 1 | Track type: 0 unknown, 1 rekordbox, 2 file, 5 CD, 6 streaming |
| `0x2c` | 4 | Track id (rekordbox id) |
| `0x30` | 4 | Track number |
| `0x6c` / `0x70` | 4 / 4 | USB / SD state: 0 loaded, 2 stopping, 3 unmounting, 4 no media |
| `0x78` | 4 | Play state (table below) |
| `0x7c` | 4 | Firmware string |
| `0x88` | 2 | Flags: `0x08` on air, `0x10` sync, `0x20` master, `0x40` playing |
| `0x8c` | 4 | Physical pitch (`0x100000` = 1.0) |
| `0x92` | 2 | Track BPM × 100 (`0xffff` = none) |
| `0x98` | 4 | Effective pitch (`0x100000` = 1.0) |
| `0xa0` | 4 | Beat number in the grid (`0xffffffff` = none) |
| `0xa4` | 2 | Beats to next cue (`0x1ff` = none) |
| `0xa6` | 1 | Beat within bar (1–4) |
| `0xd0` | 4 | Settings block marker `12 34 56 78` (when present) |
| `0xda` | 1 | Waveform colour: 01 blue, 03 RGB, 04 3-band |
| `0xdd` | 1 | Waveform position: 01 centre, 02 left |
| `0x1b6` / `0x1be` | 4 / 4 | Active loop start / end (CDJ-3000, 0x200-byte packets) |

Slots (`0x29`): 0 empty, 1 CD, 2 SD, 3 USB, 4 rekordbox, 5 and 8 streaming (unspecified service), 6 Streaming Direct Play, 7 USB 2, 9 Beatport LINK.

Play states (`0x78`): `00` no track, `02` loading, `03` playing, `04` looping, `05` paused, `06` cued, `07` cue play, `08` cue scratch, `09` searching, `0e` unplayable, `11` end of track, `12` emergency loop. Counted as playing: `03`, `04`, `07`, `12`.

Loop bounds are decoded with the documented CDJ-3000 scaling (raw × 65536 / 1000 ms) when plausible, else as raw milliseconds, else ignored. The on-air bit at `0x89` follows the channel fader; fader, volume and EQ are not on the link. XDJ-XZ probe reports also record status bytes `0x8b` and `0xcc`.

## Beat (type `0x28`, UDP 50001)

≥ `0x60` bytes: device `0x21`, name `0x0b`, next-beat / second-beat / next-bar times at `0x24` (3 × u32), pitch `0x54`, BPM × 100 `0x5a`, beat within bar `0x5c`.

## Absolute Position (type `0x0b`, UDP 50001)

CDJ-3000 class, about 30 Hz. ≥ `0x3c` bytes: device `0x21`, track length `0x24` (seconds on CDJ-3000; some AZ builds send ms, handled), playhead ms `0x28`, pitch % × 100 (signed) `0x2c`, effective BPM × 10 `0x38`. When present it drives the playhead (`position_source = "exact"`; `"beat_grid"` otherwise, `"none"` with no track); otherwise the beat grid does.

## Channels On-Air (type `0x03`, UDP 50001)

≥ `0x2d` bytes: device `0x21`, channels 1–4 at `0x24`–`0x27`; 5–6 at `0x2d`–`0x2e` on ≥ `0x35`-byte (six-channel) packets. Exposed as `mixer_on_air`. On an **XDJ-AZ** this packet appears only in Pro DJ Link mode, so it (or an AZ deck 1–4 status within 5 s) means *Pro DJ Link*; neither for 5 s means *4-deck / rekordbox lighting*.

## Mixer status (type `0x29`, UDP 50002)

Decoded **only for the XDJ-XZ** (≥ `0x38` bytes): flags `0x27` (`0x20` = tempo master), pitch `0x28`, BPM × 100 `0x2e`, tempo-master handoff `0x36` (`0x00` and `0xff` = no handoff). Mixer BPM is shown as n/a unless an analysed track is playing on an XZ deck. Other mixers' status is not parsed.

## dbserver (TCP)

Read-only client in `prolink/remotedb.py` / `remotedb_client.py`.

1. Port discovery: TCP 12523, send `00 00 00 0f "RemoteDBServer" 00`, read a u16 port. Cached per IP for 2 minutes.
2. Connect, greeting, setup (`0x0000`) with our client number.
3. Requests used:

| Type | Request |
|---|---|
| `0x1000` | Root menu (playlist tree, folder by folder, 64 rows per render) |
| `0x1105` | Playlist / folder contents |
| `0x1012` | History menu |
| `0x1004` | Track menu |
| `0x2002` | rekordbox metadata (title, artist, album, …) |
| `0x2202` | Generic metadata (unanalysed files, CDs) |
| `0x2003` | Album art |
| `0x2c04` | One ANLZ tag (fallback: PWV5/EXT, PWV3/EXT, PWV7/2EX, PQTZ/DAT) |
| `0x3000` | Render menu |

Responses: `0x4000` menu available, `0x4001` header, `0x4101` item, `0x4201` footer, `0x4002` art, `0x4f02` ANLZ tag. Item types: `0x0001` folder, `0x0008` playlist, `0x0024` history; the track sub-column (album, genre, key, …) is mapped by item type. Dropped sessions reconnect with backoff and a fresh handshake. Models without dbserver (Opus Quad, XDJ-RR, DJM, profiles with `dbserver=False`) are never asked; Observe only never opens a session.

## NFS and files

`prolink/nfs.py` is a minimal ONC-RPC client: portmap (program 100000) → MOUNT v1 (100005: `EXPORT`, `MNT`) → NFS v2 (100003: `LOOKUP`, `GETATTR`, `READ`), all UDP, AUTH_UNIX, from a reserved port when possible. Reads are 4 KB (`PROLINK_NFS_READ_SIZE`, 2–8 KB), serial, with adaptive timeouts (about 4 × RTT, clamped 0.25–0.5 s, with backoff). File names are UTF-16LE when the export uses it (the AZ does).

Exports by slot: `/B/` = SD, `/C/` = USB, `/D/` or names containing `usb2` = USB 2 (unconfirmed). On XZ/AZ the displayed labels differ (XZ: SD → USB 1, USB → USB 2; AZ: USB → USB 1, SD/7 → USB 2) but the export follows the reported slot.

Files read:

| Path | Use |
|---|---|
| `PIONEER/rekordbox/export.pdb` | Track rows, ANLZ path, artwork path, playlists (fallback) |
| `PIONEER/rekordbox/exportLibrary.db` | OneLibrary (optional, SQLCipher via pyrekordbox); used when it has the same id |
| `PIONEER/USBANLZ/…/ANLZ0000.DAT`, `.EXT`, `.2EX` | Analysis |
| `PIONEER/Artwork/…` | Thumbnails |
| The audio file (tag region only) | Hi-res embedded cover (capped read) |

## ANLZ tags

Parsed in `prolink/anlz.py`:

| Tag | File | Used for |
|---|---|---|
| `PQTZ` | `.DAT` | Beat grid |
| `PPTH` | `.DAT` | Audio file path |
| `PCOB` | `.DAT`, `.EXT` | Cues and loops |
| `PCO2` | `.EXT` | Cues with colour and comment (`0,0,0` = no colour → memory cue red) |
| `PSSI` | `.EXT` | Phrases |
| `PWAV`, `PWV2` | `.DAT` | Monochrome previews (parsed) |
| `PWV3` | `.EXT` | Blue detail waveform |
| `PWV4` | `.EXT` | Colour preview (parsed; overview is built from detail instead) |
| `PWV5` | `.EXT` | RGB detail waveform |
| `PWV6` | `.2EX` | 3-band overview |
| `PWV7` | `.2EX` | 3-band detail |
| `PWVC` | `.2EX` | Vocal thresholds |

Detail columns are 150 per second; the overview sent to clients is 1600 columns. 3-band byte order defaults to low, mid, high (configurable to mid, high, low).

## Model profiles

From `prolink/models.py`. Profiles only disable requests. "Verified" means tested on hardware in this project.

| Key | Name | Kind | Decks | Numbers | dbserver | NFS | Beats | Abs. position | Waveforms | Mode | Verified |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `xdj-az` | XDJ-AZ | all-in-one | 4 | 1–4, 33 | ✓ | ✓ | ✓ | ✓ | blue, rgb, 3band | vcdj | ✓ |
| `xdj-xz` | XDJ-XZ | all-in-one | 2 | 1, 2, 33 | ✓ | ✓ | ✓ | — | blue, rgb | vcdj | — |
| `cdj-3000x` | CDJ-3000X | player | 1 | 1–4 | ✓ | ✓ | ✓ | ✓ | blue, rgb, 3band | vcdj | — |
| `cdj-3000` | CDJ-3000 | player | 1 | 1–4 | ✓ | ✓ | ✓ | ✓ | blue, rgb, 3band | vcdj | ✓ |
| `cdj-2000nxs2` | CDJ-2000NXS2 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue, rgb | vcdj | — |
| `cdj-2000nxs` | CDJ-2000NXS | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `cdj-2000` | CDJ-2000 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `cdj-900nxs` | CDJ-900NXS | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `cdj-900` | CDJ-900 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `xdj-1000mk2` | XDJ-1000MK2 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue, rgb | vcdj | — |
| `xdj-1000` | XDJ-1000 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `xdj-700` | XDJ-700 | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `xdj-rx3` | XDJ-RX3 | all-in-one | 2 | 1, 2, 33 | ✓ | ✓ | ✓ | — | blue, rgb | vcdj | — |
| `xdj-rx2` | XDJ-RX2 | all-in-one | 2 | 1, 2, 33 | ✓ | ✓ | ✓ | — | blue, rgb | vcdj | — |
| `xdj-rx` | XDJ-RX | all-in-one | 2 | 1, 2, 33 | ✓ | ✓ | ✓ | — | blue | vcdj | — |
| `xdj-rr` | XDJ-RR | all-in-one | 2 | 1, 2, 33 | — | — | ✓ | — | blue | sniffer | — |
| `opus-quad` | OPUS-QUAD | all-in-one | 4 | 9–12, 33 | — | ✓ | — | — | blue, rgb, 3band | observe | — |
| `omnis-duo` | OMNIS-DUO | all-in-one | 2 | 1, 2, 33 | ✓ | ✓ | ✓ | — | blue, rgb, 3band | observe | — |
| `djm` | DJM mixer | mixer | 0 | 33 | — | — | ✓ | — | — | vcdj | — |
| `rekordbox` | rekordbox | software | 0 | 17 | ✓ | — | — | — | — | sniffer | — |
| `generic` | Unknown model | player | 1 | 1–4 | ✓ | ✓ | ✓ | — | blue | vcdj | — |

Notes from the profiles: XDJ-XZ has no master level, meters or Beat FX over the link (Health lists them as "not on this model"), on-air is the reported channel state, and USB-B is experimental. Opus Quad: no metadata, decks shown as 1–4, one USB at a time, IDs can drift. OMNIS-DUO: decks share one player number. XDJ-RR: no media sharing over the link. Matching is by announced name, longest key first (so CDJ-3000X is not read as CDJ-3000); auto prefers all-in-one, then player, mixer, software.

## Known limitations

- Verified on an XDJ-AZ only (plus a QEMU-emulated AZ). Everything else follows public layouts.
- XDJ-XZ: untested on hardware; sends no media-slot broadcasts (slots come from status); whether it sends status to a non-announcing observer is unknown.
- XDJ-AZ: Absolute Position and the `0xd0` settings block are unconfirmed; Auto playhead falls back to Left for the AZ and Opus Quad when `0xdd` is missing.
- CDJ-3000 sequence counter (`0xc8`–`0xcb`) usually stays zero, so Health's sequence-hole column stays blank.
- Opus Quad: no dbserver, no beat packets, ambiguous USB source, track-id drift.
- Mixer clock (`0x20`, port 50004) and non-XZ mixer status are not decoded.
- The second-USB NFS export name on all-in-ones is inferred.
- Virtual device mode cannot share UDP 50000–50002 with rekordbox.
