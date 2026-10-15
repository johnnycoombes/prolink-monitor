"""Check that a PyInstaller folder contains the Qt WebEngine files and web assets.

    python packaging/verify_bundle.py dist/ProlinkListener
    python packaging/verify_bundle.py dist/ProlinkListener.app   (macOS)
"""

from __future__ import annotations

import sys
from pathlib import Path


def verify(dist: Path) -> list[str]:
    """Return a list of problems. An empty list means the folder looks usable."""
    dist = Path(dist)
    errors: list[str] = []
    if dist.suffix == ".app":
        errors += verify_mac_plist(dist)
        exe_candidates = [dist / "Contents" / "MacOS" / "ProlinkListener"]
        roots = [dist / "Contents" / "Resources", dist / "Contents" / "Frameworks",
                 dist / "Contents" / "MacOS"]
    else:
        exe_candidates = [
            dist / "ProlinkListener.exe",
            dist / "ProlinkListener",
        ]
        roots = [dist, dist / "_internal"]
    if not any(path.is_file() for path in exe_candidates):
        errors.append("ProlinkListener executable is missing")

    def _find(relative: str) -> bool:
        return any((base / relative).is_file() for base in roots)

    if not _find("web/index.html"):
        errors.append("web/index.html is missing")
    if not _find("web/overlay.html"):
        errors.append("web/overlay.html is missing")
    if not _find("web/wavepalette.json"):
        errors.append("web/wavepalette.json (3-band palettes) is missing")
    if not _find("assets/icons/music-note.svg"):
        errors.append("assets/icons (Phosphor icons) is missing")
    if not list(dist.rglob("QtWebEngineProcess.exe")) and not list(dist.rglob("QtWebEngineProcess")):
        errors.append("QtWebEngineProcess is missing")

    def _parts() -> list[tuple[tuple[str, ...], Path]]:
        found = []
        for path in dist.rglob("*"):
            if path.is_file():
                found.append((path.relative_to(dist).parts, path))
        return found

    files = _parts()

    def _in_pyz(name: str) -> bool:
        """Pure-Python packages can live only inside the PYZ archive."""
        needle = name.encode()
        for parts, path in files:
            if path.suffix.lower() == ".pyz" or path.name.endswith(".pkg"):
                try:
                    if needle in path.read_bytes():
                        return True
                except OSError:
                    continue
        return False

    def _has_part(name: str) -> bool:
        return any(name in parts for parts, _path in files)

    if not _has_part("pyrekordbox"):
        errors.append("pyrekordbox is missing from the frozen folder")
    if not _has_part("mutagen") and not _in_pyz("mutagen"):
        errors.append("mutagen (hi-res artwork from file) is missing from the frozen folder")
    if not _has_part("sqlcipher3"):
        errors.append("sqlcipher3 is missing from the frozen folder")
    has_sqlite = False
    for parts, path in files:
        if "sqlcipher3" in parts:
            continue
        if any(part == "sqlite3" or part.startswith("_sqlite3") for part in parts):
            has_sqlite = True
            break
        if path.name.startswith("_sqlite3") or path.name.startswith("sqlite3"):
            has_sqlite = True
            break
    if not has_sqlite:
        errors.append("sqlite3 is missing from the frozen folder")
    cipher_bins = [
        path for parts, path in files
        if "sqlcipher3" in parts and (
            path.suffix.lower() in {".pyd", ".dll", ".so"} or ".so" in path.name
        )
    ]
    if not cipher_bins:
        errors.append("sqlcipher3 binary extension is missing from the frozen folder")
    return errors


def verify_mac_plist(app: Path) -> list[str]:
    """Info.plist checks for the macOS bundle."""
    import plistlib

    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        return ["Contents/Info.plist is missing"]
    try:
        info = plistlib.loads(plist.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return [f"Contents/Info.plist cannot be read ({exc})"]
    errors = []
    if not str(info.get("NSLocalNetworkUsageDescription") or "").strip():
        errors.append("Info.plist has no NSLocalNetworkUsageDescription "
                      "(macOS 14+ would block Pro DJ Link traffic)")
    if info.get("CFBundleIdentifier") != "com.johnnycoombes.prolinklistener":
        errors.append("Info.plist CFBundleIdentifier is not com.johnnycoombes.prolinklistener")
    if not info.get("CFBundleShortVersionString"):
        errors.append("Info.plist has no CFBundleShortVersionString")
    icon = str(info.get("CFBundleIconFile") or "")
    if not icon or not list((app / "Contents" / "Resources").glob(icon.split(".")[0] + "*.icns")):
        errors.append("app icon (.icns) is missing")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python packaging/verify_bundle.py dist/ProlinkListener", file=sys.stderr)
        return 2
    errors = verify(Path(args[0]))
    if errors:
        print("Bundle is incomplete:")
        for item in errors:
            print(f"  - {item}")
        return 1
    print(f"Bundle OK: {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
