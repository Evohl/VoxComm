from __future__ import annotations

import argparse
import errno
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


BUTTON_COUNT = 100
DEVICE_NAME = "VoxComm"


def button_codes() -> tuple[int, ...]:
    from evdev import ecodes

    codes = (
        list(range(ecodes.BTN_MISC, ecodes.BTN_9 + 1))
        + list(range(ecodes.BTN_JOYSTICK, ecodes.BTN_DEAD + 1))
        + list(range(ecodes.BTN_MOUSE, ecodes.BTN_BACK + 1))
        + list(range(ecodes.BTN_DIGI, ecodes.BTN_WHEEL))
        + [ecodes.BTN_WHEEL, ecodes.BTN_GEAR_UP, ecodes.BTN_TASK]
        + [
            ecodes.BTN_DPAD_UP,
            ecodes.BTN_DPAD_DOWN,
            ecodes.BTN_DPAD_LEFT,
            ecodes.BTN_DPAD_RIGHT,
            ecodes.BTN_GRIPL,
            ecodes.BTN_GRIPR,
            ecodes.BTN_GRIPL2,
            ecodes.BTN_GRIPR2,
        ]
        + list(range(ecodes.BTN_TRIGGER_HAPPY1, ecodes.BTN_TRIGGER_HAPPY40 + 1))
    )
    if len(codes) != BUTTON_COUNT or len(set(codes)) != BUTTON_COUNT:
        raise RuntimeError("Linux input does not provide 100 unique button event codes")
    return tuple(codes)


BUTTON_CODES = button_codes()


def normalize_button(button: object) -> tuple[int, str]:
    if isinstance(button, bool):
        raise ValueError("button must be Button1 through Button100")
    if isinstance(button, int):
        number = button
    elif isinstance(button, str):
        value = button.strip()
        if value[:6].casefold() == "button":
            value = value[6:]
        if not value.isdecimal():
            raise ValueError("button must be Button1 through Button100")
        number = int(value)
    else:
        raise ValueError("button must be Button1 through Button100")
    if not 1 <= number <= BUTTON_COUNT:
        raise ValueError("button must be Button1 through Button100")
    return BUTTON_CODES[number - 1], f"Button{number}"


def runtime_directory() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(runtime) / "voxcomm" if runtime else Path.home() / ".cache" / "voxcomm" / "run"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


def socket_path() -> Path:
    return runtime_directory() / "gamepad.sock"


class VirtualGamepad:
    def __init__(self):
        from evdev import UInput, ecodes

        self._device = UInput(
            {ecodes.EV_KEY: list(BUTTON_CODES)},
            name=DEVICE_NAME,
            vendor=0x1209,
            product=0x0001,
            version=1,
        )
        self._lock = threading.Lock()

    def tap(self, button: object, duration: float = 0.05) -> str:
        from evdev import ecodes

        code, name = normalize_button(button)
        with self._lock:
            if self._device is None:
                raise RuntimeError("virtual controller is stopped")
            self._device.write(ecodes.EV_KEY, code, 1)
            self._device.syn()
            try:
                time.sleep(duration)
            finally:
                self._device.write(ecodes.EV_KEY, code, 0)
                self._device.syn()
        return name

    def close(self) -> None:
        with self._lock:
            device, self._device = self._device, None
            if device is not None:
                device.close()


def _send_command(command: dict[str, object]) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(socket_path()))
        connection.sendall(json.dumps(command).encode("utf-8") + b"\n")
        response = connection.makefile("rb").readline(4096)
    try:
        result = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid response from virtual controller") from error
    if not isinstance(result, dict):
        raise RuntimeError("invalid response from virtual controller")
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    return result


def press_gamepad_button(button: object, delay: float = 0) -> str:
    _code, name = normalize_button(button)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 3600:
        raise ValueError("delay must be between 0 and 3600 seconds")
    if delay:
        time.sleep(delay)
    result = _send_command({"command": "press", "button": name})
    return str(result.get("button", name))


def _serve() -> None:
    device = VirtualGamepad()
    stopping = threading.Event()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = socket_path()
    try:
        try:
            server.bind(str(path))
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            try:
                _send_command({"command": "status"})
            except OSError:
                path.unlink(missing_ok=True)
                server.bind(str(path))
            else:
                raise RuntimeError("VoxComm virtual controller is already running") from error
        path.chmod(0o600)
        server.listen(4)
        server.settimeout(0.25)

        def stop_handler(_signum, _frame):
            stopping.set()

        import signal

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        while not stopping.is_set():
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(2)
                stream = connection.makefile("rb")
                try:
                    payload = json.loads(stream.readline(4096))
                    command = payload.get("command") if isinstance(payload, dict) else None
                    if command == "status":
                        response = {"running": True, "device": DEVICE_NAME, "buttons": BUTTON_COUNT}
                    elif command == "press":
                        pressed = device.tap(payload.get("button"))
                        response = {"ok": True, "button": pressed}
                    elif command == "stop":
                        response = {"ok": True}
                        stopping.set()
                    else:
                        response = {"error": "unknown controller command"}
                except Exception as error:
                    response = {"error": str(error)[:200]}
                connection.sendall(json.dumps(response).encode("utf-8") + b"\n")
    finally:
        server.close()
        device.close()
        path.unlink(missing_ok=True)


def start() -> str:
    try:
        status = _send_command({"command": "status"})
        if status.get("running"):
            return "VoxComm controller is already running"
    except OSError:
        pass

    process = subprocess.Popen(
        [sys.executable, "-m", "voxcomm.gamepad", "--daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + 5
    last_error: OSError | RuntimeError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            status = _send_command({"command": "status"})
            return f"VoxComm controller started ({status['buttons']} buttons)"
        except (OSError, RuntimeError) as error:
            last_error = error
            time.sleep(0.05)
    detail = f": {last_error}" if last_error else "; check access to /dev/uinput"
    raise RuntimeError(f"could not start VoxComm controller{detail}")


def stop() -> str:
    _send_command({"command": "stop"})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            _send_command({"command": "status"})
        except OSError:
            return "VoxComm controller stopped"
        time.sleep(0.05)
    raise RuntimeError("VoxComm controller did not stop in time")


def status() -> str:
    result = _send_command({"command": "status"})
    return f"{result['device']}: running, {result['buttons']} buttons"


def main() -> int:
    parser = argparse.ArgumentParser(prog="voxcomm-gamepad")
    parser.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("start", help="start the virtual controller")
    subparsers.add_parser("stop", help="stop the virtual controller")
    subparsers.add_parser("status", help="show controller status")
    press_parser = subparsers.add_parser("press", help="tap a numbered button")
    press_parser.add_argument("button", help="Button1 through Button100")
    press_parser.add_argument("--delay", type=float, default=0, metavar="SECONDS",
                              help="wait before pressing the button (0 to 3600 seconds)")
    arguments = parser.parse_args()
    if arguments.daemon:
        _serve()
        return 0
    if arguments.action is None:
        parser.print_help()
        return 0
    try:
        if arguments.action == "start":
            message = start()
        elif arguments.action == "stop":
            message = stop()
        elif arguments.action == "status":
            message = status()
        else:
            message = f"Pressed {press_gamepad_button(arguments.button, arguments.delay)}"
    except (OSError, RuntimeError, ValueError) as error:
        print(f"voxcomm-gamepad: {error}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())