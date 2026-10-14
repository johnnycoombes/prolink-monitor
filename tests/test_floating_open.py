"""1.0.12: the floating Now Playing window opens (regression from 1.0.7).

``apply_state`` and ``tick_paint`` had slid out of the class into the body of
``release_webengine_views``. ``toggle_floating_now`` calls ``apply_state``
before ``show()``, so the window raised AttributeError and never appeared.
"""

from __future__ import annotations

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _class_methods(path: str, cls: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    raise AssertionError(cls)


class FloatingApiTests(unittest.TestCase):
    def test_every_call_from_main_window_exists(self):
        methods = _class_methods(os.path.join(ROOT, "gui", "floating_now.py"),
                                 "FloatingNowPlayingWindow")
        src = open(os.path.join(ROOT, "gui", "main_window.py"), encoding="utf-8").read()
        import re

        called = set(re.findall(r"(?:_floating_now|\bwin)\.([a-z_]+)\(", src))
        qt_inherited = {"close", "show", "isVisible", "setAttribute", "raise_",
                        "activateWindow", "deleteLater", "hide"}
        missing = sorted(c for c in called if c not in methods and c not in qt_inherited
                         and not c.startswith("set"))
        self.assertEqual(missing, [])
        self.assertIn("apply_state", methods)
        self.assertIn("tick_paint", methods)

    def test_module_function_has_no_nested_methods(self):
        tree = ast.parse(open(os.path.join(ROOT, "gui", "floating_now.py"), encoding="utf-8").read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "release_webengine_views")
        nested = [n.name for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n is not fn]
        self.assertEqual(nested, [])


class FloatingOpenTests(unittest.TestCase):
    """Open the window the way MainWindow.toggle_floating_now does."""

    def test_open_sequence_shows_the_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication

            from gui import floating_now
        except ImportError as exc:  # pragma: no cover - no Qt libs
            self.skipTest(str(exc))
        if not floating_now.webengine_available():
            self.skipTest("no WebEngine")
        app = QApplication.instance() or QApplication([])
        from gui.i18n import I18n

        class Backend:
            port = 1
            status = "connected"

        settings = {"overlay_now_style": "card", "floating_now_topmost": True,
                    "floating_now_transparent": True}
        win = floating_now.FloatingNowPlayingWindow(Backend(), I18n(), lambda: settings)
        try:
            win.restore_geometry(settings)
            win.apply_settings()
            win.apply_state({"decks": []})
            win.show()
            app.processEvents()
            self.assertTrue(win.isVisible())
            win.tick_paint()
        finally:
            win.release_engine()
            win.hide()
            win.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
