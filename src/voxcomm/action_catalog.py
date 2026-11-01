from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

EXACT_SCORE = 1.0
FUZZY_THRESHOLD = 0.8
FUZZY_CAP = 0.99
FUZZY_MIN_LENGTH = 5
AMBIGUITY_MARGIN = 0.1
CLAUSE_SEPARATOR = re.compile(r"\b(?:und|dann|danach|sowie|and|then)\b|[,;]", re.IGNORECASE)
QUESTION_WORDS = frozenset({
    "wer", "wie", "was", "wann", "wo", "warum", "weshalb", "wieso", "welche", "welcher", "welches", "welchen",
    "kann", "kannst", "koennen", "koennten", "ist", "sind", "gibt", "soll", "sollte",
    "who", "what", "when", "where", "why", "how", "which", "can", "could", "is", "are", "do", "does", "will",
    "would", "should",
})
_SUFFIXES = ("ing", "ed", "en", "er", "em", "es", "e", "s", "n")
_SET_LINE = re.compile(r"\{([^{}]+)\}\s*:\s*(.+)")
_SET_REFERENCE = re.compile(r"\{([^{}]+)\}")
_REPLY = re.compile(r"\{(DE|EN)\}\s*(.+)")
_BUTTON = re.compile(r"Button\d+", re.IGNORECASE)
_PRECONDITION_FIELDS = {
    "pilot seat": "pilot_seat",
    "pilotensitz": "pilot_seat",
    "in ship": "in_ship",
    "im schiff": "in_ship",
}
_PRECONDITION_VALUES = {"yes": True, "ja": True, "no": False, "nein": False}
REQUIREMENT_TEXT = {
    ("pilot_seat", True): "Captain, ship controls are unavailable until you take the pilot seat.",
    ("pilot_seat", False): "Captain, leave the pilot seat before requesting this action.",
    ("in_ship", True): "Captain, board the ship before requesting this shipboard action.",
    ("in_ship", False): "Captain, leave the ship before requesting this action.",
}
UNKNOWN_STATE_TEXT = {
    "pilot_seat": "Captain, pilot-seat status is unknown. I am waiting for Star Citizen telemetry before enabling ship controls.",
    "in_ship": "Captain, ship status is unknown. I am waiting for Star Citizen telemetry before processing this shipboard action.",
}
_FILLER_TEXT = "bitte mal doch jetzt kurz danke hey ok okay please mich mir fuer"


@dataclass(frozen=True)
class Resolution:
    status: str  # "match", "none", "ambiguous" or "question"
    actions: tuple[dict[str, object], ...] = ()


def _normalize_phrase(value: str) -> str:
    folded = value.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _stem_tuple(phrase: str) -> tuple[str, ...]:
    return tuple(_stem(word) for word in phrase.split())


_FILLER = frozenset(stem for word in _FILLER_TEXT.split() for stem in _stem_tuple(word))


def _split_items(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,|]", text)]


def _parse_button(field: str, line_number: int) -> tuple[str, bool]:
    from voxcomm.gamepad import normalize_button

    tokens = field.split()
    hold = len(tokens) == 2 and tokens[1].casefold() == "hold"
    if len(tokens) != 1 and not hold or not _BUTTON.fullmatch(tokens[0]):
        raise ValueError(f"line {line_number}: button must be ButtonN or ButtonN hold")
    _code, button = normalize_button(tokens[0])
    return button, hold


def _parse_replies(field: str, line_number: int) -> dict[str, str]:
    replies: dict[str, str] = {}
    for part in field.split("|"):
        match = _REPLY.fullmatch(part.strip())
        if match is None:
            raise ValueError(f"line {line_number}: reply must be {{DE}} text or {{EN}} text")
        tag = match.group(1)
        if tag in replies:
            raise ValueError(f"line {line_number}: duplicate {{{tag}}} reply")
        replies[tag] = match.group(2).strip()
    return replies


