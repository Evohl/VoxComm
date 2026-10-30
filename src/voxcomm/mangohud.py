from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path


STATUS_LIFETIME_SECONDS = 12
MAX_STATUS_LENGTH = 140


def status_path() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "voxcomm" / "mangohud-status.json"


def publish_status(text: str, lifetime: int = STATUS_LIFETIME_SECONDS) -> None:
    cleaned = "\n".join(" ".join(line.split()) for line in str(text).splitlines())[:MAX_STATUS_LENGTH]
    try:
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"text": cleaned, "expires": time.time() + lifetime}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def current_status() -> str:
    try:
        value = json.loads(status_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return ""
        text = value.get("text", "")
        expires = value.get("expires")
        if (
            not isinstance(text, str)
            or isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or not math.isfinite(expires)
            or time.time() >= expires
        ):
            return ""
        text = text.translate(str.maketrans({
            "ä": "ae", "ö": "oe", "ü": "ue",
            "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
            "ß": "ss", "ẞ": "SS", "·": "|", "…": "...",
        }))
        return "\n".join(" ".join(line.split()) for line in text.splitlines())[:MAX_STATUS_LENGTH]
    except (OSError, ValueError, TypeError, OverflowError):
        return ""


def main() -> None:
    sys.stdout.write(current_status() + "\n")


if __name__ == "__main__":
    main()