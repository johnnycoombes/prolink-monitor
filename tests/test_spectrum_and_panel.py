"""Waveform spectrum, fullscreen launch, and monitor-only web panel."""

from __future__ import annotations

import math
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
    def test_kiosk_flag_is_added(self):
        from prolink.panel_launch import with_kiosk_flag

        self.assertIn("kiosk=1", with_kiosk_flag("http://127.0.0.1:8777/"))
        flagged = with_kiosk_flag("http://127.0.0.1:8777/?readonly=1")
        self.assertIn("readonly=1", flagged)
        self.assertIn("kiosk=1", flagged)

    def test_windows_kiosk_uses_edge_fullscreen(self):
        from prolink import panel_launch

        edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        with mock.patch.object(panel_launch, "_windows_browser_candidates", return_value=[edge]):
            with mock.patch.object(panel_launch.os.path, "isfile", return_value=True):
                cmd = panel_launch.kiosk_command("http://127.0.0.1:9/", platform="win32")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], edge)
        self.assertIn("--kiosk", cmd)
        self.assertIn("--edge-kiosk-type=fullscreen", cmd)
        self.assertTrue(any(part.startswith("http://127.0.0.1:9/") and "kiosk=1" in part for part in cmd))

    def test_missing_browser_falls_back(self):
        from prolink import panel_launch

        with mock.patch.object(panel_launch, "kiosk_command", return_value=None):
            with mock.patch.object(panel_launch.webbrowser, "open") as opened:
                how = panel_launch.open_monitor_panel("http://127.0.0.1:9/")
        self.assertEqual(how, "browser")
        opened.assert_called_once()
        self.assertIn("kiosk=1", opened.call_args.args[0])


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
        self.assertIn('id="fs-prompt"', html)
        self.assertIn("manifest.webmanifest", html)
        self.assertIn("requestFullscreen", html)

    def test_overlay_panel_and_card_art(self):
        path = os.path.join(ROOT, "web", "overlay.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("panel-spectrum", html)
        self.assertIn("spectrum.js", html)
        self.assertIn("artHtml(deck, pack, 'small')", html)
        self.assertIn("artHtml(deck, pack, 'large')", html)
        self.assertIn("500px", html)
        self.assertIn("48px", html)

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
        self.assertGreaterEqual(w, 560)
        self.assertGreaterEqual(h, 820)


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