def _parse_set(line: str, line_number: int, sets: dict[str, tuple[str, ...]]) -> None:
    match = _SET_LINE.fullmatch(line)
    if match is None:
        raise ValueError(f"line {line_number}: expected {{set}}: keyword, keyword")
    name = _normalize_phrase(match.group(1))
    if not name or name in sets:
        raise ValueError(f"line {line_number}: set name is empty or duplicate")
    phrases = []
    for item in _split_items(match.group(2)):
        phrase = _normalize_phrase(item)
        if not phrase:
            raise ValueError(f"line {line_number}: keywords cannot be empty")
        phrases.append(phrase)
    sets[name] = tuple(phrases)


def _parse_groups(field: str, line_number: int, sets: dict[str, tuple[str, ...]]) -> tuple[tuple, tuple]:
    """Return stemmed groups for matching and plain phrase groups for display."""
    groups = []
    display = []
    for group_text in field.split("+"):
        phrases: list[str] = []
        for item in _split_items(group_text):
            reference = _SET_REFERENCE.fullmatch(item)
            if reference is not None:
                name = _normalize_phrase(reference.group(1))
                if name not in sets:
                    raise ValueError(f"line {line_number}: unknown set {{{reference.group(1)}}}")
                phrases.extend(sets[name])
                continue
            phrase = _normalize_phrase(item)
            if not phrase:
                raise ValueError(f"line {line_number}: keywords cannot be empty")
            phrases.append(phrase)
        unique = tuple(dict.fromkeys(phrases))
        display.append(unique)
        groups.append(tuple(dict.fromkeys(_stem_tuple(phrase) for phrase in unique)))
    return tuple(groups), tuple(display)


def _parse_precondition(line: str, line_number: int) -> tuple[str, bool] | None:
    """Parse a [field value] directive; [] clears the precondition for the following actions."""
    inner = line[1:-1]
    if not inner.strip():
        return None
    words = _normalize_phrase(inner).split()
    field = _PRECONDITION_FIELDS.get(" ".join(words[:-1]))
    value = _PRECONDITION_VALUES.get(words[-1]) if words else None
    if field is None or value is None:
        raise ValueError(f"line {line_number}: precondition must be [pilot seat yes], [pilot seat no], [in ship yes] or []")
    return field, value


def unmet_requirement(action: dict[str, object], state: dict[str, bool | None]) -> tuple[str, bool] | None:
    """Return an unmet precondition, including when its game state is unknown."""
    requirement = action.get("requires")
    if requirement is None:
        return None
    field, wanted = requirement
    current = state.get(field)
    if current == wanted:
        return None
    return requirement


