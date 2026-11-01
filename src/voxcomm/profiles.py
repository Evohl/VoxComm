from dataclasses import dataclass

from voxcomm.config import AppConfig
from voxcomm.prompts import VOICE_LANGUAGE_NAMES, response_language_for_voice


def output_language(config: AppConfig, key: str, voice: str) -> str:
    if key in config.profile_output_languages:
        return config.profile_output_languages[key]
    selected = config.profile_response_languages.get(key, "")
    if selected:
        return selected
    selected = config.profile_tts_languages.get(key, config.tts_language)
    if selected:
        return selected
    voice_code = voice.split("-", 1)[0].split("_", 1)[0].lower()
    if voice_code in VOICE_LANGUAGE_NAMES:
        return VOICE_LANGUAGE_NAMES[voice_code]
    filter_code = config.profile_voice_languages.get(key, config.voice_language)
    return VOICE_LANGUAGE_NAMES.get(filter_code, response_language_for_voice(voice, "English"))


@dataclass(frozen=True)
class ResolvedProfile:
    key: str
    name: str
    global_prompt: str
    special_prompt: str
    model: str
    voice: str
    thinking: str
    response_language: str
    enabled_tools: tuple[str, ...]
    action_catalog: str
    stt_language: str = "de"
    tts_language: str = ""
    voice_language: str = ""
    auto_speak: bool = False


def resolve_profile(config: AppConfig, requested_profile: str = "") -> ResolvedProfile:
    name = requested_profile if requested_profile in config.special_prompts else ""
    key = name or "__global__"
    return ResolvedProfile(
        key=key,
        name=name,
        global_prompt=config.global_prompt,
        special_prompt=config.special_prompts.get(name, ""),
        model=config.profile_models.get(key, ""),
        voice=config.profile_voices.get(key, ""),
        thinking=config.profile_thinking.get(key, ""),
        response_language=config.profile_response_languages.get(key, ""),
        enabled_tools=tuple(config.enabled_tools.get(key, [])),
        action_catalog=config.input_action_catalogs.get(key, ""),
        stt_language=config.profile_stt_languages.get(key, config.stt_language),
        tts_language=config.profile_tts_languages.get(key, config.tts_language),
        voice_language=config.profile_voice_languages.get(key, config.voice_language),
        auto_speak=config.profile_auto_speak.get(key, config.auto_speak),
    )