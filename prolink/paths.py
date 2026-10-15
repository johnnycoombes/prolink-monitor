"""Where settings and the library cache live on each platform.

Windows and Linux keep the folders this app has always used:
``~/.prolink-monitor`` (settings, logs, sessions, captures) and
``~/.prolink-cache`` (USB/NFS cache). macOS follows Apple's layout:
``~/Library/Application Support/Prolink Listener`` and
``~/Library/Caches/Prolink Listener``. ``PROLINK_CONFIG_DIR`` overrides the
settings folder everywhere.
"""

from __future__ import annotations

import os
import sys

MAC_APP_DIR = "Prolink Listener"


def _home() -> str:
    return os.path.expanduser("~")


def default_config_dir(platform: str | None = None) -> str:
    plat = platform or sys.platform
    if plat == "darwin":
        return os.path.join(_home(), "Library", "Application Support", MAC_APP_DIR)
    return os.path.join(_home(), ".prolink-monitor")


def config_dir(platform: str | None = None) -> str:
    return os.environ.get("PROLINK_CONFIG_DIR") or default_config_dir(platform)


def cache_dir(platform: str | None = None) -> str:
    plat = platform or sys.platform
    if plat == "darwin":
        return os.path.join(_home(), "Library", "Caches", MAC_APP_DIR)
    return os.path.join(_home(), ".prolink-cache")
