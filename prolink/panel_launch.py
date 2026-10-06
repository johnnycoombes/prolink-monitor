"""Open the web monitor panel in a normal browser tab.

The panel opens through ``webbrowser`` like any other link. Fullscreen is only
available from a button on the page, and only after a click.
"""

from __future__ import annotations

import webbrowser


def open_monitor_panel(url: str) -> str:
    """Open ``url`` in the default browser. Returns ``browser``."""
    webbrowser.open(url)
    return "browser"
