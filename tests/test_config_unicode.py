import json
import tempfile
import tomllib
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from voxcomm.config import AppConfig, load_config, save_config


class ConfigUnicodeTests(unittest.TestCase):
    def test_all_strings_and_tables_round_trip(self):
        text = 'ASCII "quoted" \\ slash\n\r\t\b\f ' + ''.join(chr(code) for code in range(32))
        text += '\x7f\u00e4\U0001f680\\u1234\\ud83d\\ude80'
        profile = 'Profile "\\\n\x00\x7f\u00e4\U0001f680'
        config = AppConfig()
        for item in fields(config):
            value = getattr(config, item.name)
            if isinstance(value, str):
                setattr(config, item.name, text)
            elif isinstance(value, dict):
                setattr(config, item.name, {profile: [text, profile] if item.name == "enabled_tools" else text})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save_config(config, path)
            serialized = path.read_text(encoding="utf-8")

            self.assertEqual(load_config(path), config)
            self.assertEqual(tomllib.loads(serialized)["enabled_tools"][profile], [text, profile])
            self.assertIn(r'\U0001f680', serialized)
            self.assertIn(r'\u00e4', serialized)
            self.assertIn(r'\u007f', serialized)
            self.assertNotIn(r'\ud83d\ude80', serialized)

    def test_ascii_escaping_is_unchanged(self):
        text = 'quotes " backslash \\ newline\n tab\t formfeed\f null\x00'
        config = AppConfig(global_prompt=text, special_prompts={text: text}, enabled_tools={text: [text]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save_config(config, path)
            serialized = path.read_text(encoding="utf-8")

            self.assertIn(f"global_prompt = {json.dumps(text)}\n", serialized)
            self.assertIn(f"{json.dumps(text)} = {json.dumps(text)}\n", serialized)
            self.assertIn(f"{json.dumps(text)} = {json.dumps([text])}\n", serialized)
            self.assertEqual(load_config(path), config)

    def test_atomic_replacement_and_backup_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = AppConfig(global_prompt="Before \U0001f680")
            updated = AppConfig(global_prompt="After \U0001f680")
            save_config(original, path)
            replace = Path.replace

            def check_replacement(temporary, target):
                self.assertEqual(load_config(target), original)
                self.assertEqual(temporary.stat().st_mode & 0o777, 0o600)
                self.assertEqual(load_config(temporary), updated)
                return replace(temporary, target)

            with patch.object(Path, "replace", autospec=True, side_effect=check_replacement):
                save_config(updated, path)

            backup = path.with_name(path.name + ".bak")
            self.assertEqual(load_config(path), updated)
            self.assertEqual(load_config(backup), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_lone_surrogate_does_not_replace_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = AppConfig(global_prompt="Valid \U0001f680")
            save_config(original, path)

            with self.assertRaises(ValueError):
                save_config(AppConfig(global_prompt="Invalid \ud800"), path)

            self.assertEqual(load_config(path), original)
            self.assertFalse(path.with_name(path.name + ".bak").exists())


if __name__ == "__main__":
    unittest.main()