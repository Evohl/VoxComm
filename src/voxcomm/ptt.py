from __future__ import annotations

import os
import selectors
import threading
import time

from voxcomm.i18n import tr

_KEY_ALIASES = {
    "ctrl": "LEFTCTRL", "ctrl_l": "LEFTCTRL", "ctrl_r": "RIGHTCTRL",
    "alt": "LEFTALT", "alt_l": "LEFTALT", "alt_r": "RIGHTALT", "alt_gr": "RIGHTALT",
    "shift": "LEFTSHIFT", "shift_l": "LEFTSHIFT", "shift_r": "RIGHTSHIFT",
    "cmd": "LEFTMETA", "super": "LEFTMETA",
}


def evdev_key_code(name: str) -> int | None:
    """Map keyboard and gamepad button names to evdev input codes."""
    from evdev import ecodes

    token = name.strip().upper().removeprefix("KEY_")
    token = _KEY_ALIASES.get(token.lower(), token)
    for candidate in (token, token.replace("_", "")):
        code = getattr(ecodes, f"KEY_{candidate}", None)
        if not isinstance(code, int):
            code = getattr(ecodes, candidate, None)
        if isinstance(code, int):
            return code
    return None


def evdev_key_name(code: int) -> str | None:
    from evdev import ecodes

    names = ecodes.KEY.get(code) or ecodes.BTN.get(code)
    if names is None:
        return None
    name = names[-1] if isinstance(names, (list, tuple)) else names
    return name.removeprefix("KEY_")


def capture_input(stop: threading.Event, timeout: float = 15.0) -> str | None:
    """Capture the next keyboard key or gamepad button from readable input devices."""
    from evdev import InputDevice, ecodes, list_devices

    selector = selectors.DefaultSelector()
    for path in list_devices():
        try:
            device = InputDevice(path)
        except OSError:
            continue
        if device.capabilities().get(ecodes.EV_KEY):
            selector.register(device, selectors.EVENT_READ)
        else:
            device.close()
    if not selector.get_map():
        selector.close()
        raise PermissionError(tr("No readable input devices found (access to /dev/input required)"))
    deadline = time.monotonic() + timeout
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            for selected, _mask in selector.select(timeout=0.1):
                try:
                    events = selected.fileobj.read()
                except OSError:
                    selector.unregister(selected.fileobj)
                    continue
                for event in events:
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        return None if event.code == ecodes.KEY_ESC else evdev_key_name(event.code)
        return None
    finally:
        for key in list(selector.get_map().values()):
            key.fileobj.close()
        selector.close()


class PttListener:
    def __init__(self, key: str, on_change, extra_keys: dict[str, str] | None = None):
        self.key = key.lower()
        # Zusatz-Hotkeys: Taste -> Profilname; der allgemeine Hotkey meldet das Profil "".
        self.extra_keys = {name.lower(): profile for name, profile in (extra_keys or {}).items() if name}
        self.on_change = on_change
        self._pressed = threading.Event()
        self._profile = ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._keyboard = None

    def _key_profiles(self) -> dict[str, str]:
        profiles = {self.key: ""} if self.key else {}
        for name, profile in self.extra_keys.items():
            profiles.setdefault(name, profile)
        return profiles

    def start(self) -> None:
        self._start_input_devices()

    def _open_inputs(self):
        from evdev import InputDevice, ecodes, list_devices

        codes = {}
        for name, profile in self._key_profiles().items():
            code = evdev_key_code(name)
            if code is None:
                raise ValueError(tr("Unknown input '{name}'", name=name))
            codes.setdefault(code, profile)
        if not codes:
            return codes, []
        devices, denied = [], False
        for path in list_devices():
            try:
                device = InputDevice(path)
            except PermissionError:
                denied = True
                continue
            except OSError:
                continue
            if codes.keys() & set(device.capabilities().get(ecodes.EV_KEY, [])):
                devices.append(device)
            else:
                device.close()
        if not devices and denied:
            raise PermissionError(tr("No access to /dev/input (input device access is required)"))
        return codes, devices

    def _start_input_devices(self) -> None:
        try:
            codes, devices = self._open_inputs()
            if not codes:
                return
            if devices:
                thread = threading.Thread(target=self._watch_inputs, args=(codes, devices), daemon=True)
                thread.start()
                self._threads.append(thread)
                return
            reason = tr("No input device with the configured hotkeys was found")
        except Exception as error:
            reason = str(error) or type(error).__name__
        if os.environ.get("WAYLAND_DISPLAY"):
            # pynput sieht unter Wayland nur Tasten fokussierter X11-Fenster.
            self.on_change("error", tr("Global PTT unavailable: {reason}; use the overlay PTT button", reason=reason))
        else:
            try:
                from pynput import keyboard

                def key_name(key):
                    return str(getattr(key, "char", None) or key).replace("Key.", "").lower()

                profiles = self._key_profiles()
                self._keyboard = keyboard.Listener(
                    on_press=lambda key: self._set(True, profiles[key_name(key)]) if key_name(key) in profiles else None,
                    on_release=lambda key: self._set(False, profiles[key_name(key)]) if key_name(key) in profiles else None,
                )
                self._keyboard.start()
            except Exception as error:
                self.on_change("error", tr("Keyboard PTT unavailable ({reason}; {error})", reason=reason, error=error))

    def _watch_inputs(self, codes: dict[int, str], devices: list) -> None:
        from evdev import ecodes

        selector = selectors.DefaultSelector()
        for device in devices:
            selector.register(device, selectors.EVENT_READ)
        try:
            while not self._stop.is_set() and selector.get_map():
                for selected, _mask in selector.select(timeout=0.2):
                    device = selected.fileobj
                    try:
                        for event in device.read():
                            # value 2 ist Auto-Repeat und wird ignoriert.
                            if event.type == ecodes.EV_KEY and event.code in codes and event.value in (0, 1):
                                self._set(bool(event.value), codes[event.code])
                    except OSError:
                        selector.unregister(device)
                        device.close()
            if not self._stop.is_set():
                self.on_change("error", tr("Input device for the PTT binding was disconnected"))
        finally:
            for key in list(selector.get_map().values()):
                key.fileobj.close()
            selector.close()

    def _set(self, pressed: bool, profile: str = "") -> None:
        if pressed:
            if self._pressed.is_set():
                return
            self._profile = profile
            self._pressed.set()
            self.on_change("pressed", profile)
        elif self._pressed.is_set() and profile == self._profile:
            self._pressed.clear()
            self.on_change("released", profile)

    def stop(self) -> None:
        self._stop.set()
        keyboard_listener, self._keyboard = self._keyboard, None
        if keyboard_listener is not None:
            try:
                keyboard_listener.stop()
            except Exception:
                pass
            try:
                keyboard_listener.join(timeout=1)
            except Exception:
                pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1)

    def is_pressed(self) -> bool:
        return self._pressed.is_set() and not self._stop.is_set()


capture_key = capture_input

