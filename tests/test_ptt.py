import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evdev import ecodes

from voxcomm.ptt import PttListener, evdev_key_code, evdev_key_name


class BrokenKeyboardListener:
    def stop(self):
        raise AttributeError("_display_record")


class FakeKeyboard:
    def __init__(self):
        self._read_end, self._write_end = os.pipe()
        self.closed = False
        self.pending = []

    def fileno(self):
        return self._read_end

    def push(self, code, value):
        self.pending.append(SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value))
        os.write(self._write_end, b"x")

    def read(self):
        os.read(self._read_end, 1024)
        events, self.pending = self.pending, []
        return events

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self._read_end)
            os.close(self._write_end)


class PttTests(unittest.TestCase):
    def test_key_names_map_to_evdev_codes(self):
        self.assertEqual(evdev_key_code("F9"), ecodes.KEY_F9)
        self.assertEqual(evdev_key_code("page_up"), ecodes.KEY_PAGEUP)
        self.assertEqual(evdev_key_code("ctrl_l"), ecodes.KEY_LEFTCTRL)
        self.assertEqual(evdev_key_code("a"), ecodes.KEY_A)
        self.assertEqual(evdev_key_code("BRIGHTNESS_CYCLE"), ecodes.KEY_BRIGHTNESS_CYCLE)
        self.assertEqual(evdev_key_code("btn_south"), ecodes.BTN_SOUTH)
        self.assertIsNone(evdev_key_code("gibt_es_nicht"))

    def test_captured_key_names_round_trip_to_codes(self):
        for code in (ecodes.KEY_F9, ecodes.KEY_PAGEUP, ecodes.KEY_LEFTCTRL, ecodes.KEY_A,
                     ecodes.KEY_BRIGHTNESS_CYCLE, ecodes.BTN_SOUTH):
            self.assertEqual(evdev_key_code(evdev_key_name(code)), code)

    def test_evdev_events_drive_press_and_release_and_ignore_repeat(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event))
        keyboard = FakeKeyboard()
        thread = threading.Thread(target=listener._watch_inputs, args=({ecodes.KEY_F9: ""}, [keyboard]))
        thread.start()
        try:
            keyboard.push(ecodes.KEY_A, 1)
            keyboard.push(ecodes.KEY_F9, 1)
            keyboard.push(ecodes.KEY_F9, 2)
            keyboard.push(ecodes.KEY_F9, 0)
            deadline = threading.Event()
            for _ in range(50):
                if len(events) >= 2:
                    break
                deadline.wait(0.05)
        finally:
            listener._stop.set()
            thread.join(timeout=2)

        self.assertEqual(events, [("pressed", ""), ("released", "")])
        self.assertTrue(keyboard.closed)

    def test_profile_hotkey_reports_its_profile_and_only_it_releases(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event), {"F10": "Star Citizen"})

        listener._set(True, "Star Citizen")
        listener._set(True, "")
        listener._set(False, "")
        self.assertTrue(listener.is_pressed())
        listener._set(False, "Star Citizen")

        self.assertEqual(events, [("pressed", "Star Citizen"), ("released", "Star Citizen")])
        self.assertFalse(listener.is_pressed())

    def test_general_hotkey_wins_when_a_profile_uses_the_same_key(self):
        listener = PttListener("F9", lambda *_args: None, {"f9": "Star Citizen", "F10": "Elite"})

        self.assertEqual(listener._key_profiles(), {"f9": "", "f10": "Elite"})

    def test_open_inputs_maps_every_binding_and_rejects_unknown_names(self):
        listener = PttListener("F9", lambda *_args: None, {"F10": "Elite"})
        with patch("evdev.list_devices", return_value=[]):
            codes, devices = listener._open_inputs()
        self.assertEqual(codes, {ecodes.KEY_F9: "", ecodes.KEY_F10: "Elite"})
        self.assertEqual(devices, [])

        broken = PttListener("F9", lambda *_args: None, {"nope": "Elite"})
        with self.assertRaises(ValueError):
            broken._open_inputs()

    def test_open_inputs_discovers_gamepad_button_devices(self):
        class FakeGamepad:
            def capabilities(self):
                return {ecodes.EV_KEY: [ecodes.BTN_SOUTH]}

            def close(self):
                self.closed = True

        gamepad = FakeGamepad()
        listener = PttListener("btn_south", lambda *_args: None)
        with patch("evdev.list_devices", return_value=["/dev/input/event-gamepad"]):
            with patch("evdev.InputDevice", return_value=gamepad):
                codes, devices = listener._open_inputs()

        self.assertEqual(codes, {ecodes.BTN_SOUTH: ""})
        self.assertEqual(devices, [gamepad])

    def test_start_reports_missing_input_access_instead_of_failing(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event))

        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-1"}):
            with patch.object(listener, "_open_inputs", side_effect=PermissionError("kein Zugriff")):
                listener.start()
        listener.stop()

        self.assertIsNone(listener._keyboard)
        self.assertTrue(any("kein Zugriff" in message for _kind, message in events))

    def test_stop_tolerates_keyboard_listener_initialization_failure(self):
        listener = PttListener("f9", lambda *_args: None)
        listener._keyboard = BrokenKeyboardListener()

        listener.stop()

        self.assertTrue(listener._stop.is_set())
        self.assertIsNone(listener._keyboard)

    def test_stop_is_idempotent(self):
        listener = PttListener("f9", lambda *_args: None)
        listener._keyboard = BrokenKeyboardListener()

        listener.stop()
        listener.stop()

        self.assertTrue(listener._stop.is_set())
        self.assertIsNone(listener._keyboard)


if __name__ == "__main__":
    unittest.main()