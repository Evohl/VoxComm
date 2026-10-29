from __future__ import annotations

import time


MODIFIERS = {"LEFTALT", "RIGHTALT", "LEFTCTRL", "RIGHTCTRL", "LEFTSHIFT", "RIGHTSHIFT"}
KEY_ALIASES = {
    "ALT": "LEFTALT", "L-ALT": "LEFTALT", "R-ALT": "RIGHTALT",
    "LALT": "LEFTALT", "RALT": "RIGHTALT",
    "CTRL": "LEFTCTRL", "L-CTRL": "LEFTCTRL", "R-CTRL": "RIGHTCTRL",
    "LCTRL": "LEFTCTRL", "RCTRL": "RIGHTCTRL",
    "SHIFT": "LEFTSHIFT", "L-SHIFT": "LEFTSHIFT", "R-SHIFT": "RIGHTSHIFT",
    "LSHIFT": "LEFTSHIFT", "RSHIFT": "RIGHTSHIFT",
    "ESC": "ESC", "RETURN": "ENTER", "SPACEBAR": "SPACE",
}
SAFE_KEYS = {
    "SPACE", "ENTER", "ESC", "TAB", "BACKSPACE", "DELETE", "INSERT", "HOME", "END",
    "PAGEUP", "PAGEDOWN", "UP", "DOWN", "LEFT", "RIGHT", "CAPSLOCK",
    "MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE", "BACKSLASH", "SEMICOLON",
    "APOSTROPHE", "GRAVE", "COMMA", "DOT", "SLASH",
    *(f"{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    *(f"{digit}" for digit in "0123456789"),
    *(f"F{number}" for number in range(1, 13)),
}
BLOCKED_CHORDS = {
    frozenset(("LEFTALT", "F4")),
    frozenset(("RIGHTALT", "F4")),
    frozenset(("LEFTCTRL", "LEFTALT", "DELETE")),
    frozenset(("RIGHTCTRL", "RIGHTALT", "DELETE")),
    frozenset(("LEFTCTRL", "LEFTALT", "BACKSPACE")),
    frozenset(("RIGHTCTRL", "RIGHTALT", "BACKSPACE")),
}


def normalize_chord(keys: object) -> tuple[list[str], list[int]]:
    from evdev import ecodes

    if not isinstance(keys, list) or not 1 <= len(keys) <= 3:
        raise ValueError("keys must contain one to three key names")
    names = []
    for key in keys:
        if not isinstance(key, str):
            raise ValueError("key names must be strings")
        token = key.strip().upper().removeprefix("KEY_").replace("_", "")
        token = KEY_ALIASES.get(key.strip().upper(), KEY_ALIASES.get(token, token))
        if token not in SAFE_KEYS and token not in MODIFIERS:
            raise ValueError(f"unsupported key: {key}")
        names.append(token)
    if len(set(names)) != len(names):
        raise ValueError("duplicate keys are not allowed")
    if any(name in MODIFIERS for name in names[-1:]):
        raise ValueError("a key chord must end with a non-modifier key")
    if sum(name in MODIFIERS for name in names) > 2:
        raise ValueError("at most two modifiers are allowed")
    if len(names) > 1 and not any(name in MODIFIERS for name in names[:-1]):
        raise ValueError("modifiers must precede the key")
    if frozenset(names) in BLOCKED_CHORDS:
        raise ValueError("this system shortcut is blocked")
    codes = [getattr(ecodes, f"KEY_{name}", None) for name in names]
    if any(not isinstance(code, int) for code in codes):
        raise ValueError("unsupported key code")
    return names, codes


def format_chord(names: list[str]) -> str:
    labels = {
        "LEFTALT": "Left Alt", "RIGHTALT": "Right Alt",
        "LEFTCTRL": "Left Ctrl", "RIGHTCTRL": "Right Ctrl",
        "LEFTSHIFT": "Left Shift", "RIGHTSHIFT": "Right Shift",
        "PAGEUP": "Page Up", "PAGEDOWN": "Page Down",
    }
    return " + ".join(labels.get(name, name.title() if name in {"SPACE", "ENTER", "ESC"} else name) for name in names)


def press_key_chord(keys: object) -> str:
    from evdev import UInput, ecodes

    names, codes = normalize_chord(keys)
    device = UInput({ecodes.EV_KEY: codes}, name="VoxComm Input Control")
    pressed = []
    try:
        for code in codes:
            device.write(ecodes.EV_KEY, code, 1)
            pressed.append(code)
        device.syn()
        time.sleep(0.05)
    finally:
        try:
            for code in reversed(pressed):
                device.write(ecodes.EV_KEY, code, 0)
            if pressed:
                device.syn()
        finally:
            device.close()
    return format_chord(names)