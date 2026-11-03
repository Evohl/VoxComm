import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from voxcomm.config import (
    AppConfig, config_path, load_config, reply_override_key, reply_overrides_for_profile,
    save_config, save_overlay_position, split_reply_override_key,
)


class ConfigTests(unittest.TestCase):
    def test_reply_override_keys_round_trip_and_filter_by_profile(self):
        key = reply_override_key("Star Citizen", "Flight Systems On", "EN")

        self.assertEqual(split_reply_override_key(key), ("Star Citizen", "Flight Systems On", "EN"))
        self.assertEqual(
            reply_overrides_for_profile({key: "Ready.", reply_override_key("__global__", "A", "DE"): "x"},
                                        "Star Citizen"),
            {("Flight Systems On", "EN"): "Ready."},
        )

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = AppConfig(
                ui_language="de",
                stt_language="en",
                auto_speak=True,
                global_prompt="Custom global prompt",
                profile_models={"__global__": "qwen2.5", "Star Citizen": "qwen3:8b"},
                profile_voices={"__global__": "en_US-amy-medium", "Star Citizen": "de_DE-thorsten-high"},
                profile_thinking={"__global__": "low", "Star Citizen": "high"},
                profile_response_languages={"__global__": "English", "Star Citizen": "German"},
                profile_stt_languages={"__global__": "en", "Star Citizen": "auto"},
                profile_tts_languages={"__global__": "French", "Star Citizen": ""},
                profile_voice_languages={"__global__": "fr", "Star Citizen": ""},
                profile_auto_speak={"__global__": True, "Star Citizen": False},
                profile_output_languages={"__global__": "French", "Star Citizen": "German"},
                stt_api_key="stt-secret", tts_api_key="tts-secret",
                ptt_key="F10",
                tray_mode=True,
                game_announce_ship_entered=False,
                game_announce_pilot_left=False,
                game_announce_quantum_signature_unknown=False,
                overlay_x=-150,
                overlay_y=250,
                voice_language="de",
                special_prompts={"Star Citizen": "Flight context\nNever assume a ship is owned."},
                special_hotkeys={"Star Citizen": "f10"},
                enabled_tools={"__global__": ["web_search"], "Star Citizen": ["web_search", "input_control"]},
                input_action_catalogs={
                    "Star Citizen": "flight ready|systeme bereit => RIGHTALT + R => Flight ready",
                },
                input_reply_overrides={reply_override_key("Star Citizen", "Flight ready", "DE"): "Fahrwerk bereit."},
                controller_mode="script",
                controller_llm_keyword="assistent",
                active_special_prompt="Star Citizen",
            )
            save_config(original, path)
            self.assertEqual(load_config(path), original)

    def test_cloud_key_and_interfaces_survive_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = AppConfig(
                stt_provider="whisper.cpp",
                tts_provider="openai",
                tts_language="German",
                llm_provider="ollama-cloud",
                llm_url="https://ollama.com",
                ollama_api_key="secret",
            )

            save_config(original, path)

            self.assertEqual(load_config(path), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_keeps_previous_file_as_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save_config(AppConfig(special_prompts={"Star Citizen": "Flight context"}), path)
            previous = path.read_text(encoding="utf-8")

            save_config(AppConfig(), path)

            self.assertEqual(path.with_name("config.toml.bak").read_text(encoding="utf-8"), previous)

    def test_overlay_position_save_keeps_other_settings_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save_config(AppConfig(special_prompts={"Star Citizen": "Flight context"},
                                  input_action_catalogs={"Star Citizen": "ready => Button1"}), path)

            save_overlay_position(300, 40, path)

            stored = load_config(path)
            self.assertEqual((stored.overlay_x, stored.overlay_y), (300, 40))
            self.assertEqual(stored.special_prompts, {"Star Citizen": "Flight context"})
            self.assertEqual(stored.input_action_catalogs, {"Star Citizen": "ready => Button1"})

    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_config(Path(directory) / "missing.toml"), AppConfig())

    def test_overlay_hide_seconds_round_trip_and_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            self.assertEqual(AppConfig().overlay_hide_seconds, 10)

            save_config(AppConfig(overlay_hide_seconds=25), path)

            self.assertEqual(load_config(path).overlay_hide_seconds, 25)

    def test_legacy_additional_prompt_migrates_to_selected_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('additional_prompt = "Old specialty"\n', encoding="utf-8")

            config = load_config(path)

            self.assertEqual(config.special_prompts, {"Spezialaufgabe": "Old specialty"})
            self.assertEqual(config.active_special_prompt, "Spezialaufgabe")

    def test_config_uses_voxcomm_folder_and_copies_legacy_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "sc-ai-copilot" / "config.toml"
            legacy.parent.mkdir()
            legacy.write_text(
                'llm_model = "local-model"\n'
                'tts_voice = "en_US-amy-medium"\n'
                'llm_thinking = "low"\n'
                'response_language = "English"\n'
                '\n[llm_model_overrides]\n"Pilot" = "pilot-model"\n'
                '\n[tts_voice_overrides]\n"Pilot" = "de_DE-thorsten-high"\n'
                '\n[llm_thinking_overrides]\n"Pilot" = "high"\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}):
                config = load_config()

                self.assertEqual(config.profile_models, {"__global__": "local-model", "Pilot": "pilot-model"})
                self.assertEqual(config.profile_voices, {
                    "__global__": "en_US-amy-medium", "Pilot": "de_DE-thorsten-high"})
                self.assertEqual(config.profile_thinking, {"__global__": "low", "Pilot": "high"})
                self.assertEqual(config.profile_response_languages, {"__global__": "English"})
                self.assertEqual(config_path(), root / "voxcomm" / "config.toml")
                self.assertEqual(load_config(config_path()), config)
                self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