def parse_action_catalog(catalog: str) -> list[dict[str, object]]:
    sets: dict[str, tuple[str, ...]] = {}
    rows: list[tuple[int, str, tuple[str, bool] | None]] = []
    requirement: tuple[str, bool] | None = None
    for line_number, raw_line in enumerate(catalog.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            _parse_set(line, line_number, sets)
        elif line.startswith("["):
            if not line.endswith("]"):
                raise ValueError(f"line {line_number}: precondition must end with ]")
            requirement = _parse_precondition(line, line_number)
        else:
            rows.append((line_number, line, requirement))

    actions = []
    names: set[str] = set()
    buttons: set[str] = set()
    for line_number, line, requirement in rows:
        parts = [part.strip() for part in line.split("=>")]
        if len(parts) != 4 or not all(parts):
            raise ValueError(f"line {line_number}: expected name => keyword groups => ButtonN [hold] => {{DE}} reply | {{EN}} reply")
        name = _normalize_phrase(parts[0])
        if not name or name in names:
            raise ValueError(f"line {line_number}: action name is empty or duplicate")
        names.add(name)
        button, hold = _parse_button(parts[2], line_number)
        if button in buttons:
            raise ValueError(f"line {line_number}: each action must have its own button")
        buttons.add(button)
        groups, keywords = _parse_groups(parts[1], line_number, sets)
        actions.append({
            "name": parts[0],
            "groups": groups,
            "keywords": keywords,
            "button": button,
            "hold": hold,
            "requires": requirement,
            "replies": _parse_replies(parts[3], line_number),
        })
    return actions


def _window_score(window: tuple[str, ...], phrase: tuple[str, ...]) -> float:
    ratios = []
    for word, expected in zip(window, phrase):
        if word == expected:
            ratios.append(EXACT_SCORE)
        elif len(expected) < FUZZY_MIN_LENGTH:
            return 0.0
        else:
            ratios.append(difflib.SequenceMatcher(None, word, expected).ratio())
    score = min(ratios)
    return min(score, FUZZY_CAP) if score >= FUZZY_THRESHOLD else 0.0


def _phrase_score(words: tuple[str, ...], phrase: tuple[str, ...]) -> float:
    size = len(phrase)
    best = 0.0
    for start in range(len(words) - size + 1):
        window = words[start:start + size]
        if window == phrase:
            return EXACT_SCORE
        best = max(best, _window_score(window, phrase))
    if size == 1 and len(phrase[0]) >= FUZZY_MIN_LENGTH:
        for left, right in zip(words, words[1:]):
            best = max(best, _window_score((left + right,), phrase))
    return best


def _action_score(action: dict[str, object], words: tuple[str, ...]) -> float:
    score = EXACT_SCORE
    for group in action["groups"]:
        group_score = max((_phrase_score(words, phrase) for phrase in group), default=0.0)
        if group_score == 0.0:
            return 0.0
        score = min(score, group_score)
    return score


def _resolve_clause(actions: list[dict[str, object]],
                    words: tuple[str, ...]) -> tuple[str, dict[str, object] | None]:
    scored = []
    for action in actions:
        score = _action_score(action, words)
        if score > 0.0:
            scored.append((score, action))
    if not scored:
        return "none", None
    exact = [item for item in scored if item[0] >= EXACT_SCORE]
    pool = sorted(exact or scored, key=lambda item: item[0], reverse=True)
    top = pool[0][0]
    if sum(1 for score, _action in pool if score >= top - AMBIGUITY_MARGIN) > 1:
        return "ambiguous", None
    return "match", pool[0][1]


def _is_question(prompt: str) -> bool:
    if "?" in prompt:
        return True
    first_words = _normalize_phrase(prompt).split()[:1]
    return bool(first_words) and first_words[0] in QUESTION_WORDS


def resolve_utterance(actions: list[dict[str, object]], prompt: str) -> Resolution:
    if _is_question(prompt):
        return Resolution("question")
    clauses = []
    for part in CLAUSE_SEPARATOR.split(prompt):
        words = tuple(word for word in _stem_tuple(_normalize_phrase(part)) if word not in _FILLER)
        if words:
            clauses.append(words)
    if not clauses:
        return Resolution("none")
    outcomes = [_resolve_clause(actions, words) for words in clauses]
    if all(status == "none" for status, _action in outcomes):
        return Resolution("none")
    if any(status != "match" for status, _action in outcomes):
        return Resolution("ambiguous")
    unique: list[dict[str, object]] = []
    for _status, action in outcomes:
        if action is not None and action not in unique:
            unique.append(action)
    return Resolution("match", tuple(unique))


def contains_keyword(prompt: str, keyword: str) -> bool:
    """True when the stemmed keyword phrase appears as whole words in the prompt."""
    phrase = _stem_tuple(_normalize_phrase(keyword))
    if not phrase:
        return False
    words = _stem_tuple(_normalize_phrase(prompt))
    size = len(phrase)
    return any(words[start:start + size] == phrase for start in range(len(words) - size + 1))


_LANGUAGE_TAGS = {
    "de": "DE", "german": "DE", "deutsch": "DE",
    "en": "EN", "english": "EN", "englisch": "EN",
}


def reply_language_tag(language: str) -> str:
    name = language.strip().casefold().replace("_", "-").split("-", 1)[0]
    return _LANGUAGE_TAGS.get(name, "")


def action_reply(action: dict[str, object], language: str) -> str:
    replies = action["replies"]
    return replies.get(reply_language_tag(language)) or next(iter(replies.values()))


def apply_reply_overrides(actions: list[dict[str, object]],
                          overrides: dict[tuple[str, str], str]) -> list[dict[str, object]]:
    """Merge custom replies keyed by (action name, DE|EN) over the catalog replies."""
    merged = []
    for action in actions:
        custom = {tag: text for (name, tag), text in overrides.items() if name == action["name"]}
        merged.append({**action, "replies": {**action["replies"], **custom}} if custom else action)
    return merged


