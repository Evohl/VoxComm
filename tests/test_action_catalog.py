import unittest
from unittest.mock import patch

from voxcomm.action_catalog import match_action, parse_action_catalog


class ActionCatalogTests(unittest.TestCase):
    def test_matches_exact_keyword_and_returns_configured_chord(self):
        catalog = (
            "Flight Systems On => flight ready|systeme ein => Button1 => Flight Systems eingeschaltet\n"
            "Flight Systems Off => flight systems off|systeme aus => Button2 => Flight Systems ausgeschaltet"
        )

        action = match_action(catalog, "Du: Flight ready bitte!")

        self.assertEqual(action["name"], "Flight Systems On")
        self.assertEqual(action["button"], "Button1")
        self.assertEqual(action["reply"], "Flight Systems eingeschaltet")

    def test_matches_configured_button_and_does_not_match_questions(self):
        catalog = (
            "Landing Gear On => gear down|fahrwerk runter => Button3 => Fahrwerk ausgefahren\n"
            "Landing Gear Off => gear up => Button4 => Fahrwerk eingefahren"
        )

        action = match_action(catalog, "Fahrwerk runter bitte")

        self.assertEqual(action["button"], "Button3")
        self.assertIsNone(match_action(catalog, "Wie schalte ich das Fahrwerk runter?"))
        self.assertIsNone(match_action(catalog, "Kannst du das Fahrwerk runter schalten?"))

    def test_rejects_malformed_or_unsafe_catalog_entries(self):
        with self.assertRaises(ValueError):
            parse_action_catalog("Flight Systems On => flight ready => Button1 => ready")

    def test_on_and_off_switches_must_use_distinct_buttons(self):
        catalog = (
            "Flight Systems On => flight ready => Button1 => enabled\n"
            "Flight Systems Off => flight systems off => Button1 => disabled"
        )

        with self.assertRaisesRegex(ValueError, "own button"):
            parse_action_catalog(catalog)
        with self.assertRaises(ValueError):
            parse_action_catalog("Close Game => close game => Button1 => close")
        with self.assertRaises(ValueError):
            parse_action_catalog("Flight Systems On => flight ready => Button1 => ready")

    def test_conflicting_equal_length_keywords_are_ambiguous(self):
        catalog = (
            "Power On => power on => Button5 => enable\n"
            "Power Off => power off => Button6 => disable\n"
            "Thrusters On => power on => Button7 => enable thrusters\n"
            "Thrusters Off => thrusters off => Button8 => disable thrusters"
        )

        with self.assertRaises(ValueError):
            match_action(catalog, "power on")

    def test_comments_and_empty_lines_are_ignored(self):
        actions = parse_action_catalog(
            "# note\n\nPower On => power on => Button9 => enabled\nPower Off => power off => Button10 => disabled"
        )

        self.assertEqual(len(actions), 2)


if __name__ == "__main__":
    unittest.main()
