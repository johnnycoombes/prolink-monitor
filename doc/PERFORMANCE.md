# Performance notes

How Prolink Listener stays smooth, what still costs CPU, and when a native
(C++ / pybind11) path is worth it.

## What already runs hot

| Path | Rate | Notes |
|---|---|---|
| Absolute Position packets | ~33 Hz | Playhead source of truth |
| Desktop paint timer | up to 60 Hz | Playhead + waveform scroll only |
| Desktop state poll | 20 Hz | Metadata, library, devices |
| Web SSE | 60 Hz paint + ~15 Hz full state | Split in `Handler._events` |
| Web `requestAnimationFrame` | display refresh | Interpolates between SSE frames |

Waveform detail strips are **scroll-cached**: only the newly exposed edge is
peak-picked. Overview and detail use recycled offscreen surfaces (Qt pixmap
swap / canvas scratch) so a playing deck does not allocate every frame.

## Hot paths (still)

1. **Peak-pick paint** — every scrolled CSS pixel scans ~track columns and may
   draw 3-band fills (`gui/widgets.py`, `web/index.html`).
2. **Full `Monitor.state()`** — JSON of decks + mix + session (~15 Hz over SSE).
3. **Track load** — NFS fetch of ANLZ + pure-Python `waveform_levels` /
   `downsample` (burst on load, then cached).
4. **Sniffer mode** — hex-decode of every UDP payload via tshark text.

Library PDB totals are cached until the media set / fingerprint changes.
`state()` never calls `meta(..., load=True)` — NFS stays off the hot path;
`ensure()` runs from track-change / explicit API fetches.

## Quick wins already in this tree

- SSE split: `paint_state()` at 60 Hz, fat `state()` at ~15 Hz
- No NFS inside `state()` enrichment
- Cached library counts
- Recycled scroll buffers (Qt + web)
- Skip Qt `update()` when playhead has not moved ~4 ms and play state is unchanged

## Further Python / Qt / web work

- Draw web detail cues live (like Monitor) so scroll edge paint skips cue loops
- Binary sniffer / pcap-ng path instead of hex fields
- Optional: lower default `poll_hz` to 30 on constrained laptops (Settings)

## Native C++ — when and how

**Do not rewrite the whole app in C++ first.** The desktop shell is already
PySide6 → Qt (C++). Rewriting UI in C++ buys little until the Python hot loops
are gone.

### Worth a native module (high ROI)

Extract a small extension (`prolink._native` via **pybind11** or **Cython**):

| Function | Why |
|---|---|
| `waveform_levels` / `downsample` | Pure Python over ~150 cols/s × track length |
| Peak-pick column painter | Called every scrolled pixel × decks × Hz |
| Optional: Absolute Position / status parse | Tiny structs; nice for sniffer volume |

Keep Python owning: Pro DJ Link session policy, MixStatus, SessionRecorder,
HTTP/SSE, PySide6 UI.

Shape:

```
prolink/
  _native.cpp   # pybind11
  anlz.py       # calls _native when available, Python fallback otherwise
```

Ship wheels per platform; fall back to pure Python so `pip install` without a
compiler still works.

### Full C++ / Qt rewrite (low ROI unless productizing)

Only consider if you need:

- a signed Windows/macOS installer with hard real-time UI budgets, or
- to merge with an existing C++ stack (e.g. Beat Link–style tooling)

Cost: reimplement NFS, ANLZ, UI, packaging. The protocol work already mirrors
Deep Symmetry / Beat Link; a Java or C++ peer would duplicate that effort.

### Practical recommendation

1. Keep shipping the Python app with the SSE / paint / cache work above.
2. If profiling still shows >50% CPU in `waveform_levels` or peak-pick on a
   4-deck 60 Hz session, add the pybind11 module for those two functions.
3. Revisit a full native UI only after that module is maxed out — or if
   product/distribution goals demand it.

## How to profile

```bash
# Desktop paint + state
python -m cProfile -o /tmp/pl.prof desktop.py
# Or sample with py-spy while decks are playing
py-spy record -o /tmp/pl.svg -- python desktop.py
```

Watch `Handler._events`, `Monitor.state`, `WaveformView._draw_detail_range`,
and `anlz.waveform_levels`.
