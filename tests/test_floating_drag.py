"""Floating window drag bridge (no WebEngine required)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
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
        self.assertIn("0.28", handle.styleSheet())
        self.assertIn(":hover", handle.styleSheet())
        self.assertGreaterEqual(handle.height(), 18)

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
