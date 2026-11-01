import fcntl
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evdev import ecodes

from voxcomm.gamepad import (
    BUTTON_CODES, BUTTON_COUNT, DEVICE_NAME, HOLD_SECONDS, VirtualGamepad, _serve, normalize_button, press_gamepad_button,
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
        send_command.assert_called_once_with({"command": "press", "button": "Button37", "hold": False})

    @patch("voxcomm.gamepad._send_command", return_value={"button": "Button37"})
    def test_hold_press_sends_hold_flag(self, send_command):
        press_gamepad_button("Button37", hold=True)

        send_command.assert_called_once_with({"command": "press", "button": "Button37", "hold": True})

    def test_press_rejects_non_boolean_hold(self):
        with self.assertRaises(ValueError):
            press_gamepad_button("Button1", hold="yes")

    @patch("voxcomm.gamepad.time.sleep")
    @patch("evdev.UInput")
    def test_hold_duration_is_about_one_second(self, uinput, sleep):
        gamepad = VirtualGamepad()

        gamepad.tap("Button2", duration=HOLD_SECONDS)

        sleep.assert_called_once_with(HOLD_SECONDS)
        self.assertEqual(HOLD_SECONDS, 1.0)
        gamepad.close()

    def test_press_rejects_invalid_delays(self):
        for delay in (-1, 3601, float("nan"), float("inf"), True, "2"):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                press_gamepad_button("Button1", delay=delay)

    def test_second_daemon_preserves_running_daemon_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
                existing.bind(str(path))
                existing.listen()
                with patch("voxcomm.gamepad.socket_path", return_value=path), \
                     patch("voxcomm.gamepad.VirtualGamepad") as device, \
                     patch("voxcomm.gamepad._send_command", return_value={"running": True}):
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        _serve()

                self.assertTrue(path.exists())
                device.return_value.close.assert_called_once_with()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                    connection, _address = existing.accept()
                    connection.close()

    def test_stale_socket_is_replaced_and_owned_socket_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
                stale.bind(str(path))
            with patch("voxcomm.gamepad.socket_path", return_value=path), \
                 patch("voxcomm.gamepad.VirtualGamepad") as device, \
                 patch("voxcomm.gamepad._send_command", side_effect=ConnectionRefusedError), \
                 patch("signal.signal", side_effect=RuntimeError("stop test")):
                with self.assertRaisesRegex(RuntimeError, "stop test"):
                    _serve()

            self.assertFalse(path.exists())
            device.return_value.close.assert_called_once_with()

    def test_existing_start_lock_prevents_a_second_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"
            with path.with_suffix(".lock").open("w") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch("voxcomm.gamepad.socket_path", return_value=path), \
                     patch("voxcomm.gamepad.VirtualGamepad") as device:
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        _serve()
                device.assert_not_called()

    def test_unresponsive_socket_is_not_assumed_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
                existing.bind(str(path))
                existing.listen()
                with patch("voxcomm.gamepad.socket_path", return_value=path), \
                     patch("voxcomm.gamepad.VirtualGamepad") as device, \
                     patch("voxcomm.gamepad._send_command", side_effect=TimeoutError):
                    with self.assertRaises(TimeoutError):
                        _serve()
                self.assertTrue(path.exists())
                device.return_value.close.assert_called_once_with()

    def test_cleanup_preserves_replaced_socket_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"

            def replace_socket(*_args):
                path.unlink()
                path.write_text("replacement")
                raise RuntimeError("stop test")

            with patch("voxcomm.gamepad.socket_path", return_value=path), \
                 patch("voxcomm.gamepad.VirtualGamepad") as device, \
                 patch("signal.signal", side_effect=replace_socket):
                with self.assertRaisesRegex(RuntimeError, "stop test"):
                    _serve()

            self.assertEqual(path.read_text(), "replacement")
            device.return_value.close.assert_called_once_with()

    def test_existing_regular_file_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gamepad.sock"
            path.write_text("keep")
            with patch("voxcomm.gamepad.socket_path", return_value=path), \
                 patch("voxcomm.gamepad.VirtualGamepad") as device:
                with self.assertRaisesRegex(RuntimeError, "not a socket"):
                    _serve()

            self.assertEqual(path.read_text(), "keep")
            device.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()