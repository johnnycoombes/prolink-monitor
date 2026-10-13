"""3-band waveform (1.0.10): byte order, PWV6 overview, palettes, Auto style."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import unittest

from prolink import anlz, wavestyle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tag(fourcc: bytes, header_extra: bytes, payload: bytes) -> bytes:
    header_len = 12 + len(header_extra)
    return fourcc + struct.pack(">II", header_len, header_len + len(payload)) + header_extra + payload


def anlz_2ex(pwv7: bytes | None = None, pwv6: bytes | None = None) -> bytes:
    """A minimal .2EX: PMAI header, then PWV7 (24-byte header) / PWV6 (20-byte)."""
    tags = b""
    if pwv7 is not None:
        tags += _tag(b"PWV7", struct.pack(">III", 3, len(pwv7) // 3, 0x960000), pwv7)
    if pwv6 is not None:
        tags += _tag(b"PWV6", struct.pack(">II", 3, len(pwv6) // 3), pwv6)
    head_len = 28
    return b"PMAI" + struct.pack(">II", head_len, head_len + len(tags)) + bytes(16) + tags


def node_eval(script: str):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node is not installed")
    out = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True,
                         text=True, check=True, timeout=30)
    return json.loads(out.stdout)


class ByteOrderTests(unittest.TestCase):
    def tearDown(self):
        anlz.set_pwv7_order(anlz.DEFAULT_PWV7_ORDER)

    def test_default_is_beat_link_order(self):
        self.assertEqual(anlz.DEFAULT_PWV7_ORDER, "mid_high_low")
        self.assertEqual(anlz.pwv7_order(), "mid_high_low")
        self.assertEqual(anlz.normalize_band_order("junk"), "mid_high_low")

    def test_pwv7_fixture_mid_high_low(self):
        # One column: mid=100, high=50, low=200 in Beat Link's order.
        a = anlz.parse(anlz_2ex(pwv7=bytes([100, 50, 200])))
        self.assertEqual(a.band3_detail, bytes([100, 50, 200]))
        lows, mids, highs = anlz.decode_3band_heights(a.band3_detail, "mid_high_low")
        self.assertEqual((lows[0], mids[0], highs[0]), (80, 30, 3))   # 200*.4, 100*.3, 50*.06

    def test_pwv7_fixture_low_mid_high(self):
        # Same bytes read in OneLibraryCompanion's order: low=100, mid=50, high=200.
        a = anlz.parse(anlz_2ex(pwv7=bytes([100, 50, 200])))
        lows, mids, highs = anlz.decode_3band_heights(a.band3_detail, "low_mid_high")
        self.assertEqual((lows[0], mids[0], highs[0]), (40, 15, 12))

    def test_setting_switches_the_global_order_and_logs(self):
        data = bytes([0, 0, 120])   # a kick: only the low byte in Beat Link order
        with self.assertLogs("prolink.anlz", level="INFO") as logs:
            self.assertEqual(anlz.set_pwv7_order("low_mid_high"), "low_mid_high")
        self.assertIn("low_mid_high", "\n".join(logs.output))
        lows, _, highs = anlz.decode_3band_heights(data)
        self.assertEqual((lows[0], highs[0]), (0, 7))
        anlz.set_pwv7_order("mid_high_low")
        lows, _, highs = anlz.decode_3band_heights(data)
        self.assertEqual((lows[0], highs[0]), (48, 0))

    def test_rgb_fallback_follows_the_order(self):
        _, rgb_bl = anlz.decode_3band(bytes([0, 0, 255]), "mid_high_low")
        _, rgb_olc = anlz.decode_3band(bytes([255, 0, 0]), "low_mid_high")
        self.assertEqual(bytes(rgb_bl), bytes([0, 0, 255]))   # low -> blue
        self.assertEqual(bytes(rgb_olc), bytes([0, 0, 255]))


class Pwv6OverviewTests(unittest.TestCase):
    def test_pwv6_fixture_parses_in_mid_high_low(self):
        a = anlz.parse(anlz_2ex(pwv6=bytes([10, 20, 30, 40, 50, 60])))
        self.assertEqual(a.band3_preview, bytes([10, 20, 30, 40, 50, 60]))
        self.assertEqual(anlz.PWV6_ORDER, "mid_high_low")

    def test_stacked_weights_and_peak_normalisation(self):
        # Column 0: low 255 only. Column 1: half of everything.
        data = bytes([0, 0, 255,  128, 128, 128])
        lo, md, hi = anlz.stacked_overview(data, 2)
        totals = [lo[i] + md[i] + hi[i] for i in range(2)]
        self.assertEqual(max(totals), 255)            # loudest stacked column is full scale
        # Column 1 total: 128*(.49+.32+.25) = 135.7 is the peak; column 0: 255*.49 = 124.95
        self.assertEqual(totals[1], 255)
        self.assertAlmostEqual(lo[0], round(124.95 * 255 / 135.68), delta=1)
        self.assertGreater(lo[1], md[1])
        self.assertGreater(md[1], hi[1])               # weights 0.49 > 0.32 > 0.25

    def test_max_pool_keeps_the_transient(self):
        data = bytes([0, 0, 10] * 9 + [0, 0, 250])     # one loud column among ten
        lo, _, _ = anlz.stacked_overview(data, 1)
        self.assertEqual(lo[0], 255)

    def test_upsampling_repeats(self):
        lo, _, _ = anlz.stacked_overview(bytes([0, 0, 100]), 4)
        self.assertEqual(list(lo), [255] * 4)

    def _pack(self, stacked: bool) -> bytes:
        import app
        n = 4
        h, rgb = bytes([10] * n), bytes([1] * n * 3)
        lo, md, hi = bytes([100, 0, 50, 5]), bytes([60, 0, 40, 5]), bytes([20, 0, 30, 5])
        flags = app.BANDS_STACKED if stacked else 0
        return (app._pack_wave(h, rgb, 150.0, 1000) + app._pack_wave(h, rgb, 0.0, 1000)
                + app._pack_bands(lo, md, hi, 150.0, 1000)
                + app._pack_bands(lo, md, hi, 0.0, 1000, flags))

    def test_stacked_flag_round_trips_to_qt(self):
        from gui.widgets import parse_waveform
        detail, overview = parse_waveform(self._pack(True))
        self.assertTrue(overview["band_stacked"])
        self.assertEqual(overview["band_peak"], 255)
        self.assertFalse(detail["band_stacked"])
        _, plain = parse_waveform(self._pack(False))
        self.assertFalse(plain["band_stacked"])     # PWV7 downsample fallback stays mirrored

    def test_stacked_flag_round_trips_to_js(self):
        import base64
        b64 = base64.b64encode(self._pack(True)).decode()
        got = node_eval(
            "const w=require('./web/wavepack.js');const b=Buffer.from('%s','base64');"
            "const ab=b.buffer.slice(b.byteOffset,b.byteOffset+b.length);const r=w.decodeWavePack(ab);"
            "console.log(JSON.stringify([r.overview.bandStacked,r.overview.bandPeak,!!r.detail.bandStacked,"
            "w.stackedPick(r.overview,0,4)]))" % b64)
        self.assertEqual(got, [True, 255, False, 0])


class PaletteParityTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "web", "wavepalette.json"), encoding="utf-8") as f:
            self.doc = json.load(f)

    def test_palettes_are_the_agreed_sets(self):
        bl, cdj = self.doc["palettes"]["beatlink"], self.doc["palettes"]["cdj3000"]
        self.assertEqual((bl["low"], bl["mid"], bl["low_mid"], bl["high"]),
                         ("#2053d9", "#f2aa3c", "#a96b27", "#ffffff"))
        self.assertEqual([cdj[k] for k in wavestyle.MASK_KEYS],
                         ["#0055e1", "#ffa600", "#ffffff", "#b4690a", "#d2dcfa", "#fff0d7", "#f5ebd7"])
        self.assertEqual(cdj["mode"], "mask")

    def test_qt_reads_the_same_file(self):
        from gui.widgets import BAND_LOW, BAND_MID, BAND_OVERLAP, wave_palette
        for name, raw in self.doc["palettes"].items():
            pal = wave_palette(name)
            for key in wavestyle.MASK_KEYS:
                self.assertEqual(pal[key].name(), raw[key].lower(), (name, key))
        self.assertEqual(BAND_LOW, (32, 83, 217))
        self.assertEqual(BAND_MID, (242, 170, 60))
        self.assertEqual(BAND_OVERLAP, (169, 107, 39))

    def test_js_reads_the_same_file(self):
        got = node_eval(
            "const w=require('./web/wavepack.js');"
            "console.log(JSON.stringify({b:w.getPalette('beatlink'),c:w.getPalette('cdj3000'),"
            "x:w.normalizePalette('nope')}))")
        self.assertEqual(got["b"], self.doc["palettes"]["beatlink"])
        self.assertEqual(got["c"], self.doc["palettes"]["cdj3000"])
        self.assertEqual(got["x"], "beatlink")

    def test_pages_have_no_private_band_colours(self):
        literals = re.compile(r"rgb\(32,\s*83,\s*217\)|rgb\(242,\s*170,\s*60\)|rgb\(169,\s*107,\s*39\)"
                              r"|#0055e1|#ffa600|#b4690a", re.I)
        for name in ("index.html", "overlay.html"):
            with open(os.path.join(ROOT, "web", name), encoding="utf-8") as f:
                html = f.read()
            self.assertIsNone(literals.search(html), name)
            self.assertIn("<!--WAVE_PALETTES-->", html, name)
        with open(os.path.join(ROOT, "gui", "widgets.py"), encoding="utf-8") as f:
            self.assertNotIn("(32, 83, 217)", f.read())

    def test_mask_fill_matches_between_qt_and_js(self):
        """Same column, same palette: the same colour at every height."""
        from PySide6.QtGui import QColor, QImage, QPainter
        from gui.widgets import paint_band_column, wave_palette

        heights = (30.0, 18.0, 8.0)          # low, mid, high in px
        H = 80
        for name in ("beatlink", "cdj3000"):
            img = QImage(1, H, QImage.Format_RGB32)
            img.fill(QColor("#000000"))
            p = QPainter(img)
            paint_band_column(p, 0, H / 2, *heights, wave_palette(name))
            p.end()
            qt = [img.pixelColor(0, y).name() for y in range(H)]
            js = node_eval(
                "const w=require('./web/wavepack.js');const H=%d;const col=new Array(H).fill('#000000');"
                "const ctx={fillStyle:'',fillRect(x,y,cw,h){for(let i=Math.max(0,y);i<Math.min(H,y+h);i++)col[i]=this.fillStyle;}};"
                "w.paintBandColumn(ctx,0,H/2,%f,%f,%f,1,w.getPalette('%s'));console.log(JSON.stringify(col))"
                % (H, *heights, name))
            mismatches = [y for y in range(H) if qt[y] != js[y].lower()]
            # Rounding of the mirrored edge may differ by one row at each band boundary.
            self.assertLessEqual(len(mismatches), 8, (name, mismatches))
            self.assertEqual(qt[H // 2], js[H // 2].lower(), name)

    def test_cdj3000_mask_colours_where_all_bands_overlap(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from gui.widgets import paint_band_column, wave_palette

        img = QImage(1, 80, QImage.Format_RGB32)
        img.fill(QColor("#000000"))
        p = QPainter(img)
        paint_band_column(p, 0, 40, 30, 18, 8, wave_palette("cdj3000"))
        p.end()
        self.assertEqual(img.pixelColor(0, 40).name(), "#f5ebd7")   # all three
        self.assertEqual(img.pixelColor(0, 40 - 12).name(), "#b4690a")  # low + mid
        self.assertEqual(img.pixelColor(0, 40 - 25).name(), "#0055e1")  # low only


class AutoStyleTests(unittest.TestCase):
    def test_status_byte_mapping(self):
        self.assertEqual(wavestyle.style_for_player_byte(0x01), "blue")
        self.assertEqual(wavestyle.style_for_player_byte(0x03), "rgb")
        self.assertEqual(wavestyle.style_for_player_byte(0x04), "3band")
        self.assertIsNone(wavestyle.style_for_player_byte(0x02))
        from prolink import proto
        self.assertEqual(proto._WAVEFORM_COLORS, {1: "blue", 3: "rgb", 4: "3band"})

    def test_resolve(self):
        r = wavestyle.resolve_style
        self.assertEqual(r("auto", "3band", "rgb"), "3band")
        self.assertEqual(r("auto", "blue", "rgb"), "blue")
        self.assertEqual(r("auto", None, "blue"), "blue")    # player silent: user's fallback
        self.assertEqual(r("auto", None, "auto"), "rgb")
        self.assertEqual(r("rgb", "3band", "blue"), "rgb")   # a fixed choice wins

    def test_js_resolve_matches(self):
        cases = [["auto", "3band", "rgb"], ["auto", "blue", "rgb"], ["auto", None, "blue"],
                 ["auto", None, "auto"], ["rgb", "3band", "blue"], ["3band", None, "rgb"]]
        got = node_eval("const w=require('./web/wavepack.js');console.log(JSON.stringify(%s.map(c=>w.resolveStyle(...c))))"
                        % json.dumps(cases))
        self.assertEqual(got, [wavestyle.resolve_style(*c) for c in cases])

    def test_settings_accept_auto_palette_and_order(self):
        from gui.settings import _sanitize, display_prefs_from
        out = _sanitize({"waveform_style": "auto", "waveform_palette": "cdj3000",
                         "pwv7_order": "low_mid_high", "overlay_waveform_style": "auto",
                         "waveform_style_fallback": "auto"})
        self.assertEqual(out["waveform_style"], "auto")
        self.assertEqual(out["overlay_waveform_style"], "auto")
        self.assertEqual(out["waveform_style_fallback"], "rgb")
        self.assertEqual(out["pwv7_order"], "low_mid_high")
        prefs = display_prefs_from(out)
        self.assertEqual(prefs["waveform_palette"], "cdj3000")

    def test_deck_card_follows_the_player(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from gui.i18n import I18n
        from gui.widgets import DeckCard
        card = DeckCard(1, I18n())
        card.set_wave_style("auto", "blue")
        self.assertEqual(card.wave._style, "blue")
        card._last_deck = {"waveform_color": "3band"}
        card._apply_wave_style()
        self.assertEqual(card.wave._style, "3band")


if __name__ == "__main__":
    unittest.main()
