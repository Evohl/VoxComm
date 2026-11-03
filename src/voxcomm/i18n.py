from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files


SUPPORTED_LANGUAGES = {"en": "English", "de": "Deutsch"}
_language = "en"
_catalog: dict[str, str] = {}


@lru_cache(maxsize=None)
def _load_catalog(language: str) -> dict[str, str]:
    catalog_path = files("voxcomm").joinpath("locales", f"{language}.json")
    try:
        return json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_language(language: str) -> None:
    global _language, _catalog
    _language = language if language in SUPPORTED_LANGUAGES else "en"
    _catalog = _load_catalog(_language)


def current_language() -> str:
    return _language


def tr(message_id: str, **values: object) -> str:
    message = _catalog.get(message_id, message_id)
    return message.format(**values) if values else message


def tr_for_language(message_id: str, language: str, **values: object) -> str:
    language = language.lower() if language.lower() in SUPPORTED_LANGUAGES else "en"
    message = _load_catalog(language).get(message_id, message_id)
    return message.format(**values) if values else message


set_language("en")