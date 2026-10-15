"""Rotating log for library source, counts, and errors.

The file is ``~/.prolink-monitor/prolink-monitor.log``. ``PROLINK_CONFIG_DIR``
overrides the folder, the same way settings do. Nothing here is sent to the
player.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOGGER_NAME = "prolink.library"
_CONFIGURED = False
_LAST = ""


def log_path() -> str:
    base = os.environ.get("PROLINK_CONFIG_DIR")
    if not base:
        from prolink.paths import default_config_dir
        base = default_config_dir()
    return os.path.join(base, "prolink-monitor.log")


def configure(path: str | None = None) -> str:
    """Attach one rotating file handler. Safe to call more than once."""
    global _CONFIGURED
    dest = path or log_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not _CONFIGURED:
        handler = RotatingFileHandler(
            dest, maxBytes=1_048_576, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        _CONFIGURED = True
    return dest


def reset_for_tests(path: str | None = None) -> str:
    """Drop the handler so a test can point the log at a temp folder."""
    global _CONFIGURED, _LAST
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)
    _CONFIGURED = False
    _LAST = ""
    return configure(path)


def log_note(message: str) -> None:
    """One line in the same rotating file (HTTP bind, dbserver attempts)."""
    configure()
    text = " ".join(str(message or "").split())
    if not text:
        return
    logging.getLogger(_LOGGER_NAME).info(text)


def log_library(
    *,
    host: str = "",
    source: str = "",
    detail: str = "",
    counts: dict | None = None,
    error: str = "",
    onelibrary_tracks: int | None = None,
) -> None:
    """One line when the library source, counts, or error change."""
    global _LAST
    configure()
    totals = counts or {}
    parts = [
        f"host={host or '-'}",
        f"source={source or 'none'}",
        f"tracks={totals.get('tracks', '-')}",
        f"artists={totals.get('artists', '-')}",
        f"albums={totals.get('albums', '-')}",
    ]
    if onelibrary_tracks is not None:
        parts.append(f"onelibrary_tracks={onelibrary_tracks}")
    if detail:
        parts.append(f"detail={detail}")
    if error:
        parts.append(f"error={error}")
    line = "library " + " ".join(parts)
    if line == _LAST:
        return
    _LAST = line
    logging.getLogger(_LOGGER_NAME).info(line)
