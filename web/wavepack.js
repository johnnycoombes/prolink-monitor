/* Decode the listener's packed waveform (PLWF + PLWB + PLBC).

   Fetch gives an ArrayBuffer. Indexing that buffer does not read bytes
   (`buf[0]` is undefined), so every header check goes through a DataView.
   A short or corrupt block returns null instead of throwing, so one bad
   length cannot drop the track. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProlinkWave = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function magicAt(buf, off) {
    const dv = new DataView(buf);
    return String.fromCharCode(
      dv.getUint8(off), dv.getUint8(off + 1), dv.getUint8(off + 2), dv.getUint8(off + 3),
    );
  }

  function readWaveform(buf, off) {
    if (!buf || off < 0 || off + 24 > buf.byteLength) return null;
    const dv = new DataView(buf);
    if (magicAt(buf, off) !== "PLWF") return null;
    const ver = dv.getUint32(off + 4, true);
    const n = dv.getUint32(off + 8, true);
    if (ver !== 1 || n < 0) return null;
    const end = off + 24 + n * 4;
    if (end > buf.byteLength) return null;
    return {
      n,
      cps: dv.getFloat32(off + 12, true),
      dur: dv.getUint32(off + 16, true),
      h: new Uint8Array(buf, off + 24, n),
      rgb: new Uint8Array(buf, off + 24 + n, n * 3),
      end,
    };
  }

  function readBands(buf, off) {
    if (!buf || off < 0 || off + 24 > buf.byteLength) return null;
    if (magicAt(buf, off) !== "PLWB") return null;
    const dv = new DataView(buf);
    const n = dv.getUint32(off + 8, true);
    if (n < 0) return null;
    const start = off + 24;
    const end = start + n * 3;
    if (end > buf.byteLength) return null;
    return {
      n,
      low: new Uint8Array(buf, start, n),
      mid: new Uint8Array(buf, start + n, n),
      high: new Uint8Array(buf, start + n * 2, n),
      end,
    };
  }

  function readBlue(buf, off) {
    if (!buf || off < 0 || off + 24 > buf.byteLength) return null;
    if (magicAt(buf, off) !== "PLBC") return null;
    const dv = new DataView(buf);
    const n = dv.getUint32(off + 8, true);
    if (n < 0) return null;
    const start = off + 24;
    const end = start + n * 4;
    if (end > buf.byteLength) return null;
    return {
      n,
      h: new Uint8Array(buf, start, n),
      rgb: new Uint8Array(buf, start + n, n * 3),
      end,
    };
  }

  function attachBands(wave, bands) {
    if (!wave || !bands || bands.n !== wave.n) return;
    let peak = 0;
    const lanes = [bands.low, bands.mid, bands.high];
    for (let lane = 0; lane < lanes.length; lane++) {
      const bytes = lanes[lane];
      for (let i = 0; i < bytes.length; i++) if (bytes[i] > peak) peak = bytes[i];
    }
    // All-zero PLWB (no PWV7) stays unset so 3-band can fall back to the RGB shape.
    if (peak < 2) return;
    wave.low = bands.low;
    wave.mid = bands.mid;
    wave.high = bands.high;
    wave.bandPeak = peak;
  }

  function attachBlue(wave, block) {
    if (!wave || !block || block.n !== wave.n) return;
    wave.blueH = block.h;
    wave.blueRgb = block.rgb;
  }

  function decodeWavePack(buf) {
    const detail = readWaveform(buf, 0);
    if (!detail) return { detail: null, overview: null };
    const overview = readWaveform(buf, detail.end);
    const afterOverview = overview ? overview.end : detail.end;
    const b1 = readBands(buf, afterOverview);
    attachBands(detail, b1);
    const b2 = b1 ? readBands(buf, b1.end) : null;
    attachBands(overview, b2);
    const blueOff = b2 ? b2.end : (b1 ? b1.end : afterOverview);
    const blue1 = readBlue(buf, blueOff);
    attachBlue(detail, blue1);
    if (blue1) attachBlue(overview, readBlue(buf, blue1.end));
    return { detail, overview };
  }

  /** How many overview columns would actually be stroked. Used by the regression test. */
  function overviewInk(wave, cssW) {
    if (!wave || !wave.n || !wave.h || cssW < 1) return 0;
    let ink = 0;
    const width = cssW | 0;
    for (let x = 0; x < width; x++) {
      const i = Math.min(wave.n - 1, Math.floor(x * wave.n / width));
      ink += wave.h[i] || 0;
    }
    return ink;
  }

  return {
    readWaveform,
    readBands,
    readBlue,
    attachBands,
    attachBlue,
    decodeWavePack,
    overviewInk,
  };
});
