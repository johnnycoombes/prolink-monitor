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
      flags: dv.getUint32(off + 20, true),
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
    // Flag bit 0: PWV6 overview, contributions stacked bottom-up (peak = 255).
    wave.bandStacked = !!((bands.flags || 0) & 1);
    if (wave.bandStacked) wave.bandPeak = 255;
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

  /* ---- 3-band palettes: one definition, web/wavepalette.json ----
     The server injects it as window.PROLINK_WAVE_PALETTES; Node tests read the file. */
  const MASK_KEYS = ["low", "mid", "high", "low_mid", "low_high", "mid_high", "all"];
  const MASK_BITS = [1, 2, 4, 3, 5, 6, 7];
  let paletteDoc = null;
  function palettes() {
    if (paletteDoc) return paletteDoc;
    const g = typeof globalThis !== "undefined" ? globalThis : {};
    if (g.PROLINK_WAVE_PALETTES) paletteDoc = g.PROLINK_WAVE_PALETTES;
    else if (typeof require === "function") {
      try { paletteDoc = require("./wavepalette.json"); } catch (e) { paletteDoc = null; }
    } else if (typeof XMLHttpRequest !== "undefined") {
      // Page not served through the listener (no injection): read the file once.
      try {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", "wavepalette.json", false);
        xhr.send(null);
        if (xhr.status === 200 || xhr.status === 0) paletteDoc = JSON.parse(xhr.responseText);
      } catch (e) { paletteDoc = null; }
    }
    return paletteDoc;
  }
  function normalizePalette(name) {
    const doc = palettes();
    return doc && doc.palettes && doc.palettes[name] ? name : "beatlink";
  }
  function getPalette(name) {
    const doc = palettes();
    if (!doc || !doc.palettes) return null;
    return doc.palettes[normalizePalette(name)] || doc.palettes[doc.default];
  }

  function fillMirroredCol(ctx, x, mid, px, colW) {
    if (px < 0.4) return;
    ctx.fillRect(x, Math.round(mid - px), colW, Math.max(1, Math.round(px * 2)));
  }

  /** Mirrored 3-band column. "overlap" = reference method, "mask" = one colour per band combination. */
  function paintBandColumn(ctx, x, mid, lowPx, midPx, highPx, colW, pal) {
    if (!pal) return;
    if (pal.mode === "mask") {
      const hs = [lowPx, midPx, highPx];
      for (let k = 0; k < MASK_KEYS.length; k++) {
        let px = Infinity;
        for (let b = 0; b < 3; b++) if (MASK_BITS[k] & (1 << b)) px = Math.min(px, hs[b]);
        ctx.fillStyle = pal[MASK_KEYS[k]];
        fillMirroredCol(ctx, x, mid, px, colW);
      }
      return;
    }
    if (lowPx > midPx) {
      ctx.fillStyle = pal.low; fillMirroredCol(ctx, x, mid, lowPx, colW);
      ctx.fillStyle = pal.low_mid; fillMirroredCol(ctx, x, mid, midPx, colW);
    } else {
      if (Math.abs(lowPx - midPx) >= 0.4) { ctx.fillStyle = pal.mid; fillMirroredCol(ctx, x, mid, midPx, colW); }
      ctx.fillStyle = pal.low_mid; fillMirroredCol(ctx, x, mid, lowPx, colW);
    }
    ctx.fillStyle = pal.high; fillMirroredCol(ctx, x, mid, highPx, colW);
  }

  /** PWV6 overview column stacked from ``bottom``: total (high), low+mid (mid), low. */
  function paintStackedColumn(ctx, x, bottom, height, low, md, hi, colW, pal) {
    if (!pal) return;
    const layers = [[low + md + hi, pal.high], [low + md, pal.mid], [low, pal.low]];
    for (const [v, color] of layers) {
      const px = v / 255 * height;
      if (px < 0.4) continue;
      ctx.fillStyle = color;
      ctx.fillRect(x, Math.round(bottom - px), colW, Math.max(1, Math.round(px)));
    }
  }

  /** Max-pool: index of the loudest stacked column in [a, b). */
  function stackedPick(wave, a, b) {
    let best = a, top = -1;
    for (let i = a; i < Math.max(a + 1, b); i++) {
      const t = wave.low[i] + wave.mid[i] + wave.high[i];
      if (t > top) { top = t; best = i; }
    }
    return best;
  }
  /** Max-pool: index of the tallest height in [a, b). */
  function peakPick(h, a, b) {
    let best = a, top = -1;
    for (let i = a; i < Math.max(a + 1, b); i++) if (h[i] > top) { top = h[i]; best = i; }
    return best;
  }

  /* ---- Auto style: follow the player's 0xDA waveform colour ---- */
  const STYLES = ["rgb", "3band", "blue"];
  function resolveStyle(choice, playerColor, fallback) {
    if (choice !== "auto") return STYLES.includes(choice) ? choice : "rgb";
    if (STYLES.includes(playerColor)) return playerColor;
    return STYLES.includes(fallback) ? fallback : "rgb";
  }

  /* ---- Zoom maths (mirrors prolink/wavezoom.py) ---- */
  const ZOOM_MIN = 2, ZOOM_MAX = 30, ZOOM_STEP = 1.15;
  function clampSeconds(s) {
    s = +s;
    if (!isFinite(s)) return ZOOM_MIN;
    return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, s));
  }
  function wheelZoom(seconds, delta) {   // delta > 0 = wheel up = zoom in (Qt sign)
    return clampSeconds(clampSeconds(seconds) * Math.pow(ZOOM_STEP, -delta / 120));
  }
  function pinchZoom(seconds, startDist, dist) {
    if (!(startDist > 0) || !(dist > 0)) return clampSeconds(seconds);
    return clampSeconds(seconds * startDist / dist);
  }
  function xForMs(t, pos, visibleS, width, frac) {
    return width * frac + (t - pos) / (visibleS * 1000) * width;
  }

  return {
    MASK_KEYS, MASK_BITS, ZOOM_MIN, ZOOM_MAX, ZOOM_STEP,
    palettes, getPalette, normalizePalette, paintBandColumn, paintStackedColumn,
    stackedPick, peakPick, resolveStyle, clampSeconds, wheelZoom, pinchZoom, xForMs,
    readWaveform,
    readBands,
    readBlue,
    attachBands,
    attachBlue,
    decodeWavePack,
    overviewInk,
  };
});
