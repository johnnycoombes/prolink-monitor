"""Waveform spectrum, fullscreen launch, and monitor-only web panel."""

from __future__ import annotations

import math
import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SpectrumJsTests(unittest.TestCase):
    def test_low_energy_is_on_the_left_and_pause_decays(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = r"""
const {spectrumBars} = require("./web/spectrum.js");
const n = 64;
const low = new Array(n).fill(31);
const mid = new Array(n).fill(0);
const high = new Array(n).fill(0);
const playing = spectrumBars({
  low, mid, high, columns: n, column: 32, peak: 31,
  dt: 1, playing: true,
});
const left = playing.levels.slice(0, 8).reduce((a, b) => a + b, 0);
const right = playing.levels.slice(-8).reduce((a, b) => a + b, 0);
if (!(left > right)) {
  console.error("left", left, "right", right);
  process.exit(1);
}
const paused = spectrumBars({
  low, mid, high, columns: n, column: 32, peak: 31,
  prev: playing.levels, dt: 1, playing: false,
});
const before = playing.levels.reduce((a, b) => a + b, 0);
const after = paused.levels.reduce((a, b) => a + b, 0);
if (!(after < before * 0.8)) {
  console.error("decay", before, after);
  process.exit(1);
}
"""
        proc = subprocess.run(
            [node, "-e", script], cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class LoopbackBarTests(unittest.TestCase):
    def test_low_tone_lands_on_the_left(self):
        from prolink.loopback_audio import bars_from_samples

        rate = 44100
        n = 2048
        freq = 80.0
        samples = [math.sin(2 * math.pi * freq * i / rate) for i in range(n)]
        bars = bars_from_samples(samples, 48, rate)
        self.assertEqual(len(bars), 48)
        peak = max(range(len(bars)), key=lambda i: bars[i])
        self.assertLess(peak, 16)
        self.assertGreater(bars[peak], bars[-1])

    def test_silence_is_flat(self):
        from prolink.loopback_audio import bars_from_samples

        bars = bars_from_samples([0.0] * 256, 16, 44100)
        self.assertTrue(all(v == 0.0 for v in bars))


class PanelLaunchTests(unittest.TestCase):
    def test_panel_opens_a_normal_tab(self):
        from prolink import panel_launch

        with mock.patch.object(panel_launch.webbrowser, "open") as opened:
            how = panel_launch.open_monitor_panel("http://127.0.0.1:9/?readonly=1")
        self.assertEqual(how, "browser")
        opened.assert_called_once_with("http://127.0.0.1:9/?readonly=1")
        with open(panel_launch.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("--kiosk", src)
        self.assertNotIn("--edge-kiosk-type", src)
        self.assertNotIn("--start-fullscreen", src)


class DisplayPrefTests(unittest.TestCase):
    def test_monitor_fields_round_trip(self):
        from gui.settings import display_prefs_from

        prefs = display_prefs_from({
            "max_decks": 2,
            "zoom_bars": 8,
            "zoom_bars_by_deck": {"1": 16, "nope": 3},
            "waveform_style": "3band",
            "playhead_position": "left",
            "show_phrases": False,
            "show_empty_decks": False,
            "deck_show_waveform": False,
            "overlay_spectrum_audio": False,
        })
        self.assertEqual(prefs["max_decks"], 2)
        self.assertEqual(prefs["zoom_bars"], 8)
        self.assertEqual(prefs["zoom_bars_by_deck"], {"1": 16})
        self.assertEqual(prefs["waveform_style"], "3band")
        self.assertEqual(prefs["playhead_position"], "left")
        self.assertFalse(prefs["show_phrases"])
        self.assertFalse(prefs["show_empty_decks"])
        self.assertFalse(prefs["elements"]["deck_show_waveform"])
        self.assertTrue(prefs["elements"]["deck_show_artwork"])
        self.assertEqual(prefs["spectrum_source"], "waveform")
        self.assertTrue(prefs["show_spectrum"])

    def test_spectrum_toggle_is_separate_from_audio_input(self):
        from gui.settings import DEFAULTS, display_prefs_from, save_settings, load_settings

        prefs = display_prefs_from({
            "overlay_show_spectrum": False,
            "overlay_spectrum_audio": True,
            "show_phrases": False,
        })
        self.assertFalse(prefs["show_spectrum"])
        self.assertFalse(prefs["show_phrases"])
        self.assertEqual(prefs["spectrum_source"], "loopback")
        self.assertTrue(DEFAULTS["overlay_show_spectrum"])
        with mock.patch.dict(os.environ, {"PROLINK_CONFIG_DIR": self._tmp()}):
            save_settings({
                "show_phrases": False,
                "overlay_show_spectrum": False,
                "overlay_spectrum_audio": False,
            })
            loaded = load_settings()
        self.assertFalse(loaded["show_phrases"])
        self.assertFalse(loaded["overlay_show_spectrum"])
        self.assertFalse(loaded["overlay_spectrum_audio"])
        again = display_prefs_from(loaded)
        self.assertFalse(again["show_phrases"])
        self.assertFalse(again["show_spectrum"])
        self.assertEqual(again["spectrum_source"], "waveform")

    def _tmp(self) -> str:
        import tempfile
        path = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(path, ignore_errors=True))
        return path

    def test_display_false_is_not_forced_back_on(self):
        from app import Monitor

        mon = Monitor.__new__(Monitor)
        mon.show_phrases = True
        mon.show_spectrum = True
        mon._display = {
            "show_phrases": False,
            "show_spectrum": False,
            "spectrum_source": "waveform",
        }
        prefs = Monitor._display_prefs(mon)
        self.assertFalse(prefs["show_phrases"])
        self.assertFalse(prefs["show_spectrum"])

    def test_loopback_is_opt_in(self):
        from gui.settings import display_prefs_from

        self.assertEqual(display_prefs_from({})["spectrum_source"], "waveform")
        self.assertEqual(
            display_prefs_from({"overlay_spectrum_audio": True})["spectrum_source"],
            "loopback",
        )


class WebPanelSourceTests(unittest.TestCase):
    def test_panel_has_no_settings_controls(self):
        path = os.path.join(ROOT, "web", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn('id="zoom-switch"', html)
        self.assertNotIn('id="deck-switch"', html)
        self.assertNotIn('id="wave-switch"', html)
        self.assertNotIn('id="needle-switch"', html)
        self.assertNotIn("localStorage", html)
        self.assertIn("applyMonitorDisplay", html)
        self.assertIn('id="fs-btn"', html)
        self.assertNotIn('id="fs-prompt"', html)
        self.assertNotIn("showFullscreenPrompt", html)
        self.assertIn("manifest.webmanifest", html)
        self.assertIn("requestFullscreen", html)
        self.assertIn("decodeWavePack", html)
        self.assertNotIn("buf[off]", html)

    def test_panel_has_no_library_ui(self):
        path = os.path.join(ROOT, "web", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn('id="p-lib"', html)
        self.assertNotIn("/api/library", html)
        self.assertNotIn("e.library", html)

    def test_panel_layout_matches_monitor_cards(self):
        from gui.deck_layout import (
            ACCENT_PX, ART_PX, ART_RADIUS, BODY_GAP, BODY_MARGINS, BPM_PX,
            CARD_GAP, CARD_RADIUS, DECK_ORDER, IDENT_COLLAPSED, IDENT_WIDTH,
            OVERVIEW_FRAC, OVERVIEW_MAX, OVERVIEW_MIN, PAGE_MARGINS, PHASE_H,
            PHASE_W, PHRASE_H, PITCH_PX, READOUT_PANEL_MAX, READOUT_PANEL_MIN,
            TIME_PX,
        )

        path = os.path.join(ROOT, "web", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        start = html.find('<script id="monitor-layout"')
        self.assertGreater(start, 0)
        blob = html[start:html.find("</script>", start)]
        data = json.loads(blob[blob.find("{"):blob.rfind("}") + 1])
        self.assertEqual(data["identWidth"], IDENT_WIDTH)
        self.assertEqual(data["identCollapsed"], IDENT_COLLAPSED)
        self.assertEqual(data["readoutMin"], READOUT_PANEL_MIN)
        self.assertEqual(data["readoutMax"], READOUT_PANEL_MAX)
        self.assertEqual(data["art"], ART_PX)
        self.assertEqual(data["artRadius"], ART_RADIUS)
        self.assertEqual(data["accent"], ACCENT_PX)
        self.assertEqual(data["cardRadius"], CARD_RADIUS)
        self.assertEqual(data["cardGap"], CARD_GAP)
        self.assertEqual(data["pageMargins"], list(PAGE_MARGINS))
        self.assertEqual(data["bodyMargins"], list(BODY_MARGINS))
        self.assertEqual(data["bodyGap"], BODY_GAP)
        self.assertEqual(data["phraseH"], PHRASE_H)
        self.assertEqual(data["overviewFrac"], OVERVIEW_FRAC)
        self.assertEqual(data["overviewMin"], OVERVIEW_MIN)
        self.assertEqual(data["overviewMax"], OVERVIEW_MAX)
        self.assertEqual(data["bpm"], BPM_PX)
        self.assertEqual(data["pitch"], PITCH_PX)
        self.assertEqual(data["time"], TIME_PX)
        self.assertEqual(data["phaseW"], PHASE_W)
        self.assertEqual(data["phaseH"], PHASE_H)
        self.assertEqual(data["order2"], list(DECK_ORDER[2]))
        self.assertEqual(data["order4"], list(DECK_ORDER[4]))
        self.assertIn('class="accent"', html)
        self.assertIn('class="tags" data-tags', html)
        self.assertNotIn("num-row", html)
        self.assertIn("applyMonitorLayout", html)
        self.assertIn("layoutWave", html)

    def test_overlay_panel_and_card_art(self):
        path = os.path.join(ROOT, "web", "overlay.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("panel-spectrum", html)
        self.assertIn("showSpectrum ? '<canvas class=\"panel-spectrum\"></canvas>' : ''", html)
        self.assertIn("spectrum.js", html)
        self.assertIn("artHtml(deck, pack, 'small')", html)
        self.assertIn("artHtml(deck, pack, 'large')", html)
        self.assertIn("500px", html)
        self.assertIn("48px", html)
        self.assertIn("decodeWavePack", html)
        self.assertIn("wavepack.js", html)
        self.assertIn("max-width:100%", html)
        self.assertIn("#root.embed-mode.now-card-mode", html)

    def test_no_settings_endpoint(self):
        path = os.path.join(ROOT, "app.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("/api/settings", src)
        self.assertNotIn("def do_POST", src)
        self.assertNotIn("def do_PUT", src)


class FloatingDefaultSizeTests(unittest.TestCase):
    def test_card_default_is_taller_for_large_art(self):
        from gui.floating_now import FloatingNowPlayingWindow

        w, h = FloatingNowPlayingWindow.CARD_DEFAULT_SIZE
        # 500px art, text, the overview wave, inset, and the drag handle.
        self.assertGreaterEqual(w, 680)
        self.assertGreaterEqual(h, 940)
        pw, ph = FloatingNowPlayingWindow.PANEL_DEFAULT_SIZE
        self.assertGreaterEqual(pw, 960)
        self.assertGreaterEqual(ph, 320)


class ArtworkSizeTests(unittest.TestCase):
    def test_small_uses_thumbnail_and_skips_audio(self):
        from prolink import artwork as artmod

        calls = []

        def fetch(remote, cache_name):
            calls.append(cache_name)
            if cache_name.startswith("art_"):
                return b"\xff\xd8\xff" + b"t" * 80
            return None

        media = mock.Mock()
        media._fetch = fetch
        track = mock.Mock(
            file_path="contents/track.mp3",
            artwork_path="PIONEER/Artwork/a.jpg",
            artwork_id=9,
        )
        data, source = artmod.resolve_artwork(
            track, media, host="192.0.2.10", slot="usb",
            open_remotedb=lambda: None, local_music_root="", size="small",
        )
        self.assertEqual(source, "thumbnail")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        self.assertFalse(any("audiohead" in name for name in calls))

    def test_large_still_prefers_embedded_over_thumbnail(self):
        import os
        import shutil
        import tempfile

        from prolink import artwork as artmod

        media = mock.Mock()
        media._fetch = mock.Mock(return_value=None)
        track = mock.Mock(
            file_path="contents/t.mp3",
            artwork_path="PIONEER/Artwork/a.jpg",
            artwork_id=0,
        )
        root = tempfile.mkdtemp()
        try:
            dest = os.path.join(root, "t.mp3")
            with open(dest, "wb") as f:
                f.write(b"ID3" + b"\x00" * 32)
            with mock.patch.object(
                artmod.embedded_art, "extract_embedded_cover",
                return_value=b"\xff\xd8\xff" + b"E" * 40,
            ):
                _data, source = artmod.resolve_artwork(
                    track, media, host="192.0.2.10", slot="usb",
                    open_remotedb=None, local_music_root=root, size="large",
                )
            self.assertEqual(source, "embedded")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
