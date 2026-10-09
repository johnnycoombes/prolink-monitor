"""Keep pytest from treating imported helpers as tests.

``prolink.wnp.test_connection`` is the Settings "Test" button. ``tests/test_wnp.py``
imports it, and pytest would otherwise collect that function because its name
starts with ``test_``.
"""

from __future__ import annotations

import inspect
import os
import sys


def pytest_pycollect_makeitem(collector, name, obj):
    if not name.startswith("test_"):
        return None
    if not (inspect.isfunction(obj) or inspect.ismethod(obj)):
        return None
    owner = getattr(collector, "module", None)
    if owner is None:
        return None
    if getattr(obj, "__module__", None) != owner.__name__:
        return []
    return None


def pytest_sessionfinish(session, exitstatus):
    """Leave before Qt's atexit handler on GitHub-hosted Ubuntu.

    The suite itself finishes cleanly. ``setup-python`` still points
    ``LD_LIBRARY_PATH`` at its own ``lib/``, and Qt then dies with SIGBUS
    (exit 135) while destroying a QObject, which fails the job after
    "passed". This runs only when every test passed, so a real failure
    still exits through pytest.
    """
    if os.environ.get("CI") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        return
    if exitstatus != 0:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
