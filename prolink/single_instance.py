"""One Prolink Listener process.

A second launch used to start another process. With close-to-tray the first
window is hidden, so it looked like a fresh start, and the new process could
not bind the web port the first one still held.

The first process listens on 127.0.0.1. A later launch connects, sends one
line (``show``, ``open-panel``, or ``open-overlay``), and exits. The first
process brings its window forward, including when that window is in the tray.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading
import time

# Local only. Not the web panel port.
INSTANCE_PORT = 47331
INSTANCE_HOST = "127.0.0.1"
COMMANDS = ("show", "open-panel", "open-overlay")


def launch_command(argv: list[str] | None = None) -> str:
    """What a second process should ask the running one to do."""
    args = list(sys.argv if argv is None else argv)
    if args:
        args = args[1:]
    if "--open-overlay" in args:
        return "open-overlay"
    if "--open-panel" in args or "--open-web" in args:
        return "open-panel"
    return "show"


def apply_instance_command(command: str, *, show, open_panel, open_overlay) -> None:
    """Bring the existing window forward, then honour an open request.

    ``show`` is ``restore_from_tray``: it works when the window is visible and
    when it was hidden by close-to-tray or minimize-to-tray.
    """
    show()
    if command == "open-panel":
        open_panel()
    elif command == "open-overlay":
        open_overlay()


class InstanceHub:
    """Accepts one-line commands from a later launch."""

    def __init__(self, port: int = INSTANCE_PORT, host: str = INSTANCE_HOST):
        self.port = int(port)
        self.host = host
        self._commands: queue.Queue[str] = queue.Queue()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        """Bind the command port. False when another listener already has it."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if exclusive is not None:
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                    except OSError:
                        pass
            sock.bind((self.host, self.port))
            sock.listen(8)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        self.port = int(sock.getsockname()[1])
        self._thread = threading.Thread(
            target=self._loop, name="prolink-instance", daemon=True,
        )
        self._thread.start()
        return True

    def poll(self) -> list[str]:
        found: list[str] = []
        while True:
            try:
                found.append(self._commands.get_nowait())
            except queue.Empty:
                return found

    def close(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(1.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            worker = threading.Thread(
                target=self._handle, args=(conn,), name="prolink-instance-cmd", daemon=True,
            )
            worker.start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(2.0)
            data = b""
            while b"\n" not in data and len(data) < 128:
                chunk = conn.recv(128)
                if not chunk:
                    break
                data += chunk
            text = data.decode("utf-8", "replace").strip()
            command = text.splitlines()[0].strip() if text else "show"
            if command not in COMMANDS:
                command = "show"
            self._commands.put(command)
            conn.sendall(b"ok\n")
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def notify_instance(command: str, *, port: int = INSTANCE_PORT,
                    host: str = INSTANCE_HOST, timeout: float = 0.4,
                    attempts: int = 8) -> bool:
    """Tell the running instance what to do. False when nobody answers."""
    payload = (command.strip() or "show") + "\n"
    for _ in range(max(1, attempts)):
        try:
            with socket.create_connection((host, int(port)), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(payload.encode("utf-8"))
                reply = sock.recv(16)
            if reply.startswith(b"ok"):
                return True
        except (ConnectionRefusedError, TimeoutError, socket.timeout):
            time.sleep(0.05)
            continue
        except OSError:
            return False
    return False


def claim_or_handoff(command: str, *, port: int = INSTANCE_PORT,
                     host: str = INSTANCE_HOST) -> InstanceHub | None:
    """Become the primary process, or hand ``command`` to the one already running.

    ``None`` means this process should exit: the running instance accepted the
    command. A hub means this process is the one that should open the window.
    When the port is busy and nothing answers, a hub that is not listening is
    returned so the app still starts.
    """
    hub = InstanceHub(port=port, host=host)
    if hub.start():
        return hub
    if notify_instance(command, port=port, host=host):
        print("Prolink Listener is already running.", file=sys.stderr)
        return None
    if hub.start():
        return hub
    if notify_instance(command, port=port, host=host):
        print("Prolink Listener is already running.", file=sys.stderr)
        return None
    print(
        "Single-instance port is busy and nobody answered. Starting anyway.",
        file=sys.stderr,
    )
    return hub
