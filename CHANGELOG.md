# Changelog

All notable changes to Prolink Listener (prolink-monitor). Generated from the per-version notes in the README, which remain there as well. Dates are the tag dates.

## 1.0.24 — unreleased

Web panel on phones and tablets: below 900 px wide, deck cards take their own height and the page scrolls (the BPM block no longer sits on the waveform), decks stack one per row on phones and two per row from 600 px, the waveform gets a fixed readable height, the readouts wrap under it, text is larger, touch screens get finger-sized buttons, and the layout respects notches and home bars (safe-area insets). The desktop layout above 900 px is unchanged. Cleanup: removed unused code — Monitor.artwork_source, the PWV4 colour-overview decoders (decode_color_preview and its numpy/Python versions; the raw PWV4 tag is still read), decode_blue, _track_useful, mutagen_available, ARTWORK_SOURCES, card_corner_to_legacy_pos, NOW_STYLES / PANEL_POSITIONS, build_now_playing_preview_query, LibraryPage.set_backend, the never-read sidebar_compact setting, the unused PORT_MIXER (UDP 50004) and TYPE_MIXER_CLOCK constants, and unused imports. No protocol or XDJ-AZ behaviour changes.

## 1.0.23 — 2026-10-16

Overlay Panel and Bar styles show elapsed / remaining time (the Panel strip never had a time readout). Title-only metadata re-asks are now bounded: the web panel and overlays stop after four tries with growing gaps, and the desktop stops once its backoff is used up, so a track without a detail analysis no longer re-fetches forever. The desktop only refreshes the Monitor when the metadata actually changed. Event streams reuse the desktop UI tick's snapshot instead of building a second one, which leaves more time for the desktop Monitor. Desktop Monitor scrolling is smooth again: the waveform cache is drawn at full screen resolution (sharp at 200% scaling) with a sub-pixel offset instead of whole-pixel steps, the 60 Hz paint timer is precise (Windows coarse timers alternated 16/31 ms frames), and the shown playhead advances evenly between status updates instead of stepping back and forth. Memory cues now show in red in the desktop Monitor, web panel and overlays (a PCO2 colour of 0,0,0 means no colour, so they were drawn black). Stored loops show as a bar along the top, like the CDJ. ProLink, dbserver and NFS code are unchanged, and the XDJ-AZ behaves as before.

## 1.0.22 — 2026-10-16

Hot cue markers are back on every view (Monitor, web panel, overlays). When the dbserver title arrived before the ANLZ files were read (common on the XDJ-AZ while export.pdb is still loading, or after a slow first NFS read), the title-only entry had no cues, and every view cached it as final; the waveform turned up a moment later but the cues never did. `/api/track` now finishes the analysis before answering, the web panel and overlays fetch the metadata again when it is title-only, and the desktop Monitor replaces a title-only entry once the analysis lands. Nothing else changes; the XDJ-AZ, XDJ-XZ and the player/USB stay read-only.

## 1.0.21 — 2026-10-16

Web panel and overlay performance: overlays use a lite event stream (`/api/events?lite=1`) without the Health fields; unchanged frames are skipped (re-sent at least once a second); playhead frames drop from 60 Hz to 30 Hz and are skipped while every deck is stopped; waveform and artwork use ETag revalidation (304) instead of no-store; HTML revalidates with an ETag; text and waveform responses are gzipped; overlays cache their element lookups and only write changed text; the panel reads deck colours once a second instead of every frame; hidden tabs stop painting. ProLink, dbserver and NFS code are unchanged, and the XDJ-AZ behaves as before.

## 1.0.20 — 2026-10-16

XDJ-XZ only (from dysentery v0.2.2 notes; re-implemented): the XZ mixer's tempo-master handoff now treats 0x00 (no master) and 0xff (no handoff) as no handoff; the mixer's always-on synced/playing flags are no longer exposed, only tempo master; mixer BPM shows n/a unless an analysed track is playing on an XZ deck; library reads for an XZ deck fall back to the IP its status came from; and the XZ probe report records status fingerprint bytes (0x8b, 0xcc). Other models, including the XDJ-AZ, are unchanged.

## 1.0.19 — 2026-10-16

