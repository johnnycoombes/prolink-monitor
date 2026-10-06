"""Open the web monitor panel in a fullscreen browser window when we can.

Browsers ignore ``requestFullscreen`` without a click, so the desktop app
launches Edge or Chrome with ``--kiosk`` on Windows. A normal tab still gets
a one-tap prompt, and the page ships a fullscreen PWA manifest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def with_kiosk_flag(url: str) -> str:
    """Mark a panel URL as opened by the desktop kiosk launcher."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["kiosk"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _windows_browser_candidates() -> list[str]:
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    rels = (
        os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join("Google", "Chrome", "Application", "chrome.exe"),
    )
    found: list[str] = []
    for root in roots:
        if not root:
            continue
        for rel in rels:
            path = os.path.join(root, rel)
            if path not in found:
                found.append(path)
    for name in ("msedge", "chrome"):
        which = shutil.which(name)
        if which and which not in found:
            found.append(which)
    return found


def _posix_browser_candidates() -> list[str]:
    names = (
        "microsoft-edge",
        "microsoft-edge-stable",
        "msedge",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    )
    found: list[str] = []
    for name in names:
        which = shutil.which(name)
        if which and which not in found:
            found.append(which)
    return found


def kiosk_command(url: str, *, platform: str | None = None) -> list[str] | None:
    """Argv for a kiosk browser, or None when no known browser is installed."""
    system = platform or sys.platform
    target = with_kiosk_flag(url)
    if system == "win32":
        candidates = _windows_browser_candidates()
        flag = "--kiosk"
    else:
        candidates = _posix_browser_candidates()
        flag = "--kiosk"
    for exe in candidates:
        if system == "win32" and not os.path.isfile(exe) and shutil.which(exe) != exe:
            continue
        if system != "win32" and not os.path.isfile(exe):
            continue
        extra = ["--no-first-run", "--disable-session-crashed-bubble"]
        exe_name = exe.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if system == "win32" and exe_name.startswith("msedge"):
            extra.append("--edge-kiosk-type=fullscreen")
        return [exe, flag, target, *extra]
    return None


def open_monitor_panel(url: str) -> str:
    """Open the panel fullscreen when a kiosk browser exists.

    Returns a short description: ``kiosk`` or ``browser``.
    """
    cmd = kiosk_command(url)
    if cmd:
        try:
            subprocess.Popen(cmd, close_fds=True)  # noqa: S603 - fixed browser argv
            return "kiosk"
        except OSError:
            pass
    webbrowser.open(with_kiosk_flag(url))
    return "browser"
