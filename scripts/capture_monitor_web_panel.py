#!/usr/bin/env python3
"""Side-by-side grabs of the desktop Monitor page and the web panel.

Uses the same demo tracks as the web panel's ?mock=1 view. No player is
required. Writes PNGs under /opt/cursor/artifacts.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.i18n import I18n  # noqa: E402
from gui.pages import MonitorPage  # noqa: E402
from gui.theme import STYLESHEET  # noqa: E402

OUT = os.environ.get("PROLINK_SHOT_DIR", "/opt/cursor/artifacts")
W, H = 1400, 920
CHROME = os.environ.get("CHROME", "/opt/google/chrome/chrome")
COLORS = ("#ffb020", "#22d3ee", "#f472b6", "#4ade80")
TITLES = (
    ("Night Drive", "Demo Artist", "8A", 1, True),
    ("Glasshouse", "North Line", "4A", 2, False),
    ("Second Sun", "Kite Club", "11B", 3, False),
    ("Harbour", "Demo Artist", "8A", 4, False),
)
PITCHES = (1.25, -0.80, 0.0, 0.40)


def _wave(n: int = 1800):
    h = bytearray(n)
    rgb = bytearray(n * 3)
    low = bytearray(n)
    mid = bytearray(n)
    high = bytearray(n)
    for i in range(n):
        h[i] = min(31, round(6 + (math.sin(i / 11) * 0.5 + 0.5) * 24))
        low[i] = min(31, round(4 + (max(0.0, math.sin(i / 7)) ** 2) * 26))
        mid[i] = min(31, round(5 + (max(0.0, math.sin(i / 5 + 1)) ** 2) * 20))
        high[i] = min(31, round(3 + abs(math.sin(i / 3)) * 14))
        rgb[i * 3] = 40 + (i % 50)
        rgb[i * 3 + 1] = 140
        rgb[i * 3 + 2] = 210
    return bytes(h), bytes(rgb), bytes(low), bytes(mid), bytes(high)


def _pack(magic: bytes, n: int, body: bytes) -> bytes:
    header = struct.pack("<4sIIfII", magic, 1, n, 150.0, 240000, 0)
    return header + body


def _png(color: str) -> bytes:
    img = QImage(72, 72, QImage.Format_RGB32)
    img.fill(QColor("#14171d"))
    p = QPainter(img)
    p.fillRect(10, 10, 52, 52, QColor(color))
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


class _Backend:
    def __init__(self):
        h, rgb, low, mid, high = _wave()
        n = len(h)
        packed = (
            _pack(b"PLWF", n, h + rgb)
            + _pack(b"PLWF", n, h + rgb)
            + _pack(b"PLWB", n, low + mid + high)
            + _pack(b"PLWB", n, low + mid + high)
        )
        beats = [[i * 500, (i % 4) + 1] for i in range(480)]
        self.meta_by_id = {}
        self.art_by_id = {}
        self.wave = packed
        for i, row in enumerate(TITLES):
            tid = i + 1
            self.art_by_id[tid] = _png(COLORS[i])
            self.meta_by_id[tid] = {
                "id": tid,
                "title": row[0],
                "artist": row[1],
                "key": row[2],
                "album": "Monitor",
                "genre": "House",
                "duration_ms": 240000,
                "has_artwork": True,
                "cues": [{"t": 30000, "type": "cue", "hot": 1, "color": [255, 212, 94]}],
                "phrases": [
                    {"beat": 1, "text": "Intro"},
                    {"beat": 33, "text": "Drop"},
                    {"beat": 129, "text": "Outro"},
                ],
                "beats": beats,
            }

    def meta(self, track_id, **_kwargs):
        return self.meta_by_id.get(int(track_id))

    def artwork(self, track_id, **_kwargs):
        return self.art_by_id.get(int(track_id))

    def waveform(self, track_id, **_kwargs):
        return self.wave


def _state(decks: int) -> dict:
    rows = []
    for i, row in enumerate(TITLES):
        rows.append({
            "number": row[3],
            "track_id": i + 1,
            "playing": row[4],
            "on_air": row[4],
            "bpm": 126 + i,
            "track_bpm": 126,
            "pitch": PITCHES[i],
            "speed": 1.0 if row[4] else 0.0,
            "position_ms": 48000 + i * 8000,
            "duration_ms": 240000,
            "state": "playing" if row[4] else "cued",
            "master": row[4],
            "sync": i == 1,
            "name": "XDJ-AZ",
            "beat": 1,
            "bar": 1,
            "waveform_position": "left",
        })
    return {"decks": rows, "devices": [{"number": 1, "name": "XDJ-AZ"}]}


def _grab_monitor(app: QApplication, decks: int, path: str) -> None:
    backend = _Backend()
    page = MonitorPage(I18n(), backend)
    page.setStyleSheet(STYLESHEET)
    page.resize(W, H)
    page.show()
    page.apply_prefs({
        "show_phrases": True,
        "zoom_bars": 4,
        "max_decks": decks,
        "waveform_style": "rgb",
        "playhead_position": "left",
        "show_empty_decks": True,
    })
    for _ in range(8):
        app.processEvents()
    page.update_state(_state(decks))
    for _ in range(12):
        app.processEvents()
    page.resize(W, H)
    page.update_state(_state(decks))
    for _ in range(8):
        app.processEvents()
    pix = page.grab()
    if pix.isNull() or pix.width() < 100:
        raise SystemExit(f"monitor grab failed for {decks} decks")
    pix.save(path)
    page.close()
    app.processEvents()


def _grab_web(decks: int, path: str) -> None:
    url = f"file://{ROOT}/web/index.html?mock=1&decks={decks}"
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--window-size={W},{H}",
        "--virtual-time-budget=4000",
        f"--screenshot={path}",
        url,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        raise SystemExit(f"web grab failed for {decks} decks")


def _side_by_side(left: str, right: str, out: str, left_label: str, right_label: str) -> None:
    a = QImage(left)
    b = QImage(right)
    if a.isNull() or b.isNull():
        raise SystemExit(f"could not load {left} or {right}")
    gap = 18
    label_h = 36
    width = a.width() + gap + b.width()
    height = label_h + max(a.height(), b.height())
    canvas = QImage(width, height, QImage.Format_RGB32)
    canvas.fill(QColor("#07080a"))
    p = QPainter(canvas)
    p.setPen(QColor("#e8eaf0"))
    font = QFont("DejaVu Sans", 13)
    font.setBold(True)
    p.setFont(font)
    p.drawText(12, 24, left_label)
    p.drawText(a.width() + gap + 12, 24, right_label)
    p.drawImage(0, label_h, a)
    p.drawImage(a.width() + gap, label_h, b)
    p.end()
    canvas.save(out)
    print(out, canvas.width(), canvas.height())


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    for decks in (4, 2):
        mon = os.path.join(OUT, f"_monitor_{decks}.png")
        web = os.path.join(OUT, f"_web_{decks}.png")
        _grab_monitor(app, decks, mon)
        _grab_web(decks, web)
        _side_by_side(
            mon, web,
            os.path.join(OUT, f"monitor_and_web_panel_{decks}deck.png"),
            "Desktop Monitor",
            "Web panel",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
