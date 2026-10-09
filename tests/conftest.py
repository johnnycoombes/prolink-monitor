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
    """Leave before Qt's atexit handler on GitHub-hosted runners.

    The suite itself finishes cleanly, then Qt crashes while shutting down.
    Ubuntu reports that as SIGBUS (exit 135). Windows Git bash turns the
    same teardown crash into exit 127. Either one fails the job after
    "passed". This runs only when every test passed, so a real failure
    still exits through pytest.
    """
    if os.environ.get("CI") != "true":
        return
    if exitstatus != 0:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
