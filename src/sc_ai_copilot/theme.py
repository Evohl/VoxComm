from __future__ import annotations

import atexit
import configparser
import math
import os
import shutil
import tempfile
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


_glyph_dir: Path | None = None


def glyph_path(kind: str, color: QColor) -> str:
    """PNG für Pfeile/Häkchen, die Qt-Stylesheets als image: url(...) brauchen."""
    global _glyph_dir
    if _glyph_dir is None:
        _glyph_dir = Path(tempfile.mkdtemp(prefix="sc-ai-copilot-"))
        atexit.register(shutil.rmtree, _glyph_dir, True)
    path = _glyph_dir / f"{kind}-{color.name()[1:]}.png"
    if not path.exists():
        lines = {
            "down": [(6, 9), (12, 16), (18, 9)],
            "up": [(6, 16), (12, 9), (18, 16)],
            "check": [(5, 12.5), (10, 17.5), (19, 7)],
        }[kind]
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(QPen(color, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in lines]))
        painter.end()
        pixmap.save(str(path), "PNG")
    return path.as_posix()


def dialog_stylesheet(accent: QColor, surface: QColor, text: QColor) -> str:
    field = rgba(text, 16)
    return f"""
        QDialog {{ background-color: {surface.name()}; }}
        QLabel {{ color: {text.name()}; background: transparent; }}
        QLabel#dialogTitle {{ font-size: 18px; font-weight: 700; }}
        QLabel#sectionTitle {{ color: {accent.name()}; font-size: 11px; font-weight: 700; }}
        QLabel#hint {{ color: {rgba(text, 150)}; }}
        QFrame#section {{ background-color: {rgba(text, 9)}; border: 1px solid {rgba(accent, 70)}; border-radius: 12px; }}
        QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
            background-color: {field}; color: {text.name()}; border: 1px solid {rgba(text, 45)};
            border-radius: 8px; padding: 6px 8px; selection-background-color: {rgba(accent, 120)};
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {accent.name()}; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }}
        QComboBox::down-arrow {{ image: url("{glyph_path('down', accent)}"); width: 12px; height: 12px; }}
        QComboBox QAbstractItemView {{
            background-color: {surface.name()}; color: {text.name()}; border: 1px solid {rgba(accent, 120)};
            selection-background-color: {rgba(accent, 110)}; selection-color: {text.name()}; outline: none; padding: 4px;
        }}
        QSpinBox {{ padding-right: 28px; }}
        QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: none; }}
        QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: none; }}
        QSpinBox::up-arrow {{ image: url("{glyph_path('up', accent)}"); width: 10px; height: 10px; }}
        QSpinBox::down-arrow {{ image: url("{glyph_path('down', accent)}"); width: 10px; height: 10px; }}
        QCheckBox {{ color: {text.name()}; spacing: 8px; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {rgba(accent, 170)}; background: transparent; }}
        QCheckBox::indicator:checked {{ background-color: {accent.name()}; image: url("{glyph_path('check', surface)}"); }}
        QPushButton {{
            background-color: transparent; color: {accent.name()}; border: 1px solid {rgba(accent, 140)};
            border-radius: 8px; padding: 6px 14px;
        }}
        QPushButton:hover {{ background-color: {rgba(accent, 46)}; }}
        QPushButton:pressed {{ background-color: {rgba(accent, 96)}; }}
        QPushButton:disabled {{ color: {rgba(text, 90)}; border-color: {rgba(text, 40)}; }}
        QToolButton#iconButton {{ background-color: transparent; border: 1px solid {rgba(accent, 140)}; border-radius: 8px; }}
        QToolButton#iconButton:hover {{ background-color: {rgba(accent, 46)}; }}
        QToolButton#iconButton:pressed {{ background-color: {rgba(accent, 96)}; }}
        QToolButton#iconButton:disabled {{ border-color: {rgba(text, 40)}; background-color: {rgba(text, 12)}; }}
        QPushButton#primary {{ background-color: {accent.name()}; color: {surface.name()}; border: 1px solid {accent.name()}; font-weight: 600; }}
        QPushButton#primary:hover {{ background-color: {accent.lighter(115).name()}; }}
        QPushButton#primary:pressed {{ background-color: {accent.darker(110).name()}; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {rgba(accent, 150)}; border-radius: 4px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


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
    pen = QPen(color, 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
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
