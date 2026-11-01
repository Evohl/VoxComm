from __future__ import annotations

import re
from pathlib import Path

GAME_EXECUTABLE = re.compile(r"(?:^|[\\/])Bin64[\\/]StarCitizen\.exe$", re.IGNORECASE)


def find_game_executable(proc_root: Path = Path("/proc")) -> str | None:
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        for argument in raw.split(b"\0"):
            text = argument.decode("utf-8", "replace")
            if GAME_EXECUTABLE.search(text):
                return text
    return None
