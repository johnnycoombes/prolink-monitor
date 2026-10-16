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


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _fresh_dbserver_port_cache():
    """Fake dbservers reuse 127.0.0.1; never let one test's port leak into another."""
    from prolink import remotedb
    remotedb._PORT_CACHE.clear()
    yield
    remotedb._PORT_CACHE.clear()


@_pytest.fixture(autouse=True)
def _collect_qt_garbage_between_tests():
    """Free each test's widgets before the next test, not inside its event loop.

    A widget caught in a reference cycle (for example a page whose frame is
    held by a caught exception's traceback) outlives its test. The cyclic GC
    then destroys it at an arbitrary allocation, often inside the next
    test's ``QApplication.processEvents()``. Tearing a widget tree down from
    inside Qt's event dispatch corrupts the heap: segfault/abort (exit 139)
    in tests/test_az_anlz.py on macos-14 Python 3.14, reproducible on Linux.
    Collecting here keeps destruction deterministic. Tests that do not use
    Qt pay one gc pass.
    """
    import gc

    yield
    gc.collect()
    if "PySide6.QtWidgets" in sys.modules:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            from PySide6.QtCore import QCoreApplication, QEvent

            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
