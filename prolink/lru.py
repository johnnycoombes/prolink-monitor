"""Small bounded caches (by entry count and optionally by bytes)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Iterable


def _size_of(value: Any) -> int:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, dict):          # artwork cache: {"large": (bytes, src), ...}
        total = 0
        for v in value.values():
            total += _size_of(v[0] if isinstance(v, tuple) and v else v)
        return total
    if isinstance(value, tuple) and value:
        return _size_of(value[0])
    return 0


class BoundedLRU(OrderedDict):
    """A dict that drops its least recently used entries.

    ``max_items`` caps the number of entries, ``max_bytes`` (optional) the
    total size of bytes values. ``pinned`` returns keys that must never be
    evicted (the tracks loaded on a deck right now: the per-deck hot set), so a
    burst of browsing cannot push a playing track's waveform out.
    Reads with ``get``/``[]`` count as use. Safe for the callers here, which
    already hold their own lock; a private lock guards the bookkeeping.
    """

    def __init__(self, max_items: int = 50, max_bytes: int | None = None,
                 pinned: Callable[[], Iterable] | None = None):
        super().__init__()
        self.max_items = max(1, int(max_items))
        self.max_bytes = max_bytes
        self.pinned = pinned
        self.bytes = 0
        self._guard = threading.RLock()

    def __getitem__(self, key):
        with self._guard:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key, default=None):
        with self._guard:
            if key in self:
                return self[key]
            return default

    def __setitem__(self, key, value) -> None:
        with self._guard:
            if key in self:
                self.bytes -= _size_of(super().__getitem__(key))
            super().__setitem__(key, value)
            self.move_to_end(key)
            self.bytes += _size_of(value)
            self._evict(keep=key)

    def __delitem__(self, key) -> None:
        with self._guard:
            self.bytes -= _size_of(super().__getitem__(key))
            super().__delitem__(key)

    def pop(self, key, *default):
        with self._guard:
            if key in self:
                value = super().__getitem__(key)
                del self[key]
                return value
            if default:
                return default[0]
            raise KeyError(key)

    def clear(self) -> None:
        with self._guard:
            super().clear()
            self.bytes = 0

    def _over(self) -> bool:
        if len(self) > self.max_items:
            return True
        return self.max_bytes is not None and self.bytes > self.max_bytes

    def _evict(self, keep=None) -> None:
        if not self._over():
            return
        try:
            hot = set(self.pinned() or ()) if self.pinned else set()
        except Exception:
            hot = set()
        for key in list(self.keys()):
            if not self._over():
                break
            if key == keep or key in hot:
                continue
            del self[key]
