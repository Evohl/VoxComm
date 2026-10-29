from __future__ import annotations

import queue
import re
import threading

MIN_SENTENCE_CHARS = 24
# Ein Punkt hinter diesen Wörtern oder hinter einzelnen Zeichen/Zahlen beendet keinen Satz.
ABBREVIATIONS = frozenset({
    "bzw", "ca", "nr", "usw", "etc", "vgl", "evtl", "ggf", "inkl", "max", "min", "dr", "prof", "str", "bsp", "sog",
})
BOUNDARY = re.compile(r"(?P<mark>[.!?…]+[\"')\]»”]*)\s+(?=\S)|\n+\s*(?=\S)")
LAST_WORD = re.compile(r"(\w+)$")


def _is_abbreviation(text_before_period: str) -> bool:
    word = LAST_WORD.search(text_before_period)
    if word is None:
        return False
    token = word.group(1)
    return len(token) == 1 or token.isdigit() or token.casefold() in ABBREVIATIONS


class SentenceSplitter:
    """Zerlegt einen wachsenden Text in Sätze; zu kurze Sätze werden mit dem folgenden zusammengelegt."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[str]:
        sentences: list[str] = []
        start = 0
        for match in BOUNDARY.finditer(self._buffer):
            mark = match.group("mark")
            if mark == "." and _is_abbreviation(self._buffer[start:match.start("mark")]):
                continue
            candidate = self._buffer[start:match.end()].strip()
            if len(candidate) < MIN_SENTENCE_CHARS:
                continue
            sentences.append(candidate)
            start = match.end()
        self._buffer = self._buffer[start:]
        if final:
            rest = self._buffer.strip()
            self._buffer = ""
            if rest:
                sentences.append(rest)
        return sentences


def split_sentences(text: str) -> list[str]:
    splitter = SentenceSplitter()
    return splitter.feed(text) + splitter.flush()


class SentenceStream:
    """Nimmt LLM-Textteile entgegen und liefert fertige Sätze als Iterator an den Sprecher."""

    def __init__(self, cancel_event: threading.Event, on_first=None) -> None:
        self._splitter = SentenceSplitter()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._cancel_event = cancel_event
        self._on_first = on_first
        self.started = False

    def feed(self, delta: str) -> None:
        self._put(self._splitter.feed(delta))

    def finish(self) -> None:
        self._put(self._splitter.flush())
        self._queue.put(None)

    def _put(self, sentences: list[str]) -> None:
        for sentence in sentences:
            if not self.started:
                self.started = True
                if self._on_first is not None:
                    self._on_first()
            self._queue.put(sentence)

    def __iter__(self):
        while not self._cancel_event.is_set():
            try:
                sentence = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if sentence is None:
                return
            yield sentence
