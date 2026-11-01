from __future__ import annotations

import re
from pathlib import Path

WINE_DRIVE_ROOT = re.compile(r"^Z:/", re.IGNORECASE)
BIN64_DIR = re.compile(r"/Bin64/StarCitizen\.exe$", re.IGNORECASE)


def log_path_from_executable(executable: str) -> Path | None:
    """Map a Wine executable path (Z:\\ is the Linux root) to the Game.log next to Bin64."""
    normalized = executable.replace("\\", "/")
    if not WINE_DRIVE_ROOT.match(normalized):
        return None
    linux_path = "/" + normalized[len("Z:/"):]
    if not BIN64_DIR.search(linux_path):
        return None
    game_dir = BIN64_DIR.sub("", linux_path)
    return Path(game_dir) / "Game.log"
