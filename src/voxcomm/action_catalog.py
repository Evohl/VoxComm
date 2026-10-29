from __future__ import annotations

import re
import unicodedata


QUESTION_WORDS = {"wie", "warum", "was", "welche", "welcher", "welches", "erklaer", "erklaere", "erklaeren"}


def _normalize_phrase(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def parse_action_catalog(catalog: str) -> list[dict[str, object]]:
    actions = []
    names = set()
    buttons = set()
    states: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(catalog.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("=>")]
        if len(parts) != 4 or not all(parts):
            raise ValueError(f"line {line_number}: expected action name => keywords => ButtonN => reply")
        action_name = parts[0]
        normalized_name = _normalize_phrase(action_name)
        state_match = re.fullmatch(r"(.+)\s+(on|off)", action_name, flags=re.IGNORECASE)
        if not state_match:
            raise ValueError(f"line {line_number}: action name must end in On or Off")
        if normalized_name in names:
            raise ValueError(f"line {line_number}: duplicate action name")
        names.add(normalized_name)
        action_base = _normalize_phrase(state_match.group(1))
        action_state = state_match.group(2).casefold()
        states.setdefault(action_base, set()).add(action_state)

        keywords = [_normalize_phrase(item) for item in parts[1].split("|")]
        if any(not keyword for keyword in keywords):
            raise ValueError(f"line {line_number}: keywords cannot be empty")
        from voxcomm.gamepad import normalize_button

        _code, button = normalize_button(parts[2])
        if button in buttons:
            raise ValueError(f"line {line_number}: each action must have its own button")
        buttons.add(button)
        actions.append({
            "name": action_name,
            "keywords": keywords,
            "button": button,
            "reply": parts[3],
        })
    if any(action_states != {"on", "off"} for action_states in states.values()):
        raise ValueError("each action must have a matching On and Off entry")
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
    distinct = {action["button"] for action in matches}
    if len(distinct) != 1:
        raise ValueError("ambiguous action keywords map to different buttons")
    return matches[0]
