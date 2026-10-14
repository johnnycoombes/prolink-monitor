#!/usr/bin/env python3
"""Fresh README screenshots (1.0.12) from demo data. No player is needed.

Writes into ``doc/`` by default (``PROLINK_SHOT_DIR`` overrides):
monitor.png (Qt, 3-band), web-panel.png, library.png (playlist tree),
overlay-card.png, overlay-panel.png, overlay-bar.png and floating-now.png.
Qt runs offscreen; the web pages use headless Chrome with ``mock=1``.
"""

from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import capture_monitor_web_panel as cmw  # noqa: E402
from gui.i18n import I18n  # noqa: E402
from gui.theme import STYLESHEET  # noqa: E402

OUT = os.environ.get("PROLINK_SHOT_DIR", os.path.join(ROOT, "doc"))
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")


def _chrome(url: str, path: str, w: int, h: int, transparent: bool = False) -> None:
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           "--force-device-scale-factor=1", f"--window-size={w},{h}",
           "--virtual-time-budget=5000", "--allow-file-access-from-files",
           f"--screenshot={path}", url]
    if transparent:
        cmd.insert(1, "--default-background-color=101418ff")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def _settle(app, n=12):
    for _ in range(n):
        app.processEvents()


def monitor(app) -> None:
    from gui.pages import MonitorPage

    page = MonitorPage(I18n(), cmw._Backend())
    page.setStyleSheet(STYLESHEET)
    page.resize(cmw.W, cmw.H)
    page.show()
    page.apply_prefs({"show_phrases": True, "zoom_bars": 4, "max_decks": 4,
                      "waveform_style": "3band", "playhead_position": "left",
                      "show_empty_decks": True})
    _settle(app)
    page.update_state(cmw._state(4))
    _settle(app)
    page.grab().save(os.path.join(OUT, "monitor.png"))
    page.close()


class _LibBackend:
    def __getattr__(self, name):
        return lambda *a, **k: {}


TREE = [
    ("Warm-up", True, 0), ("Deep House", False, 1), ("Afro & Organic", False, 1),
    ("Peak time", True, 0), ("Techno", True, 1), ("Hypnotic", False, 2),
    ("Driving", False, 2), ("Tech House", False, 1), ("Closing", False, 0),
    ("Smart: 8A 124-126", False, 0), ("Tagged: vocals", False, 0),
]
TRACKS = [
    ("Night Drive", "Demo Artist", "Monitor", 124.0, "8A"),
    ("Glasshouse", "North Line", "Glass EP", 125.0, "4A"),
    ("Second Sun", "Kite Club", "Second Sun", 126.0, "11B"),
    ("Harbour", "Demo Artist", "Monitor", 124.5, "8A"),
    ("Low Tide", "Mara Vey", "Coastal", 123.0, "9A"),
    ("Signal Path", "Kite Club", "Signals", 127.0, "5A"),
    ("Paper Lanterns", "North Line", "Glass EP", 122.0, "2B"),
    ("Undertow", "Mara Vey", "Coastal", 126.0, "8B"),
]


def library(app) -> None:
    from gui.pages import LibraryPage

    page = LibraryPage(I18n(), _LibBackend())
    page.setStyleSheet(STYLESHEET)
    page.resize(cmw.W, cmw.H)
    page.show()
    _settle(app)
    for key, val in (("tracks", "12,128"), ("artists", "6,859"), ("albums", "5,304")):
        page.cards[key].setText(val)
    page.source_label.setText("Live: TCP dbserver · player 1 as client #5, USB slot")
    rows = [{"id": i + 1, "name": n, "folder": f, "depth": d, "host": "demo"}
            for i, (n, f, d) in enumerate(TREE)]
    page._fill_sidebars({"playlists": rows, "history": [
        {"id": 900, "name": "HISTORY 2026-10-12"}, {"id": 901, "name": "HISTORY 2026-10-10"}]})
    page._fill_table([{"id": i, "title": t, "artist": a, "album": al, "bpm": b, "key": k}
                      for i, (t, a, al, b, k) in enumerate(TRACKS)])
    _settle(app)
    page.grab().save(os.path.join(OUT, "library.png"))
    page.close()


def overlay_settings(app) -> None:
    from gui.pages import OverlayPage

    class B:
        port = 8787
        status = "connected"

        def __getattr__(self, name):
            return lambda *a, **k: None

    page = OverlayPage(I18n(), B())
    page.setStyleSheet(STYLESHEET)
    page.resize(cmw.W, cmw.H)
    page.show()
    _settle(app, 30)
    page.grab().save(os.path.join(OUT, "overlay-settings.png"))
    page.close()


def floating(app) -> None:
    from gui.floating_now import FloatingNowPlayingWindow
    from gui.overlay_url import build_floating_overlay_file_url

    settings = {"overlay_now_style": "card", "overlay_now_pos": "tl",
                "overlay_waveform_style": "3band", "floating_now_transparent": False}

    class B:
        port = 0

    win = FloatingNowPlayingWindow(B(), I18n(), lambda: settings)
    win.resize(520, 700)
    win.show()
    done = {"ok": False}
    win._web.loadFinished.connect(lambda ok: done.__setitem__("ok", ok))
    # Over HTTP like the app (the file:// form does not load playhead.js in WebEngine).
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=os.path.join(ROOT, "web"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = build_floating_overlay_file_url(settings).split("?", 1)[1]
    win.load_url(f"http://127.0.0.1:{srv.server_port}/overlay.html?{url}&float=1")
    import time

    end = time.time() + 15
    while time.time() < end:
        app.processEvents()
        if done["ok"]:
            break
    end = time.time() + 3
    while time.time() < end:
        app.processEvents()
    win.grab().save(os.path.join(OUT, "floating-now.png"))
    win.release_engine()
    win.close()
    srv.shutdown()


def web() -> None:
    _chrome(f"file://{ROOT}/web/index.html?mock=1&decks=4", os.path.join(OUT, "web-panel.png"),
            cmw.W, cmw.H)
    base = f"file://{ROOT}/web/overlay.html?layout=nowplaying&decks=1&mix=0&next=0&mock=1&preview=1"
    _chrome(base + "&style=card&pos=tl&wave=3band", os.path.join(OUT, "overlay-card.png"),
            560, 760, True)
    _chrome(base + "&style=panel&pos=bottom&wave=3band", os.path.join(OUT, "overlay-panel.png"),
            1100, 520, True)
    _chrome(base + "&style=bar&pos=bottom&wave=rgb", os.path.join(OUT, "overlay-bar.png"),
            1100, 380, True)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    monitor(app)
    library(app)
    try:
        overlay_settings(app)
    except Exception as exc:  # keep the other shots
        print(f"overlay-settings skipped: {exc}")
    floating(app)
    web()
    for name in sorted(os.listdir(OUT)):
        if name.endswith(".png"):
            print(os.path.join(OUT, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
