"""Artwork path helpers and JPEG normalization."""

from __future__ import annotations

import io
import os
import re


ARTWORK_SOURCES = (
    "embedded",
    "remotedb-hires",
    "nfs-hires",
    "remotedb",
    "thumbnail",
    "none",
)


def highres_nfs_art_path(thumbnail_path: str) -> str:
    """Map rekordbox thumbnail path to ``_m`` (~240px) variant when present."""
    path = (thumbnail_path or "").replace("\\", "/").strip()
    if not path or "_m." in path.lower():
        return path
    base, ext = os.path.splitext(path)
    if not ext:
        ext = ".jpg"
    return f"{base}_m{ext}"


def map_player_path_to_local(local_root: str, player_path: str) -> str | None:
    """Map a rekordbox/USB file path to a path under *local_root*."""
    root = (local_root or "").strip()
    if not root:
        return None
    p = (player_path or "").replace("\\", "/").strip()
    if not p:
        return None
    for prefix in (
        "/C/", "C:/", "c:/",
        "/contents/", "contents/",
        "/PIONEER/", "PIONEER/",
    ):
        if p.upper().startswith(prefix.upper()):
            p = p[len(prefix):]
            break
    p = p.lstrip("/")
    if not p:
        return None
    joined = os.path.normpath(os.path.join(root, p))
    root_norm = os.path.normpath(root)
    if not joined.startswith(root_norm):
        return None
    return joined


def normalize_artwork_jpeg(raw: bytes, *, max_px: int = 1000, quality: int = 88) -> bytes:
    """Resize/compress to JPEG capped at *max_px* on the longest edge."""
    if not raw:
        return raw
    try:
        from PIL import Image
    except ImportError:
        return raw if raw[:3] == b"\xff\xd8\xff" else raw
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return raw
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_px:
        scale = max_px / float(longest)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def looks_like_image(data: bytes) -> bool:
    if not data or len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False
