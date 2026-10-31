"""Fur colors, derived shading, and the small appearance preview.

The cat is drawn with vector brushes, so a single base color is expanded into a
small palette: a lighter crown, a darker underside, a readable rim, a chest
patch, and accent colors for sleep marks and highlights. Eyes, nose, and collar
keep their own colors so the face stays readable on both light and dark coats.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from .cat_shapes import draw_cat_base, draw_idle_face

DEFAULT_CAT_COLOR = "#2B333D"
DEFAULT_NAME = "Miso charcoal"

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

PRESETS: list[tuple[str, str]] = [
    ("Cream", "#EFE6DE"),
    ("Ivory", "#F2EADB"),
    ("Charcoal", "#2E2E2E"),
    ("Grey", "#B0B0B0"),
    ("Apricot", "#F0B085"),
    ("Sage", "#A9BFA3"),
    ("Plum", "#7E4B5C"),
    ("Arctic Cyan", "#66D9EF"),
    ("Electric Mint", "#57F2B3"),
    ("Aurora Teal", "#20C9B7"),
    ("Neon Lavender", "#B69CFF"),
    ("Cosmic Purple", "#9061F9"),
    ("Rose Quartz", "#FF8FB1"),
    ("Coral Ember", "#FF795E"),
    ("Sunset Gold", "#FFC857"),
    ("Ice Blue", "#8DBBFF"),
    ("Pearl White", "#E7EDF5"),
    ("Graphite", "#65717F"),
    ("Midnight Navy", "#243B67"),
]

_PRESET_BY_HEX = {hex_value.upper(): name for name, hex_value in PRESETS}


def normalize_color(value) -> str:
    """Return a validated #RRGGBB string, falling back to the default coat."""
    text = str(value or "").strip()
    if text and not text.startswith("#"):
        text = "#" + text
    if HEX_RE.match(text):
        return text.upper()
    return DEFAULT_CAT_COLOR


def color_name(value) -> str:
    color = normalize_color(value)
    return _PRESET_BY_HEX.get(color, "Custom coat" if color != DEFAULT_CAT_COLOR else DEFAULT_NAME)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


_WHITE = QColor(255, 255, 255)
_BLACK = QColor(0, 0, 0)


class FurPalette:
    """Shades derived from the chosen coat color."""

    def __init__(self, hex_value: str):
        base = QColor(normalize_color(hex_value))
        lum = (0.299 * base.red() + 0.587 * base.green() + 0.114 * base.blue()) / 255.0
        self.hex = base.name().upper()
        self.light_coat = lum > 0.55
        self.base = base
        self.top = _mix(base, _WHITE, 0.17)
        self.mid = _mix(base, _WHITE, 0.09)
        self.dark = _mix(base, _BLACK, 0.30)
        self.body_top = _mix(base, _WHITE, 0.11)
        self.edge = _mix(base, _BLACK, 0.62) if lum > 0.14 else _mix(base, _WHITE, 0.32)
        if lum < 0.55:
            self.chest = _mix(base, _WHITE, 0.34)
        else:
            self.chest = _mix(base, _BLACK, 0.20)
        self.chest_light = _mix(self.chest, _WHITE, 0.32)
        self.tail_tip = _mix(base, _WHITE, 0.30) if lum < 0.55 else _mix(base, _BLACK, 0.28)
        self.eye = QColor("#A9E6BD") if lum < 0.55 else QColor("#3E9E72")
        self.eye_pen = self.eye.darker(145)
        self.whisker = (
            QColor(200, 212, 224, 190) if lum < 0.55 else QColor(54, 68, 80, 205)
        )
        self.zzz = _mix(base, _WHITE, 0.58)
        self.spark = _mix(base, _WHITE, 0.74)
        # The face has to stay legible even on a near-black coat.
        self.blush = QColor(220, 130, 140)
        # Flat-faced coat: dot eyes and the small "w" mouth are ink, not iris
        # colors, so they flip to a warm off-white on dark coats.
        self.ink = QColor("#2A2520") if lum > 0.45 else QColor("#F3EFE7")
        self.ink_hi = (
            QColor(255, 255, 255, 215) if lum > 0.45 else QColor(24, 28, 32, 160)
        )
        self.eye = self.ink
        self.eye_pen = self.ink_hi
        self.soft_edge = QColor(self.edge)
        self.soft_edge.setAlpha(115)


DEFAULT_PALETTE = FurPalette(DEFAULT_CAT_COLOR)


class CatPreview(QWidget):
    """Compact portrait painted with the same traced shapes as the pet."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setMinimumSize(148, 148)
        self.setMaximumSize(148, 148)
        self._pal = FurPalette(color)

    def set_color(self, color: str):
        self._pal = FurPalette(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        glow = QRadialGradient(
            QPointF(self.width() / 2, self.height() * 0.45), self.width() * 0.62
        )
        glow.setColorAt(0.0, QColor(255, 255, 255, 16))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawRect(self.rect())

        scale = min(self.width() / 176.0, self.height() / 182.0) * 0.94
        p.translate(
            (self.width() - 176 * scale) / 2,
            (self.height() - 182 * scale) / 2 + 2 * scale,
        )
        p.scale(scale, scale)
        draw_cat_base(p, self._pal)
        draw_idle_face(p, self._pal)
        p.end()
