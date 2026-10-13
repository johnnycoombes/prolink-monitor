"""Phosphor icons ship in assets/icons and render through gui.icons."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui import icons


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_icon_exists(self):
        for name in (icons.NOTE, icons.LOOP, icons.LIST, icons.STAR,
                     icons.STAR_FILL, icons.STAR_HALF, icons.GRIP):
            self.assertTrue(os.path.isfile(icons.icon_path(name)), name)

    def test_pixmap_is_recoloured(self):
        pix = icons.icon_pixmap(icons.STAR_FILL, "#ff0000", 24, 1.0)
        image = pix.toImage()
        c = image.pixelColor(12, 12)
        self.assertGreater(c.alpha(), 200)
        self.assertGreater(c.red(), 200)
        self.assertLess(c.green(), 40)

    def test_stars_html(self):
        html = icons.stars_html(3)
        self.assertEqual(html.count("star-fill-"), 3)
        self.assertEqual(html.count("<img"), 5)
        self.assertEqual(icons.stars_html(0), "")

    def test_web_pages_have_no_glyph_icons(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for page in ("index.html", "overlay.html"):
            with open(os.path.join(root, "web", page), encoding="utf-8") as fh:
                text = fh.read()
            for glyph in ("♪", "&#9834;", "↻", "★", "☆", "☰"):
                self.assertNotIn(glyph, text, f"{page} still has {glyph}")


if __name__ == "__main__":
    unittest.main()
