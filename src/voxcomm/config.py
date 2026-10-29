from __future__ import annotations

import os
import json
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_GLOBAL_PROMPT = "Erfinde keine Fakten, Ausgaben, Quellen oder Systemzustände."


@dataclass
class AppConfig:
    stt_url: str = "tcp://127.0.0.1:10300"
    stt_provider: str = "wyoming"
    stt_language: str = "de"
    tts_url: str = "tcp://127.0.0.1:10200"
    tts_provider: str = "wyoming"
    tts_language: str = ""
    tts_voice: str = ""
    auto_speak: bool = False
    global_prompt: str = DEFAULT_GLOBAL_PROMPT
    voice_language: str = ""
    response_language: str = "Deutsch"
    special_prompts: dict[str, str] = field(default_factory=dict)
    special_hotkeys: dict[str, str] = field(default_factory=dict)
    enabled_tools: dict[str, list[str]] = field(default_factory=dict)
    input_action_catalogs: dict[str, str] = field(default_factory=dict)
    active_special_prompt: str = ""
    llm_provider: str = "ollama"
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = ""
    ollama_api_key: str = ""
    ptt_key: str = "F9"
    sample_rate: int = 16000
    tray_mode: bool = False
    overlay_x: int | None = None
    overlay_y: int | None = None


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
    if "special_prompts" not in raw and raw.get("additional_prompt", "").strip():
        raw["special_prompts"] = {"Spezialaufgabe": raw["additional_prompt"]}
        raw["active_special_prompt"] = "Spezialaufgabe"
    config = AppConfig(**{key: value for key, value in raw.items() if key in AppConfig.__dataclass_fields__})
    if migrate_legacy:
        save_config(config)
    return config


def save_config(config: AppConfig, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    values = asdict(config)
    prompts = values.pop("special_prompts")
    hotkeys = values.pop("special_hotkeys")
    enabled_tools = values.pop("enabled_tools")
    input_action_catalogs = values.pop("input_action_catalogs")
    rows = [f"{key} = {json.dumps(value, ensure_ascii=True)}" if isinstance(value, str)
            else f"{key} = {str(value).lower() if isinstance(value, bool) else value}"
            for key, value in values.items() if value is not None]
    for table, entries in (("special_prompts", prompts), ("special_hotkeys", hotkeys),
                           ("enabled_tools", enabled_tools), ("input_action_catalogs", input_action_catalogs)):
        if entries:
            rows.extend(["", f"[{table}]"])
            rows.extend(f"{json.dumps(name, ensure_ascii=True)} = {json.dumps(value, ensure_ascii=True)}"
                        for name, value in entries.items())
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
