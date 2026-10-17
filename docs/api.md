# HTTP API

The built-in server (`app.py` `Handler`) answers **GET only** (other methods get the standard 501). Base URL: `http://<host>:<port>` where port is the one that actually bound — 8777 by default, else 8787, 8877, 9777, 18777, or a free port (Health shows it). By default it listens on `0.0.0.0`; `http_bind = 127.0.0.1` (or `--http-host 127.0.0.1`) keeps it local. There is no authentication; keep it on a trusted network.

All endpoints are read-only towards the players. The session endpoints only change the app's own in-memory recorder.

Errors are JSON: `{"error": "not_found"}` (404), `{"error": "bad_request"}` (400, e.g. a non-numeric id), `{"error": "<message>"}` (500). JSON responses are `Cache-Control: no-store` and gzipped when larger than 1 KB and the client sends `Accept-Encoding: gzip`.

Examples below show the shape of each response with shortened, illustrative values; field names are exactly those in the code.

## Index

| Route | Returns |
|---|---|
| [`/`, `/index.html`](#pages-and-static-files) | Web panel |
| [`/overlay`, `/overlay.html`](#pages-and-static-files) | OBS overlay |
| `/<file>` | Static files from `web/` |
| [`/api/state`](#get-apistate) | Full state snapshot |
| [`/api/events`](#get-apievents) | Server-sent events |
| [`/api/track/<id>`](#get-apitrackid) | Track metadata, grid, cues, phrases |
| [`/api/waveform/<id>`](#get-apiwaveformid) | Binary waveforms |
| [`/api/artwork/<id>`](#get-apiartworkid) | JPEG artwork |
| [`/api/setlist`](#get-apisetlist) | SmartTiming now playing and setlist |
| [`/api/session`](#session) and `/start`, `/stop`, `/clear`, `/export` | Session recorder |
| [`/api/library/tracks`](#get-apilibrarytracks) | Track search |
| [`/api/library/playlists`](#get-apilibraryplaylists) | Playlist tree and history |
| [`/api/library/playlist/<id>`](#get-apilibraryplaylistid) | Tracks in a playlist |
| [`/api/library/loaded`](#get-apilibraryloaded) | Tracks on decks |
| [`/api/library/diagnostics`](#get-apilibrarydiagnostics) | Library report (download) |
| [`/api/xz-probe`](#get-apixz-probe) | XZ probe report (download) |

## Pages and static files

| Route | Notes |
|---|---|
| `/`, `/index.html` | Web panel. Query: `readonly=1` or `ro=1` hides Record |
| `/overlay`, `/overlay.html` | Overlay. Query parameters: [user guide](user-guide.md#url-parameters) |
| `/<path>` (not under `/api/`) | Any file in `web/` (`playhead.js`, `wavepack.js`, `spectrum.js`, `wavepalette.json`, `manifest.webmanifest`, `icon-192.png`); paths outside `web/` are 404 |

HTML gets the wave palette injected, `Cache-Control: no-cache`, `Pragma: no-cache`, `X-Asset-Version` (file mtime) and an ETag. Static files: `no-cache` + ETag (304 on match).

## GET /api/state

Full snapshot, built from cache only.

```json
{
  "t": 1760666400.12,
  "paint": false,
  "decks": [
    {
      "number": 1, "name": "XDJ-AZ", "firmware": "1.30",
      "track_id": 412, "track_key": "9f2c41d07ab35e18c6d2a4f0",
      "slot": "usb", "slot_label": "USB 1", "track_type": "rekordbox", "loaded_from": 1,
      "state": "playing", "playing": true,
      "bpm": 126.0, "track_bpm": 126.0, "pitch": 0.0, "speed": 1.0,
      "position_ms": 61234.5, "playhead_age_s": 0.04, "duration_ms": 372000,
      "beat": 129, "bar": 1, "bar_beat": "Bar 33.1",
      "beat_packets": 812, "absolute_packets": 0, "position_source": "beat_grid",
      "cue_in": 32, "master": true, "sync": false, "on_air": true,
      "waveform_position": null, "waveform_color": "rgb",
      "loop_start_ms": null, "loop_end_ms": null,
      "title": "Track", "artist": "Artist", "key": "8A"
    }
  ],
  "display": {"show_phrases": true, "show_vocals": true, "show_next_cue": true, "zoom_bars": 4, "playhead_position": "auto", "...": "..."},
  "spectrum": null,
  "devices": [{"number": 1, "name": "XDJ-AZ", "kind": "player", "ip": "192.168.1.212"}],
  "packets": 10423,
  "mode": "virtual device #5 \"monitor\"", "mode_kind": "vcdj", "mode_detail": "#5 \"monitor\"",
  "device_number_note": "#5 (as set, free)",
  "all_in_one": [], "duplicate_paths": [], "mixer_on_air": {},
  "model": {"setting": "auto", "source": "detected", "profile": {"key": "xdj-az", "...": "..."}, "detected": []},
  "observe": {}, "az_mode": "pro_dj_link", "host": "192.168.1.212",
  "library": {}, "library_error": null,
  "library_source": {"source": "remotedb", "detail": ""},
  "artwork_sources": {"1": "dbserver"}, "artwork_source_last": "dbserver",
  "onelibrary": {}, "anlz": [], "nfs": [{"host": "192.168.1.212", "export": "/C/", "rtt_ms": 3.1, "last_rtt_ms": 2.9, "mount_rtt_ms": 4.0, "calls": 120, "timeouts": 0}],
  "dbserver": {}, "link_timing": [], "capture": {},
  "mix": {}, "audience_deck": {"number": 1, "...": "..."}, "wnp": {},
  "now_playing": null, "pending": null, "setlist": [],
  "session": {"recording": false, "...": "..."}
}
```

`decks[].title/artist/key` appear only once metadata for that exact `track_key` is cached. `cue_in` is `null` when no cue is ahead. XDJ-XZ setups add XZ-specific keys (mixer tempo and tempo master, no-status warning). Exact values of the `library`, `onelibrary`, `observe`, `dbserver`, `capture`, `wnp` objects are diagnostic and may grow.

## GET /api/events

Server-Sent Events (`text/event-stream`). Each event is a single `data:` line of JSON.

| Query | Effect |
|---|---|
| `lite=1` (also `true`, `yes`) | State frames carry only `t, paint, decks, display, spectrum, audience_deck, now_playing, pending, setlist, mix` |

- **State frames** (`"paint": false`): about 15 per second, same shape as `/api/state` (or the lite subset).
- **Paint frames** (`"paint": true`): about 30 per second in between, with `t`, `decks` (number, position, speed, flags, loop…), `audience_deck`, a `display` subset (`show_phrases`, `show_vocals`, `show_next_cue`, `playhead_smoothing`, `show_spectrum`, `spectrum_source`) and `spectrum` when the source is system audio.
- A frame identical to the previous one of its kind is skipped, but something is re-sent at least once a second. Paint frames are always sent while any deck plays.
- A `: ping` comment goes out about once a second.
- At most 12 concurrent streams; more get HTTP 503.

```js
const es = new EventSource('/api/events?lite=1');
es.onmessage = e => { const s = JSON.parse(e.data); if (!s.paint) render(s); else movePlayheads(s); };
```

## GET /api/track/&lt;id&gt;

Metadata and analysis for a track id.

| Query | Meaning |
|---|---|
| `deck` | Deck number (selects the medium when ids collide across players/slots) |
| `key` | Expected `track_key`; 404 if the cached entry has a different key |

The server finishes the ANLZ read before answering when it can. 404 `{"error": "no_analysis"}` when nothing is known.

```json
{
  "id": 412, "track_key": "9f2c41d07ab35e18c6d2a4f0",
  "title": "Track", "artist": "Artist", "album": "", "genre": "Techno",
  "key": "8A", "label": "", "comment": "", "color": "", "year": 2024, "rating": 0,
  "track_bpm": 126.0, "duration_ms": 372000, "bitrate": 320, "has_artwork": true,
  "beats": [[120, 1], [596, 2]],
  "cues": [
    {"hot": 1, "type": "cue", "t": 15360, "end": 0, "color": [40, 226, 20], "text": "Drop", "memory": false},
    {"hot": 0, "type": "cue", "t": 600, "end": 0, "color": null, "text": "", "memory": true}
  ],
  "phrases": [{"beat": 1, "kind": 1, "text": "Intro"}],
  "vocals": [{"t": 60000, "end": 92000}],
  "detail_columns": 55800, "overview_columns": 1600,
  "library_source": "remotedb"
}
```

`cues[].color` is `null` when the export has no colour (clients draw memory cues red, hot cues yellow). A non-zero `end` is a loop end. A title-only entry (published before the analysis) has `detail_columns: 0`.

## GET /api/waveform/&lt;id&gt;

`application/octet-stream`. Same `deck` and `key` query as `/api/track`. 404 `{"error": "no_waveform"}`. With `key`: `Cache-Control: private, max-age=60` and an ETag (304 on match); without: `no-store`.

The body is six blocks back to back, each with a 24-byte little-endian header `<4sIIfII>`: magic, version (1), column count, columns per second (150 for detail, 0 for overview), duration ms, flags.

| # | Magic | Contents after header |
|---|---|---|
| 1 | `PLWF` | RGB detail: heights[n], then RGB[3n] |
| 2 | `PLWF` | RGB overview (1600 columns) |
| 3 | `PLWB` | 3-band detail: lows[n], mids[n], highs[n] |
| 4 | `PLWB` | 3-band overview; flag bit 0 = PWV6 stacked contributions |
| 5 | `PLBC` | Blue detail: heights[n], RGB[3n] |
| 6 | `PLBC` | Blue overview |

Empty lanes have `n = 0`. `web/wavepack.js` is the reference decoder.

## GET /api/artwork/&lt;id&gt;

`image/jpeg`. Query: `deck`, `key`, `size=large|small` (anything other than `small` is large). 404 `{"error": "no_artwork"}`. With `key`: `private, no-cache` + ETag (art can upgrade to hi-res for the same key); without: `no-store`.

## GET /api/setlist

SmartTiming state (`prolink/mixstatus.py`).

```json
{
  "set_active": true,
  "now_playing": {"deck": 1, "track_id": 412, "title": "Track", "artist": "Artist", "...": "..."},
  "pending": null,
  "setlist": [
    {"deck": 2, "track_id": 77, "title": "Earlier", "artist": "Someone", "bpm": 125.0, "key": "7A", "started_at": 1760665800.0, "reported_at": 1760665861.0}
  ],
  "config": {"time_between_sets": 30.0, "allowed_interrupt_beats": 8, "beats_until_reported": 128, "use_on_air": true}
}
```

`config` shows the defaults from `MixStatusConfig`.

## Session

| Route | Action / returns |
|---|---|
| `/api/session` | Recorder state |
| `/api/session/start` | Start recording; returns state |
| `/api/session/stop` | Stop (list kept); returns state |
| `/api/session/clear` | Clear; returns state |
| `/api/session/export?fmt=csv\|json\|m3u` | Download `session.csv`, `session.json` (default) or `session.m3u` (`Content-Disposition: attachment`) |

```json
{
  "recording": true, "paused": false, "started_at": 1760665000.0, "t0": 1760665000.0,
  "elapsed_s": 1834.2, "elapsed": "00:30:34", "clock": "set",
  "tracks": [], "exports": []
}
```

These are GET requests that change state, so do not expose the server to untrusted networks.

## GET /api/library/tracks

| Query | Default | Meaning |
|---|---|---|
| `q` | empty | Case-insensitive search |
| `limit` | 100 | 1–500 |
| `offset` | 0 | Paging |

```json
{
  "tracks": [{"id": 412, "title": "Track", "artist": "Artist", "album": "", "bpm": 126.0, "key": "8A", "host": "192.168.1.212", "slot": 3, "source": "remotedb"}],
  "total": 1, "offset": 0, "limit": 100, "query": "track",
  "hosts": ["192.168.1.212"], "multi_player": false,
  "library_source": "remotedb", "error": ""
}
```

## GET /api/library/playlists

```json
{
  "playlists": [
    {"id": 5, "name": "Techno", "folder": true, "parent_id": 0, "depth": 0, "host": "192.168.1.212", "slot": 3, "source": "remotedb"},
    {"id": 9, "name": "Peak", "folder": false, "parent_id": 5, "depth": 1, "host": "192.168.1.212", "slot": 3, "source": "remotedb"}
  ],
  "history": [{"id": 1, "name": "HISTORY 001", "host": "192.168.1.212", "slot": 3, "source": "remotedb"}],
  "readable": true, "present": true, "errors": [],
  "pyrekordbox": true, "library_source": "remotedb", "remotedb_errors": {}
}
```

## GET /api/library/playlist/&lt;id&gt;

| Query | Default | Meaning |
|---|---|---|
| `host` | first player | Player IP |
| `limit` | 200 | Max rows |

Returns `{"id", "host", "tracks", "library_source"}` (`remotedb` or `nfs-cache`); `tracks` rows are `{"id", "title", "artist", "album", "bpm", "key", "genre", "host", "slot", "source"}` (live) or `{"id", "title", "artist", "album", "genre", "key", "bpm"}` (USB copy).

## GET /api/library/loaded

```json
{"tracks": [{"id": 412, "deck": 1, "track_key": "9f2c41d07ab35e18c6d2a4f0", "title": "Track", "artist": "Artist", "key": "8A", "bpm": 126.0, "has_artwork": true, "source": "deck"}]}
```

## GET /api/library/diagnostics

Download `library-diagnostics.json`: per host, live (dbserver) vs fallback (USB copy) tree stats and trees (`id, name, folder, parent_id, depth`), and the first 20 rows of a playlist side by side (`row`, `live`, `fallback`). Read-only.

## GET /api/xz-probe

Download `xz-probe-report.json`: `generated`, `version`, `platform`, `mode` (kind, description, device number and note, numbers heard, local IP), observe status, devices (`number, name, ip, mac, kind, all_in_one`), NFS exports, slot bytes, firmware, XZ status fingerprints, `exportLibrary.db` presence, dbserver and library errors, mixer on-air flags. Nothing is sent to the player to build it.
