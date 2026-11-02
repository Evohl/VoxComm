from __future__ import annotations

import configparser
import math
import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication


def _kdeglobals_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "kdeglobals"


def read_kde_color(section: str, key: str, path: Path | None = None) -> QColor | None:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path or _kdeglobals_path(), encoding="utf-8")
    except (OSError, configparser.Error):
        # Teilweise geparste Dateien sind für die Farbsuche unbrauchbar.
        return None
    try:
        red, green, blue = (int(part) for part in parser.get(section, key, fallback="").split(",")[:3])
    except ValueError:
        return None
    return QColor(red, green, blue)


def system_accent() -> QColor:
    color = read_kde_color("General", "AccentColor")
    if color is not None:
        return color
    palette = QApplication.palette()
    return palette.color(QPalette.ColorRole.Accent if hasattr(QPalette.ColorRole, "Accent") else QPalette.ColorRole.Highlight)


def system_surface() -> QColor:
    return read_kde_color("Colors:Window", "BackgroundNormal") or QApplication.palette().color(QPalette.ColorRole.Window)


def system_text() -> QColor:
    return read_kde_color("Colors:Window", "ForegroundNormal") or QApplication.palette().color(QPalette.ColorRole.WindowText)


def rgba(color: QColor, alpha: int) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def mix_colors(first: QColor, second: QColor, weight: float) -> QColor:
    """Mischt zwei Farben; weight ist der Anteil von first (0..1)."""
    return QColor(
        round(first.red() * weight + second.red() * (1 - weight)),
        round(first.green() * weight + second.green() * (1 - weight)),
        round(first.blue() * weight + second.blue() * (1 - weight)),
    )


def _gear_path(center: float, outer: float, inner: float, hole: float, teeth: int = 8) -> QPainterPath:
    points = []
    for tooth in range(teeth):
        base = 2 * math.pi * tooth / teeth
        step = 2 * math.pi / teeth
        for offset, radius in ((-0.30, inner), (-0.17, outer), (0.17, outer), (0.30, inner)):
            angle = base + offset * step
            points.append(QPointF(center + radius * math.cos(angle), center + radius * math.sin(angle)))
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    path.addEllipse(QPointF(center, center), hole, hole)
    return path


def make_icon(kind: str, color: QColor, size: int = 96) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64, size / 64)
    pen_width = 6 if kind == "refresh" else 4.5
    pen = QPen(color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(color)
    if kind == "mic":
        painter.drawRoundedRect(QRectF(24, 6, 16, 30), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(15, 16, 34, 28), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(32, 44), QPointF(32, 56))
        painter.drawLine(QPointF(23, 56), QPointF(41, 56))
    elif kind == "play":
        painter.drawPolygon(QPolygonF([QPointF(20, 12), QPointF(20, 52), QPointF(52, 32)]))
    elif kind == "gear":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_gear_path(32, 27, 20, 8))
    elif kind == "refresh":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(14, 14, 36, 36), 40 * 16, 280 * 16)
        end_angle = math.radians(320)
        end = QPointF(32 + 18 * math.cos(end_angle), 32 - 18 * math.sin(end_angle))
        tangent = QPointF(-math.sin(end_angle), -math.cos(end_angle))
        normal = QPointF(-tangent.y(), tangent.x())
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end + tangent * 10, end + normal * 8, end - normal * 8]))
    painter.end()
    return QIcon(pixmap)
