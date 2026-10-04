#!/usr/bin/env python3
"""Card style in all four corners (Playwright + floating WebEngine)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.capture_floating_overlay_parity import (  # noqa: E402
    _compose,
    _floating_shot,
    _playwright_shot,
    _start_doc_server,
)
from gui.overlay_url import build_parity_screenshot_url  # noqa: E402
from gui.settings import load_settings, save_settings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    server, port = _start_doc_server()
    app = QApplication(sys.argv)
    out_dir = os.path.join(ROOT, "docs", "screenshots")
    base = load_settings()
    corners = ("tl", "tr", "bl", "br")

    for corner in corners:
        settings = {**base, "overlay_now_style": "card", "overlay_now_pos": corner}
        url = build_parity_screenshot_url(port, settings)
        if "float=1" not in url:
            url += "&float=1"
        w, h = 520, 680
        tmp_web = os.path.join(out_dir, f"_tmp-{corner}-web.png")
        tmp_float = os.path.join(out_dir, f"_tmp-{corner}-float.png")
        out = os.path.join(out_dir, f"parity-card-{corner}.png")
        _playwright_shot(url, tmp_web, w, h)
        _floating_shot(app, url, settings, tmp_float, w, h)
        _compose(tmp_web, tmp_float, out, w, h)
        os.remove(tmp_web)
        os.remove(tmp_float)
        print("Wrote", out)

    save_settings(base)
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
