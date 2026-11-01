from __future__ import annotations

import os
import json
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_GLOBAL_PROMPT = "Erfinde keine Fakten, Ausgaben, Quellen oder Systemzustände."


@dataclass
class AppConfig:
    ui_language: str = "en"
    stt_url: str = "tcp://127.0.0.1:10300"
    stt_provider: str = "wyoming"
    stt_api_key: str = ""
    stt_language: str = "de"
    tts_url: str = "tcp://127.0.0.1:10200"
    tts_provider: str = "wyoming"
    tts_api_key: str = ""
    tts_language: str = ""
    auto_speak: bool = False
    global_prompt: str = DEFAULT_GLOBAL_PROMPT
    voice_language: str = ""
    special_prompts: dict[str, str] = field(default_factory=dict)
    profile_models: dict[str, str] = field(default_factory=dict)
    profile_voices: dict[str, str] = field(default_factory=dict)
    profile_thinking: dict[str, str] = field(default_factory=dict)
    profile_response_languages: dict[str, str] = field(default_factory=dict)
    profile_stt_languages: dict[str, str] = field(default_factory=dict)
    profile_tts_languages: dict[str, str] = field(default_factory=dict)
    profile_voice_languages: dict[str, str] = field(default_factory=dict)
    profile_auto_speak: dict[str, bool] = field(default_factory=dict)
    profile_output_languages: dict[str, str] = field(default_factory=dict)
    special_hotkeys: dict[str, str] = field(default_factory=dict)
    enabled_tools: dict[str, list[str]] = field(default_factory=dict)
    input_action_catalogs: dict[str, str] = field(default_factory=dict)
    input_reply_overrides: dict[str, str] = field(default_factory=dict)
    controller_mode: str = "hybrid"
    controller_llm_keyword: str = "assistent"
    active_special_prompt: str = ""
    llm_provider: str = "ollama"
    llm_url: str = "http://127.0.0.1:11434"
    ollama_api_key: str = ""
    ptt_key: str = "F9"
    sample_rate: int = 16000
    tray_mode: bool = False
    game_announce_ship_entered: bool = True
    game_announce_ship_left: bool = True
    game_announce_pilot_taken: bool = True
    game_announce_pilot_left: bool = True
    game_announce_nav_target_set: bool = True
    game_announce_quantum_arrived: bool = True
    overlay_hide_seconds: int = 10
    overlay_x: int | None = None
    overlay_y: int | None = None


def reply_override_key(profile: str, action: str, tag: str) -> str:
    return f"{profile}\t{action}\t{tag}"


def split_reply_override_key(key: str) -> tuple[str, str, str]:
    profile, rest = key.split("\t", 1)
    action, tag = rest.rsplit("\t", 1)
    return profile, action, tag


def reply_overrides_for_profile(overrides: dict[str, str], profile: str) -> dict[tuple[str, str], str]:
    result = {}
    for key, text in overrides.items():
        owner, action, tag = split_reply_override_key(key)
        if owner == profile:
            result[(action, tag)] = text
    return result


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "voxcomm" / "config.toml"


def _legacy_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sc-ai-copilot" / "config.toml"


def load_config(path: Path | None = None) -> AppConfig:
    target = path or config_path()
    migrate_legacy = False
    if path is None and not target.exists():
        legacy = _legacy_config_path()
        if legacy.exists():
            target = legacy
            migrate_legacy = True
    if not target.exists():
        return AppConfig()
    with target.open("rb") as config_file:
        raw = tomllib.load(config_file)
    for settings_name, legacy_field, legacy_table in (
        ("profile_models", "llm_model", "llm_model_overrides"),
        ("profile_voices", "tts_voice", "tts_voice_overrides"),
        ("profile_thinking", "llm_thinking", "llm_thinking_overrides"),
    ):
        settings = dict(raw.get(settings_name, {}))
        legacy_value = raw.pop(legacy_field, "")
        legacy_profiles = raw.pop(legacy_table, {})
        for profile, value in legacy_profiles.items():
            settings.setdefault(profile, value)
        if legacy_value:
            settings.setdefault("__global__", legacy_value)
        if settings:
            raw[settings_name] = settings
    response_languages = dict(raw.get("profile_response_languages", {}))
    legacy_response_language = raw.pop("response_language", "")
    if legacy_response_language:
        response_languages.setdefault("__global__", legacy_response_language)
    if response_languages:
        raw["profile_response_languages"] = response_languages
    if "special_prompts" not in raw and raw.get("additional_prompt", "").strip():
        raw["special_prompts"] = {"Spezialaufgabe": raw["additional_prompt"]}
        raw["active_special_prompt"] = "Spezialaufgabe"
    config = AppConfig(**{key: value for key, value in raw.items() if key in AppConfig.__dataclass_fields__})
    if migrate_legacy:
        save_config(config)
    return config


def _toml_string(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    result = []
    for character in encoded:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("TOML strings cannot contain Unicode surrogates")
        if codepoint > 0xFFFF:
            result.append(f"\\U{codepoint:08x}")
        elif codepoint >= 0x7F:
            result.append(f"\\u{codepoint:04x}")
        else:
            result.append(character)
    return "".join(result)


def _toml_table_value(value: str | list[str] | bool) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return str(value).lower()
    return "[" + ", ".join(_toml_string(item) for item in value) + "]"


def save_config(config: AppConfig, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    values = asdict(config)
    prompts = values.pop("special_prompts")
    hotkeys = values.pop("special_hotkeys")
    enabled_tools = values.pop("enabled_tools")
    input_action_catalogs = values.pop("input_action_catalogs")
    reply_overrides = values.pop("input_reply_overrides")
    profile_tables = tuple((name, values.pop(name)) for name in (
        "profile_models", "profile_voices", "profile_thinking", "profile_response_languages",
        "profile_stt_languages", "profile_tts_languages", "profile_voice_languages", "profile_auto_speak",
        "profile_output_languages"))
    rows = [f"{key} = {_toml_string(value)}" if isinstance(value, str)
            else f"{key} = {str(value).lower() if isinstance(value, bool) else value}"
            for key, value in values.items() if value is not None]
    for table, entries in (("special_prompts", prompts), ("special_hotkeys", hotkeys),
                           ("enabled_tools", enabled_tools), ("input_action_catalogs", input_action_catalogs),
                           ("input_reply_overrides", reply_overrides), *profile_tables):
        if entries:
            rows.extend(["", f"[{table}]"])
            rows.extend(f"{_toml_string(name)} = {_toml_table_value(value)}"
                        for name, value in entries.items())
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        shutil.copyfile(target, backup)
        os.chmod(backup, 0o600)
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)


def save_overlay_position(x: int, y: int, path: Path | None = None) -> None:
    stored = load_config(path)
    stored.overlay_x = x
    stored.overlay_y = y
    save_config(stored, path)
