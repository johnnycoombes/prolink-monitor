/* Playhead between status packets, shared by the overlay and the web panel.

   The numbers match prolink/playhead_smooth.py and prolink.link.wrap_loop_position.
   Direct snaps. Normal caps a catch-up at ±15% of one packet ( ±8% under 80 ms).
   Strong is ±8% / ±4%. A seek, a loop wrap, a loop start or exit, and a
   reverse snap. The playhead coasts for one second after the last packet and
   then holds. While a loop is active the position is folded back into
   [loop_start, loop_end]. The scrolling waveform uses syncDetailCache so its
   origin stays on that same playhead. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProlinkPlayhead = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const MODES = {direct: 1, normal: 1, strong: 1};
  const NOMINAL_INTERVAL_S = 1 / 6;
  const SMALL_ERROR_MS = 80;
  const FREEZE_AFTER_S = 1;
  const JUMP_MS = 400;
  const LOOP_WRAP_MIN_MS = 20;
  const CAPS = {
    normal: [0.15, 0.08],
    strong: [0.08, 0.04],
  };

  function normalizeMode(value) {
    const mode = String(value || "normal").toLowerCase().trim();
    return MODES[mode] ? mode : "normal";
  }

  function correctionMs(errorMs, speed, mode, intervalS) {
    mode = normalizeMode(mode);
    const error = +errorMs || 0;
    if (mode === "direct") return error;
    const caps = CAPS[mode];
    const frac = Math.abs(error) >= SMALL_ERROR_MS ? caps[0] : caps[1];
    const interval = intervalS > 0 ? intervalS : NOMINAL_INTERVAL_S;
    const travel = Math.max(Math.abs(+speed || 0), 0.05) * 1000 * interval;
    const cap = frac * travel;
    if (error > 0) return Math.min(error, cap);
    return Math.max(error, -cap);
  }

  function freezeInstant(now, lastPacket, freezeAfter) {
    const hold = freezeAfter == null ? FREEZE_AFTER_S : freezeAfter;
    if (!(lastPacket > 0)) return now;
    if (now - lastPacket > hold) return lastPacket + hold;
    return now;
  }

  function wrapLoopPosition(pos, looping, start, end) {
    if (!looping || start == null || end == null) return +pos || 0;
    const a = +start;
    const b = +end;
    if (!(b > a)) return +pos || 0;
    const len = b - a;
    const p = +pos || 0;
    if (p >= b) return a + ((p - a) % len + len) % len;
    if (p < a && a - p < len) return a;
    return p;
  }

  function extrapolatePlayhead(opts) {
    opts = opts || {};
    let pos = +opts.posMs || 0;
    const playing = !!opts.playing;
    const speed = opts.speed == null ? (playing ? 1 : 0) : +opts.speed || 0;
    if (playing && speed) {
      const age = Math.max(0, +opts.packetAgeS || 0);
      const room = FREEZE_AFTER_S - age;
      const step = Math.min(Math.max(0, +opts.dt || 0), Math.max(0, room));
      pos += step * 1000 * speed;
    }
    pos = wrapLoopPosition(pos, !!opts.looping, opts.loopStartMs, opts.loopEndMs);
    const dur = +opts.durationMs || 0;
    if (dur) pos = Math.min(pos, dur);
    return Math.max(0, pos);
  }

  /** First visible detail column so the needle stays on the playhead. */
  function detailWindowStart(posMs, cps, visibleSeconds, needle) {
    const cols = visibleSeconds * cps;
    return (posMs / 1000) * cps - cols * needle;
  }

  /**
   * CSS pixels from the fixed needle to the loop-start edge.
   * Constant for a given phase inside the loop, on every wrap.
   */
  function loopHighlightOffsetPx(posMs, loopStart, opts) {
    const width = opts.width;
    const needle = opts.needle;
    const cps = opts.cps;
    const visibleSeconds = opts.visibleSeconds;
    const start = detailWindowStart(posMs, cps, visibleSeconds, needle);
    const pxPerCol = width / (visibleSeconds * cps);
    const x0 = ((loopStart / 1000) * cps - start) * pxPerCol;
    return x0 - width * needle;
  }

  /**
   * Shift a detail cache onto the playhead window.
   * shiftDevice > 0 means the bitmap moves right (playhead jumped backward).
   * full means the caller should repaint the whole view at trueStart.
   */
  function syncDetailCache(cacheStart, trueStart, pxPerCol, cssWidth, dpr) {
    if (!(pxPerCol > 0) || !(cssWidth > 0)) {
      return {cacheStart: trueStart, shiftDevice: 0, full: true};
    }
    const scale = dpr > 0 ? dpr : 1;
    const aheadCss = (cacheStart - trueStart) * pxPerCol;
    const shiftDevice = Math.trunc(aheadCss * scale);
    const viewDevice = Math.round(cssWidth * scale);
    if (shiftDevice === 0) {
      return {cacheStart: cacheStart, shiftDevice: 0, full: false};
    }
    if (Math.abs(shiftDevice) >= Math.max(1, viewDevice)) {
      return {cacheStart: trueStart, shiftDevice: 0, full: true};
    }
    const movedCss = shiftDevice / scale;
    return {
      cacheStart: cacheStart - movedCss / pxPerCol,
      shiftDevice: shiftDevice,
      full: false,
    };
  }

  /**
   * One status sample. Snaps on a seek, a reverse, a loop start or exit, and
   * a backward wrap. Smaller errors are capped. The anchor is then folded
   * into the active loop.
   */
  function followSample(model, sample, now) {
    model = model || {};
    const reported = +sample.position_ms || 0;
    const speed = sample.playing ? (+sample.speed || 0) : 0;
    const looping = !!sample.looping;
    const loopStart = sample.loopStartMs;
    const loopEnd = sample.loopEndMs;
    const mode = normalizeMode(sample.mode);
    const packetAt = now - Math.max(0, +sample.packetAgeS || 0);

    function commit(pos, snap) {
      const folded = wrapLoopPosition(pos, looping, loopStart, loopEnd);
      model.anchorMs = folded;
      model.anchorAt = now;
      model.speed = speed;
      model.looping = looping;
      model.loopStart = loopStart;
      model.loopEnd = loopEnd;
      model.packetAt = packetAt;
      model.mode = mode;
      model.primed = true;
      model.snapped = snap;
      return folded;
    }

    if (!model.primed) return commit(reported, true);

    const when = freezeInstant(now, model.packetAt);
    let projected = model.anchorMs + (when - model.anchorAt) * 1000 * (model.speed || 0);
    projected = wrapLoopPosition(projected, model.looping, model.loopStart, model.loopEnd);
    const error = reported - projected;
    const reversed = model.speed && speed && (model.speed > 0) !== (speed > 0);
    const loopChanged = !!model.looping !== looping;
    const seek = Math.abs(error) > JUMP_MS || reversed || loopChanged || (!speed && model.speed);
    const wrap = looping && error < -LOOP_WRAP_MIN_MS;
    if (seek || wrap) return commit(reported, true);

    const interval = now > model.anchorAt ? now - model.anchorAt : NOMINAL_INTERVAL_S;
    const step = correctionMs(error, speed || model.speed, mode, interval);
    return commit(projected + step, false);
  }

  function projectSample(model, now, durationMs) {
    if (!model || !model.primed) return 0;
    const when = freezeInstant(now, model.packetAt);
    let pos = model.anchorMs + (when - model.anchorAt) * 1000 * (model.speed || 0);
    pos = wrapLoopPosition(pos, model.looping, model.loopStart, model.loopEnd);
    if (durationMs) pos = Math.min(pos, +durationMs);
    return Math.max(0, pos);
  }

  // Absolute bar.beat in 4/4 from the status beat number (matches
  // prolink.fields.bar_beat). beatInBar (1-4) anchors the downbeat.
  function barBeat(beat, beatInBar) {
    const b = Math.floor(+beat || 0);
    const k = Math.floor(+beatInBar || 0);
    if (!(b > 0) || b >= 0x7FFFFFFF) return null;
    if (k >= 1 && k <= 4 && b >= k) return [Math.floor((b - k) / 4) + 1, k];
    return [Math.floor((b - 1) / 4) + 1, ((b - 1) % 4) + 1];
  }

  function barBeatText(beat, beatInBar) {
    const got = barBeat(beat, beatInBar);
    return got ? ('Bar ' + got[0] + '.' + got[1]) : '';
  }

  return {
    NOMINAL_INTERVAL_S: NOMINAL_INTERVAL_S,
    SMALL_ERROR_MS: SMALL_ERROR_MS,
    FREEZE_AFTER_S: FREEZE_AFTER_S,
    JUMP_MS: JUMP_MS,
    LOOP_WRAP_MIN_MS: LOOP_WRAP_MIN_MS,
    normalizeMode: normalizeMode,
    correctionMs: correctionMs,
    freezeInstant: freezeInstant,
    wrapLoopPosition: wrapLoopPosition,
    extrapolatePlayhead: extrapolatePlayhead,
    detailWindowStart: detailWindowStart,
    loopHighlightOffsetPx: loopHighlightOffsetPx,
    syncDetailCache: syncDetailCache,
    followSample: followSample,
    projectSample: projectSample,
    barBeat: barBeat,
    barBeatText: barBeatText,
  };
});
