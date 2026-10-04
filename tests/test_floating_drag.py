"""Floating window drag bridge (no WebEngine required)."""

from __future__ import annotations

import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from gui.floating_now import (
    FloatingNowPlayingWindow,
    _FloatingDragBridge,
    _FloatingDragHandle,
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

        FloatingNowPlayingWindow._begin_drag(win, 150, 260)
        self.assertTrue(win._dragging)
        win.grabMouse.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
