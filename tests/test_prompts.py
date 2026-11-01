import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voxcomm.prompts import build_system_prompt, load_persona, response_language_for_voice


TEST_PERSONA = (
    "Du bist VoxComm, die persönliche KI des Nutzers auf seinem Mobiglas. "
    "Du bist weder ein Schiffscomputer noch eine Schiffs-KI. Sprich nur Captain oder Commander an."
)


class PromptTests(unittest.TestCase):
    def setUp(self):
        self._persona_patch = patch("voxcomm.prompts.load_persona", return_value=TEST_PERSONA)
        self._persona_patch.start()

    def tearDown(self):
        self._persona_patch.stop()

    def test_piper_voice_selects_response_language(self):
        self.assertEqual(response_language_for_voice("de_DE-thorsten-medium"), "German")
        self.assertEqual(response_language_for_voice("en_US-amy-medium"), "English")
        self.assertEqual(response_language_for_voice("data", "English"), "English")

    def test_prompt_contains_global_rule_mobiglas_persona_time_and_language(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        prompt = build_system_prompt(piper_voice="de_DE-thorsten-medium", current_time=now)

        self.assertTrue(prompt.startswith("Erfinde keine Fakten"))
        self.assertIn(f"Lokale aktuelle Zeit: {now.astimezone().isoformat(timespec='seconds')}", prompt)
        self.assertTrue(prompt.endswith(
            "VERBINDLICHE ANTWORTSPRACHE: German. Verfasse deine gesamte Antwort in dieser Sprache, "
            "unabhängig von der Sprache der Anfrage, des Profils oder des Kontexts. Wechsle nicht die Sprache."
        ))
        self.assertIn("Erfinde keine Fakten", prompt)
        self.assertIn("persönliche KI des Nutzers auf seinem Mobiglas", prompt)
        self.assertIn("weder ein Schiffscomputer noch eine Schiffs-KI", prompt)
        self.assertIn("Captain oder Commander", prompt)

    def test_prompt_optimizes_output_for_speech_before_the_profile(self):
        prompt = build_system_prompt(
            piper_voice="de_DE-thorsten-medium", special_prompt_name="Star Citizen", special_prompt="Kontext")

        self.assertIn("Sprachausgabe (TTS)", prompt)
        self.assertIn("kein Markdown und keine Emojis", prompt)
        self.assertIn("Internetadressen", prompt)
        self.assertLess(prompt.index("Sprachausgabe (TTS)"), prompt.index("Spezialprofil: Star Citizen"))
        self.assertIn("Sprachausgabe (TTS)", build_system_prompt(global_prompt=""))

    def test_tts_rules_preserve_native_non_latin_scripts(self):
        prompt = build_system_prompt(piper_voice="ja_JP-voice")

        self.assertIn("Chinesisch, Japanisch und Koreanisch", prompt)
        self.assertIn("üblichen Originalschrift", prompt)
        self.assertIn("Transliteration in lateinische Buchstaben ist nicht erwünscht", prompt)
        self.assertIn("VERBINDLICHE ANTWORTSPRACHE: Japanese", prompt)

    def test_special_profile_is_separate_and_optional(self):
        base = build_system_prompt(piper_voice="en_US-amy-medium")
        extended = build_system_prompt(
            piper_voice="en_US-amy-medium",
            special_prompt_name="Star Citizen",
            special_prompt="Star Citizen specific context",
        )

        self.assertNotIn("Spezialprofil", base)
        self.assertIn("Spezialprofil: Star Citizen\n\nStar Citizen specific context", extended)

    def test_selected_language_is_last_and_overrides_profile_language(self):
        prompt = build_system_prompt(
            piper_voice="de_DE-thorsten-medium",
            special_prompt_name="Deutschprofil",
            special_prompt="Antworte immer auf Deutsch.",
            extra_context="Der Log-Kontext ist deutsch.",
            response_language="English",
        )

        self.assertLess(prompt.index("Spezialprofil: Deutschprofil"), prompt.index("VERBINDLICHE ANTWORTSPRACHE"))
        self.assertTrue(prompt.endswith("Wechsle nicht die Sprache."))
        self.assertIn("VERBINDLICHE ANTWORTSPRACHE: English", prompt)

    def test_global_prompt_is_not_included_in_named_profiles(self):
        global_prompt = "Global-only instructions"
        prompt = build_system_prompt(global_prompt=global_prompt)
        special = build_system_prompt(
            special_prompt_name="Star Citizen",
            special_prompt="Profile-only instructions",
            global_prompt=global_prompt,
        )

        self.assertIn(global_prompt, prompt)
        self.assertNotIn(global_prompt, special)
        self.assertIn("Profile-only instructions", special)
        self.assertIn("persönliche KI des Nutzers auf seinem Mobiglas", prompt)
        self.assertNotIn("Copilot für Linux und den allgemeinen Desktop", prompt)

    def test_persona_file_is_seeded_and_edits_are_read(self):
        with TemporaryDirectory() as directory:
            persona_file = Path(directory) / "persona.txt"

            initial = load_persona(persona_file)
            self.assertIn("moeglichst kompakt", initial)
            self.assertNotIn("Star Citizen", initial)
            self.assertNotIn("UEX", initial)
            self.assertNotIn("Levski", initial)
            self.assertTrue(persona_file.exists())

            persona_file.write_text("Custom editable persona", encoding="utf-8")
            self.assertEqual(load_persona(persona_file), "Custom editable persona")

    def test_unknown_voice_uses_fallback_language(self):
        prompt = build_system_prompt(fallback_language="Deutsch")

        self.assertIn("VERBINDLICHE ANTWORTSPRACHE: Deutsch", prompt)


if __name__ == "__main__":
    unittest.main()