from __future__ import annotations

import json
from importlib.resources import files


SUPPORTED_LANGUAGES = {"en": "English", "de": "Deutsch"}
_language = "en"
_catalog: dict[str, str] = {}


def set_language(language: str) -> None:
    global _language, _catalog
    _language = language if language in SUPPORTED_LANGUAGES else "en"
    catalog_path = files("voxcomm").joinpath("locales", f"{_language}.json")
    try:
        _catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _catalog = {}


def current_language() -> str:
    return _language


def tr(message_id: str, **values: object) -> str:
    message = _catalog.get(message_id, message_id)
    return message.format(**values) if values else message


set_language("en")