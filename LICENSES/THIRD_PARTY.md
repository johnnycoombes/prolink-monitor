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
- its reading of PWV7 as low, mid, high, offered as the optional `pwv7_order` setting;
- drawing the PWV6 overview stacked from the bottom.

The drawing code in `gui/widgets.py` and `web/wavepack.js` was written independently for this project.

## Beat Link (reference only)

[Deep-Symmetry/beat-link](https://github.com/Deep-Symmetry/beat-link) is licensed under the Eclipse Public License. No Beat Link code is included. Its published numbers are used as reference values: the PWV7 byte order mid, high, low and band scales (0.4, 0.3, 0.06), the PWV6 weights (0.49, 0.32, 0.25), and the "Beat Link" palette colours (low `#2053d9`, mid `#f2aa3c`, overlap `#a96b27`, high white).
