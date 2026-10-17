"""1.0.24: web panel small-screen layout and dead-code cleanup."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _index():
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
        return f.read()


class SmallScreenCss(unittest.TestCase):
    def test_viewport_and_safe_area(self):
        src = _index()
        self.assertIn("viewport-fit=cover", src)
        self.assertIn("env(safe-area-inset-left)", src)

    def test_phone_and_tablet_grids(self):
        src = _index()
        self.assertRegex(src, r"@media \(max-width:900px\)\{[^@]*grid-template-columns:minmax\(0, 1fr\)")
        self.assertIn("@media (min-width:600px) and (max-width:900px)", src)
        self.assertIn("grid-template-columns:repeat(2, minmax(0, 1fr))", src)

    def test_cards_not_squeezed_and_no_sideways_scroll(self):
        block = re.search(r"@media \(max-width:900px\)\{(.*?)\n\}", _index(), re.S).group(1)
        self.assertIn(".deck{flex:none", block)
        self.assertIn("overflow-x:hidden", block)
        self.assertIn(".canvases{flex:none;height:clamp(", block)

    def test_touch_targets(self):
        src = _index()
        m = re.search(r"@media \(pointer:coarse\)\{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("min-height:36px", m.group(1))


class CleanupTests(unittest.TestCase):
    def test_removed_names_stay_removed(self):
        from prolink import anlz, artwork_util, hires_art, proto
        for mod, name in ((proto, "PORT_MIXER"), (proto, "TYPE_MIXER_CLOCK"),
                          (anlz, "decode_color_preview"), (anlz, "decode_blue"),
                          (artwork_util, "ARTWORK_SOURCES"), (hires_art, "mutagen_available")):
            self.assertFalse(hasattr(mod, name), name)
        # Ports the app does use are still there.
        self.assertEqual((proto.PORT_ANNOUNCE, proto.PORT_BEAT, proto.PORT_STATUS),
                         (50000, 50001, 50002))


if __name__ == "__main__":
    unittest.main()
