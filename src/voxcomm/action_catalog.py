from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

from voxcomm.prompts import VOICE_LANGUAGE_NAMES
from voxcomm.skills.star_citizen.preconditions import REQUIREMENT_TEXT, UNKNOWN_STATE_TEXT, parse_precondition

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
NEGATION_WORDS = frozenset({"nicht", "nein", "kein", "keine", "keinen", "keinem", "keiner", "keines",
                            "not", "no", "never", "dont", "cannot", "cant", "wont"})
_SUFFIXES = ("ing", "ed", "en", "er", "em", "es", "e", "s", "n")
_SET_LINE = re.compile(r"\{([^{}]+)\}\s*:\s*(.+)")
_SET_REFERENCE = re.compile(r"\{([^{}]+)\}")
_REPLY = re.compile(r"\{([A-Za-z]{2})\}\s*(.+)")
_BUTTON = re.compile(r"Button\d+", re.IGNORECASE)
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


def parse_replies(field: str, line_number: int = 0) -> dict[str, str]:
    replies: dict[str, str] = {}
    parts = []
    part = []
    index = 0
    while index < len(field):
        character = field[index]
        if character == "\\" and index + 1 < len(field) and field[index + 1] in ("\\", "|"):
            part.append(field[index + 1])
            index += 2
            continue
        if character == "|":
            parts.append("".join(part))
            part = []
        else:
            part.append(character)
        index += 1
    parts.append("".join(part))
    for part in parts:
        match = _REPLY.fullmatch(part.strip())
        if match is None or not match.group(2).strip():
            raise ValueError(f"line {line_number}: reply must be {{language code}} text")
        tag = "CS" if match.group(1).upper() == "CZ" else match.group(1).upper()
        if tag.lower() not in VOICE_LANGUAGE_NAMES:
            raise ValueError(f"line {line_number}: unknown reply language {{{match.group(1)}}}")
        if tag in replies:
            raise ValueError(f"line {line_number}: duplicate {{{tag}}} reply")
        replies[tag] = match.group(2).strip()
    return replies


def format_replies(replies: dict[str, str]) -> str:
    return " | ".join(f"{{{tag}}} " + text.replace("\\", "\\\\").replace("|", "\\|")
                      for tag, text in replies.items())


def merge_catalog_reply_overrides(catalog: str, overrides: dict[tuple[str, str], str]) -> tuple[str, set[tuple[str, str]]]:
    try:
        parse_action_catalog(catalog)
    except ValueError:
        return catalog, set()
    merged = set()
    lines = []
    for line in catalog.splitlines(keepends=True):
        parts = line.rstrip("\r\n").split("=>")
        if len(parts) != 4:
            lines.append(line)
            continue
        action = parts[0].strip()
        replies = parse_replies(parts[3])
        normalized_overrides: dict[str, list[tuple[str, str]]] = {}
        for (name, tag), text in overrides.items():
            normalized_tag = reply_language_tag(tag)
            if name == action and normalized_tag:
                normalized_overrides.setdefault(normalized_tag, []).append((tag, text))
        for normalized_tag, entries in normalized_overrides.items():
            if len({text for _tag, text in entries}) != 1:
                continue
            text = entries[0][1]
            if text.strip() and not any(delimiter in text for delimiter in ("\n", "\r", "=>")):
                replies[normalized_tag] = text.strip()
                merged.update((action, tag) for tag, _text in entries)
        if any(name == action for name, _tag in merged):
            ending = line[len(line.rstrip("\r\n")):]
            lines.append("=>".join(parts[:3]) + "=> " + format_replies(replies) + ending)
        else:
            lines.append(line)
    return "".join(lines), merged


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
    if not line[1:-1].strip():
        return None
    return parse_precondition(_normalize_phrase(line[1:-1]).split(), line_number)


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
            "replies": parse_replies(parts[3], line_number),
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


def classify_utterance(prompt: str, keyword: str = "") -> str:
    normalized = _normalize_phrase(re.sub(r"(?i)\b(don|can|won)['’]t\b", r"\1t", prompt))
    words = normalized.split()
    if any(word in NEGATION_WORDS for word in words):
        return "negated"
    keyword_words = _normalize_phrase(keyword).split()
    while words:
        if keyword_words and words[:len(keyword_words)] == keyword_words:
            words = words[len(keyword_words):]
        elif words[0] in {"hey", "hi", "hallo", "hello", "bitte", "please"}:
            words = words[1:]
        else:
            break
    if "?" in prompt or (words and words[0] in QUESTION_WORDS):
        return "question"
    return "command"


def resolve_utterance(actions: list[dict[str, object]], prompt: str) -> Resolution:
    classification = classify_utterance(prompt)
    if classification != "command":
        return Resolution(classification)
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


_LANGUAGE_TAGS = {label.casefold(): code.upper() for code, label in VOICE_LANGUAGE_NAMES.items()} | {
    "de": "DE", "german": "DE", "deutsch": "DE",
    "en": "EN", "english": "EN", "englisch": "EN",
    "francais": "FR", "français": "FR", "französisch": "FR",
    "cz": "CS", "tschechisch": "CS", "spanisch": "ES",
}


def reply_language_tag(language: str) -> str:
    name = language.strip().casefold().replace("_", "-").split("-", 1)[0]
    return _LANGUAGE_TAGS.get(name, name.upper() if name in VOICE_LANGUAGE_NAMES else "")


def action_reply(action: dict[str, object], language: str) -> str:
    replies = action["replies"]
    return replies.get(reply_language_tag(language)) or next(iter(replies.values()))


def apply_reply_overrides(actions: list[dict[str, object]],
                          overrides: dict[tuple[str, str], str]) -> list[dict[str, object]]:
    """Merge custom replies keyed by action name and language code over the catalog replies."""
    merged = []
    for action in actions:
        custom = {tag: text for (name, tag), text in overrides.items() if name == action["name"]}
        merged.append({**action, "replies": {**action["replies"], **custom}} if custom else action)
    return merged


