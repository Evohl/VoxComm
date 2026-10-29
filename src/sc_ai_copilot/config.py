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
    stt_language: str = "de"
    tts_url: str = "tcp://127.0.0.1:10200"
    tts_voice: str = ""
    auto_speak: bool = False
    global_prompt: str = DEFAULT_GLOBAL_PROMPT
    voice_language: str = ""
    response_language: str = "Deutsch"
    special_prompts: dict[str, str] = field(default_factory=dict)
    special_hotkeys: dict[str, str] = field(default_factory=dict)
    active_special_prompt: str = ""
    llm_provider: str = "ollama"
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = ""
    ptt_key: str = "F9"
    joystick_device: int = -1
    joystick_button: int = 0
    sample_rate: int = 16000
    tray_mode: bool = False
    overlay_x: int | None = None
    overlay_y: int | None = None


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sc-ai-copilot" / "config.toml"


def load_config(path: Path | None = None) -> AppConfig:
    target = path or config_path()
    if not target.exists():
        return AppConfig()
    with target.open("rb") as config_file:
        raw = tomllib.load(config_file)
    if "special_prompts" not in raw and raw.get("additional_prompt", "").strip():
        raw["special_prompts"] = {"Spezialaufgabe": raw["additional_prompt"]}
        raw["active_special_prompt"] = "Spezialaufgabe"
    return AppConfig(**{key: value for key, value in raw.items() if key in AppConfig.__dataclass_fields__})


def save_config(config: AppConfig, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    values = asdict(config)
    prompts = values.pop("special_prompts")
    hotkeys = values.pop("special_hotkeys")
    rows = [f"{key} = {json.dumps(value, ensure_ascii=True)}" if isinstance(value, str)
            else f"{key} = {str(value).lower() if isinstance(value, bool) else value}"
            for key, value in values.items() if value is not None]
    for table, entries in (("special_prompts", prompts), ("special_hotkeys", hotkeys)):
        if entries:
            rows.extend(["", f"[{table}]"])
            rows.extend(f"{json.dumps(name, ensure_ascii=True)} = {json.dumps(value, ensure_ascii=True)}"
                        for name, value in entries.items())
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
