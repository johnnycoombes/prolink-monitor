"""Names and paths for the Windows app and its signed updates.

Private signing keys never live in this file. The public trust anchor, when
JC has generated one, is ``packaging/tuf/root.json``.
"""

from __future__ import annotations

import os
from pathlib import Path

# tufup archive names only allow letters, numbers, underscore, and hyphen.
APP_NAME = "prolink-listener"
DISPLAY_NAME = "Prolink Listener"
WINDOWS_EXE_NAME = "ProlinkListener.exe"
DIST_FOLDER_NAME = "ProlinkListener"

GITHUB_OWNER = "johnnycoombes"
GITHUB_REPO = "prolink-monitor"
# Stable tag whose release assets are the TUF metadata and update archives.
# This is not the zip JC installs by hand.
UPDATES_TAG = "updates"

# Short expirations (tufup's timestamp default is 1 day) would make the app
# refuse updates whenever a month passes without a release. 90 days matches
# how this project is actually shipped. The cost is that a stolen timestamp
# key could freeze updates for that long. Root still lasts a year.
EXPIRATION_DAYS = {
    "root": 365,
    "targets": 90,
    "snapshot": 90,
    "timestamp": 90,
}

ROLE_NAMES = ("root", "targets", "snapshot", "timestamp")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def trusted_root_in_source() -> Path:
    return repo_root() / "packaging" / "tuf" / "root.json"


def config_dir() -> Path:
    """Same folder as ``settings.json`` (``~/.prolink-monitor`` by default)."""
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".prolink-monitor"


def library_cache_dir() -> Path:
    """USB/NFS cache. The updater must never write here."""
    return Path.home() / ".prolink-cache"


def update_cache_dir() -> Path:
    """Downloaded TUF metadata and archives. Not the library cache."""
    return config_dir() / "updates"


def releases_page_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


def _base_url(env_name: str, default: str) -> str:
    raw = os.environ.get(env_name, "").strip() or default
    return raw if raw.endswith("/") else raw + "/"


def metadata_base_url() -> str:
    """Directory URL that serves ``timestamp.json`` and the other metadata."""
    default = (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/releases/download/{UPDATES_TAG}/"
    )
    return _base_url("PROLINK_UPDATE_METADATA_URL", default)


def target_base_url() -> str:
    """Directory URL that serves ``prolink-listener-<version>.tar.gz``."""
    default = (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/releases/download/{UPDATES_TAG}/"
    )
    return _base_url("PROLINK_UPDATE_TARGET_URL", default)


def app_version() -> str:
    from prolink import __version__

    return str(__version__)
