"""Prove a frozen build can import pyrekordbox and open a SQLCipher fixture.

    ProlinkListener.exe --check-onelibrary

The Windows release job runs that. It does not talk to a player.
"""

from __future__ import annotations

import os
import sys
import tempfile


def run(dest: str | None = None) -> None:
    """Import masterdb, build a one-row SQLCipher file, and read it back."""
    import sqlcipher3
    import pyrekordbox.masterdb  # noqa: F401
    from pyrekordbox.onelibrary.database import BLOB, OneLibrary
    from pyrekordbox.utils import deobfuscate
    from sqlalchemy import text

    from prolink.pysqlcipher_dialect import register

    register()
    key = deobfuscate(BLOB)
    if dest:
        path = dest
        if os.path.exists(path):
            os.remove(path)
    else:
        folder = tempfile.mkdtemp(prefix="prolink-ol-")
        path = os.path.join(folder, "exportLibrary.db")
    conn = sqlcipher3.connect(path)
    escaped = str(key).replace("'", "''")
    conn.execute(f"PRAGMA key = '{escaped}'")
    conn.execute("CREATE TABLE content (content_id INTEGER, title TEXT)")
    conn.execute("INSERT INTO content (content_id, title) VALUES (1, 'Fixture')")
    conn.commit()
    conn.close()

    db = OneLibrary(path)
    try:
        row = db.session.execute(text("SELECT title FROM content")).one()
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()
    title = row[0]
    if title != "Fixture":
        raise RuntimeError(f"fixture row was {title!r}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dest = args[0] if args else None
    try:
        run(dest)
    except Exception as exc:
        _report(f"FAIL {exc}")
        return 1
    _report("OK")
    return 0


def _report(text: str) -> None:
    try:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
