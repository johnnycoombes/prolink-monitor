"""Stop worker threads on the way out, then leave if any are still alive.

The desktop process used to return from the Qt loop while Pro DJ Link
sockets, the web server, or a WebEngine helper were still running. Callers
join the named workers with a timeout. If any are still alive after a short
grace period, ``os._exit`` ends the process.

``request_process_exit`` does nothing until ``arm_process_exit``. Tests build
``QApplication`` and must not be killed by that hard exit.
"""

from __future__ import annotations

import os
import threading
import time

# Threads this app starts. Daemon or not, they are joined on the way out.
WORKER_PREFIXES = (
    "prolink-",
    "wnp-",
    "spectrum-loopback",
    "library-browse",
)

_armed = False


def arm_process_exit() -> None:
    """Allow the real app entry to force-exit. Tests leave this off."""
    global _armed
    _armed = True


def is_worker(name: str) -> bool:
    return str(name or "").startswith(WORKER_PREFIXES)


def worker_threads() -> list[threading.Thread]:
    current = threading.current_thread()
    main = threading.main_thread()
    found: list[threading.Thread] = []
    for thread in threading.enumerate():
        if thread is current or thread is main or not thread.is_alive():
            continue
        if is_worker(thread.name):
            found.append(thread)
    return found


def blocking_threads() -> list[str]:
    """Non-daemon threads that would keep the interpreter alive after main returns."""
    current = threading.current_thread()
    names: list[str] = []
    for thread in threading.enumerate():
        if not thread.is_alive() or thread.daemon:
            continue
        if thread is current or thread is threading.main_thread():
            continue
        names.append(thread.name)
    return names


def join_workers(timeout: float = 1.5) -> list[str]:
    """Join named workers. Returns the names that are still alive.

    Each worker is marked daemon when Python still allows it, so a thread
    that misses the timeout cannot hold the process open by itself.
    """
    workers = worker_threads()
    deadline = time.monotonic() + max(0.0, float(timeout))
    for thread in workers:
        try:
            thread.daemon = True
        except RuntimeError:
            pass
        if not thread.is_alive():
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            thread.join(remaining)
    return [thread.name for thread in workers if thread.is_alive()]


def force_exit_if_lingering(names: list[str], exit_fn=None) -> bool:
    """Call ``os._exit(0)`` when ``names`` is not empty. Returns whether it did."""
    if not names:
        return False
    (exit_fn or os._exit)(0)
    return True


def lingering_names() -> list[str]:
    """Workers still running, plus any non-daemon thread besides main."""
    names = join_workers(0.0)
    for name in blocking_threads():
        if name not in names:
            names.append(name)
    return names


def request_process_exit(grace: float = 0.4, timeout: float = 1.5) -> list[str]:
    """Join workers. After ``grace`` seconds, hard-exit if anything remains.

    Returns the names that were still alive when the grace timer was armed.
    An empty list means the process can leave normally. Unarmed callers
    (pytest) get that join without ``os._exit``.
    """
    lingering = join_workers(timeout)
    blockers = [name for name in blocking_threads() if name not in lingering]
    names = lingering + blockers
    if not _armed:
        return names

    def _later() -> None:
        # Still here after the grace period: a worker, Qt, or WebEngine did
        # not leave. os._exit does not run atexit handlers.
        still = lingering_names()
        force_exit_if_lingering(still or ["prolink-quit"])

    timer = threading.Timer(max(0.05, float(grace)), _later)
    timer.daemon = True
    timer.start()
    return names
