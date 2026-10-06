/* Waveform-driven bar spectrum for the panel Now Playing overlay.
   Pro DJ Link does not carry audio, so this shapes bars from the analysed
   low/mid/high waveform at the playhead. It is not an FFT of the music. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProlinkSpectrum = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const BARS = 48;

  function clamp01(v) {
    if (v < 0) return 0;
    if (v > 1) return 1;
    return v;
  }

  function sampleAt(arr, c0, c1, frac) {
    if (!arr) return 0;
    const a = arr[c0] || 0;
    const b = arr[c1] || 0;
    return a * (1 - frac) + b * frac;
  }

  /**
   * Build the next bar levels.
   * @param {object} input
   * @param {Uint8Array|number[]|null} input.low
   * @param {Uint8Array|number[]|null} input.mid
   * @param {Uint8Array|number[]|null} input.high
   * @param {Uint8Array|number[]|null} input.height
   * @param {number} input.columns
   * @param {number} input.column playhead column (fractional)
   * @param {number} [input.peak]
   * @param {number} [input.bars]
   * @param {number[]|null} [input.prev]
   * @param {number} [input.dt] seconds since last frame
   * @param {boolean} [input.playing]
   * @returns {{levels:number[], target:number[]}}
   */
  function spectrumBars(input) {
    const src = input || {};
    const nBars = src.bars > 1 ? (src.bars | 0) : BARS;
    const columns = Math.max(1, src.columns | 0);
    let col = +src.column;
    if (!Number.isFinite(col)) col = 0;
    if (col < 0) col = 0;
    if (col > columns - 1) col = columns - 1;
    const peak = src.peak > 1 ? src.peak : 31;
    const low = src.low;
    const mid = src.mid;
    const high = src.high;
    const height = src.height;
    const hasBands = !!(low && mid && high && low.length >= columns
      && mid.length >= columns && high.length >= columns);
    const dt = Math.max(0, Math.min(0.25, +src.dt || 0.016));
    const playing = !!src.playing;

    const target = new Array(nBars);
    for (let i = 0; i < nBars; i++) {
      const u = nBars === 1 ? 0 : i / (nBars - 1);
      const offset = (i - (nBars - 1) / 2) * 0.42;
      let c = col + offset;
      if (c < 0) c = 0;
      if (c > columns - 1) c = columns - 1;
      const c0 = Math.floor(c);
      const c1 = Math.min(columns - 1, c0 + 1);
      const frac = c - c0;
      let energy = 0;
      if (hasBands) {
        const l = sampleAt(low, c0, c1, frac) / peak;
        const m = sampleAt(mid, c0, c1, frac) / peak;
        const h = sampleAt(high, c0, c1, frac) / peak;
        const wL = Math.exp(-Math.pow((u - 0.15) / 0.22, 2));
        const wM = Math.exp(-Math.pow((u - 0.48) / 0.24, 2));
        const wH = Math.exp(-Math.pow((u - 0.84) / 0.20, 2));
        const wSum = wL + wM + wH || 1;
        energy = (l * wL + m * wM + h * wH) / wSum * 1.15;
      } else if (height && height.length >= columns) {
        const amp = sampleAt(height, c0, c1, frac) / peak;
        const shape = Math.sin(Math.PI * u) * 0.65 + 0.35;
        energy = amp * shape;
      }
      target[i] = clamp01(energy);
    }

    const prev = src.prev;
    const levels = (prev && prev.length === nBars) ? prev.slice() : target.slice();
    const attack = 1 - Math.exp(-dt / 0.07);
    const release = 1 - Math.exp(-dt / 0.18);
    const decay = 1 - Math.exp(-dt / 0.42);
    for (let i = 0; i < nBars; i++) {
      const goal = playing ? target[i] : 0;
      const k = !playing ? decay : (goal > levels[i] ? attack : release);
      levels[i] = levels[i] + (goal - levels[i]) * k;
    }
    return { levels: levels, target: target };
  }

  return { BARS: BARS, spectrumBars: spectrumBars };
});
