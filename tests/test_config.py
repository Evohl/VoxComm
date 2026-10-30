import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from voxcomm.config import AppConfig, config_path, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = AppConfig(
                stt_language="en",
                auto_speak=True,
                global_prompt="Custom global prompt",
                llm_model="qwen2.5",
                ptt_key="F10",
                tray_mode=True,
                overlay_x=-150,
                overlay_y=250,
                voice_language="de",
                response_language="English",
                special_prompts={"Star Citizen": "Flight context\nNever assume a ship is owned."},
                special_hotkeys={"Star Citizen": "f10"},
                enabled_tools={"__global__": ["web_search"], "Star Citizen": ["web_search", "input_control"]},
                input_action_catalogs={
                    "Star Citizen": "flight ready|systeme bereit => RIGHTALT + R => Flight ready",
                },
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
            legacy.write_text('llm_model = "local-model"\n', encoding="utf-8")

            with patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}):
                config = load_config()

                self.assertEqual(config.llm_model, "local-model")
                self.assertEqual(config_path(), root / "voxcomm" / "config.toml")
                self.assertEqual(load_config(config_path()), config)
                self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
