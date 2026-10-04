#!/usr/bin/env python3
"""Capture floating Now Playing screenshots for PR docs (offscreen Qt)."""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.floating_now import FloatingNowPlayingWindow  # noqa: E402
from gui.i18n import I18n  # noqa: E402
from gui.settings import load_settings, save_settings  # noqa: E402


class _MockBackend:
    status = "connected"

    def meta(self, track_id, *, deck=None, track_key=None):
        return {
            "id": track_id,
            "track_key": track_key or "",
            "title": "Example Track",
            "artist": "Demo Artist",
            "key": "8A",
            "duration_ms": 300000,
            "has_artwork": False,
        }

    def artwork(self, *args, **kwargs):
        return None

    def waveform(self, *args, **kwargs):
        return None


def _grab(win: FloatingNowPlayingWindow, path: str) -> None:
    from scripts.capture_floating_overlay_parity import _wait_overlay_ready

    win.show()
    QApplication.processEvents()
    _wait_overlay_ready(win._web)
    pix = win._web.grab()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pix.save(path)


def main() -> int:
    app = QApplication(sys.argv)
    i18n = I18n()
    backend = _MockBackend()
    out_dir = os.path.join(ROOT, "docs", "screenshots")
    base = load_settings()

    card_settings = {**base, "overlay_now_style": "card", "overlay_now_pos": "left"}
    save_settings(card_settings)
    from gui.overlay_url import build_floating_overlay_file_url

    card = FloatingNowPlayingWindow(backend, i18n, lambda: card_settings)
    card.load_url(build_floating_overlay_file_url(card_settings, mock=True) + "&float=1")
    card.restore_geometry({**card_settings, "floating_now_width": 0, "floating_now_height": 0})
    card.apply_settings()
    for _ in range(40):
        QApplication.processEvents()
        time.sleep(0.03)
    _grab(card, os.path.join(out_dir, "floating-now-card.png"))
    card.close()

    panel_settings = {**base, "overlay_now_style": "panel", "overlay_now_pos": "bottom"}
    save_settings(panel_settings)
    panel = FloatingNowPlayingWindow(backend, i18n, lambda: panel_settings)
    panel.load_url(build_floating_overlay_file_url(panel_settings, mock=True) + "&float=1")
    panel.restore_geometry({**panel_settings, "floating_now_panel_width": 0, "floating_now_panel_height": 0})
    panel.apply_settings()
    for _ in range(40):
        QApplication.processEvents()
        time.sleep(0.03)
    _grab(panel, os.path.join(out_dir, "floating-now-panel.png"))
    panel.close()

    save_settings(base)
    print("Wrote", out_dir)
    QTimer.singleShot(0, app.quit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
