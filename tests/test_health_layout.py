"""Health page layout: every overview line stays visible at narrow widths and
any DPI (1.0.15 squeezed the LINK card so its rows drew over each other)."""
import os
import unittest

try:
    import PySide6  # noqa: F401
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

STATE = {
    "t": 1, "packets": 2729, "mode": 'virtual device #5 "monitor"', "host": "192.168.1.212",
    "az_mode": "pro_dj_link",
    "model": {"line": "XDJ-AZ (detected) — 4 deck(s); dbserver, NFS, beats, precise position; "
                      "waveforms blue/rgb/3band; Pro DJ Link mode", "profile": {}},
    "all_in_one": [{"ip": "192.168.1.212", "model": "XDJ-AZ", "devices": [1, 2, 3, 4, 33],
                    "note": "decks 1-4 + mixer (33) on one IP"}],
    "device_number_note": "#5 (as set, free)", "decks": [],
}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class HealthLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _page(self, width, height):
        from gui import i18n, pages, theme
        self._app.setStyleSheet(theme.STYLESHEET)
        page = pages.HealthPage(i18n.I18n())
        page.update_state(dict(STATE))
        page.resize(width, height)
        page.show()
        for _ in range(3):
            self._app.processEvents()
        return page

    def _overview_widgets(self, page):
        return [page._mode_val, page._host_val, page._pkts_val, page._rate_val,
                *page._overview_labels, page._az_lbl, page._az_val, page._model_val,
                page._aio_val, page._number_val, page._probe_btn]

    def test_no_overlap_and_nothing_squeezed(self):
        from PySide6.QtCore import QPoint, QRect
        for width, height in ((560, 420), (720, 500), (1100, 700)):
            page = self._page(width, height)
            try:
                widgets = [w for w in self._overview_widgets(page) if w.isVisible()]
                rects = []
                for w in widgets:
                    top_left = w.mapTo(page.scroll_area.widget(), QPoint(0, 0))
                    rects.append((w, QRect(top_left, w.size())))
                    need = (w.heightForWidth(w.width()) if w.hasHeightForWidth()
                            else w.minimumSizeHint().height())
                    self.assertGreaterEqual(
                        w.height() + 1, need,
                        f"{type(w).__name__} {getattr(w, 'text', lambda: '')()!r} "
                        f"clipped at {width}px: {w.height()} < {need}")
                for i, (a, ra) in enumerate(rects):
                    for b, rb in rects[i + 1:]:
                        inter = ra.intersected(rb)
                        self.assertTrue(
                            inter.isEmpty() or inter.height() <= 1,
                            f"overlap at {width}px: {a.text() if hasattr(a, 'text') else a} / "
                            f"{b.text() if hasattr(b, 'text') else b}")
                # Every card is at least as tall as its content needs.
                from PySide6.QtWidgets import QFrame
                for card in page.findChildren(QFrame):
                    if card.objectName() == "Card" and card.layout() and card.isVisible():
                        lay = card.layout()
                        need = (lay.totalHeightForWidth(card.width()) if lay.hasHeightForWidth()
                                else lay.totalMinimumSize().height())
                        self.assertGreaterEqual(card.height() + 1, need)
            finally:
                page.close()

    def test_page_scrolls_instead_of_squeezing(self):
        page = self._page(700, 400)
        try:
            body = page.scroll_area.widget()
            self.assertGreater(body.height(), page.height())
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
