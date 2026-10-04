#!/usr/bin/env python3
"""Side-by-side web overlay (Playwright) vs floating window (WebEngine) parity shots."""

from __future__ import annotations

import os
import sys
import threading
import time

# Software GL for headless WebEngine compositing (must be set before Qt WebEngine loads).
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _DummySource:
    def start(self, _cb) -> None:
        pass

    def stop(self) -> None:
        pass


def _seed_demo_artwork(monitor) -> None:
    """Sharp cover for mock overlay screenshots (/api/artwork/1)."""
    fixture = os.path.join(ROOT, "tests", "fixtures", "sharp_cover.jpg")
    if not os.path.isfile(fixture):
        return
    try:
        from prolink.track_key import track_cache_key

        with open(fixture, "rb") as f:
            data = f.read()
        key = track_cache_key("127.0.0.1", "/export/USB", (1, 1), "usb", 1)
        monitor._art_cache[key] = (data, "nfs-hires")
        monitor._last_art_source = "nfs-hires"
        monitor._art_source_by_deck[1] = "nfs-hires"
    except Exception:
        pass


def _start_doc_server() -> tuple[object, int]:
    from app import Handler, Monitor, open_http_server

    monitor = Monitor("127.0.0.1", _DummySource(), cache_dir=os.path.join(ROOT, ".cache"))
    _seed_demo_artwork(monitor)
    Handler.monitor = monitor
    server, port = open_http_server(0, handler=Handler, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _playwright_shot(url: str, path: str, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_selector("#card.show", timeout=30_000)
        page.wait_for_function(
            """() => {
              const t = document.querySelector('#card .title');
              return t && t.textContent && !t.textContent.includes('waiting');
            }""",
            timeout=30_000,
        )
        # Let rAF paint loop draw waveforms / phrase strip.
        page.wait_for_timeout(800)
        page.screenshot(path=path, full_page=False)
        browser.close()


def _wait_overlay_ready(web, *, timeout_ms: int = 20_000) -> bool:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    state = {"ready": False, "done": False}

    def _poll() -> None:
        if state["done"]:
            return

        def _cb(result) -> None:
            state["ready"] = bool(result)
            state["done"] = True

        web.page().runJavaScript(
            """(() => {
              const card = document.querySelector('#card.show');
              if (!card) return false;
              const t = card.querySelector('.title');
              return !!(t && t.textContent && t.textContent.indexOf('waiting') === -1);
            })()""",
            _cb,
        )

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        state["done"] = False
        _poll()
        while not state["done"] and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        if state["ready"]:
            for _ in range(30):
                QApplication.processEvents()
                time.sleep(0.03)
            return True
        time.sleep(0.05)
    return False


def _floating_shot(
    app,
    url: str,
    settings: dict,
    path: str,
    width: int,
    height: int,
) -> dict:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QImage, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from gui.floating_now import FloatingNowPlayingWindow
    from gui.i18n import I18n

    class _MockBackend:
        status = "connected"

        @property
        def port(self) -> int:
            return 8765

    # Opaque window for capture: WA_TranslucentBackground + QWidget.grab() is often
    # solid black under offscreen Qt (not a runtime rendering failure). Production
    # transparent mode still uses a transparent page background inside the web view.
    cap_settings = {
        **settings,
        "floating_now_transparent": False,
        "floating_now_topmost": False,
    }

    i18n = I18n()
    win = FloatingNowPlayingWindow(_MockBackend(), i18n, lambda: cap_settings)
    win.load_url(url)
    win.resize(width, height)
    win.show()
    QApplication.processEvents()

    ready = _wait_overlay_ready(win._web)
    pix = win._web.grab()
    win.close()

    # Sample centre pixel alpha on the web view grab (card area should be opaque panel).
    img: QImage = pix.toImage()
    cx, cy = img.width() // 2, img.height() // 2
    px = img.pixelColor(cx, cy)
    meta = {
        "overlay_ready": ready,
        "web_grab_size": (pix.width(), pix.height()),
        "centre_rgba": (px.red(), px.green(), px.blue(), px.alpha()),
    }
    pix.save(path)
    return meta


def _compose(
    web_path: str,
    float_path: str,
    out_path: str,
    width: int,
    height: int,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    web_pix = QPixmap(web_path)
    float_pix = QPixmap(float_path)
    gap = 16
    label_h = 28
    canvas = QPixmap(width * 2 + gap, height + label_h)
    canvas.fill(QColor("#07080a"))
    painter = QPainter(canvas)
    painter.setPen(Qt.white)
    painter.drawText(8, 20, "Web overlay (Playwright · preview+mock+embed)")
    painter.drawText(width + gap + 8, 20, "Floating window (WebEngine · same URL)")
    painter.drawPixmap(0, label_h, web_pix)
    painter.drawPixmap(width + gap, label_h, float_pix)
    painter.end()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)


def _pair(port: int, settings: dict, out_path: str, width: int, height: int) -> dict:
    from PySide6.QtWidgets import QApplication

    from gui.overlay_url import build_parity_screenshot_url

    url = build_parity_screenshot_url(port, settings)
    if "float=1" not in url:
        url += "&float=1"
    tmp_web = out_path + ".web.png"
    tmp_float = out_path + ".float.png"

    _playwright_shot(url, tmp_web, width, height)
    app = QApplication.instance() or QApplication(sys.argv)
    meta = _floating_shot(app, url, settings, tmp_float, width, height)
    _compose(tmp_web, tmp_float, out_path, width, height)
    os.remove(tmp_web)
    os.remove(tmp_float)
    print("Wrote", out_path, meta)
    return meta


def main() -> int:
    from gui.settings import load_settings, save_settings

    server, port = _start_doc_server()
    out_dir = os.path.join(ROOT, "docs", "screenshots")
    base = load_settings()

    card_settings = {**base, "overlay_now_style": "card", "overlay_now_pos": "left"}
    save_settings(card_settings)
    card_meta = _pair(port, card_settings, os.path.join(out_dir, "parity-now-card.png"), 520, 680)

    panel_settings = {**base, "overlay_now_style": "panel", "overlay_now_pos": "bottom"}
    save_settings(panel_settings)
    panel_meta = _pair(
        port, panel_settings, os.path.join(out_dir, "parity-now-panel.png"), 960, 280
    )

    save_settings(base)
    server.shutdown()
    print("card", card_meta)
    print("panel", panel_meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
