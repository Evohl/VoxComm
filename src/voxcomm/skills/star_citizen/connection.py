from __future__ import annotations

import re

from voxcomm.action_catalog import classify_utterance


_COMMAND = re.compile(
    r"^(?:(?:hey|hello|hallo|please|bitte|captain)\s+)*"
    r"(?P<verb>connect|connecte|verbinde|verbinden|disconnect|disconnecte|trenne|trennen)\s+"
    r"(?:(?:me|mich|to|with|from|the|my|mit|von|vom|das|dem|mein|meinem|meines|meine)\s+)*"
    r"(?:ship|schiff|raumschiff)(?:\s+(?:please|bitte))?[.!]*$",
    re.IGNORECASE,
)


def ship_connection_command(text: str) -> bool | None:
    if classify_utterance(text) != "command":
        return None
    match = _COMMAND.fullmatch(text.strip())
    if match is None:
        return None
    return match.group("verb").casefold() not in {"disconnect", "disconnecte", "trenne", "trennen"}