#!/usr/bin/env python3
"""Side-by-side web overlay vs floating window (card + panel) for PR docs."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from gui.floating_now import FloatingNowPlayingWindow  # noqa: E402
from gui.i18n import I18n  # noqa: E402
from gui.overlay_url import build_floating_overlay_file_url  # noqa: E402
from gui.settings import load_settings, save_settings  # noqa: E402


class _MockBackend:
    status = "connected"

    @property
    def port(self) -> int:
        return 8765


def _wait_load(view: QWebEngineView, ms: int = 3500) -> None:
    done = {"ok": False}

    def _finished(_ok: bool) -> None:
        done["ok"] = True

    view.loadFinished.connect(_finished)
    end = QTimer()
    end.setSingleShot(True)
    end.timeout.connect(lambda: None)
    end.start(ms)
    while not done["ok"] and end.isActive():
        QApplication.processEvents()
    for _ in range(8):
        QApplication.processEvents()
        QTimer.singleShot(200, lambda: None)
        QApplication.processEvents()


def _pair(
    app: QApplication,
    settings: dict,
    out_path: str,
    width: int,
    height: int,
) -> None:
    url = build_floating_overlay_file_url(settings)
    qurl = QUrl(url)

    web = QWebEngineView()
    web.resize(width, height)
    web.load(qurl)
    _wait_load(web)

    i18n = I18n()
    backend = _MockBackend()
    floating = FloatingNowPlayingWindow(backend, i18n, lambda: settings)
    floating.load_url(url)
    floating.resize(width, height)
    floating.show()
    _wait_load(floating._web)

    web_pix = web.grab()
    float_pix = floating.grab()
    floating.close()

    gap = 16
    label_h = 28
    canvas = QPixmap(width * 2 + gap, height + label_h)
    canvas.fill(Qt.black)
    painter = QPainter(canvas)
    painter.setPen(Qt.white)
    painter.drawText(8, 20, "Web overlay (embed)")
    painter.drawText(width + gap + 8, 20, "Floating window (WebEngine)")
    painter.drawPixmap(0, label_h, web_pix)
    painter.drawPixmap(width + gap, label_h, float_pix)
    painter.end()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    print("Wrote", out_path)


def main() -> int:
    app = QApplication(sys.argv)
    out_dir = os.path.join(ROOT, "docs", "screenshots")
    base = load_settings()

    card_settings = {**base, "overlay_now_style": "card", "overlay_now_pos": "left"}
    save_settings(card_settings)
    _pair(app, card_settings, os.path.join(out_dir, "parity-now-card.png"), 420, 520)

    panel_settings = {**base, "overlay_now_style": "panel", "overlay_now_pos": "bottom"}
    save_settings(panel_settings)
    _pair(app, panel_settings, os.path.join(out_dir, "parity-now-panel.png"), 960, 220)

    save_settings(base)
    QTimer.singleShot(0, app.quit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
