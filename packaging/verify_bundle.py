"""Check that a PyInstaller folder contains the Qt WebEngine files and web assets.

    python packaging/verify_bundle.py dist/ProlinkListener
"""

from __future__ import annotations

import sys
from pathlib import Path


def verify(dist: Path) -> list[str]:
    """Return a list of problems. An empty list means the folder looks usable."""
    dist = Path(dist)
    errors: list[str] = []
    exe_candidates = [
        dist / "ProlinkListener.exe",
        dist / "ProlinkListener",
    ]
    if not any(path.is_file() for path in exe_candidates):
        errors.append("ProlinkListener executable is missing")

    def _find(relative: str) -> bool:
        return (dist / relative).is_file() or (dist / "_internal" / relative).is_file()

    if not _find("web/index.html"):
        errors.append("web/index.html is missing")
    if not _find("web/overlay.html"):
        errors.append("web/overlay.html is missing")
    if not list(dist.rglob("QtWebEngineProcess.exe")) and not list(dist.rglob("QtWebEngineProcess")):
        errors.append("QtWebEngineProcess is missing")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python packaging/verify_bundle.py dist/ProlinkListener", file=sys.stderr)
        return 2
    errors = verify(Path(args[0]))
    if errors:
        print("Windows bundle is incomplete:")
        for item in errors:
            print(f"  - {item}")
        return 1
    print(f"Bundle OK: {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
