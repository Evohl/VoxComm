import unittest
from unittest.mock import patch

from evdev import ecodes

from voxcomm.input_control import format_chord, normalize_chord, press_key_chord


class InputControlTests(unittest.TestCase):
    def test_normalizes_and_formats_modifier_chord(self):
        names, codes = normalize_chord(["L-ALT", "n"])

        self.assertEqual(names, ["LEFTALT", "N"])
        self.assertEqual(codes, [ecodes.KEY_LEFTALT, ecodes.KEY_N])
        self.assertEqual(format_chord(names), "Left Alt + N")

    def test_rejects_ambiguous_or_dangerous_chords(self):
        for keys in ("X", [], ["LEFTALT"], ["N", "LEFTALT"], ["LEFTALT", "F4"],
                     ["LEFTCTRL", "LEFTALT", "DELETE"], ["BTN_SOUTH"]):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                normalize_chord(keys)

    def test_press_sends_one_tap_and_releases_keys_in_reverse_order(self):
        device = unittest.mock.Mock()
        with patch("evdev.UInput", return_value=device):
            self.assertEqual(press_key_chord(["LEFTSHIFT", "X"]), "Left Shift + X")

        self.assertEqual(device.write.call_args_list, [
            unittest.mock.call(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1),
            unittest.mock.call(ecodes.EV_KEY, ecodes.KEY_X, 1),
            unittest.mock.call(ecodes.EV_KEY, ecodes.KEY_X, 0),
            unittest.mock.call(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0),
        ])
        self.assertEqual(device.syn.call_count, 2)
        device.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()