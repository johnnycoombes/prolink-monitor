#!/usr/bin/env python3
"""Grab the vocal lane (on and off) and the overlay countdown.

Demo tracks only. No player. Writes PNGs under /opt/cursor/artifacts.
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

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.i18n import I18n  # noqa: E402
from gui.pages import MonitorPage  # noqa: E402
from gui.theme import STYLESHEET  # noqa: E402

OUT = os.environ.get("PROLINK_SHOT_DIR", "/opt/cursor/artifacts")
W, H = 1400, 920
CHROME = os.environ.get("CHROME", "/opt/google/chrome/chrome")


def _wave(n: int = 1800):
    h = bytearray(n)
    rgb = bytearray(n * 3)
    for i in range(n):
        h[i] = min(31, round(6 + (math.sin(i / 11) * 0.5 + 0.5) * 24))
        rgb[i * 3] = 40 + (i % 50)
        rgb[i * 3 + 1] = 140
        rgb[i * 3 + 2] = 210
    return bytes(h), bytes(rgb)


def _pack(magic: bytes, n: int, body: bytes) -> bytes:
    return struct.pack("<4sIIfII", magic, 1, n, 150.0, 240000, 0) + body


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
        h, rgb = _wave()
        n = len(h)
        self.wave = _pack(b"PLWF", n, h + rgb) + _pack(b"PLWF", n, h + rgb)
        beats = [[i * 500, (i % 4) + 1] for i in range(480)]
        self._meta = {
            1: {
                "id": 1, "title": "Night Drive", "artist": "Demo Artist",
                "key": "8A", "album": "Monitor", "genre": "House",
                "duration_ms": 240000, "has_artwork": True,
                "cues": [
                    {"t": 30000, "type": "cue", "hot": 1, "text": "Intro"},
                    {"t": 54000, "type": "cue", "hot": 2, "text": "Drop"},
                ],
                "phrases": [
                    {"beat": 1, "text": "Intro"},
                    {"beat": 33, "text": "Drop"},
                    {"beat": 129, "text": "Outro"},
                ],
                "vocals": [{"t": 16000, "end": 72000}, {"t": 110000, "end": 160000}],
                "beats": beats,
            },
            2: {
                "id": 2, "title": "Glasshouse", "artist": "North Line",
                "key": "4A", "album": "Monitor", "genre": "House",
                "duration_ms": 240000, "has_artwork": True,
                "cues": [{"t": 70000, "type": "cue", "hot": 1, "text": "Break"}],
                "phrases": [{"beat": 1, "text": "Intro"}, {"beat": 65, "text": "Chorus"}],
                "vocals": [{"t": 40000, "end": 90000}],
                "beats": beats,
            },
        }
        self.art = {1: _png("#ffb020"), 2: _png("#22d3ee")}

    def meta(self, track_id, **_kwargs):
        return self._meta.get(int(track_id))

    def artwork(self, track_id, **_kwargs):
        return self.art.get(int(track_id))

    def waveform(self, track_id, **_kwargs):
        return self.wave


def _state() -> dict:
    return {
        "decks": [
            {
                "number": 1, "track_id": 1, "playing": True, "on_air": True,
                "bpm": 126, "track_bpm": 126, "pitch": 1.25, "speed": 1.0,
                "position_ms": 48000, "duration_ms": 240000, "state": "playing",
                "master": True, "sync": False, "name": "XDJ-AZ",
                "beat": 1, "bar": 1, "waveform_position": "left",
            },
            {
                "number": 2, "track_id": 2, "playing": False, "on_air": False,
                "bpm": 126, "track_bpm": 126, "pitch": -0.8, "speed": 0.0,
                "position_ms": 20000, "duration_ms": 240000, "state": "cued",
                "master": False, "sync": True, "name": "XDJ-AZ",
                "beat": 1, "bar": 1, "waveform_position": "left",
            },
        ],
        "devices": [{"number": 1, "name": "XDJ-AZ"}],
    }


def _grab_monitor(app: QApplication, vocals: bool, path: str) -> None:
    page = MonitorPage(I18n(), _Backend())
    page.setStyleSheet(STYLESHEET)
    page.resize(W, H)
    page.show()
    page.apply_prefs({
        "show_phrases": True,
        "show_vocals": vocals,
        "show_next_cue": True,
        "zoom_bars": 4,
        "max_decks": 2,
        "waveform_style": "rgb",
        "playhead_position": "left",
        "show_empty_decks": True,
    })
    for _ in range(6):
        app.processEvents()
    page.update_state(_state())
    for _ in range(10):
        app.processEvents()
    page.update_state(_state())
    for _ in range(6):
        app.processEvents()
    pix = page.grab()
    if pix.isNull() or pix.width() < 100:
        raise SystemExit(f"monitor grab failed ({path})")
    pix.save(path)
    page.close()
    app.processEvents()


def _chrome(url: str, path: str, width: int, height: int) -> None:
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        "--virtual-time-budget=4000",
        f"--screenshot={path}",
        url,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        raise SystemExit(f"chrome grab failed: {url}")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    _grab_monitor(app, True, os.path.join(OUT, "monitor_vocal_lane_on.png"))
    _grab_monitor(app, False, os.path.join(OUT, "monitor_vocal_lane_off.png"))
    _chrome(
        f"file://{ROOT}/web/index.html?mock=1&decks=2",
        os.path.join(OUT, "vocal_lane_on.png"), W, H,
    )
    _chrome(
        f"file://{ROOT}/web/index.html?mock=1&decks=2&vocals=0",
        os.path.join(OUT, "vocal_lane_off.png"), W, H,
    )
    _chrome(
        f"file://{ROOT}/web/overlay.html?mock=1&style=panel&spread=1",
        os.path.join(OUT, "overlay_countdown.png"), 1280, 720,
    )
    print("wrote vocal and countdown screenshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
