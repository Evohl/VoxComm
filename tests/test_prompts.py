import unittest
from datetime import datetime, timezone

from voxcomm.prompts import build_system_prompt, response_language_for_voice


class PromptTests(unittest.TestCase):
    def test_piper_voice_selects_response_language(self):
        self.assertEqual(response_language_for_voice("de_DE-thorsten-medium"), "German")
        self.assertEqual(response_language_for_voice("en_US-amy-medium"), "English")

    def test_prompt_contains_only_global_rule_time_and_language(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        prompt = build_system_prompt(piper_voice="de_DE-thorsten-medium", current_time=now)

        self.assertTrue(prompt.startswith("Erfinde keine Fakten"))
        self.assertIn(f"Lokale aktuelle Zeit: {now.astimezone().isoformat(timespec='seconds')}", prompt)
        self.assertIn("Antworte immer in der Sprache German", prompt)
        self.assertIn("Erfinde keine Fakten", prompt)
        self.assertNotIn("Star Citizen", prompt)

    def test_special_profile_is_separate_and_optional(self):
        base = build_system_prompt(piper_voice="en_US-amy-medium")
        extended = build_system_prompt(
            piper_voice="en_US-amy-medium",
            special_prompt_name="Star Citizen",
            special_prompt="Star Citizen specific context",
        )

        self.assertNotIn("Spezialprofil", base)
        self.assertIn("Spezialprofil: Star Citizen\n\nStar Citizen specific context", extended)

    def test_custom_global_prompt_is_always_included(self):
        prompt = build_system_prompt(global_prompt="My global instructions")

        self.assertIn("My global instructions", prompt)
        self.assertNotIn("Copilot für Linux und den allgemeinen Desktop", prompt)

    def test_unknown_voice_uses_fallback_language(self):
        prompt = build_system_prompt(fallback_language="Deutsch")

        self.assertIn("Antworte immer in der Sprache Deutsch", prompt)


if __name__ == "__main__":
    unittest.main()