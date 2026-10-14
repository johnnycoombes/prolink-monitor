#!/usr/bin/env python3
"""README screenshots from a live snapshot, rendered with this tree's code.

A snapshot directory holds what a running app served (all read-only GETs):
``state.json`` (/api/state), ``track_<id>.json``, ``wave_<id>.bin``,
``art_<id>_large.jpg`` and ``art_<id>_small.jpg``. This script replays it
through a local HTTP server and grabs the web panel and overlays with Qt
WebEngine (offscreen), and the desktop Monitor page with the same data.
Usage: capture_replay_shots.py SNAPSHOT_DIR [OUT_DIR]
"""

from __future__ import annotations

import http.server
import json
import os
import re
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WEB = os.path.join(ROOT, "web")
MARK = b"<!--WAVE_PALETTES-->"


def _read(path, mode="rb"):
    try:
        with open(path, mode) as f:
            return f.read()
    except OSError:
        return None


class Snapshot:
    def __init__(self, folder: str):
        self.dir = folder
        self.state = json.loads(_read(os.path.join(folder, "state.json"), "r"))
        self.t0 = time.time()
        self.t_state = float(self.state.get("t") or self.t0)

    def live_state(self) -> dict:
        """The snapshot frozen at capture time: positions stay put."""
        s = dict(self.state)
        s["t"] = time.time()
        decks = []
        for d in s.get("decks") or []:
            d = dict(d)
            d["playhead_age_s"] = 0
            d["playing"] = False if d.get("playing") else d.get("playing")
            decks.append(d)
        s["decks"] = decks
        return s

    def file(self, name):
        return _read(os.path.join(self.dir, name))


def make_handler(snap: Snapshot):
    from prolink.wavestyle import palette_json

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._page("index.html")
            if path in ("/overlay", "/overlay.html"):
                return self._page("overlay.html")
            if path == "/api/state":
                return self._send(200, json.dumps(snap.live_state()).encode(), "application/json")
            if path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    for _ in range(600):
                        self.wfile.write(b"data: " + json.dumps(snap.live_state()).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.2)
                except OSError:
                    pass
                return
            m = re.match(r"^/api/(track|waveform|artwork)/(\d+)$", path)
            if m:
                kind, tid = m.group(1), m.group(2)
                if kind == "track":
                    body, ctype = snap.file(f"track_{tid}.json"), "application/json"
                elif kind == "waveform":
                    body, ctype = snap.file(f"wave_{tid}.bin"), "application/octet-stream"
                else:
                    size = "small" if "size=small" in self.path else "large"
                    body, ctype = snap.file(f"art_{tid}_{size}.jpg"), "image/jpeg"
                if body:
                    return self._send(200, body, ctype)
                return self._send(404, b"{}", "application/json")
            if path.startswith("/api/"):
                return self._send(404, b"{}", "application/json")
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if full.startswith(WEB) and os.path.isfile(full):
                ext = os.path.splitext(full)[1]
                ctype = {".js": "text/javascript", ".json": "application/json",
                         ".png": "image/png", ".css": "text/css"}.get(ext, "application/octet-stream")
                return self._send(200, _read(full), ctype)
            return self._send(404, b"", "text/plain")

        def _page(self, name):
            body = _read(os.path.join(WEB, name))
            body = body.replace(MARK, b"<script>window.PROLINK_WAVE_PALETTES="
                                + palette_json().encode() + b";</script>", 1)
            self._send(200, body, "text/html; charset=utf-8")

    return H


def grab_web(app, url: str, w: int, h: int, out: str, wait_s: float = 7.0) -> None:
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView()
    view.resize(w, h)
    view.page().setBackgroundColor(Qt.black)
    view.load(QUrl(url))
    view.show()
    end = time.time() + wait_s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    view.grab().save(out)
    view.page().deleteLater()
    view.deleteLater()
    app.processEvents()


def grab_monitor(app, snap: Snapshot, out: str, w=1400, h=920) -> None:
    from gui.i18n import I18n
    from gui.pages import MonitorPage
    from gui.theme import STYLESHEET

    class Backend:
        def meta(self, track_id, **_k):
            raw = snap.file(f"track_{int(track_id)}.json")
            return json.loads(raw) if raw else None

        def artwork(self, track_id, size=None, **_k):
            return snap.file(f"art_{int(track_id)}_{'small' if size == 'small' else 'large'}.jpg")

        def waveform(self, track_id, **_k):
            return snap.file(f"wave_{int(track_id)}.bin")

        def __getattr__(self, name):
            return lambda *a, **k: None

    state = snap.live_state()
    disp = dict(state.get("display") or {})
    prefs = {**disp, **(disp.get("elements") or {})}
    page = MonitorPage(I18n(), Backend())
    page.setStyleSheet(STYLESHEET)
    page.resize(w, h)
    page.show()
    page.apply_prefs(prefs)
    for _ in range(3):
        for _ in range(10):
            app.processEvents()
        page.update_state(state)
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)
    page.grab().save(out)
    page.close()


def main() -> int:
    snap = Snapshot(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "doc")
    os.makedirs(out, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(snap))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    grab_monitor(app, snap, os.path.join(out, "monitor.png"))
    grab_web(app, base + "/", 1400, 920, os.path.join(out, "web-panel.png"))
    ov = base + "/overlay?layout=nowplaying&decks=1&mix=0&next=0&preview=1"
    grab_web(app, ov + "&style=card&pos=tl&wave=3band", 560, 900, os.path.join(out, "overlay-card.png"))
    grab_web(app, ov + "&style=panel&pos=bottom&wave=3band", 1100, 520, os.path.join(out, "overlay-panel.png"))
    grab_web(app, ov + "&style=bar&pos=bottom&wave=3band", 1100, 380, os.path.join(out, "overlay-bar.png"))
    srv.shutdown()
    print(out, flush=True)
    os._exit(0)  # WebEngine teardown can segfault offscreen


if __name__ == "__main__":
    raise SystemExit(main())
