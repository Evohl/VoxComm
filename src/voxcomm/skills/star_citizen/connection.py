from __future__ import annotations

import re

from voxcomm.action_catalog import CLAUSE_SEPARATOR, classify_utterance
from voxcomm.config import DEFAULT_SHIP_CONNECT_KEYWORDS, DEFAULT_SHIP_DISCONNECT_KEYWORDS, DEFAULT_SHIP_TARGET_KEYWORDS


_CONNECTION_NOUNS = frozenset({"verbindung", "connection", "connetion"})


def _matched_keywords(text: str, keywords: str) -> set[str]:
    matches = set()
    for keyword in keywords.split(","):
        keyword = keyword.strip()
        if keyword and re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text, re.IGNORECASE):
            matches.add(keyword.casefold())
    return matches


def ship_connection_command(text: str, connect_keywords: str = DEFAULT_SHIP_CONNECT_KEYWORDS,
                            disconnect_keywords: str = DEFAULT_SHIP_DISCONNECT_KEYWORDS,
                            ship_keywords: str = DEFAULT_SHIP_TARGET_KEYWORDS) -> bool | None:
    if classify_utterance(text) != "command" or CLAUSE_SEPARATOR.search(text):
        return None
    if not _matched_keywords(text, ship_keywords):
        return None
    connect = _matched_keywords(text, connect_keywords)
    disconnect = _matched_keywords(text, disconnect_keywords)
    if connect & disconnect:
        return None
    if disconnect:
        return False if not (connect - _CONNECTION_NOUNS) else None
    return True if connect else None