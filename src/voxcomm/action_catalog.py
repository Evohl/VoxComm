from __future__ import annotations

import re
import unicodedata

from voxcomm.input_control import normalize_chord


QUESTION_WORDS = {"wie", "warum", "was", "welche", "welcher", "welches", "erklaer", "erklaere", "erklaeren"}


def _normalize_phrase(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def parse_action_catalog(catalog: str) -> list[dict[str, object]]:
    actions = []
    for line_number, raw_line in enumerate(catalog.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("=>")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"line {line_number}: expected keywords => keys => reply")
        keywords = [_normalize_phrase(item) for item in parts[0].split("|")]
        if any(not keyword for keyword in keywords):
            raise ValueError(f"line {line_number}: keywords cannot be empty")
        key_names = [key.strip() for key in parts[1].split("+")]
        normalized_names, _codes = normalize_chord(key_names)
        actions.append({"keywords": keywords, "keys": normalized_names, "reply": parts[2]})
    return actions


def match_action(catalog: str, prompt: str) -> dict[str, object] | None:
    normalized_prompt = _normalize_phrase(prompt)
    if not normalized_prompt:
        return None
    prompt_words = normalized_prompt.split()
    if "?" in prompt or any(word in QUESTION_WORDS for word in prompt_words[:3]):
        return None
    padded_prompt = f" {normalized_prompt} "
    candidates = []
    for action in parse_action_catalog(catalog):
        for keyword in action["keywords"]:
            if f" {keyword} " in padded_prompt:
                candidates.append((len(keyword.split()), action))
    if not candidates:
        return None
    longest = max(length for length, _action in candidates)
    matches = [action for length, action in candidates if length == longest]
    distinct = {tuple(action["keys"]) for action in matches}
    if len(distinct) != 1:
        raise ValueError("ambiguous action keywords map to different keys")
    return matches[0]
