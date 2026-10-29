import unittest
from unittest.mock import patch

from evdev import ecodes

from voxcomm.gamepad import (
    BUTTON_CODES, BUTTON_COUNT, DEVICE_NAME, VirtualGamepad, normalize_button, press_gamepad_button,
)


class GamepadTests(unittest.TestCase):
    def test_exposes_one_hundred_distinct_numbered_buttons(self):
        self.assertEqual(BUTTON_COUNT, 100)
        self.assertEqual(len(BUTTON_CODES), 100)
        self.assertEqual(len(set(BUTTON_CODES)), 100)
        self.assertFalse(any(ecodes.BTN_GAMEPAD <= code <= ecodes.BTN_THUMBR for code in BUTTON_CODES))
        self.assertEqual(normalize_button("Button1"), (BUTTON_CODES[0], "Button1"))
        self.assertEqual(normalize_button("button100"), (BUTTON_CODES[99], "Button100"))

    def test_rejects_names_outside_numbered_button_range(self):
        for button in (0, 101, "Button0", "Button101", "BTN_SOUTH", True, "Button 2"):
            with self.subTest(button=button), self.assertRaises(ValueError):
                normalize_button(button)

    @patch("evdev.UInput")
    def test_virtual_device_has_only_button_events_and_is_named_voxcomm(self, uinput):
        device = uinput.return_value
        gamepad = VirtualGamepad()

        capabilities = uinput.call_args.args[0]
        self.assertEqual(list(capabilities), [ecodes.EV_KEY])
        self.assertEqual(capabilities[ecodes.EV_KEY], list(BUTTON_CODES))
        self.assertEqual(uinput.call_args.kwargs["name"], DEVICE_NAME)
        self.assertEqual(uinput.call_args.kwargs["vendor"], 0x1209)
        self.assertEqual(uinput.call_args.kwargs["product"], 0x0001)
        gamepad.close()
        device.close.assert_called_once_with()

    @patch("voxcomm.gamepad.time.sleep")
    @patch("evdev.UInput")
    def test_tap_sends_and_releases_the_same_button(self, uinput, _sleep):
        device = uinput.return_value
        gamepad = VirtualGamepad()

        self.assertEqual(gamepad.tap("Button37"), "Button37")

        code = BUTTON_CODES[36]
        self.assertEqual(device.write.call_args_list, [
            unittest.mock.call(ecodes.EV_KEY, code, 1),
            unittest.mock.call(ecodes.EV_KEY, code, 0),
        ])
        self.assertEqual(device.syn.call_count, 2)
        gamepad.close()

    @patch("voxcomm.gamepad._send_command", return_value={"button": "Button37"})
    @patch("voxcomm.gamepad.time.sleep")
    def test_press_waits_before_sending_button(self, sleep, send_command):
        self.assertEqual(press_gamepad_button("Button37", delay=2.5), "Button37")

        sleep.assert_called_once_with(2.5)
        send_command.assert_called_once_with({"command": "press", "button": "Button37"})

    def test_press_rejects_invalid_delays(self):
        for delay in (-1, 3601, float("nan"), float("inf"), True, "2"):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                press_gamepad_button("Button1", delay=delay)


if __name__ == "__main__":
    unittest.main()