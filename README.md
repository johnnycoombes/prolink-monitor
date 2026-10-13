# prolink-monitor

A live Pro DJ Link monitor. It joins the booth network as a virtual player, reads the library and analysis from the player while the set is running, and draws decks, waveforms, and artwork on this computer. It is read-only toward the player and the USB stick: it never writes a Rekordbox database, never sends library edits, and a packet capture only records what already arrived.

This is a fork of [fidow/prolink-monitor](https://github.com/fidow/prolink-monitor). The desktop app is **Prolink Listener**. The current release is **1.0.8**. This tree is **1.0.9**.

## Contents

- [What's new](#whats-new)
- [Screenshots](#screenshots)
- [Supported hardware](#supported-hardware)
- [Install](#install)
- [Features](#features)
- [HTTP API](#http-api)
- [Troubleshooting](#troubleshooting)
- [Release process](#release-process)
- [Credits](#credits)

## What's new

**1.0.9.** Icons are [Phosphor](https://phosphoricons.com/) SVGs instead of text symbols. The artwork placeholder is a music note, the loop badge uses Repeat, the sidebar button uses List, and ratings are filled and outlined stars. The floating overlay's drag strip uses the Dots Six Vertical grip. The Qt app recolours them to the theme, and the web panel and overlays draw them inline in `currentColor`. ON AIR, MASTER, SYNC, deck numbers, status dots, and the key stay as text.

**1.0.8.** Monitor deck rows measure their text before they draw it, so a short row no longer squashes the title over the artist or stacks the readout on itself. The title stays on one line unless the row is tall enough for two. The artist stays. Album, label, genre, and the other detail lines appear only when they fit. Artwork shrinks with the row. BPM, pitch, and the clock stay readable; tags, the beat line, and the next cue drop from the bottom when the height runs out. An empty deck is a deck number and a small “no track”. The web panel already packed its rows and is unchanged.

**1.0.7.** The Monitor and the web panel use the spare height in a deck row. Title and artist scale with the row. When there is room, the left side adds album, label, genre, year, key, rating, colour, comment, and a played bar, and the right side adds master/sync, loop, the beat and phrase, and the next cue. Rekordbox `(blank)` is not drawn. **File > Exit**, tray Quit, and a close that is set to quit stop the link sockets, the web server, the library workers, and the process.

**1.0.6.** The loop highlight on the overlay and the web panel stays on the playhead. While a loop is active the position is folded into the loop, and the wave scrolls by the device pixels it draws, so the green region does not drift at 125% and 150%. The desktop Monitor uses that same scroll. Checked with a 32-beat loop at 123.2 BPM.

**1.0.5.** Bar (`style=bar`) draws the scrolling detail wave. Panel and Bar are both that full-width strip. The spectrum on Bar still follows the spectrum switch (`spectrum=0` hides it). Panel keeps the visualiser unless `spectrum=0`.

**1.0.4.** Artwork is requested as soon as a track id is known, and a successful dbserver or NFS lookup sets `has_artwork` before the waveform arrives. If the saved web port is inside a Windows excluded range, the log and Health name that range and the server uses the next fallback. Panel is the strip with the larger cover and the visualiser. Bar is the strip with the small cover.

**1.0.3.** One bad OneLibrary date no longer blanks the library. The Library page shows the same live dbserver source as Health. Track columns keep title, artist, album, key, and BPM, and the rows are zebra-striped. `style=bar` is its own layout. Overlay URLs can lock the spectrum with `spectrum=1`. A dbserver session that never connects is not counted as a reconnect.

**1.0.2.** The Windows zip includes pyrekordbox. The Library reads the player’s dbserver first. The log rotates. The floating window has a visible drag strip.

**1.0.1.** The web server stays up across a reconnect. A second launch brings the first window forward instead of starting another process.

## Screenshots

![Monitor](doc/monitor.png)

![Library](doc/library.png)

![Web panel](doc/web-panel.png)

![Overlay card](doc/overlay-card.png)

![Overlay panel](doc/overlay-panel.png)

![Overlay bar](doc/overlay-bar.png)

![Overlay settings](doc/overlay-settings.png)

## Supported hardware

Field offsets, NFS, and waveform decoding were checked against an **AlphaTheta XDJ-AZ, firmware 1.30**, on Windows 11. That is the only player the original work was verified on. The desktop shell has also been run on Linux without live gear. Anything else below is what the code expects from public packet layouts, and is marked when this project has not confirmed it on hardware.

**XDJ-AZ**

- The AZ uses player numbers 1–4 and 33 (the mixer section). Rekordbox uses 17. This app announces as device **5** by default.
- **2-deck and 4-deck.** While Utility → PRO DJ LINK is on, the AZ sends a Channels On-Air packet (type `0x03` on UDP 50001). Health calls that **Pro DJ Link**. When that packet goes quiet for more than a second, Health calls it **4-deck**. With no AZ on the network the line stays blank. The Monitor page only has those two layouts: decks **1–2**, or **3–1–2–4**.
- **Source slots** in the status packet: 0 empty, 1 CD, 2 SD, 3 USB, 4 rekordbox, 6 Streaming Direct Play, 7 USB 2 (AZ 4-deck), 9 Beatport LINK. Bytes 5 and 8 are only documented as a streaming service. They are not labelled Apple Music, TIDAL, or SoundCloud.
- **OneLibrary.** The status track id is the rekordbox id. `export.pdb` is read first because that row holds the ANLZ path. `exportLibrary.db` is used when it has the same id. A readable OneLibrary file with no content rows must not hide the `export.pdb` row (that used to blank every waveform on the AZ). A title match can choose between the two when they disagree. The Library reads the player’s dbserver first, as soon as the player address is known, including while `export.pdb` is still loading. That session asks the port-discovery service on TCP **12523** for the dbserver port, then connects to the port the player returns (it is often not 1051). The client number is the virtual device this app announced (default **5**), so it does not take deck 3 on an AZ that already uses 1–4. If the decks have not announced yet, the app still opens that TCP session and targets deck 1 until a player number arrives. The NFS cache is the fallback. A bad `export.pdb` does not drop the mount or skip OneLibrary. Nothing is written back. The Windows zip includes `pyrekordbox`, `sqlcipher3`, and `sqlite3` as real files under `_internal`. A source checkout still installs OneLibrary from `requirements-onelibrary.txt`.
- **Unverified on the AZ:** Absolute Position packets (type `0x0b`). Health shows `absolute_packets` and whether the playhead is using Absolute Position or the beat grid, so a capture answers it. Some AZ builds put track length in milliseconds in that packet; CDJ-3000 uses seconds. Also unverified: the settings block at `0xD0` (waveform colour at `0xDA`, playhead at `0xDD`). Auto playhead still falls back to **Left** for an AZ (and for Opus Quad) when that byte is missing.

**CDJ-3000 family**

- Absolute Position is the documented precise playhead: about every 30 ms on UDP 50001, type `0x0b`. This project has not re-checked that on a CDJ-3000 in the booth.
- The factory playhead default in the code is **Centre**. CDJ-2000NXS2 and XDJ-XZ have no such setting and are always centred.
- Long status packets (512 bytes) carry the loop that is actually playing. Shorter packets leave the loop fields at zero.
- The sequence counter at bytes `0xC8`–`0xCB` usually stays zero, so Health’s sequence-hole column stays blank until that counter is seen stepping by one.

Two other choices were written for separate players and are untested on a multi-CDJ booth: the medium is read from the player the track says it was loaded from, and the NFS file-name encoding is detected (the AZ uses UTF-16LE).

## Install

The computer and the player need to be on the same network. The monitor core (`app.py`, `monitor.py`, `probe.py`) needs no extra Python packages. The desktop app, the updater, and a few optional readers do.

### Windows, from the GitHub Release zip

You do not need Python for this path.

1. Open the [Releases](https://github.com/johnnycoombes/prolink-monitor/releases) page and download `ProlinkListener-<version>-win64.zip` from a versioned release (the current one is `v1.0.8`). Leave the release named `updates` alone. That one is signed metadata for the updater, not an installer.
2. Extract the zip to a folder you will keep, for example `C:\Users\User\Prolink Listener`. Do not run the program from inside the zip.
3. Double-click `ProlinkListener.exe`.
4. If Windows asks whether the app may use private networks, allow it. A phone on the booth Wi-Fi needs that to open the panel.
5. Settings stay in `C:\Users\<you>\.prolink-monitor\settings.json`. The USB library cache stays in `C:\Users\<you>\.prolink-cache`. A rotating log of the library source, counts, and errors is `C:\Users\<you>\.prolink-monitor\prolink-monitor.log`. The first launch does not copy or reset either folder. The zip includes `pyrekordbox`, so OneLibrary opens without a separate install.

Npcap is only needed when rekordbox is open. See [Troubleshooting](#troubleshooting).

### Updates

A couple of seconds after the Windows app opens, it looks for a newer **signed** release. The packaged app then asks:

> Version X is available. This copy is Y. Install it now? Prolink Listener will close and open again. Your settings stay in the .prolink-monitor folder and the library cache stays in .prolink-cache.

**Yes** downloads that build, replaces the app files, closes, and opens again. **No** stays on the version you have. Settings and the cache are not part of the update.

To turn the check off: **Settings → Desktop**, clear **Check for updates when the app starts**, then Save.

The check trusts only updates signed to match `root.json` inside the app ([tufup](https://github.com/dennisvang/tufup)). A zip somebody else uploaded is ignored. If GitHub cannot be reached, the app starts as usual and writes a line to `.prolink-monitor\update.log`.

If you started with `python desktop.py`, the same check runs, but it will not overwrite the checkout. **Yes** on that dialog only opens the Releases page.

### One copy at a time

A second launch of `ProlinkListener.exe` (and of `python desktop.py`) does not start a second process. It talks to `127.0.0.1:47331` and asks the first copy to come forward, including when that window was hidden by close-to-tray or minimize-to-tray. Extra flags on the second launch:

| Flag | What the running copy does |
|---|---|
| (none) | Show the window |
| `--open-panel` or `--open-web` | Show the window and open the web panel |
| `--open-overlay` | Show the window and open the overlay |

If that port is busy and nobody answers, the app starts anyway.

While connected, closing or minimizing the window hides it to the tray (both are on by default). Quit from the tray menu when you want the process to exit.

### Run from source

Python **3.10 or newer**. Continuous tests use 3.12 and 3.14. The Windows package is built with 3.14. Current PySide6 wheels allow 3.10 up to, but not including, 3.15.

```bash
pip install -r requirements.txt
python desktop.py
```

`requirements.txt` is PySide6, PySide6-Addons, and Pillow. `python -m gui` is the same window.

Optional, only when you need them:

```bash
pip install "tufup>=0.10,<0.11"          # the update check
pip install "pyrekordbox @ git+https://github.com/dylanljones/pyrekordbox.git@master"
                                         # OneLibrary / exportLibrary.db
pip install soundcard                    # overlay bar visualiser from system audio
```

Tests also need `pytest` and `packaging`.

Other entry points:

| Command | What it is |
|---|---|
| `python app.py` | Web panel and HTTP API, no desktop window |
| `python monitor.py` | Console monitor |
| `python probe.py` | Print status bytes that change |
| `python probe.py --replay file.plc` | Replay a Health capture. Sends nothing |

`app.py` flags: `--host`, `--mode vcdj|sniffer`, `--number` (default 5), `--port` (default 8777), `--http-host` (default `0.0.0.0`), `--iface`, `--no-open`.

## Features

The sidebar is **Monitor**, **Devices**, **Health**, **Library**, **Session**, **Overlay**, **Settings**, and **About**. Hide it with **☰**, **Hide sidebar**, or **Ctrl+B**.

| Shortcut | Action |
|---|---|
| `Ctrl+R` | Start or stop session recording |
| `+` / `-` | Zoom the waveform (fewer or more bars) |
| `Ctrl+B` | Sidebar |
| `Ctrl+O` | Open the overlay URL |

### Monitor

![Monitor](doc/monitor.png)

Each deck card can show artwork, title, artist, album / genre / label, MASTER / SYNC / ON AIR, the waveform, BPM, tempo (±%), elapsed and remaining time, key, and play state. Hidden columns give the wave more room. Empty decks can be hidden. The page only lays out **2** or **4** decks.

**Waveforms.** Three colour styles, the same ones a CDJ draws:

| Style | Analysis tag | How a column is read |
|---|---|---|
| RGB | `PWV5` | Height in bits 2–6. Colour in bits 13–15 red, 7–9 green, 10–12 blue. |
| 3-band | `PWV7` | Three bytes, ordered **mid, high, low**. Drawn as separate bands. |
| Blue | `PWV3` | Height in bits 0–4. Bits 5–7 pick a shade. |

The overview is the detail wave reduced by peak, not `PWV4`. Zoom is in **4/4 bars** (1, 2, 4, 8, or 16). Each deck can keep its own zoom.

**Phrase strip.** Rekordbox phrase names (intro, verse, chorus, and the rest) sit under the overview when **Phrase strip under waveform** is on.

**Vocal lane.** An optional strip under the phrases. A column counts as vocal when the mid band is above the PWVC threshold and the low and high bands stay under theirs. The preview wave is PWV6, with PWV7 as the fallback. On by default (`show_vocals`).

**Beat grid and cues.** Memory cues and hot cues A–H are drawn on the overview and the detail wave. Hover a wave for a time; click to pin a cue time.

**Playhead.** **Auto** follows the player. **Centre** puts the needle in the middle. **Left** puts it at a quarter of the strip width. Auto uses Centre for a CDJ-3000 and Left for an XDJ-AZ or Opus Quad when the settings byte is missing. CDJ-2000NXS2 and XDJ-XZ stay centred. The overlay overview wave does not use this control.

**Smoothing** (`playhead_smoothing`, default **Normal**):

- **Direct** snaps to each packet.
- **Normal** moves at most 15% of one packet’s travel when the error is 80 ms or more, and 8% when it is smaller.
- **Strong** uses 8% and 4%.
- After a second with no packet the needle holds. A seek, pause, reverse, loop wrap, or track change snaps immediately. A jump past 400 ms is treated as a seek.

The window paints at 60 Hz by default. Metadata refreshes more slowly. See [doc/PERFORMANCE.md](doc/PERFORMANCE.md).

**NEXT countdown.** `NEXT: <name> in m:ss` for the next hot cue, memory cue, or phrase change after the playhead. A hot cue with no name is `CUE A` (and so on). A memory cue with no name is `MEMORY`. Loop cues are skipped. The text is amber at 10 seconds or less (`#ff9f45`) and red at 3 seconds or less (`#ff3b30`). On by default (`show_next_cue`). A seek picks a new target immediately.

**Artwork.** The card uses a large image. The panel uses a larger cover than the bar (and the spectrum visualiser unless `spectrum=0`). The bar uses a small image. The page requests `/api/artwork/<id>` as soon as a track id is known. If that request fails, the placeholder is shown. Large art is tried in this order, and stops at the first image: a cover embedded in a local music mirror, dbserver high-res, the NFS `_m` thumbnail, dbserver standard, the NFS thumbnail, then an embedded cover read from the audio header only (ID3, FLAC, or MP4). The audio file is not downloaded. The player and the USB stay read-only. A successful dbserver or NFS lookup sets `has_artwork` immediately, before the waveform metadata arrives.

**Session.** **Record session** (also the Session page, and `Ctrl+R`) keeps a chronological list of on-air tracks. The set clock starts at `00:00:00` and pauses when nothing is on air. Export CSV, JSON, or M3U. Auto-save writes all three on Stop, under `~/.prolink-monitor/sessions` unless you set another folder.

### Library

![Library](doc/library.png)

Search the USB tracks, browse OneLibrary playlists and history when `pyrekordbox` can open `exportLibrary.db`, and see artwork for tracks that are on decks. Classic `export.pdb` search works without that package. The page says when OneLibrary is present but not readable yet. The Windows zip bundles `pyrekordbox`. A source checkout still uses the optional install below.

Live menus read the player’s dbserver first. The NFS copy is the fallback. Health’s library line includes the exact reason when that session does not open (port discovery, client number, or a rejected setup). A setup that never connects is not counted as a reconnect. A bad date on one OneLibrary row is left blank; the rest of the table still loads. A `pyrekordbox` deprecation warning (the old `db6` name) is not a library error. `db6` is imported only when `masterdb` is missing. A bad `export.pdb` is reported and the rest of the load continues.

### Health

- Packet rates, per deck, and whether position is coming from Absolute Position or the beat grid.
- **XDJ-AZ** mode: Pro DJ Link or 4-deck, as above.
- **Link timing** over the last 30 seconds. A gap longer than 400 ms counts as late. Sequence holes stay blank on CDJ-3000-class players that leave the counter at zero.
- **ANLZ path** for the track on each deck, and which library row it came from.
- **Library source** and the exact dbserver failure when the live session is down. The source is live dbserver, the NFS cache, or not connected.
- **dbserver reconnects** and the last error. A dropped remotedb socket is retried with a short backoff and a fresh handshake. A session that never opens stays at 0 reconnects and still shows the error. That retry does not restart the web server. Nothing is written to the player.
- The same library line is appended to `~/.prolink-monitor/prolink-monitor.log` (or `%USERPROFILE%\.prolink-monitor\prolink-monitor.log` on Windows). The file rotates at about 1 MB and keeps three backups.
- NFS round-trip for mounted media.
- **What’s Now Playing** status.
- **Web server:** listening or not, client count, thread count, and the last error. **Restart web server** binds the same port again. Use it if the panel goes quiet after a reconnect. See [Troubleshooting](#troubleshooting).
- **Capture.** A 5–60 second listen-only recording, saved as a `.plc` file under `~/.prolink-monitor/captures`. It sends nothing. Replay with `python probe.py --replay path.plc`.

### Web panel

![Web panel](doc/web-panel.png)

**Open web panel** (or `python app.py`) opens the panel in a normal browser tab. It mirrors the Monitor view: decks, waveform, phrases, vocal lane, playhead, zoom, and the countdown. Those controls stay in the desktop app. The page has no library pill and no settings. A fullscreen button on the page works only after you click it.

The port saved in Settings defaults to **8777**. The server tries that port first. If it cannot bind it, it listens on **8787**, then 8877, 9777, 18777, then any free port. Health and the log name the port that listened. On a PC where 8777 cannot be bound, open **8787**:

```
http://127.0.0.1:8787/
```

`?readonly=1` hides Record. The server listens on `0.0.0.0` by default, so a phone on the same Wi-Fi can open `http://<this-PC>:8787/` when that is the port that listened. Turn LAN access off under **Allow phones on the same Wi-Fi**. The examples below use 8787 for that case. If 8777 did bind, use 8777 instead. Overlay, panel, and floating-window links in the app use the port that actually listened.

### OBS overlay

With the web server running, add a Browser source. Use the port that listened (8787 when 8777 did not bind):

```
http://127.0.0.1:8787/overlay
```

The page background is transparent. Now Playing has three styles.

![Overlay card](doc/overlay-card.png)

![Overlay panel](doc/overlay-panel.png)

![Overlay bar](doc/overlay-bar.png)

- **Card** (`style=card`, the default): tall card, high-res cover, overview wave. Corners are `tl`, `tr`, `bl`, `br`.
- **Panel** (`style=panel`): full-width strip, larger cover, scrolling detail wave, and the spectrum visualiser unless `spectrum=0`. `pos=bottom` or `pos=top`.
- **Bar** (`style=bar`): the same full-width strip with a small cover and the same scrolling detail wave. The spectrum follows the spectrum switch: on unless the URL says `spectrum=0`. `pos=bottom` or `pos=top`.

**Spectrum visualiser.** On by default (`overlay_show_spectrum`). Panel keeps it unless the URL says `spectrum=0`. Bar uses that same switch (`showSpectrum` in the page): `spectrum=0` hides it, and anything else shows it. Pro DJ Link has no audio, so the bars follow the analysed low / mid / high wave at the playhead and decay while paused. **Bar visualiser: use system audio** (`overlay_spectrum_audio`) is off until you turn it on, and then needs the optional `soundcard` package (WASAPI loopback). That checkbox picks the input. It is not the on/off switch.

The **Overlay** page builds the URL. Copy it into OBS and size the source to the canvas (for example 1920×1080).

**Floating window.** The same page also opens in a frameless always-on-top window. Drag the strip along the top. It is a dark bar with a grip, brightens when the pointer is on it, and stays visible when the window is transparent. **Alt+drag** moves the window from anywhere on the page, including the waveform. The card remembers its own place. Panel and Bar share the wide window’s saved place.

| Param | Values | Default | Meaning |
|---|---|---|---|
| `layout` | `nowplaying`, `dual`, `minimal`, `setlist` | `nowplaying` | `setlist` is now-playing plus a scrolling history |
| `style` | `card`, `panel`, `bar` | `card` | Now Playing only. Panel is the large-art strip with the visualiser on unless `spectrum=0`. Bar is the small-art strip. |
| `pos` | card: `tl` `tr` `bl` `br`; panel and bar: `bottom` `top` | `bl` or `bottom` | Where that style sits |
| `corner` | `bl`, `br`, `tl`, `tr`, `center` | `bl` | Other layouts |
| `decks` | `1`–`4` | `1` (`2` for dual) | How many decks |
| `wave` | `rgb`, `3band`, `blue` | `rgb` | Overlay wave colour |
| `scale` or `font` | `0.5`–`2.5` | `1` | Size |
| `accent` | 6-digit hex | — | Accent colour, for example `ff6b61` |
| `tags` | `1` / `0` | on | MASTER / SYNC / ON AIR |
| `bpm` | `1` / `0` | on | BPM |
| `next` | `1` / `0` | on | MixStatus “about to take over” lower third |
| `playing` | `1` / `0` | on | Only decks that have a track |
| `mix` | `1` / `0` | on | SmartTiming now-playing on single-deck layouts |
| `preview` | `1` | off | Dark background for a local look |
| `float` | `1` | off | Used by the floating window |
| `embed` | `1` | off | Tighter frame |

With `mix=1`, now-playing, minimal, and setlist follow the audience track: playing and on air for 128 beats (about two phrases), with short drop-outs ignored. That rule is the SmartTiming port from prolink-connect. Dual layout shows the chosen decks across the window and does not follow that single audience pick. The cue countdown follows the desktop **Next cue countdown** setting, the same as Monitor. `next=0` hides the MixStatus lower third only.

```
http://127.0.0.1:8787/overlay?layout=nowplaying&style=panel&pos=bottom
http://127.0.0.1:8787/overlay?layout=nowplaying&style=bar&pos=bottom&spectrum=1
http://127.0.0.1:8787/overlay?layout=nowplaying&style=bar&pos=bottom&spectrum=0
http://127.0.0.1:8787/overlay?layout=dual&corner=tl&decks=4&playing=0
http://127.0.0.1:8787/overlay?layout=setlist&corner=bl
```

### Floating window

A topmost window of the same overlay page. Transparent by default. It reloads after the web server comes back, so a reconnect does not leave a blank frame. The card remembers its own place. Panel and Bar share the wide window’s saved place.

### What’s Now Playing

Off until you enable it. Settings → What’s Now Playing sends the audience track (the master on-air deck) to [What’s Now Playing](https://github.com/whatsnowplaying/whats-now-playing) once per change. Host `localhost`, port **8899**, optional shared secret. No cover art is sent: that app’s remote input drops image data. If What’s Now Playing is closed, the listener keeps running and Health shows the error. **Test** checks the connection. Nothing is written to the player.

In What’s Now Playing: **Core Settings → Source → Remote**, and leave **Output & Display → Web Server** enabled.

### Settings

![Overlay settings](doc/overlay-settings.png)

Saved at `~/.prolink-monitor/settings.json`. On Windows that is `C:\Users\<you>\.prolink-monitor\settings.json`. `PROLINK_CONFIG_DIR` overrides the folder. The USB cache is `~/.prolink-cache`.

| Key | Default | What it does |
|---|---|---|
| `mode` | `auto` | `auto`, `vcdj` (virtual device), or `sniffer` |
| `host` | empty | Player address. Empty means auto-detect |
| `number` | `5` | Virtual device number |
| `name` | `monitor` | Name announced on the link |
| `iface` | empty | Capture interface for sniffer mode |
| `port` | `8777` | Web server port. Tried first. If it cannot bind, the server uses 8787, then 8877, 9777, 18777, then any free port. |
| `http_bind` | `0.0.0.0` | `127.0.0.1` keeps the panel on this PC |
| `start_web_server` | on | Start the panel with the app |
| `auto_connect` | on | Connect when the window opens |
| `max_decks` | `4` | 2 or 4 |
| `waveform_style` | `rgb` | `rgb`, `3band`, or `blue` |
| `zoom_bars` | `4` | Bars in the detail wave |
| `show_phrases` | on | Phrase strip |
| `show_vocals` | on | Vocal lane |
| `show_next_cue` | on | NEXT countdown |
| `playhead_position` | `auto` | `auto`, `centre`, or `left` |
| `playhead_smoothing` | `normal` | `direct`, `normal`, or `strong` |
| `poll_hz` | `60` | Playhead refresh |
| `show_empty_decks` | on | Keep unused deck slots |
| `deck_show_*` | on | Per-element visibility on the Monitor card |
| `local_music_root` | empty | Optional mirror used for embedded covers |
| `overlay_layout` | `nowplaying` | Overlay layout |
| `overlay_now_style` | `card` | `card`, `panel`, or `bar` |
| `overlay_now_pos` | `bl` | Card corner, or `bottom` / `top` for panel and bar |
| `overlay_show_spectrum` | on | Spectrum visualiser. Panel keeps it unless `spectrum=0`. Bar follows the same switch. |
| `overlay_spectrum_audio` | off | Drive that visualiser from system audio |
| `overlay_playing_only` | on | Overlay `playing` |
| `overlay_mix` | on | SmartTiming now-playing |
| `overlay_show_tags` / `overlay_show_bpm` / `overlay_show_next` | on | Overlay tags, BPM, MixStatus next |
| `floating_now_topmost` | on | Floating window stays above other windows |
| `floating_now_transparent` | on | Transparent floating background |
| `wnp_enabled` | off | What’s Now Playing output |
| `wnp_host` / `wnp_port` / `wnp_secret` | `localhost` / `8899` / empty | Where to send it |
| `check_for_updates` | on | Signed-release check on startup |
| `close_to_tray` / `minimize_to_tray` | on | Hide instead of quitting while connected |
| `session_autosave` | off | Write CSV, JSON, and M3U when recording stops |

**Connect** and **Save & reconnect** stop the link and start it again, which also closes and reopens the web server on the same port. Changing the network interface does that too. A dbserver retry does not, and a player disappearing does not restart the web server by itself. **Health → Restart web server** rebinds the panel without dropping the link.

### How it joins the link

The player sends detailed status by unicast, and only to devices that announced themselves. Beats and keep-alives are broadcast.

| Mode | When | Npcap |
|---|---|---|
| Virtual device (`vcdj`) | rekordbox closed. Announces on UDP 50000 and receives status on 50002. | Not needed |
| Passive capture (`sniffer`) | rekordbox is open and already linked. Reads packets without binding those ports. | Required, with tshark |
| `auto` (default) | Virtual device when port 50002 is free, otherwise sniffer. | Only in the sniffer case |

| UDP port | What arrives |
|---|---|
| 50000 | Keep-alives (who is on the network) |
| 50001 | Beats, and Absolute Position (`0x0b`) on CDJ-3000-class players |
| 50002 | Detailed player status (unicast) |
| 50004 | Mixer clock (`0x20`) |

The mixer’s **ON AIR** flag is bit `0x08` of the flags at byte `0x89` in each deck’s status. It follows the channel fader. Fader position, volume, and EQ are not on the link: moving the faders on an AZ changed that one bit and nothing else. Field `0x26`–`0x27` is not “activity” on the AZ (the documented idle/playing meaning did not match a stopped deck).

Library files come over **NFS v2 on UDP**, via the portmapper (UDP 111) and the mountd / nfs ports the player returns. There is no single fixed NFS port to open. The paths are `PIONEER/rekordbox/export.pdb`, optional `exportLibrary.db`, `PIONEER/USBANLZ/.../ANLZ0000.DAT` (and `.EXT` / `.2EX`), and `PIONEER/Artwork/...`. The dbserver query port is TCP 12523; the player answers with its own dbserver port (often 1051). That session is read-only.

## HTTP API

Default base is `http://127.0.0.1:8777` when that port bound, and `http://127.0.0.1:8787` when the server had to fall back. State uses stable keys (`playing`, `cued`, `usb`), not display text.

| Route | Returns |
|---|---|
| `/` | Web panel |
| `/overlay` | Transparent OBS page |
| `/api/state` | Full JSON, including now-playing, setlist, and session |
| `/api/setlist` | SmartTiming mix status and setlist |
| `/api/session` | Session recorder |
| `/api/session/start`, `/stop`, `/clear` | Start, stop (keep the list), or clear |
| `/api/session/export?fmt=csv\|json\|m3u` | Download the playlist |
| `/api/library/tracks?q=&limit=&offset=` | Search tracks |
| `/api/library/playlists` | OneLibrary playlists and history |
| `/api/library/playlist/<id>?host=&limit=` | Tracks in one playlist |
| `/api/library/loaded` | Tracks currently on decks |
| `/api/events` | Server-sent events: a full snapshot about 15 times a second, and a light playhead frame at 60 Hz |
| `/api/track/<id>` | Metadata, beat grid, cues, phrases. Optional `deck` and `key` |
| `/api/waveform/<id>` | Binary waveforms. Optional `deck` and `key` |
| `/api/artwork/<id>?size=large\|small` | JPEG. Optional `deck` and `key` |

`/api/waveform/<id>` is blocks back to back. Each starts with a 24-byte little-endian header: magic, version, column count, columns per second, duration in ms, flags, then heights and RGB. Magic `PLWF` is the colour wave (detail, then overview). Optional `PLWB` and `PLBC` blocks carry the 3-band and blue lanes.

## Troubleshooting

**Firewall.** Allow UDP **50000–50002** on the DJ network (and UDP 50004 if you care about the mixer clock). Allow TCP on the web port. Settings default to **8777**. If that port cannot be bound, the app listens on **8787**, then 8877, 9777, 18777, then a free port, skipping any of those that sit in a Windows excluded range. The reason 8777 failed (for example an excluded range, WinError 10013, or the port already in use) is written to `~/.prolink-monitor/prolink-monitor.log` and shown on Health, including the range when `netsh` lists one. Overlay, panel, and floating-window URLs use the port that actually listened. Settings keep 8777; the next launch tries it again. When 8777 stays unbound, open **8787**. NFS is not one port: allow UDP **111** (portmapper) and the mountd / nfs ports the player returns. On Windows, accept private-network access for Prolink Listener when the prompt appears.

**`PermissionError: [WinError 10013]` on the web port.** Hyper-V, WSL, or another reservation often owns it. Check with `netsh interface ipv4 show excludedportrange protocol=tcp`. If 8777 is between a Start Port and an End Port, that is why it never listens: the port is excluded, not merely busy, and it will not show up in `netstat`. Health and the log name that range. The app then binds the next fallback outside the range. To choose one: `python app.py --port 18777`, or set **Web server port** in Settings.

**Npcap, only when rekordbox is open.** Virtual device mode needs rekordbox closed, because it binds UDP 50000–50002 and Windows will not share them (`WSAEACCES`, “could not open UDP port 50000”). Close rekordbox, or switch to **Passive capture** and install [Npcap](https://www.wireshark.org) (the Wireshark installer). Point Settings at `tshark` if it is not on `PATH`. Passive mode goes quiet when nothing else is linked to the player.

**The web panel does not load after a reconnect.** Fixed in **1.0.1**: the accept loop used to stop while the port still looked open, so the browser hung and a second copy could not bind. Current builds keep the server up across a reconnect and reload the floating overlay. If a page is still stuck, open **Health** and press **Restart web server**, or Connect again. Do not start a second copy to “get the port back”.

**Two copies.** A second double-click should bring the first window forward, including from the tray. If you still see two `ProlinkListener.exe` processes, quit both from the tray (or Task Manager) and start once. The instance port is `127.0.0.1:47331` and is not the web panel.

**No decks.** The player has to be on and on the same subnet. In virtual device mode the device number must be free (default 5). In passive mode, rekordbox (or another linked program) has to be open. Several virtual adapters (VMware, Hyper-V) can make auto-detect pick the wrong address: set the player address, or `--host`. “could not find interface” means pass `--iface` with a number from `tshark -D`.

**Library errors.** A warning about `pyrekordbox.db6` is not a failed library; the supported import is `masterdb`. One bad date does not blank the track list. If the page is empty, check that a USB stick analysed in rekordbox is mounted on the player, and Health’s library line. That line names the dbserver failure (port discovery on TCP 12523, the client number, or a rejected setup). The same text is in `~/.prolink-monitor/prolink-monitor.log`. The Windows zip includes `pyrekordbox`. A source checkout installs it from `requirements-onelibrary.txt`. Waveforms need the ANLZ files on that stick; Health shows the path it resolved for each deck. A bad `export.pdb` is shown there and does not drop the rest of the library.

**Track title, no waveform.** The stick has to be inserted and carry rekordbox analysis. An empty OneLibrary content table must not be the only row the app trusts; 1.0.x falls back to `export.pdb` for the ANLZ path.

## Release process

Tests run on every pull request and every push to `main`: pytest on Ubuntu and Windows, Python 3.12 and 3.14. The Windows app is a PyInstaller folder build (not one file). Qt WebEngine needs `QtWebEngineProcess.exe` beside the Qt libraries. PyInstaller 6.16 or newer is what builds the 3.14 interpreter used for that package.

### Cut a release

1. Set `__version__` in `prolink/__init__.py` to the new number, for example `1.1.0`. Merge that to `main`.
2. Tag that commit and push the tag. The tag is the version with a leading `v`:

   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```

3. The **Windows release** workflow builds `ProlinkListener-<version>-win64.zip` and attaches it to the GitHub Release. The text after `v` has to match `__version__`, or the build stops.
4. If the signing secrets below are set, the same workflow publishes signed update files on a release named `updates`. If they are not set, it prints a note and still publishes the zip.

Pull requests also run the Windows build and upload the zip as an Actions artifact. Once signing is on, install the copy from the GitHub Release, not a pull-request artifact. The workflow’s tag-only jobs do not run on a pull request. That is expected.

### Signing secrets

Private keys are never committed.

1. From the repository root:

   ```bash
   pip install "tufup>=0.10,<0.11"
   python scripts/tuf_init_keys.py
   ```

2. Keep the password. The script writes `packaging/tuf/keystore/` and `packaging/tuf/tuf-keys.zip` (both gitignored) and `packaging/tuf/root.json` (commit this and push it). The Windows build copies `root.json` into the app.
3. GitHub → **Settings → Secrets and variables → Actions**:
   - `TUFUP_KEYS_B64`: base64 of `packaging/tuf/tuf-keys.zip`
   - `TUFUP_KEY_PASSWORD`: the password from step 2

   PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("packaging\tuf\tuf-keys.zip")) | Set-Clipboard`

   bash: `base64 -w 0 packaging/tuf/tuf-keys.zip`

4. Tag a release only after `root.json` is on `main` and both secrets exist.

There are four TUF roles: `root`, `targets`, `snapshot`, and `timestamp`. A signature stays valid for 90 days. Root lasts a year. Do not regenerate the keys after people are running a build that contains the old `root.json`. Those copies ignore updates signed by a different key.

The `updates` release holds `timestamp.json`, `snapshot.json`, `targets.json`, `root.json`, and `prolink-listener-<version>.tar.gz`. Do not install that tar.gz by hand.

### Re-publish a zip that is already released

Use this when the versioned zip is already on GitHub and only the signed metadata is missing. Do not regenerate the keys, and do not push the tag again.

1. **Actions → Windows release → Run workflow**.
2. Branch: `main`.
3. Tag: the existing release, for example `v1.0.1`.
4. Run it. The job is **Sign existing release**. It downloads `ProlinkListener-<version>-win64.zip` from that release and uploads signed files to `updates`. It does not rebuild the app.

## Credits

Fork of [fidow/prolink-monitor](https://github.com/fidow/prolink-monitor) by [@fidow](https://github.com/fidow). The Pro DJ Link engine, NFS library access, web panel, console monitor, and protocol probe started there.

Ideas and small pieces adapted under their own licences:

- [chrisle/alphatheta-connect](https://github.com/chrisle/alphatheta-connect) (MIT). The dbserver reconnect backoff, the header-only NFS artwork read (`prolink/art_header.py`), and the PWVC vocal test. Our 3-band columns are ordered mid, high, low, which is not that project’s low, mid, high order. The retry loop and the vocal drawing are written here.
- [evanpurkhiser/prolink-connect](https://github.com/evanpurkhiser/prolink-connect) (MIT). SmartTiming now-playing and setlist rules in `prolink/mixstatus.py`.
- [dennisvang/tufup](https://github.com/dennisvang/tufup) (MIT) and [The Update Framework](https://theupdateframework.io/). Signed auto-update.
- [soundcard](https://github.com/bastibe/SoundCard) (MIT), optional, for WASAPI loopback on the overlay visualiser.
- [Phosphor Icons](https://github.com/phosphor-icons/core) (MIT). The icons in `assets/icons/`; licence text in [`LICENSES/THIRD_PARTY.md`](LICENSES/THIRD_PARTY.md).
- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) (MIT), for OneLibrary. The Windows zip includes it. A source checkout installs it from `requirements-onelibrary.txt`. SQLAlchemy 2 no longer ships `sqlite+pysqlcipher`, so `prolink/pysqlcipher_dialect.py` is a small adaptation of SQLAlchemy 1.4’s dialect (MIT) that sends `PRAGMA key` before any other statement.

Reference only. No GPL or EPL source is included in this tree:

- [Deep Symmetry](https://github.com/Deep-Symmetry)’s [DJ Link Ecosystem Analysis](https://djl-analysis.deepsymmetry.org/) (from [dysentery](https://github.com/Deep-Symmetry/dysentery) and [crate-digger](https://github.com/Deep-Symmetry/crate-digger)). That write-up is the field reference for the packets, `export.pdb`, and the analysis files, and it should be credited when the research is reused. The code repositories are Eclipse Public License. None of that source is copied here.
- [Beat Link](https://github.com/Deep-Symmetry/beat-link) `WaveformDetail` / `WaveformFinder`, used as the reference for RGB, 3-band, and blue column layout. Beat Link is EPL. This project does not include that source.
- Reverse engineering by [@henrybetts](https://github.com/henrybetts) and [@flesniak](https://github.com/flesniak), which the Deep Symmetry work builds on.
- Other GPL and EPL Pro DJ Link projects were read the same way: to check a field offset or a behaviour, not to vendor the code.

Independent project. Not affiliated with or endorsed by AlphaTheta or Pioneer DJ.

## Layout

```
desktop.py                 Prolink Listener launcher
gui/                       desktop shell
app.py                     web server and API
monitor.py                 console monitor
probe.py                   protocol probe and capture replay
web/index.html             web panel
web/overlay.html           OBS overlay
assets/icons/              Phosphor SVG icons (MIT)
doc/                       screenshots used above
requirements.txt           PySide6, addons, Pillow
updater/                   signed update check (tufup)
packaging/                 PyInstaller spec, root.json, bundle check
prolink/                   link, NFS, pdb, ANLZ, library, artwork, vocals
```
