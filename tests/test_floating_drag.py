"""Floating window drag bridge (no WebEngine required)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from gui.floating_now import (
    FloatingNowPlayingWindow,
    _FloatingDragBridge,
    _FloatingDragHandle,
    saved_window_origin,
)


class FloatingDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_drag_moves_window(self):
        class WinStub:
            pass

        win = WinStub()
        win._drag_offset = None
        win._dragging = False
        win.move = mock.Mock()
        win.frameGeometry = mock.Mock(return_value=mock.Mock(topLeft=lambda: QPoint(100, 200)))
        win.grabMouse = mock.Mock()
        win.releaseMouse = mock.Mock()
        win.mouseGrabber = mock.Mock(return_value=win)
        win._save_timer = mock.Mock()
        win.windowHandle = mock.Mock(return_value=None)

        handle = mock.Mock()
        handle.startSystemMove.return_value = True
        win.windowHandle = mock.Mock(return_value=handle)
        FloatingNowPlayingWindow._begin_drag(win, 150, 260, origin="bridge")
        self.assertTrue(win._dragging)
        handle.startSystemMove.assert_not_called()
        win.grabMouse.assert_not_called()

        FloatingNowPlayingWindow._drag_to(win, 170, 280)
        win.move.assert_called_with(QPoint(120, 220))

        FloatingNowPlayingWindow._end_drag(win)
        self.assertFalse(win._dragging)
        win.releaseMouse.assert_called()

    def test_bridge_forwards_to_window(self):
        win = mock.Mock()
        bridge = _FloatingDragBridge(win)
        bridge.dragStart(10, 20)
        win._begin_drag.assert_called_with(10, 20, origin="bridge")
        bridge.dragMove(11, 21)
        win._drag_to.assert_called_with(11, 21)
        bridge.dragEnd()
        win._end_drag.assert_called()

    def test_drag_handle_calls_system_move(self):
        win = mock.Mock()
        win._begin_system_drag = mock.Mock()
        handle = _FloatingDragHandle(win)
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPoint(2, 2),
            QPoint(2, 2),
            QPoint(2, 2),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        handle.mousePressEvent(ev)
        win._begin_system_drag.assert_called_once_with("handle")
        sheet = handle.styleSheet()
        self.assertIn("rgba(8,10,14,0.92)", sheet)
        self.assertIn(":hover", sheet)
        self.assertIn("rgba(46,54,66,0.98)", sheet)
        self.assertGreaterEqual(handle.height(), 16)

    def test_strip_falls_back_when_system_move_declines(self):
        win = mock.Mock()
        win._begin_system_drag = mock.Mock(return_value=False)
        win._begin_drag = mock.Mock()
        win._dragging = False
        win._drag_offset = None
        handle = _FloatingDragHandle(win)
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPoint(4, 4),
            QPoint(40, 50),
            QPoint(40, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        handle.mousePressEvent(ev)
        win._begin_drag.assert_called_once()
        args = win._begin_drag.call_args
        self.assertEqual(args.kwargs.get("origin") or args.args[-1], "handle")

    def test_strip_reads_on_a_light_and_a_dark_desktop(self):
        win = mock.Mock()
        handle = _FloatingDragHandle(win)
        handle.resize(240, handle.height())
        image = QImage(handle.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        handle.render(image)

        edge = image.pixelColor(6, image.height() // 2)
        self.assertGreater(edge.alpha(), 200)
        self.assertLess(edge.red(), 40)

        light = 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                if pixel.red() > 200 and pixel.green() > 200 and pixel.blue() > 200:
                    light += 1
        self.assertGreater(light, 8)

        def _composite(backdrop: QColor) -> QImage:
            out = QImage(image.size(), QImage.Format.Format_ARGB32)
            out.fill(backdrop)
            painter = QPainter(out)
            painter.drawImage(0, 0, image)
            painter.end()
            return out

        on_white = _composite(QColor(255, 255, 255))
        on_black = _composite(QColor(0, 0, 0))
        white_edge = on_white.pixelColor(6, image.height() // 2)
        black_edge = on_black.pixelColor(6, image.height() // 2)
        # Dark glass stays dark on a white desktop. The light grip stays light on black.
        self.assertLess(white_edge.red(), 80)
        grip = max(
            on_black.pixelColor(x, y).red()
            for y in range(on_black.height())
            for x in range(on_black.width())
        )
        self.assertGreater(grip, 200)
        self.assertGreater(black_edge.red(), 0)

    def test_saved_origin_keeps_a_negative_coordinate(self):
        self.assertEqual(saved_window_origin(-1, -1), (80, 80))
        self.assertEqual(saved_window_origin(-240, 30), (-240, 30))

    def test_overlay_page_calls_the_drag_bridge(self):
        root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "overlay.html")
        text = open(root, encoding="utf-8").read()
        self.assertIn("floatingDrag", text)
        self.assertIn("dragStart", text)
        self.assertIn("altKey", text)
        self.assertIn("connectFloatingDrag", text)


if __name__ == "__main__":
    unittest.main()
