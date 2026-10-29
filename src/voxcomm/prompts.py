from __future__ import annotations

from datetime import datetime

from voxcomm.config import DEFAULT_GLOBAL_PROMPT


VOICE_LANGUAGE_NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese", "ka": "Georgian",
    "kk": "Kazakh", "ko": "Korean", "ku": "Kurdish", "lb": "Luxembourgish", "lv": "Latvian",
    "ml": "Malayalam", "mr": "Marathi", "ne": "Nepali", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak",
    "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian", "sv": "Swedish", "sw": "Swahili",
    "te": "Telugu", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh": "Chinese",
}


def response_language_for_voice(voice_name: str, fallback: str = "Deutsch") -> str:
    if not voice_name:
        return fallback.strip() or "Deutsch"
    language_code = voice_name.split("-", 1)[0].split("_", 1)[0].lower()
    return VOICE_LANGUAGE_NAMES.get(language_code, language_code.upper())


def build_system_prompt(
    piper_voice: str = "",
    special_prompt_name: str = "",
    special_prompt: str = "",
    fallback_language: str = "Deutsch",
    current_time: datetime | None = None,
    global_prompt: str = DEFAULT_GLOBAL_PROMPT,
) -> str:
    now = current_time or datetime.now().astimezone()
    timestamp = now.astimezone().isoformat(timespec="seconds") if now.tzinfo else now.isoformat(timespec="seconds")
    response_language = response_language_for_voice(piper_voice, fallback_language)
    sections = [
        global_prompt.strip(),
        f"Lokale aktuelle Zeit: {timestamp}. Dieser Zeitstempel ist maßgeblich; leite relative Zeitangaben wie gestern, heute und morgen daraus ab.",
        f"Antworte immer in der Sprache {response_language}; diese Sprache ist aus der ausgewählten Piper-Stimme abgeleitet.",
    ]
    extra = special_prompt.strip()
    if extra:
        heading = f"Spezialprofil: {special_prompt_name.strip()}" if special_prompt_name.strip() else "Spezialprofil"
        sections.extend([heading, extra])
    return "\n\n".join(sections)