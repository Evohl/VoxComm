import unittest
from unittest.mock import patch

from voxcomm.action_catalog import match_action, parse_action_catalog


class ActionCatalogTests(unittest.TestCase):
    def test_matches_exact_keyword_and_returns_configured_chord(self):
        catalog = "flight ready|systeme bereit => RIGHTALT + R => Flight Ready Taste gesendet"

        action = match_action(catalog, "Du: Flight ready bitte!")

        self.assertEqual(action["keys"], ["RIGHTALT", "R"])
        self.assertEqual(action["reply"], "Flight Ready Taste gesendet")

    def test_prefers_longest_keyword_and_does_not_match_questions(self):
        catalog = "gear => N => Gear-Taste gesendet\nfahrwerk runter => LEFTALT + N => Fahrwerk-Taste gesendet"

        action = match_action(catalog, "Fahrwerk runter bitte")

        self.assertEqual(action["keys"], ["LEFTALT", "N"])
        self.assertIsNone(match_action(catalog, "Wie schalte ich das Fahrwerk runter?"))
        self.assertIsNone(match_action(catalog, "Kannst du das Fahrwerk runter schalten?"))

    def test_rejects_malformed_or_unsafe_catalog_entries(self):
        with self.assertRaises(ValueError):
            parse_action_catalog("flight ready => LEFTALT => ready")
        with self.assertRaises(ValueError):
            parse_action_catalog("close game => LEFTALT + F4 => close")

    def test_conflicting_equal_length_keywords_are_ambiguous(self):
        catalog = "power on => U => enable\npower on => I => thrusters"

        with self.assertRaises(ValueError):
            match_action(catalog, "power on")

    @patch("voxcomm.action_catalog.normalize_chord", return_value=(["X"], [1]))
    def test_comments_and_empty_lines_are_ignored(self, _normalize):
        actions = parse_action_catalog("# note\n\nx => X => done")

        self.assertEqual(len(actions), 1)


if __name__ == "__main__":
    unittest.main()
