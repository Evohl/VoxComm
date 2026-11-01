import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evdev import ecodes
from PySide6.QtWidgets import QApplication

from voxcomm.app import Overlay
from voxcomm.config import AppConfig
from voxcomm.ptt import PttListener


class MockDevice:
    def __init__(self, name):
        self.name = name
        self.result = []
        self.closed = False

    def read(self):
        if isinstance(self.result, OSError):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


class MockSelector:
    def __init__(self, listener, steps):
        self.listener = listener
        self.steps = iter(steps)
        self.devices = {}

    def register(self, device, _events):
        self.devices[id(device)] = SimpleNamespace(fileobj=device)

    def unregister(self, device):
        del self.devices[id(device)]

    def get_map(self):
        return self.devices

    def select(self, timeout=None):
        try:
            device, result = next(self.steps)
        except StopIteration:
            self.listener._stop.set()
            return []
        if callable(result):
            result()
            result = []
        device.result = result
        return [(self.devices[id(device)], 1)]

    def close(self):
        pass


def key_event(code, value):
    return SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value)


class PttDisconnectSafetyTests(unittest.TestCase):
    def watch(self, listener, devices, steps, codes=None):
        selector = MockSelector(listener, steps)
        with patch("voxcomm.ptt.selectors.DefaultSelector", return_value=selector):
            listener._watch_inputs(codes or {(ecodes.KEY_F9, None): ""}, devices)

    def test_unrelated_disconnect_and_release_do_not_release_active_device(self):
        events = []
        checkpoints = []
        listener = PttListener("f9", lambda *event: events.append(event))
        active, other = MockDevice("active"), MockDevice("other")
        steps = [
            (active, [key_event(ecodes.KEY_F9, 1)]),
            (other, [key_event(ecodes.KEY_F9, 0)]),
            (other, OSError("disconnected")),
            (active, lambda: checkpoints.append((listener.is_pressed(), list(events)))),
            (active, [key_event(ecodes.KEY_F9, 0)]),
        ]

        self.watch(listener, [active, other], steps)

        self.assertEqual([event for event in events if event[0] != "error"],
                         [("pressed", ""), ("released", "")])
        self.assertEqual(checkpoints, [(True, [("pressed", "")])])
        self.assertTrue(active.closed and other.closed)

    def test_held_device_disconnect_cancels_even_with_another_device_available(self):
        events = []
        checkpoints = []
        listener = PttListener("f9", lambda *event: events.append(event), {"f10": "Ship"})
        active, other = MockDevice("active"), MockDevice("other")
        steps = [
            (active, [key_event(ecodes.KEY_F10, 1)]),
            (active, OSError("disconnected")),
            (other, lambda: checkpoints.append((listener.is_pressed(), list(events)))),
            (other, [key_event(ecodes.KEY_F10, 0), key_event(ecodes.KEY_F9, 1),
                     key_event(ecodes.KEY_F9, 0)]),
        ]

        self.watch(listener, [active, other], steps,
                   {(ecodes.KEY_F9, None): "", (ecodes.KEY_F10, None): "Ship"})

        self.assertEqual([event for event in events if event[0] != "error"],
                         [("pressed", "Ship"), ("cancelled", "Ship"),
                          ("pressed", ""), ("released", "")])
        self.assertTrue(any(event[0] == "error" for event in events))
        self.assertFalse(checkpoints[0][0])
        self.assertEqual([kind for kind, _text in checkpoints[0][1]], ["pressed", "cancelled", "error"])
        self.assertFalse(listener._pressed.is_set())

    def test_different_binding_on_same_device_cannot_release_active_binding(self):
        events = []
        checkpoints = []
        listener = PttListener("f9", lambda *event: events.append(event), {"f10": ""})
        device = MockDevice("keyboard")
        self.watch(listener, [device], [
            (device, [key_event(ecodes.KEY_F9, 1), key_event(ecodes.KEY_F10, 0)]),
            (device, lambda: checkpoints.append((listener.is_pressed(), list(events)))),
            (device, [key_event(ecodes.KEY_F9, 0)]),
        ], {(ecodes.KEY_F9, None): "", (ecodes.KEY_F10, None): ""})

        self.assertEqual(events, [("pressed", ""), ("released", "")])
        self.assertEqual(checkpoints, [(True, [("pressed", "")])])

    def test_last_idle_device_disconnect_reports_error_without_release(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event))
        device = MockDevice("keyboard")

        self.watch(listener, [device], [(device, OSError("disconnected"))])

        self.assertEqual([kind for kind, _text in events], ["error"])
        self.assertTrue(device.closed)

    def test_last_held_device_disconnect_cancels_once_and_reports_error(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event))
        device = MockDevice("keyboard")

        self.watch(listener, [device], [
            (device, [key_event(ecodes.KEY_F9, 1)]),
            (device, OSError("disconnected")),
        ])
        listener._set(False)

        self.assertEqual([kind for kind, _text in events], ["pressed", "cancelled", "error"])
        self.assertFalse(listener._pressed.is_set())

    def test_keyboard_fallback_preserves_direct_profile_press_and_release(self):
        events = []
        listener = PttListener("f9", lambda *event: events.append(event), {"f10": "Ship"})

        class MockKeyboardListener:
            def __init__(self, on_press, on_release):
                self.on_press = on_press
                self.on_release = on_release

            def start(self):
                pass

            def stop(self):
                pass

            def join(self, timeout=None):
                pass

        keyboard = SimpleNamespace(Listener=MockKeyboardListener)
        with patch.object(listener, "_open_inputs", return_value=({(ecodes.KEY_F9, None): ""}, [])), \
             patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True), \
             patch.dict("sys.modules", {"pynput": SimpleNamespace(keyboard=keyboard)}):
            listener.start()
            listener._keyboard.on_press(SimpleNamespace(char="f10"))
            listener._keyboard.on_release(SimpleNamespace(char="f9"))
            self.assertTrue(listener.is_pressed())
            listener._keyboard.on_release(SimpleNamespace(char="f10"))
            listener.stop()

        self.assertEqual(events, [("pressed", "Ship"), ("released", "Ship")])

    def test_queued_disconnect_cancels_capture_before_stopping_without_submission(self):
        app = QApplication.instance() or QApplication([])
        events = []
        capture_finished = threading.Event()
        pause = threading.Event()
        captured = {}
        cancellation_order = []

        def record(is_recording, sample_rate):
            captured["is_recording"] = is_recording
            while is_recording():
                pause.wait(0.01)
            return b"\0" * (sample_rate * 2)

        with patch("voxcomm.app.load_config", return_value=AppConfig()), \
             patch("voxcomm.app.PttListener"), \
             patch("voxcomm.app.record_until_released", side_effect=record), \
             patch("voxcomm.app.transcribe_sync") as transcribe, \
             patch("voxcomm.app.chat") as chat:
            overlay = Overlay()
            capture = overlay._capture
            cancel = overlay._cancel_active_work

            def capture_and_notify(*args):
                try:
                    capture(*args)
                finally:
                    capture_finished.set()

            def cancel_before_stop():
                stop = overlay._capture_stop
                cancel()
                cancellation_order.append((stop.is_set(),
                                           all(event.is_set() for event in overlay._request_cancellations)))

            listener = PttListener("f9", lambda kind, text: (events.append((kind, text)),
                                                             overlay.events.ptt.emit(kind, text)))
            active, other = MockDevice("active"), MockDevice("other")
            worker = threading.Thread(target=self.watch, args=(listener, [active, other], [
                (active, [key_event(ecodes.KEY_F9, 1)]),
                (active, OSError("disconnected")),
            ]))
            with patch.object(overlay, "_capture", side_effect=capture_and_notify), \
                 patch.object(overlay, "_cancel_active_work", side_effect=cancel_before_stop):
                worker.start()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(overlay._recording)
                app.processEvents()
                self.assertTrue(capture_finished.wait(2))
            self.assertFalse(overlay._recording)
            self.assertIsNone(overlay._capture_stop)
            self.assertFalse(captured["is_recording"]())
            self.assertEqual(cancellation_order, [(False, True)])
            self.assertTrue(any(kind == "error" for kind, _text in events))
            self.assertIn("disconnected", overlay.status.text().lower())
            overlay._ptt_event("released", "")
            self.assertIn("disconnected", overlay.status.text().lower())
            transcribe.assert_not_called()
            chat.assert_not_called()
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


if __name__ == "__main__":
    unittest.main()