XDJ-XZ only (ideas from prodj-beats, MIT; re-implemented): automatic device number tries 3, then 4, then the usual order, never a number in use; the XZ mixer's status (device 33) is decoded for mixer tempo and tempo master in the API and Health; Health no longer treats the XZ's missing master level, meters and Beat FX as missing (on-air is the reported channel state); and a Health warning appears when the XZ announces but no status is decoded within 15 s (wrong adapter, two paths or a number clash). Other models, including the XDJ-AZ, are unchanged.

## 1.0.18 — 2026-10-16

Performance: the UI tick never does network or file I/O (cache-only reads, misses go to a bounded worker that emits track_ready), in-flight dedupe in ensure(), adaptive NFS timeouts (clamp(4×RTT, 0.25–0.5 s) with backoff, bounded worst case), 4 KB serial NFS reads (PROLINK_NFS_READ_SIZE 2–8 KB), numpy waveform decoding (~15–20× faster, byte-identical, pure-Python fallback), bounded LRU caches pinned to on-deck tracks, cached settings, one SSE frame per tick, and a negative cache with backoff so missing waveforms are no longer re-requested at 20 Hz. With 200 ms NFS latency, the median UI tick dropped from 809 ms to ~6 ms.

## 1.0.17 — 2026-10-16

XDJ-XZ connection advice, slot names and duplicate-path warning (from Beat Link Trigger's documentation; reference only, no code copied).

- **LINK port first.** The XZ profile, README and Health now recommend the LINK port over Ethernet with **Virtual device**. **Observe only** over the USB-B cable is labelled experimental and unreliable: the built-in USB network adapter on this hardware family drops out from time to time.
- **Two network paths warning.** When the same device announces from two addresses within 10 s (for example over the USB network adapter and Ethernet at once), Health shows a warning with the tip to disable the USB network adapter.
- **Slot names.** The XZ has two USB ports and no SD: the reported "SD" slot is shown as **USB 1** and "USB" as **USB 2**. On the XDJ-AZ it is the reverse ("USB" = USB 1, "SD" or code 7 = USB 2), which matches what the AZ reports today (USB 1 as "USB"). Labels only: the NFS export is still chosen by the reported slot (`/B/`, `/C/`). Deck state gains `slot_label`.
- **Mixer on-air.** Channels On-Air packets now carry the per-channel flags (1–4, plus 5–6 on six-channel mixers), read from whichever mixer sends them, including device 33 on the XZ and AZ; they are in `/api/state` (`mixer_on_air`) and the probe report.
- **Notes.** Opus Quad: one USB at a time, and its track IDs can drift from `export.pdb`. XZ: cannot be remote controlled (this app is read-only anyway).

## 1.0.16 — 2026-10-16

Health page layout fix, an honest XDJ-AZ mode label, and an optional Intel Mac build.

- **Health is readable again.** In 1.0.15 the top LINK card grew (model, all-in-one and device-number lines), but Qt does not pass the height of word-wrapped labels up through a plain card, so when the window was not tall enough the card was squeezed below its content and the column headers, the `XDJ-AZ` row, the mode label and the new lines drew over each other (clearly at 150 % scaling). The Health page now scrolls instead of squeezing, every card is kept at least as tall as its content at its current width, the overview values wrap, and the deck and NFS tables keep room for four rows at the current font size. Checked offscreen at 560–1100 px wide and 100/150/200 % scale; a layout test fails if any overview line is clipped or overlaps another.
- **XDJ-AZ mode.** 1.0.15 said *4-deck / rekordbox lighting* whenever no Channels On-Air packet had arrived in the last second, even on a live Pro DJ Link session over dbserver (that packet is not always seen, for example away from the mixer port). Now a status packet from AZ deck 1–4 in the last 5 s also means **Pro DJ Link**, the label is **unknown** for the first 5 s, and *4-deck / rekordbox lighting* is shown only after 5 s with neither packet.
- **Intel Mac build is optional.** Pull requests and tagged releases build Windows and Apple Silicon only. The Intel (x86_64) Mac app is built by hand with the new **macOS Intel build** workflow, which can attach it to an existing release. *(Later superseded: the current `Release builds` workflow builds `macos-x86_64` alongside `arm64` on every pull request and tag.)*

## 1.0.15 — 2026-10-15

XDJ-XZ support (untested on hardware yet), a device-model setting, and a safer device number.

- **Device model setting.** Settings → Connection → **Device model**: *Auto-detect* (default) or a fixed model — XDJ-AZ, XDJ-XZ, CDJ-3000, CDJ-3000X, CDJ-2000/NXS/NXS2, CDJ-900/NXS, XDJ-1000/MK2, XDJ-700, XDJ-RX/RX2/RX3, XDJ-RR, Opus Quad, OMNIS-DUO, DJM mixers or rekordbox. CLI: `--model xdj-xz` (see `prolink/models.py` for the keys). See [Device model profiles](docs/protocol.md#model-profiles).
- **All-in-ones on Health.** Units whose decks and mixer share one IP (XDJ-XZ, XDJ-AZ, Opus Quad, XDJ-RX3, OMNIS-DUO) get their own line, e.g. `XDJ-XZ @ 169.254.12.3 (#1, 2, 33)`.
- **Listen before announcing.** The virtual device waits 2.5 s, notes every number other gear announces, and never claims one already in use. The XZ says "go ahead" to any number and never defends its own, so this matters there. **Device number** now goes 0–15; **Auto** (0) picks the first free of 5, 6, 4, 3, 7…15. The default stays 5 and is kept when free, so an XDJ-AZ behaves as before apart from the 2.5 s wait.
- **Observe only (USB link) mode.** Listens on UDP 50000–50002 and sends nothing. It picks the network adapter the XZ's USB-B cable creates (usually `169.254.x.x`), falls back to tshark when another program holds the ports, and Health explains when no status packets arrive. It never opens dbserver sessions; tracks come from NFS only.
- **One library per export.** On a unit with SD and USB on the same IP, each deck's library comes from the export its slot maps to (`/B/` = SD, `/C/` = USB). Units with one export are unchanged.
- **dbserver.** Port discovery (TCP 12523) runs once per IP and is reused for 2 minutes; the client number never matches a deck or mixer number seen on the link; models without dbserver (Opus Quad, XDJ-RR, mixers) are not asked.
- **Ideas from [SuperTimecodeConverter](https://github.com/fiverecords/SuperTimecodeConverter)** (MIT), re-implemented in Python: the dbserver client number never matches a device at the target's IP (an XZ ignores requests claiming to be its own deck), taking the highest free number from 4 down to 1; number-claim packets (stages `0x00`, `0x02`, `0x04`) never count as a player, so no ghost decks; listening sockets bind to `0.0.0.0` (on macOS binding to the interface address silently drops `0x28` beat packets); keep-alives go to each address once.
- **Beat Link review fixes** (ideas re-implemented; no EPL code copied):
  - The Opus Quad reports its decks as 9–12; they are shown as 1–4, and 9–12 are never taken automatically.
  - The virtual device listens for a full 3 s discovery window (CDJs send a keep-alive every ~2 s) before announcing, and moves to another number if a final-stage claim or a "number in use" defence names ours (beat-link #91).
  - XDJ-AZ Pro DJ Link mode vs rekordbox lighting / 4-deck mode (a Channels On-Air packet from the AZ within 1 s) is shown on Health and in the detected model line.
  - Slot code 7 (USB 2) reads from the second USB export.
  - Unanalysed files (track type 2) skip the USB database lookup, which would match the wrong track, and are cached separately. Waveforms and beat grids that are not ready at load are retried after 3, 8 and 20 s.
  - Play state `0x12` (emergency loop) counts as playing; ShowKontrol (`TCS-SHOWKONTROL`) packets are ignored; the keep-alive peer count (byte `0x30`) is parsed.
- The XZ's "does not defend device numbers" note is now marked unconfirmed. Checked against dysentery #75 (CDJ-2000NXS captures): this app does not classify devices by keep-alive byte `0x25`, by status-packet length or by media-slot broadcasts, so none of its corrections needed a parsing change.
- **XZ probe report.** Health → **Export XZ probe report** saves a read-only JSON file (also `GET /api/xz-probe`): adapters, packet types heard per IP and device, device names, NFS exports, slot bytes, firmware, whether `exportLibrary.db` is present, dbserver and library errors. Nothing is sent to the player.

## 1.0.14 — 2026-10-15

A macOS build, working zoom buttons with a 32-bar step, and the overview strip in the panel and bar overlays.

- **Prolink Listener for Mac.** Releases now include `ProlinkListener-<version>-macos-arm64` (Apple Silicon) and `-macos-x86_64` (Intel), each as a `.dmg` and a `.zip`, built with PyInstaller on GitHub's macOS runners. See [macOS, from the GitHub Release](README.md#macos-from-the-github-release). The app is not signed or notarised, and it does not update itself: when a new version is out it says so and opens the Releases page.
- On macOS, settings live in `~/Library/Application Support/Prolink Listener` and the library cache in `~/Library/Caches/Prolink Listener`. Windows and Linux keep `~/.prolink-monitor` and `~/.prolink-cache`.
- The bundle's `Info.plist` carries `NSLocalNetworkUsageDescription`, so macOS 14 and later can ask for Local Network access, which Pro DJ Link needs. The network interface and its prefix are read from `ifconfig` on macOS (Windows still uses PowerShell). Fonts fall back to the system font and Menlo.
- **Zoom buttons work again.** Since the 1.0.10 wheel zoom, a saved wheel zoom (`wave_zoom_seconds_by_deck`) always took priority over the bar setting, so after one wheel move the `1 2 4 8 16` buttons and the per-deck chip changed nothing. Buttons and wheel now share one zoom state per deck: a button (toolbar, deck chip or **All**) replaces the wheel zoom with bars × 4 beats at the track's BPM, the wheel adjusts from there, and the lit button follows (none while the wheel sits between two). The web panel drops its browser-saved wheel zoom when the desktop bar setting changes.
- **32-bar zoom.** The buttons are now `1 2 4 8 16 32 All`, and the wheel reaches 96 s (32 bars at 80 BPM), up from 30 s.
- **Overview in the panel and bar overlays.** `/overlay?style=panel` and `style=bar` never had the whole-track overview strip; only the card did. Both now show it between the track row and the scrolling wave (above it with `pos=top`), with the playhead, cues and loop, in the same colours as the card. The coloured blocks in the panel are the spectrum visualiser, which is idle while the deck is stopped.
- Tests also run on macOS in CI, and `packaging/verify_bundle.py` checks a `.app` (executable, Qt WebEngine helper, web files, Info.plist and icon).

## 1.0.13 — 2026-10-14

Smaller deck text and a bar counter.

- **Smaller identity text.** In each deck's left column the title, artist, key and deck number are about a third smaller, on the desktop Monitor and the web panel alike (title 22 → 14 px, artist 16 → 11 px, number 16/20 → 11 px, key 16/18 → 12 px at a 240 px row, still scaled with the row height). A title now gets two lines on any row 96 px or taller (it used to need 240 px), so a long title shows in full instead of being cut short after the first line.
- **Bar.beat counter.** The beat readout is now the standard 4/4 counter, **`Bar 33.2`** (bar 33, beat 2), followed by the phrase: `Bar 33.2 · Chorus`. The bar is computed from the player's beat-grid beat number in its status packets, and the player's own 1–4 beat counter marks the downbeat, so the bar changes on beat 1 even when the grid's first beat isn't a downbeat. The JSON state has the same text as `bar_beat`. It appears on the Monitor, the web panel, and the card, panel and bar overlays (next to the clock), and `barbeat=0` hides it on an overlay. With no beat number it falls back to `Beat 2/4`.
- Screenshots recaptured, including the overlay settings page.

## 1.0.12 — 2026-10-14

The floating Now Playing window opens again. Since 1.0.7, `apply_state` and `tick_paint` had slipped out of the window class into the body of a module function during the clean-exit change. Opening the window called `apply_state` before `show()`, raised `AttributeError` inside the Qt slot, and nothing appeared and nothing was logged. Both methods are back on the class. A failure while opening is now logged and shown in a message box. A test opens the window the way the app does, and another checks that every method the main window calls exists. The drag-strip grip is sized to the 22 px strip so the top and bottom dots are no longer clipped. README screenshots were recaptured from demo data with `scripts/capture_readme_shots.py`.

## 1.0.11 — 2026-10-14

The Library shows every playlist and folder, and artwork can come from the audio file.

- **Full playlist tree.** The live (dbserver) Library only asked the player for the root playlist menu and then dropped the folders, so any playlist inside a folder never appeared. It now walks every folder (one read-only folder request per folder, with a loop guard and a node cap), keeps folders as headers, and indents playlists under them. Smart playlists are listed like any other playlist. Long menus are still read in full, 64 rows at a time.
- **Fallback tree.** The NFS fallback reads the OneLibrary `playlist_id_parent` and `attribute` columns (1 = folder, 4 = smart playlist). Before, attribute 2 was treated as a folder and the parent was ignored. When OneLibrary is not available, the fallback reads `export.pdb`'s playlist tree and playlist entries.
- **Track columns.** The second line of a dbserver track row is whatever the player's sub-column is set to (album, genre, comment, key, and so on). It is now mapped by the row's item type instead of always going into Artist. A cached `export.pdb` row is only merged when its title matches, so a stick with different track ids no longer shows another track's artist or album.
- **Library diagnostics.** **Library > Diagnostics** (or `/api/library/diagnostics`) saves a read-only JSON report: folder and playlist counts and tree depth from the live player and from the USB copy, the full tree from both, and the first 20 rows of a playlist side by side.
- **Hi-res artwork from file.** Large artwork is read from the cover embedded in the audio file over NFS, with mutagen, using ranged reads of the tag region only. It loads in the background and is cached in memory and on disk; until it is ready the player's dbserver art is shown. Settings: **Hi-res artwork from file** (on by default) and **Hi-res artwork read cap (MB)** (16 by default, 1 to 64). The player and USB stay read-only.

## 1.0.10 — 2026-10-13

3-band waveforms and a zoomable scrolling wave.

- **Byte order.** PWV7 (the scrolling 3-band wave in `.2EX`) and PWV6 (the overview) are read as **low, mid, high** by default. That is the order [rbxport](https://github.com/chrisle/rbxport) both reads and writes: it measured rekordbox's own files (PWV4's bass channel tracks PWV6 byte 0 at r = 0.83–0.96 over 11 tracks, the last byte is the quiet high band with a mean near 3 against about 30 for the others, and its generated PWV7 matches rekordbox's envelopes at r ≥ 0.97). OneLibraryCompanion reads PWV7 the same way. The Deep Symmetry analysis and Beat Link say mid, high, low; that is still an option. Each tag has its own setting, `pwv7_order` and `pwv6_order`, and the log prints both. **If kick drums are not blue on 3-Band**, open **Settings > Display > 3-Band byte order (scrolling / overview)**, pick *Mid, high, low (Deep Symmetry / Beat Link)*, save, and reload the track (or set `"pwv7_order": "mid_high_low"` and `"pwv6_order": "mid_high_low"` in `settings.json`). In the default order the scrolling bands share one scale (value / 127, as rbxport draws them); the Beat Link order keeps Beat Link's band scales.
- **PWV6 overview.** The 3-band overview comes from PWV6 when the track has it: low, mid and high are stacked from the bottom at rbxport's scales, low/128, mid/256 and high/128 of the strip, and a loud column clips at the top. Tracks without PWV6 keep the PWV7 downsample. Every overview pixel max-pools the columns under it, so short hits do not drop out. The vocal lane reads PWV6 in the same order.
- **Two palettes.** *CDJ-3000* is the default: rekordbox's own 3-band colours, one for each band combination (low `#0055e1`, mid `#ffa600`, high `#ffffff`, low+mid `#b4690a`, low+high `#d2dcfa`, mid+high `#fff0d7`, all three `#f5ebd7`), as sampled from rekordbox in mixxxdj/mixxx#12326 and used by rbxport and OneLibraryCompanion. *Beat Link* is the old look: blue lows, amber mids, brown overlap, white highs. Both are defined once, in `web/wavepalette.json`, which the desktop app, the web panel and the overlays all read. Overlays take `palette=beatlink` for the old look.
- **Auto waveform style.** *Auto* follows the colour each player is set to (status byte 0xDA: 01 blue, 03 RGB, 04 3-band). When a player does not report it, Auto draws the *Auto fallback* you pick (RGB by default). Overlays take `wave=auto` and `wavefb=`.
- **Zoom.** On the Monitor, the mouse wheel over a deck's scrolling wave zooms it from 2 s to 30 s in smooth steps (×1.15 per notch). The playhead stays where it is (centre or left). Double-click resets the deck to the bar zoom. Each deck keeps its own zoom in `settings.json` (`wave_zoom_seconds_by_deck`). The web panel does the same with the wheel and a two-finger pinch, and remembers it in the browser. Overlays take a fixed `zoom=<seconds>` and have no zoom controls. Loops, cues and beat lines are placed from the same window at every zoom, the phrase strip is unaffected, and zoomed-out views max-pool so they stay sharp.

## 1.0.9 — 2026-10-13

Icons are [Phosphor](https://phosphoricons.com/) SVGs instead of text symbols. The artwork placeholder is a music note, the loop badge uses Repeat, the sidebar button uses List, and ratings are filled and outlined stars. The floating overlay's drag strip uses the Dots Six Vertical grip. The Qt app recolours them to the theme, and the web panel and overlays draw them inline in `currentColor`. ON AIR, MASTER, SYNC, deck numbers, status dots, and the key stay as text.

## 1.0.8 — 2026-10-12

Monitor deck rows measure their text before they draw it, so a short row no longer squashes the title over the artist or stacks the readout on itself. The title stays on one line unless the row is tall enough for two. The artist stays. Album, label, genre, and the other detail lines appear only when they fit. Artwork shrinks with the row. BPM, pitch, and the clock stay readable; tags, the beat line, and the next cue drop from the bottom when the height runs out. An empty deck is a deck number and a small “no track”. The web panel already packed its rows and is unchanged.

## 1.0.7 — 2026-10-12

The Monitor and the web panel use the spare height in a deck row. Title and artist scale with the row. When there is room, the left side adds album, label, genre, year, key, rating, colour, comment, and a played bar, and the right side adds master/sync, loop, the beat and phrase, and the next cue. Rekordbox `(blank)` is not drawn. **File > Exit**, tray Quit, and a close that is set to quit stop the link sockets, the web server, the library workers, and the process.

## 1.0.6 — 2026-10-12

The loop highlight on the overlay and the web panel stays on the playhead. While a loop is active the position is folded into the loop, and the wave scrolls by the device pixels it draws, so the green region does not drift at 125% and 150%. The desktop Monitor uses that same scroll. Checked with a 32-beat loop at 123.2 BPM.

## 1.0.5 — 2026-10-12

Bar (`style=bar`) draws the scrolling detail wave. Panel and Bar are both that full-width strip. The spectrum on Bar still follows the spectrum switch (`spectrum=0` hides it). Panel keeps the visualiser unless `spectrum=0`.

## 1.0.4 — 2026-10-12

Artwork is requested as soon as a track id is known, and a successful dbserver or NFS lookup sets `has_artwork` before the waveform arrives. If the saved web port is inside a Windows excluded range, the log and Health name that range and the server uses the next fallback. Panel is the strip with the larger cover and the visualiser. Bar is the strip with the small cover.

## 1.0.3 — 2026-10-12

One bad OneLibrary date no longer blanks the library. The Library page shows the same live dbserver source as Health. Track columns keep title, artist, album, key, and BPM, and the rows are zebra-striped. `style=bar` is its own layout. Overlay URLs can lock the spectrum with `spectrum=1`. A dbserver session that never connects is not counted as a reconnect.

## 1.0.2 — 2026-10-12

The Windows zip includes pyrekordbox. The Library reads the player’s dbserver first. The log rotates. The floating window has a visible drag strip.

## 1.0.1 — 2026-10-11

The web server stays up across a reconnect. A second launch brings the first window forward instead of starting another process.

## 1.0.0 — 2026-10-11

First tagged release of this fork (no per-version notes in the README).
