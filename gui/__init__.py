"""Desktop GUI for the Pro DJ Link monitor (PySide6)."""

__all__ = ["run"]


def run() -> int:
    from .app import main
    return main()
