"""Phosphor icons (MIT) for the Qt GUI, recoloured to the theme.

The SVGs live in ``assets/icons`` and use ``currentColor``. They are rendered
with QSvgRenderer after swapping ``currentColor`` for the requested colour.
Rich-text labels (loop badge, rating) get an ``<img>`` that points at a cached
PNG, because QLabel rich text cannot take a QPixmap directly.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from prolink.bundle import bundle_root

NOTE = "music-note"
LOOP = "repeat"
LIST = "list"
STAR = "star"
STAR_FILL = "star-fill"
STAR_HALF = "star-half-fill"
GRIP = "dots-six-vertical-bold"


def icons_dir() -> str:
    """``assets/icons`` in a checkout or inside the PyInstaller bundle."""
    return os.path.join(bundle_root(), "assets", "icons")


def icon_path(name: str) -> str:
    return os.path.join(icons_dir(), f"{name}.svg")


@lru_cache(maxsize=None)
def _svg_source(name: str) -> str:
    with open(icon_path(name), encoding="utf-8") as fh:
        return fh.read()


def _color_name(color) -> str:
    """``#rrggbb`` or ``#rrggbb@alpha``; SVG has no #AARRGGBB form."""
    c = QColor(color)
    return c.name() if c.alpha() >= 255 else f"{c.name()}@{c.alpha()}"


def _svg_with(name: str, color: str) -> tuple[QSvgRenderer, float]:
    rgb, _, alpha = color.partition("@")
    data = _svg_source(name).replace("currentColor", rgb)
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    return renderer, (int(alpha) / 255.0 if alpha else 1.0)


@lru_cache(maxsize=256)
def _render(name: str, color: str, px: int) -> QImage:
    renderer, opacity = _svg_with(name, color)
    image = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setOpacity(opacity)
        renderer.render(painter, QRectF(0, 0, px, px))
        painter.end()
    return image


def icon_pixmap(name: str, color="#ffffff", size: int = 16, dpr: float = 2.0) -> QPixmap:
    """A ``size``×``size`` logical pixmap, rendered at ``dpr`` for sharp HiDPI."""
    dpr = max(1.0, float(dpr or 1.0))
    px = max(1, int(round(size * dpr)))
    pix = QPixmap.fromImage(_render(name, _color_name(color), px))
    pix.setDevicePixelRatio(dpr)
    return pix


def icon(name: str, color="#ffffff", size: int = 16) -> QIcon:
    result = QIcon()
    for dpr in (1.0, 2.0):
        result.addPixmap(icon_pixmap(name, color, size, dpr))
    return result


def paint_icon(painter: QPainter, name: str, rect: QRectF, color) -> None:
    """Draw an icon straight into ``rect`` (vector, so any size stays crisp)."""
    renderer, opacity = _svg_with(name, _color_name(color))
    if renderer.isValid():
        painter.save()
        painter.setOpacity(painter.opacity() * opacity)
        renderer.render(painter, rect)
        painter.restore()


def _cache_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "prolink-listener-icons")
    os.makedirs(path, exist_ok=True)
    return path


def icon_html(name: str, color="#ffffff", size: int = 12) -> str:
    """``<img>`` tag for QLabel rich text, backed by a cached 3x PNG."""
    color = _color_name(color)
    key = hashlib.sha1(f"{name}|{color}|{size}".encode()).hexdigest()[:16]
    path = os.path.join(_cache_dir(), f"{name}-{key}.png")
    if not os.path.isfile(path):
        _render(name, color, size * 3).save(path, "PNG")
    url = path.replace("\\", "/")
    return f'<img src="{url}" width="{size}" height="{size}" style="vertical-align:middle">'


def stars_html(stars: int, color="#ffd45e", size: int = 12) -> str:
    """Five Phosphor stars: ``stars`` filled, the rest outlined."""
    n = max(0, min(5, int(stars or 0)))
    if not n:
        return ""
    return icon_html(STAR_FILL, color, size) * n + icon_html(STAR, color, size) * (5 - n)
