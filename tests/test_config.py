import tempfile
import unittest
from pathlib import Path

from voxcomm.config import AppConfig, load_config, save_config


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

    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_config(Path(directory) / "missing.toml"), AppConfig())

    def test_legacy_additional_prompt_migrates_to_selected_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('additional_prompt = "Old specialty"\n', encoding="utf-8")

            config = load_config(path)

            self.assertEqual(config.special_prompts, {"Spezialaufgabe": "Old specialty"})
            self.assertEqual(config.active_special_prompt, "Spezialaufgabe")


if __name__ == "__main__":
    unittest.main()
