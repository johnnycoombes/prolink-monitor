"""A 32-beat loop on the overlay must not walk the highlight off the playhead.

The desktop model already folds a loop. The overlay and the web panel were
still scrolling the waveform by truncated CSS pixels and blitting that shift
in rounded device pixels. At 125% or 150% the bitmap and the green loop
region stopped sharing a time base, and the highlight drifted across the wave.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from prolink import link, proto
from prolink.playhead_smooth import correction_ms, sync_detail_origin
from gui.pages import extrapolate_playhead

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BPM = 123.2
BEATS = 32
WRAPS = 4
FPS = 60


def _loop_ms() -> float:
    return BEATS * 60000.0 / BPM


def _window(pos_ms: float, *, cps: float, visible_s: float, needle: float) -> float:
    cols = visible_s * cps
    return (pos_ms / 1000.0) * cps - cols * needle


def _offset(pos_ms: float, loop_start: float, *, width: float, needle: float,
            cps: float, visible_s: float) -> float:
    px_per_col = width / (visible_s * cps)
    start = _window(pos_ms, cps=cps, visible_s=visible_s, needle=needle)
    x0 = ((loop_start / 1000.0) * cps - start) * px_per_col
    return x0 - width * needle


def _coast(dpr: float, zoom_bars: int) -> dict:
    """Play a 32-beat loop and scroll the detail wave the way the overlay does."""
    loop_len = _loop_ms()
    start = 8000.0
    end = start + loop_len
    cps = 150.0
    needle = 0.25
    # One bar is four beats. Zoom is how many bars the detail window shows.
    visible_s = zoom_bars * 4 * 60.0 / BPM
    width = 1600.0
    px_per_col = width / (visible_s * cps)
    dt = 1.0 / FPS
    duration = (loop_len / 1000.0) * WRAPS
    cache = None
    broken_cache = None
    broken_visual = None
    max_resid_dev = 0.0
    max_broken_css = 0.0
    max_phase = 0.0
    wraps_seen = 0
    prev = None
    t = 0.0
    while t <= duration + dt:
        raw = start + t * 1000.0
        pos = link.wrap_loop_position(raw, True, start, end)
        ideal = start + (raw - start) % loop_len
        max_phase = max(max_phase, abs(pos - ideal))
        if pos < start or pos >= end:
            raise AssertionError(f"playhead {pos} left [{start}, {end})")
        window = _window(pos, cps=cps, visible_s=visible_s, needle=needle)
        if cache is None:
            cache = window
            broken_cache = window
            broken_visual = window
        new_cache, _shift, _full = sync_detail_origin(cache, window, px_per_col, width, dpr)
        cache = new_cache
        resid_dev = (cache - window) * px_per_col * dpr
        max_resid_dev = max(max_resid_dev, abs(resid_dev))
        # The old overlay truncated CSS pixels, then rounded the device blit.
        ahead = (broken_cache - window) * px_per_col
        shift_css = int(ahead)
        if shift_css != 0 and abs(shift_css) < width:
            moved_css = round(shift_css * dpr) / dpr
            broken_visual = broken_visual - moved_css / px_per_col
            broken_cache = broken_cache - shift_css / px_per_col
        elif abs(shift_css) >= width:
            broken_cache = window
            broken_visual = window
        broken_css = (broken_visual - window) * px_per_col
        max_broken_css = max(max_broken_css, abs(broken_css))
        # The highlight is a pure function of the playhead. Subtracting the
        # phase leaves zero on every frame, including the frame after a wrap.
        offset = _offset(pos, start, width=width, needle=needle, cps=cps, visible_s=visible_s)
        phase_px = ((pos - start) / 1000.0) * (width / visible_s)
        if abs(offset + phase_px) > 0.05:
            raise AssertionError(f"highlight {offset} left the playhead phase {phase_px}")
        if prev is not None and pos < prev:
            wraps_seen += 1
        prev = pos
        t += dt
    return {
        "max_phase_ms": max_phase,
        "max_resid_dev": max_resid_dev,
        "max_broken_css": max_broken_css,
        "wraps_seen": wraps_seen,
        "loop_ms": loop_len,
    }


class OverlayLoopDriftTests(unittest.TestCase):
    def test_32_beat_loop_at_123_2_bpm_does_not_drift(self):
        # 4-bar zoom: the loop is wider than the window, so a wrap repaints.
        # 16-bar zoom: the wrap is an incremental scroll. Both must hold.
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for zoom in (4, 16):
                with self.subTest(dpr=dpr, zoom=zoom):
                    out = _coast(dpr, zoom)
                    self.assertLess(out["max_phase_ms"], 1e-4)
                    self.assertLess(out["max_resid_dev"], 1.0)
                    self.assertGreaterEqual(out["wraps_seen"], WRAPS)
                    if dpr in (1.25, 1.5) and zoom == 4:
                        self.assertGreater(out["max_broken_css"], 20.0)

    def test_engine_folds_an_unwrapped_az_clock_across_wraps(self):
        loop_len = _loop_ms()
        start = 8000
        end = int(round(start + loop_len))
        length = end - start
        deck = link.Deck(1)
        now = 1_000.0
        status = proto.Status(
            track_id=7, play_state="looping", play_state_raw=0x04,
            pitch=1.0, beat_count=1, bpm=BPM,
            loop_start_ms=start, loop_end_ms=end,
        )
        # Absolute position already owns the playhead, as on the AZ. A beat
        # time of 0 must not be treated as a wrap back to the start of the track.
        deck._exact = True
        deck.status = status
        deck._model = (float(start), now, 1.0)
        deck.on_status(status, now)
        self.assertIsNotNone(deck.loop_window())
        offsets = []
        # Absolute position on some AZ builds keeps counting through the loop.
        for n in range(WRAPS * 8):
            raw = start + int(round(n * length / 8))
            deck.on_absolute_position(proto.AbsolutePosition(
                device_number=1, name="XDJ-AZ", track_length_s=501,
                position_ms=raw, pitch_percent=0.0, effective_bpm=BPM,
            ), now)
            pos = deck._model[0]
            self.assertGreaterEqual(pos, start)
            self.assertLess(pos, end)
            ideal = start + (raw - start) % (end - start)
            self.assertAlmostEqual(pos, ideal, delta=1.5)
            if n % 8 == 0:
                offsets.append(_offset(
                    pos, start, width=1600, needle=0.25, cps=150,
                    visible_s=4 * 4 * 60.0 / BPM))
        self.assertGreaterEqual(len(offsets), WRAPS)
        for offset in offsets[1:]:
            self.assertAlmostEqual(offset, offsets[0], delta=1.0)

    def test_coast_freezes_and_a_seek_or_loop_exit_snaps(self):
        held = extrapolate_playhead(
            1000, playing=True, speed=1.0, dt=5.0, packet_age_s=0.0,
            looping=True, loop_start_ms=0, loop_end_ms=50_000,
        )
        self.assertAlmostEqual(held, 2000, delta=1)
        inside = extrapolate_playhead(
            19_900, playing=True, speed=1.0, dt=0.25,
            looping=True, loop_start_ms=10_000, loop_end_ms=20_000,
        )
        self.assertAlmostEqual(inside, 10_150, delta=1)
        exited = extrapolate_playhead(
            25_000, playing=True, speed=1.0, dt=0.0,
            looping=False, loop_start_ms=10_000, loop_end_ms=20_000,
        )
        self.assertAlmostEqual(exited, 25_000, delta=1)
        sought = extrapolate_playhead(
            90_000, playing=True, speed=1.0, dt=0.0,
            looping=True, loop_start_ms=10_000, loop_end_ms=20_000,
        )
        # A published seek is the new anchor. dt 0 does not walk it, and the
        # position is still folded into the loop the player says is active.
        self.assertGreaterEqual(sought, 10_000)
        self.assertLess(sought, 20_000)

    def test_pages_use_the_shared_playhead(self):
        for name in ("overlay.html", "index.html"):
            with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
                html = f.read()
            self.assertIn('src="playhead.js"', html)
            self.assertIn("extrapolatePlayhead(", html)
            self.assertIn("syncDetailCache(", html)
            self.assertNotIn("function wrapLoopPosition", html)
            self.assertNotIn("shiftF | 0", html)
            self.assertNotIn("Math.round(shift * r)", html)


class PlayheadJsParityTests(unittest.TestCase):
    def test_js_matches_the_python_step(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = r"""
