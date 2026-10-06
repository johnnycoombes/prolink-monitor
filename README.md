# prolink-monitor

**Fork of [fidow/prolink-monitor](https://github.com/fidow/prolink-monitor).**

Live monitoring for AlphaTheta / Pioneer DJ players over Pro DJ Link: which track
sits on each deck, how far into it, at what tempo and key, with a scrolling
waveform and moving playhead.

The monitor core still talks to the gear directly and needs **no external Python
packages**. This fork adds a PySide6 **Prolink Listener** desktop app, player-style
waveform colours, configurable deck cards, a hideable sidebar, and a transparent
**OBS Browser Source** overlay.

![Prolink Listener — 4 decks RGB](doc/desktop-4deck-rgb.png)

---

## This fork

Compared with the [original project](https://github.com/fidow/prolink-monitor):

| | Original | This fork |
|---|---|---|
| UI | Browser panel (`python app.py`) | Same web panel **plus** a native **Prolink Listener** desktop app (`python desktop.py`) |
| Navigation | Single live panel | Sidebar: Monitor, Devices, **Health**, Library, **Session**, Overlay, Settings, About — **hideable** (`Ctrl+B`) |
| Settings | Zoom / decks remembered in the browser | Connection, display, **per-deck element**, overlay, session and **desktop tray** settings under `~/.prolink-monitor/` |
| Waveforms | Colour detail (RGB) with blue fallback | Switchable **RGB**, **3-Band** and **Blue**; cue / hot-cue / **phrase** markers; hover time + click cue readout; **per-deck zoom** |
| Library | Counts only | Search USB tracks; OneLibrary **playlists / history** browse; on-deck artwork grid |
| Zoom | Seconds | **4/4 bars** (1–16), BPM-aware window |
| Deck cards | Fixed layout | Toggle artwork, BPM, tempo, time, key, tags, waveform and more |
| Streaming | — | Transparent `/overlay` page for OBS (now playing / dual / minimal / **setlist**); SmartTiming setlist; overlay waveform colour |
| Session | — | **Record** a chronological playlist with a **set clock** (pauses on dead air); export CSV / JSON / M3U |
| Language | English / Spanish | English only |

Everything else — Pro DJ Link engine, NFS library, console monitor, protocol probe —
comes from the original and is still the same idea.

---

## Screenshots

### Desktop — Monitor

Waveforms fill the available height. Switch **2** or **4** visible decks, zoom in
**4/4 bars** (1–16) and waveform colour (**RGB** / **3BAND** / **BLUE**) from the
toolbar. Layout order is **1–2** in two-deck mode and **3–1–2–4** in four-deck mode.
Hot cues show as lettered markers (A–H) on the overview and detail waveforms.

![4 decks RGB](doc/desktop-4deck-rgb.png)

![2 decks RGB](doc/desktop-2deck-rgb.png)

![4 decks 3BAND](doc/desktop-4deck-3band.png)

![4 decks BLUE](doc/desktop-4deck-blue.png)

![2 decks BLUE](doc/desktop-2deck-blue.png)

### Desktop — Sidebar

Hide the sidebar with **☰**, **Hide sidebar**, or **Ctrl+B** for a wider Monitor; a
**Navigate** menu stays in the top bar. Preference is remembered.

![Sidebar](doc/desktop-sidebar.png)

### Desktop — Settings, Library, Devices

Connection, display, deck elements and behaviour live under **Settings**.
**Library** browses `export.pdb` tracks (search/filter), OneLibrary playlists and
history when readable, and an artwork strip for tracks currently on decks.
**Devices** lists players and mixers on the link. Multi-player (separate CDJs) browse
aggregates all mounted hosts — still worth validating on a multi-CDJ booth.

![Settings](doc/desktop-settings.png)

![Library stats](doc/desktop-library.png)

![Devices](doc/desktop-devices.png)

### OBS overlay

Transparent HTML served at `/overlay`, driven by the same SSE API as the web panel.
Now Playing **Card** uses the high-res cover. **Panel** (bar) uses the small cover
and a waveform-driven spectrum above the scrolling wave (`?style=panel`). Pro DJ
Link has no audio, so that spectrum follows the analysed low/mid/high waveform
at the playhead and decays while paused. System-audio loopback is an opt-in
desktop setting and needs the optional `soundcard` package.

![Now playing overlay](doc/overlay-nowplaying.png)

![All decks overlay](doc/overlay-all-decks.png)

### Web panel

The original browser panel is still available via `python app.py`.

![Web panel](doc/web-panel.png)

---

## Tested on

**An AlphaTheta XDJ-AZ (firmware 1.30), on Windows 11, and nothing else** — that is
what the original author verified field offsets, NFS access and waveform decoding
against. The desktop shell in this fork has also been exercised on Linux/X11
without live DJ gear.

Two things were written with separate players in mind, both untested:

- the medium is read from whichever player the track reports it was *loaded from*,
  rather than from one fixed address;
- the NFS file-name encoding is detected rather than assumed (the XDJ-AZ uses
  UTF-16LE).

If you try it, on any player or any operating system, an issue saying what worked
and what did not is very welcome.

---

## What it shows

Per deck:

- **Track**: title, artist, album, genre, label, year and artwork.
- **Position**: elapsed and remaining time, absolute beat and position in the bar.
- **Tempo**: effective BPM (track tempo with the fader applied), pitch percentage,
  musical key.
- **State**: playing / cued / paused / end of track, plus the `MASTER`, `SYNC` and
  `ON AIR` flags.
- **Waveform**: the detail view scrolling under a fixed playhead, with the bar grid
  and **cue / hot-cue markers** (A–H badges); above it, an overview of the whole
  track with the played part lit and the same cue markers.

The panel pulses on the downbeat of every deck that is playing.

The header (web) or Monitor toolbar (desktop) has switches for **waveform zoom in
4/4 bars** (1 / 2 / 4 / 8 / 16, also `+` and `-` on the keyboard), how many **decks**
to show (**2** or **4**), and the **waveform colour** (RGB / 3BAND / BLUE). Layout
order is **1–2** in two-deck mode and **3–1–2–4** in four-deck mode. Those choices
are remembered. A **Record session** button captures a chronological playlist with a
**set clock** from the first track at `00:00:00` that pauses on dead air (also under
**Session** in the desktop sidebar, and via `/api/session`).

---

## Requirements

- **Python 3.10 or newer**. The monitor core (`app.py`, `monitor.py`, `probe.py`)
  needs no packages.
- **Desktop GUI**: install `PySide6` (`pip install -r requirements.txt`).
- The computer and the player on the **same network**.
- **Wireshark / Npcap** ([wireshark.org](https://www.wireshark.org)) — only to run
  this while rekordbox is open.

| Your situation | What runs | Npcap |
|---|---|---|
| rekordbox closed | virtual device mode, plain UDP sockets | not needed |
| rekordbox open | passive capture | **required** |

---

## Usage

### Desktop app (PySide6)

```bash
pip install -r requirements.txt
python desktop.py
```

Or `python -m gui`. The window has a sidebar for **Monitor**, **Devices**,
**Health**, **Library**, **Session**, **Overlay**, **Settings** and **About**. Connection mode,
player address, device number, zoom (4/4 bars), visible decks, waveform style and
more live under Settings and are saved to `~/.prolink-monitor/settings.json`.

**Sidebar.** Hide it with the **☰** button, **Hide sidebar** at the bottom of the
panel, or **Ctrl+B**. While hidden, a **Navigate** menu appears in the top bar.
Preference is remembered (also under Settings → Behaviour → Show sidebar).

**System tray.** While connected, minimizing (or closing, if enabled) can send the
app to the system tray so the link stays up without a taskbar window. Tray menu:
Show, Record session, Open overlay, Connect / Disconnect, Quit. Toggle under
**Settings → Desktop**.

**Hotkeys** (while the app is focused):

| Shortcut | Action |
|---|---|
| `Ctrl+R` | Start / stop session Record |
| `+` / `=` / `Ctrl+=` | Zoom in (fewer bars) |
| `-` / `Ctrl+-` | Zoom out (more bars) |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+O` | Open OBS overlay URL |

When minimized to the tray, use the tray menu for Record / Overlay / Quit.

**Connection health.** The **Health** page shows overall status-packet rate, per-deck
**beat** vs **Absolute Position (AP)** packet rates and which position source each
deck is using, plus NFS UDP RTT for mounted media (library / waveform reads).
It also shows the optional What's Now Playing output: off, the last track sent,
or the error if that app is not running.

**What's Now Playing.** Settings → What's Now Playing can send the audience
track (the master on-air deck, the same one the overlay shows) to
[What's Now Playing](https://github.com/whatsnowplaying/whats-now-playing).
It is off until you enable it. Each track is sent once, when it changes.
Nothing is written to the player or to `master.db`. If What's Now Playing is
closed, the listener keeps running and Health shows the error. Cover art is
not included: that app's remote input drops image data and only downloads a
cover from a public web address.

In What's Now Playing: open Settings, choose **Core Settings → Source → Remote**,
then **Output & Display → Web Server** and leave it enabled (port **8899**
unless you changed it). Set a shared secret on the Remote source only if you
want one. In prolink-monitor: tick **Send the audience track**, leave host
as `localhost` when both apps are on this computer, use the same port and
secret, Save, then **Test**. Play the master on-air deck; that track is what
gets sent.

**Deck elements.** Under Settings you can turn individual Monitor card pieces on or
off — artwork, title, artist, album/genre/label, MASTER/SYNC/ON AIR tags, waveform,
BPM, tempo (±%), elapsed/remaining, key and play state. Hidden columns free space
for the waveform. The identity column is wide enough for the full `ON AIR` chip.

**Waveforms.** Cards split the Monitor viewport evenly so waves grow with the window
and deck count. Colour follows the player: **RGB**, **3BAND** (stacked blue / amber /
white the way a CDJ draws it) or **BLUE**. The same switch is on the Monitor page,
in Settings, and on the web panel. **Cue markers** (memory cues and hot cues A–H)
and **phrase markers** (PSSI intro / verse / chorus / …) are drawn on overview and
detail strips. Hover the wave for a time readout; click to pin a cue time. Each deck
has its own zoom chip (`Nb*`); toolbar **All** clears overrides. On the web panel,
Alt-click a wave to cycle that deck’s zoom; right-click clears it.

**Session playlist.** Hit **Record session** on the Monitor toolbar (or open
**Session**) to capture every newly played on-air track in order. Timestamps use a
**set clock**: the first track is `00:00:00`, and the clock **pauses whenever nothing
is on-air** (true set length, not wall clock). Stop keeps the list; Clear resets it.
Export the playlist as **CSV**, **JSON** or **M3U** from the Session page, or enable
**Settings → Session → Auto-save** to write all three when you press Stop
(`~/.prolink-monitor/sessions` by default). The web panel has the same Record control
and a live strip; HTTP endpoints are `/api/session`, `/api/session/start`,
`/api/session/stop`, `/api/session/clear`, and `/api/session/export?fmt=csv|json|m3u`.

**Smooth playhead.** Prolink Listener paints the playhead/waveforms on a separate timer
(default **60 Hz**, Settings → Playhead refresh rate) while metadata and library
stats refresh at ~20 Hz. Waveform strips are cached and scrolled instead of being
fully redrawn every frame; the web panel uses the same scroll-cache idea. SSE
sends lightweight playhead frames at 60 Hz and a full state snapshot ~15 times a
second — see [doc/PERFORMANCE.md](doc/PERFORMANCE.md) for bottlenecks and a
native (C++) roadmap.

The monitor core itself still needs no packages; PySide6 is only for the desktop
shell. The optional local web server can be started from Settings and opened with
**Open web panel**. The **Overlay** page builds a transparent OBS Browser Source
URL from the same server.

### OBS overlay

With the web server running (via `python app.py` or the desktop app), add a
**Browser** source in OBS pointed at:

```
http://127.0.0.1:8777/overlay
```

The page background is transparent. Query parameters:

| Param | Values | Default | Meaning |
|---|---|---|---|
| `layout` | `nowplaying` / `dual` / `minimal` / `setlist` | `nowplaying` | Card style (`setlist` = now-playing + scrolling history) |
| `corner` | `bl` / `br` / `tl` / `tr` / `center` | `bl` | Screen corner |
| `decks` | `1`–`4` | `1` (`2` for dual) | How many decks to show |
| `wave` | `rgb` / `3band` / `blue` | `rgb` | Mini-waveform colour style |
| `scale` / `font` | `0.5`–`2.5` | `1` | Overall overlay scale for different canvases |
| `accent` | 6-digit hex | — | Custom accent colour (e.g. `accent=ff6b61`) |
| `tags` | `1` / `0` | `1` | Show MASTER / SYNC / ON AIR tags |
| `bpm` | `1` / `0` | `1` | Show BPM readout |
| `next` | `1` / `0` | `1` | Show lower-third when MixStatus is about to promote |
| `playing` | `1` / `0` | `1` | Only decks that have a track |
| `mix` | `1` / `0` | `1` | Use SmartTiming now-playing (single-deck layouts) |
| `preview` | `1` / `0` | `0` | Dark preview background (local testing) |

With `mix=1` (default), `nowplaying` / `minimal` / `setlist` follow the **audience** track —
the deck that has been playing and on-air long enough (prolink-connect
SmartTiming: 128 beats ≈ two phrases, brief drop-outs ignored). The **setlist** layout
shows that track on top with a scrolling history of previously reported tracks underneath
(ideal for Twitch/OBS). Dual layout still
shows the selected decks side by side and **spans the full window width** (4 decks
in one row when `decks=4`), with a mini overview waveform and
**elapsed / −remaining** times on each card. Live JSON is also on `/api/setlist`.

Example:

```
http://127.0.0.1:8777/overlay?layout=dual&corner=tl&decks=4&playing=0
http://127.0.0.1:8777/overlay?layout=nowplaying&mix=1
http://127.0.0.1:8777/overlay?layout=setlist&corner=bl&mix=1
http://127.0.0.1:8777/overlay?layout=nowplaying&scale=1.25&accent=ff6b61&tags=0&bpm=0
```

With `mix=1`, a **next-track** lower third appears when another on-air deck has
accumulated enough SmartTiming beats to be about to take over (`pending` on
`/api/state` / `/api/setlist`).

In the desktop app, open **Overlay**, pick layout/corner/**waveform colour**, then
**Copy URL** into OBS. Set the Browser source size to your canvas (e.g. 1920×1080).
The same overlay waveform colour is also under **Settings → Overlay**.

### Web panel

```bash
python app.py
```

It finds the player, picks a mode, starts the server and opens
<http://127.0.0.1:8777/> in a normal browser tab. From the desktop app,
**Open web panel** does the same. The page follows the **Monitor** view
(decks, waveform, phrases, playhead, zoom). Those controls are not on the web
panel. A small fullscreen button on the page can fill the screen after you
click it; nothing opens fullscreen on its own. The HTTP server listens on
**all interfaces** by default (`0.0.0.0`), so a phone on the same Wi‑Fi can
open the panel too.

| Option | What it does |
|---|---|
| `--host 192.168.2.100` | player address (auto-detected when omitted) |
| `--mode vcdj` | force virtual device mode |
| `--mode sniffer` | force passive capture |
| `--number 5` | device number to announce as (1-6) |
| `--port 8777` | web server port |
| `--http-host 0.0.0.0` | bind address (`127.0.0.1` = this PC only) |
| `--iface` | capture interface for sniffer mode (see `tshark -D`) |
| `--no-open` | do not open a browser |

#### Connect from Android (same Wi‑Fi)

1. Start Prolink Listener on the laptop/PC that is on the DJ network  
   (`python app.py` or the desktop app with **Start local web server** on).
2. Confirm **Settings → Allow phones on the same Wi‑Fi (LAN)** is checked  
   (or leave the default `--http-host 0.0.0.0`).
3. Note the **phone / LAN** URL printed in the console, e.g.  
   `http://192.168.1.42:8777/` — that is this PC’s LAN IP, not `127.0.0.1`.
4. On the phone, join the **same Wi‑Fi** as the PC (not mobile data / guest VLAN).
5. Open Chrome (or any browser) and paste that URL.  
   For a read-only view (hides **Record**):  
   `http://192.168.1.42:8777/?readonly=1`
6. If it does not load: allow port **8777** (or your chosen port) through the PC
   firewall for private networks; on Windows, when prompted accept “Private”
   network access for Python / Prolink Listener.

The panel is already usable on a phone (stacked deck cards, sticky header). Use
`?readonly=1` when you only want to watch, not arm session recording.

There is a console monitor too, no browser involved:

```bash
python monitor.py
```

### Protocol probe

To find out whether some control on the player travels over the network:

```bash
python probe.py
```

It learns the idle state for five seconds, then prints every byte that changes,
naming the field when it knows it. Move the control you want to check: if nothing
appears, that data is not transmitted.

Bytes that took more than two values while learning are dropped as counters. `--all`
keeps them. It reports how many bytes it is watching, and runs alongside the panel.

---

## The two modes

The player **does not broadcast its status**: it sends it by unicast, and only to
addresses that announced themselves beforehand. Beat packets and keep-alives are
broadcast; the detailed status is not.

### Virtual device mode (`--mode vcdj`)

Announces itself on port 50000 as one more device, after which the player sends its
status to us directly. The keep-alive is byte-for-byte identical to the one
rekordbox emits, with only the name, device number, MAC and IP changed.

Needs rekordbox closed: it claims UDP 50000-50002 exclusively and Windows returns
`WSAEACCES` to anything else.

An XDJ-AZ takes device numbers 1-4 plus 33 (the mixer section) and rekordbox uses
17, so the default is **5**.

### Passive mode (`--mode sniffer`)

Captures through Npcap, which sits below the socket layer, so it reads the packets
Windows is handing to rekordbox without opening a port. It needs some other program
linked to the player: close rekordbox and it goes quiet.

### `auto` (the default)

Uses the virtual device when port 50002 is free, passive capture when it is not.

---

## How it works

### Live state — Pro DJ Link (UDP)

| Port | Contents |
|---|---|
| 50000 | keep-alive: who is on the network (broadcast) |
| 50001 | beats: arrives exactly on the beat, with tempo and position in the bar; **Absolute Position** (type `0x0b`) on CDJ-3000-class players ~every 30 ms |
| 50002 | detailed status of each player (unicast) |
| 50004 | mixer packet, type 0x20: 44 bytes at ~140 Hz, and only a counter inside |

The XDJ-AZ status packet is 292 bytes. Field offsets are verified against real
captures from the unit. Whether the AZ also emits Absolute Position packets is
**unconfirmed** — each deck's live state exposes `absolute_packets` and
`position_source` (`exact` vs `beat_grid`) so a capture or a running session
shows it immediately. `python probe.py` also names type `0x0b` fields.

#### The playhead

Position is a continuous model (base position, base time, speed). When Absolute
Position packets arrive they become the source of truth (~30 Hz playhead in ms).
Otherwise the model is corrected from the beat grid: 20 % of the error per
observation, re-placed outright only past 400 ms (cue / seek rather than noise).

Each beat's instant comes from the **beat packet**, which arrives on the beat; the
status packet lags it by a variable 2-50 ms. In passive mode the timestamp is
`frame.time_epoch`, tshark's capture time, since tshark delivers its output in
batches and read time says nothing about arrival time.

Measured on the real stream (beat-grid path): ±1.3 ms.

The scrolling detail waveform keeps that needle fixed and moves the wave under
it. Players with a **Waveform Current Position** setting report it in the CDJ
status packet when settings block 1 is present (`12 34 56 78` at offset `0xD0`;
byte `0xDD` is `01` centre or `02` left; byte `0xDA` is the waveform colour).
CDJ-3000 defaults to Centre. XDJ-AZ and Opus Quad default to Left. Older players
(CDJ-2000NXS2, XDJ-XZ) have no setting and are always centred. The desktop
Monitor view chooses the playhead: **Auto** (follow the player, with that model
fallback when the byte is missing), **Centre**, or **Left**. The web panel
follows that choice. Left places the needle at 25% of the strip width. Whether
the XDJ-AZ actually sends
the settings block is unverified — Auto still uses the Left fallback, and
`python probe.py` names bytes `0xDA` and `0xDD` so toggling the setting on the
player shows up. The OBS overlay wave is a full-track overview with a moving
needle, so this control does not apply to it.

### Metadata and waveforms — NFS

The player exports the inserted USB/SD over **NFS v2 on UDP**. That is where
these come from:

- `PIONEER/rekordbox/export.pdb` — the rekordbox database: tracks, artists, albums,
  genres, keys, labels and artwork paths.
- `PIONEER/rekordbox/exportLibrary.db` — optional **OneLibrary / Device Library
  Plus** (XDJ-AZ and friends). Encrypted SQLCipher; playlists and history created
  on the player itself only live here. Classic `export.pdb` is still written by
  rekordbox and remains the default for live deck metadata. To read OneLibrary::

      pip install "pyrekordbox @ git+https://github.com/dylanljones/pyrekordbox.git@master"

  The Library page then shows playlist / history counts when the file is present
  and lets you browse playlists / history / USB tracks when `pyrekordbox` can
  open the DB. Classic `export.pdb` search works without that dependency.
  and readable.
- `PIONEER/USBANLZ/.../ANLZ0000.DAT` / `.EXT` / `.2EX` — per-track analysis: beat
  grid, cues, phrases and the waveforms.
- `PIONEER/Artwork/...` — the artwork itself.

This route needs no device number, unlike the dbserver protocol: an XDJ-AZ
occupies all four player numbers.

Cached in `~/.prolink-cache/<ip>/`.

### Waveforms

Detail waveforms follow [Beat Link `WaveformDetail`](https://deepsymmetry.org/beatlink/apidocs/org/deepsymmetry/beatlink/data/WaveformDetail.html):

| Style | Tag | How a column is read |
|---|---|---|
| RGB | `PWV5` | 16-bit word. Height is bits 2-6. Colour is bits 13-15 red, 7-9 green, 10-12 blue, each scaled by 255/7. |
| 3-Band | `PWV7` | 3 bytes, ordered mid, high, low. Heights are `round(low*0.4)`, `round(mid*0.3)`, `round(high*0.06)`, drawn as separate bands. |
| Blue | `PWV3` | Height is bits 0-4. Bits 5-7 pick a shade from `WaveformDetail.COLOR_MAP`. |

The scrolling overview is the detail waveform reduced by peak, not `PWV4`.

Drawing takes the peak of **every** column falling inside a pixel. At 150 columns
per second there are usually more columns than pixels, and sampling just one of them
drops transients.

---

## The mixer channels

The mixer reports an **ON AIR** flag per channel, computed by the mixer section and
carried in each deck's status packet (bit 0x08 of the flags, byte 0x89). It tracks
the channel fader.

Verified on the unit with `probe.py`, closing the faders:

```
dev 3  byte 0x89: 0xBC -> 0xB4     (base+on air+sync+master  ->  base+sync+master)
dev 1  byte 0x89: 0x9C -> 0x94     (base+on air+sync         ->  base+sync)
dev 2  byte 0x89: 0x9C -> 0x94
dev 4  byte 0x89: 0x9C -> 0x94
```

The same state is duplicated in field **0x26-0x27**: `0x0100` open, `0x0000`
closed. The reference documentation describes that field as "activity" (0 idle, 1
playing), which does not hold here: two decks measured `0x0100` and two `0x0000`
with all four tracks stopped.

The panel shows this with an `ON AIR` tag, a `CHANNEL CLOSED` tag, and by dimming
decks whose channel is closed.

**The position of faders, volumes and EQ is not transmitted.** Moving the channel
faders up and down with the probe watching 1548 of 1548 bytes, unfiltered, changed
that one bit and nothing else.

---

## Layout

```
desktop.py          PySide6 desktop app launcher
gui/                desktop shell (sidebar, pages, settings, waveforms)
app.py              web server and API
monitor.py          console monitor
probe.py            protocol probe: reports bytes that change
web/index.html      the panel
web/overlay.html    transparent OBS Browser Source overlay
doc/                README screenshots
requirements.txt    desktop dependency (PySide6)
prolink/
  proto.py          Pro DJ Link packets (keep-alive, beat, status)
  link.py           engine: virtual device, passive capture, deck state
  nfs.py            ONC-RPC / MOUNT / NFS v2 client
  pdb.py            export.pdb parser
  anlz.py           analysis file parser (waveforms, beats, cues)
  library.py        ties NFS + database + analysis together, with caching
  mixstatus.py      SmartTiming now-playing / setlist (prolink-connect style)
  session.py        realtime session playlist recorder (bars helpers too)
```

### API

| Route | Returns |
|---|---|
| `/` | live monitor panel |
| `/overlay` | transparent OBS overlay |
| `/api/state` | full state as JSON (includes `now_playing` / `setlist` / `session`) |
| `/api/setlist` | SmartTiming mix status and setlist only |
| `/api/session` | session playlist recorder state (set clock) |
| `/api/session/start` | start recording (first track → `00:00:00`) |
| `/api/session/stop` | stop recording (keep the list) |
| `/api/session/clear` | clear the session playlist |
| `/api/library/tracks?q=` | search/filter `export.pdb` tracks |
| `/api/library/playlists` | OneLibrary playlists + history lists |
| `/api/library/playlist/<id>` | tracks in a OneLibrary playlist/history |
| `/api/library/loaded` | tracks currently on decks |
| `/api/events` | the same state over SSE, 60 times a second |
| `/api/track/<id>` | metadata, beat grid, cues and phrases |
| `/api/waveform/<id>` | waveforms in binary: detail and overview |
| `/api/artwork/<id>` | artwork as JPEG |

`/api/waveform/<id>` format: two blocks back to back (detail then overview), each
with a 24-byte little-endian header — `PLWF`, version, column count, columns per
second (float), duration in ms, flags — followed by the heights (1 byte, 0-31) and
the RGB (3 bytes per column). Optional `PLWB` / `PLBC` blocks after that carry
3-band and blue lanes for the style switch.

State is sent with **stable keys**, not display text (`playing`, `cued`, `usb`...);
the interface supplies the English labels.

---

## Troubleshooting

**`PermissionError: [WinError 10013]` when starting the web panel.** Windows has
blocked the HTTP port (often Hyper-V / WSL reserved ranges, or something already
listening). The app will try a few alternate ports automatically; to pick one
yourself:

```bash
python app.py --port 18777
```

Or set **Web server port** in the desktop Settings. Check reserved ranges with
`netsh interface ipv4 show excludedportrange protocol=tcp`.

**"could not open UDP port 50000".** rekordbox is running. Close it, or use
`--mode sniffer`.

**No decks show up in virtual device mode.** Check the player is powered on and on
the same subnet, and that the number you are using (`--number`) is not already taken.
Devices seen are listed in the panel header even before their status arrives.

**No decks show up in passive mode.** This mode only sees status while another
program is linked to the player. With rekordbox closed, use virtual device mode.

**"could not find interface".** Pass it by hand: `--iface` with the number `tshark -D`
lists.

**Auto-detection picks the wrong address.** On machines with VMware or Hyper-V
installed there are several virtual adapters. Pass `--host` with the player's
address.

**The track shows but the waveform does not.** The USB drive has to be inserted in
the player and carry rekordbox analysis. The header shows whether the library loaded
and how many tracks it has.

---

## Credits

This repository is a **fork of [fidow/prolink-monitor](https://github.com/fidow/prolink-monitor)**
by [@fidow](https://github.com/fidow). The Pro DJ Link engine, NFS library access,
web panel architecture, console monitor and protocol probe started there.

The protocol research that work builds on:

- [dysentery](https://github.com/Deep-Symmetry/dysentery) and
  [crate-digger](https://github.com/Deep-Symmetry/crate-digger) by Deep Symmetry,
  which document the packets, `export.pdb` and the analysis files.
- [Beat Link](https://github.com/Deep-Symmetry/beat-link) `WaveformDetail` /
  `WaveformFinder` — used here as the reference for RGB, 3-Band and Blue column
  layout.
- The reverse engineering by [@henrybetts](https://github.com/henrybetts) and
  [@flesniak](https://github.com/flesniak) that those projects are built on.

Independent project — not affiliated with or endorsed by AlphaTheta / Pioneer DJ.
