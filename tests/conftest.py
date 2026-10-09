"""Keep pytest from treating imported helpers as tests.

``prolink.wnp.test_connection`` is the Settings "Test" button. ``tests/test_wnp.py``
imports it, and pytest would otherwise collect that function because its name
starts with ``test_``.
"""

from __future__ import annotations

import inspect


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
