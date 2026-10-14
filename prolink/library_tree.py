"""Playlist tree helpers shared by the live (dbserver) and fallback sources.

Read-only: these only reshape rows that were already read.
"""

from __future__ import annotations

from typing import Any, Iterable


def with_depth(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order a flat ``parent_id`` list depth-first and add ``depth``.

    Rows whose parent is missing (or that form a loop) are attached to the
    root so nothing is silently dropped.
    """
    items = [dict(r) for r in rows if int(r.get("id") or 0)]
    ids = {int(r["id"]) for r in items}
    children: dict[int, list[dict[str, Any]]] = {}
    for r in items:
        parent = int(r.get("parent_id") or 0)
        if parent not in ids or parent == int(r["id"]):
            parent = 0
        r["parent_id"] = parent
        children.setdefault(parent, []).append(r)
    for kids in children.values():
        kids.sort(key=lambda r: (int(r.get("sort") or 0), not r.get("folder"),
                                 str(r.get("name") or "").lower()))
    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(parent: int, depth: int) -> None:
        for r in children.get(parent, []):
            rid = int(r["id"])
            if rid in seen:
                continue
            seen.add(rid)
            r["depth"] = depth
            out.append(r)
            walk(rid, depth + 1)

    walk(0, 0)
    for r in items:  # a loop with no path to the root
        if int(r["id"]) not in seen:
            r["parent_id"] = 0
            r["depth"] = 0
            out.append(r)
    return out


def tree_stats(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Folder / playlist counts and the deepest level (root = 0)."""
    rows = list(rows)
    folders = sum(1 for r in rows if r.get("folder"))
    return {
        "folders": folders,
        "playlists": len(rows) - folders,
        "nested": sum(1 for r in rows if int(r.get("parent_id") or 0)),
        "max_depth": max((int(r.get("depth") or 0) for r in rows), default=0),
    }
