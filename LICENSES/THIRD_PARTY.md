# Third-party licences

## Phosphor Icons

The SVG icons in `assets/icons/` (and the copies inlined in `web/index.html` and `web/overlay.html`) are from [Phosphor Icons](https://github.com/phosphor-icons/core), used under the MIT License.

```
MIT License

Copyright (c) 2023 Phosphor Icons

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## OneLibraryCompanion (reference only)

[hdelplan/OneLibraryCompanion](https://github.com/hdelplan/OneLibraryCompanion) is licensed under the GNU GPL v3, not MIT. No OneLibraryCompanion source is included or translated in this tree. Prolink Listener 1.0.10 used it as a reference for:

- the "CDJ-3000" 3-band palette values in `web/wavepalette.json` (`#0055e1`, `#ffa600`, `#ffffff`, `#b4690a`, `#d2dcfa`, `#fff0d7`, `#f5ebd7`);
- the idea of filling each band combination (low, mid, high and their overlaps) in its own colour;
- its reading of PWV7 as low, mid, high (now the default `pwv7_order`, see rbxport below);
- drawing the PWV6 overview stacked from the bottom.

The drawing code in `gui/widgets.py` and `web/wavepack.js` was written independently for this project.

## rbxport (reference only)

[chrisle/rbxport](https://github.com/chrisle/rbxport) is licensed under the GNU GPL v2 or later. No rbxport source is included or translated in this tree. Prolink Listener 1.0.10 uses its published findings as reference values:

- **Byte order.** rbxport reads and writes PWV6 and PWV7 as low, mid, high (`crates/rbl-anlz/src/encode.rs` `pwv6()` / `pwv7()`, `src/canvas/waveform.ts`, `docs/reference/usb-export-db.md`). Its evidence comes from rekordbox's own files: PWV4's bass channel runs with PWV6 byte 0 at r = 0.83-0.96 on 11 tracks, the last byte is the quiet high band (mean about 3 against about 30), and its generated PWV7 matches rekordbox's scrolling envelopes at r >= 0.97. That is why `pwv7_order` and `pwv6_order` default to `low_mid_high` here, against the Deep Symmetry analysis and Beat Link (mid, high, low), which remain an option.
- **PWV6 stack scales.** low/128, mid/256, high/128 of the strip, fitted by rbxport against a rekordbox capture.
- **Palette.** rbxport uses the same seven rekordbox colours as the "CDJ-3000" palette here, sampled from rekordbox in mixxxdj/mixxx#12326.

## Beat Link (reference only)

[Deep-Symmetry/beat-link](https://github.com/Deep-Symmetry/beat-link) is licensed under the Eclipse Public License. No Beat Link code is included. Its published numbers are used as reference values for the optional `mid_high_low` byte order and its PWV7 band scales (0.4, 0.3, 0.06), and for the "Beat Link" palette colours (low `#2053d9`, mid `#f2aa3c`, overlap `#a96b27`, high white).
