from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


APP_NAME = "VoxComm"
APP_ID = "voxcomm"
APP_COMMENT = "Lokaler Sprach- und Funkassistent"

# Verlauf in userSpaceOnUse, weil objectBoundingBox bei senkrechten Linien nichts zeichnet.
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="accent" gradientUnits="userSpaceOnUse" x1="20" y1="20" x2="108" y2="108">
      <stop offset="0" stop-color="#29c5fe"/>
      <stop offset="1" stop-color="#7be3b5"/>
    </linearGradient>
    <linearGradient id="panel" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="128">
      <stop offset="0" stop-color="#2a3138"/>
      <stop offset="1" stop-color="#171b1f"/>
    </linearGradient>
  </defs>
  <rect x="6" y="6" width="116" height="116" rx="30" fill="url(#panel)"/>
  <rect x="6.75" y="6.75" width="114.5" height="114.5" rx="29.25" fill="none" stroke="url(#accent)" stroke-opacity="0.55" stroke-width="1.5"/>
  <circle cx="64" cy="64" r="38" fill="none" stroke="url(#accent)" stroke-opacity="0.35" stroke-width="5"/>
  <g stroke="url(#accent)" stroke-width="8" stroke-linecap="round">
    <line x1="36" y1="56" x2="36" y2="72"/>
    <line x1="50" y1="46" x2="50" y2="82"/>
    <line x1="64" y1="34" x2="64" y2="94"/>
    <line x1="78" y1="46" x2="78" y2="82"/>
    <line x1="92" y1="56" x2="92" y2="72"/>
  </g>
</svg>
"""

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def make_app_icon() -> QIcon:
    renderer = QSvgRenderer(QByteArray(ICON_SVG.encode()))
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _launcher_command() -> str:
    script = Path(sys.executable).with_name(APP_ID)
    command = str(script) if script.exists() else f"{sys.executable} -m voxcomm.app"
    return command


def _desktop_exec(command: str) -> str:
    # Leerzeichen im Programmpfad müssen laut Desktop-Entry-Spezifikation gequotet werden.
    program, _, arguments = command.partition(" -m ")
    quoted = f'"{program}"' if " " in program else program
    return f"{quoted} -m {arguments}" if arguments else quoted


def install_launcher(data_home: Path | None = None, command: str | None = None) -> tuple[Path, Path]:
    """Legt Starter und Icon im Anwendungsmenü des Benutzers an und gibt beide Pfade zurück."""
    base = data_home or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    icon_path = base / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    desktop_path = base / "applications" / f"{APP_ID}.desktop"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_text(ICON_SVG, encoding="utf-8")
    desktop_path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Comment={APP_COMMENT}\n"
        f"Exec={_desktop_exec(command or _launcher_command())}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
        f"StartupWMClass={APP_ID}\n",
        encoding="utf-8",
    )
    return desktop_path, icon_path