const ph = require("./web/playhead.js");
const bpm = 123.2;
const beats = 32;
const loopLen = beats * 60000 / bpm;
const start = 8000;
const end = start + loopLen;
const cases = [];
for (const err of [100, 40, -100, -20]) {
  for (const mode of ["direct", "normal", "strong"]) {
    cases.push({
      kind: "correction",
      error: err, speed: 1, mode, interval: 1/6,
      value: ph.correctionMs(err, 1, mode, 1/6),
    });
  }
}
const wraps = [];
let raw = start;
const dt = 1/60;
let cache = null;
let maxResid = 0;
const dpr = 1.25;
const zoomBars = 16;
const cps = 150;
const needle = 0.25;
const visible = zoomBars * 4 * 60 / bpm;
const width = 1600;
const pxPerCol = width / (visible * cps);
const offsets = [];
for (let t = 0; t <= (loopLen / 1000) * 4 + 1e-9; t += dt) {
  raw = start + t * 1000;
  const pos = ph.wrapLoopPosition(raw, true, start, end);
  const cols = visible * cps;
  const windowStart = (pos / 1000) * cps - cols * needle;
  if (cache == null) cache = windowStart;
  const synced = ph.syncDetailCache(cache, windowStart, pxPerCol, width, dpr);
  cache = synced.cacheStart;
  const resid = Math.abs((cache - windowStart) * pxPerCol * dpr);
  if (resid > maxResid) maxResid = resid;
  const offset = ph.loopHighlightOffsetPx(pos, start, {
    width, needle, cps, visibleSeconds: visible,
  });
  const phasePx = ((pos - start) / 1000) * (width / visible);
  offsets.push(Math.abs(offset + phasePx));
}
const model = {};
let followed = null;
const samples = [];
for (let n = 0; n < 32; n++) {
  const t = n * (loopLen / 1000) / 8;
  const reported = start + t * 1000;
  followed = ph.followSample(model, {
    position_ms: reported, playing: true, speed: 1, looping: true,
    loopStartMs: start, loopEndMs: end, mode: "normal", packetAgeS: 0,
  }, 1000 + t);
  let phase = (reported - start) % loopLen;
  if (phase < 0) phase += loopLen;
  if (!(phase < loopLen) || loopLen - phase < 1e-4) phase = 0;
  const ideal = start + phase;
  samples.push(Math.abs(followed - ideal));
}
const lastT = 31 * (loopLen / 1000) / 8;
const atHold = ph.projectSample(model, 1000 + lastT + 1);
const later = ph.projectSample(model, 1000 + lastT + 8);
console.log(JSON.stringify({
  cases, maxResid, offsets, samples, atHold, later, loopLen,
}));
"""
        proc = subprocess.run(
            [node, "-e", script], cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        for case in payload["cases"]:
            got = correction_ms(case["error"], case["speed"], case["mode"], case["interval"])
            self.assertAlmostEqual(case["value"], got, delta=1e-6)
        self.assertLess(payload["maxResid"], 1.0)
        self.assertGreater(len(payload["offsets"]), 100)
        for gap in payload["offsets"]:
            self.assertLess(gap, 0.05)
        for err in payload["samples"]:
            self.assertLess(err, 1.0)
        # No further packets: coast for one second, then hold.
        self.assertAlmostEqual(payload["atHold"], payload["later"], delta=1e-4)


if __name__ == "__main__":
    unittest.main()
