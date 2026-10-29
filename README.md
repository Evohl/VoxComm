# VoxComm

**VoxComm** is a Linux desktop voice copilot for games and other focused workflows. It combines push-to-talk recording, Wyoming-compatible Whisper speech recognition, a local Ollama or llama.cpp model, and optional Wyoming Piper speech output.

> **Current scope:** VoxComm transcribes microphone audio, asks the configured model for a response, can search the public web when the active prompt profile allows it, and can speak that response. It does not inspect the screen, execute shell commands, or send keyboard or mouse actions.

## Features

- Push-to-talk from an overlay button, a global keyboard or controller-button binding, or tray click-toggle.
- Configurable general hotkey and a separate hotkey for each specialty prompt profile.
- Wyoming-compatible Whisper STT and optional Piper TTS.
- Ollama and llama.cpp chat endpoints.
- A global prompt plus manually selected or hotkey-activated specialty prompts.
- Optional web search, enabled independently for the global prompt or each specialty profile.
- Optional automatic speech output and replay of the last answer.
- Tray mode, draggable overlay, remembered position, and a reset control.

## Requirements

- Linux desktop session with a Qt-supported graphics platform.
- Python 3.11 or newer. Python 3.14 is currently used in the development environment.
- PortAudio runtime/development library for microphone capture and playback (`portaudio` / `portaudio-2.0` pkg-config entry).
- Qt 6 runtime and a working Qt platform plugin. On Wayland, VoxComm prefers XWayland (`xcb`) when `DISPLAY` is available because native Wayland does not allow clients to position their own windows or reliably request always-on-top. An explicitly set `QT_QPA_PLATFORM` is respected.
- A working microphone and audio output visible to PortAudio.
- Network access to the configured STT, LLM and, if enabled, TTS endpoints.
- Global keyboard and controller-button bindings use `evdev` and require read access to the matching devices under `/dev/input`. Some distributions require membership in the `input` group; use the least-privileged per-session device access your system supports.

## Installation

Install system packages for Python development, PortAudio and Qt/X11 compatibility using your distribution's package manager. Package names vary; common names include `python3`, `python3-venv`, `python3-dev`, `portaudio`, `portaudio-devel` or `portaudio19-dev`, and XWayland/XCB runtime libraries.

From a checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[input]'
voxcomm
```

Install a launcher and icon in the user's application menu with:

```bash
voxcomm --install-launcher
```

This writes `voxcomm.desktop` and the VoxComm icon beneath `${XDG_DATA_HOME:-~/.local/share}`. Remove those two files to uninstall the launcher. Settings remain under the historical path `${XDG_CONFIG_HOME:-~/.config}/sc-ai-copilot/config.toml` to preserve existing user configuration; the application command itself is `voxcomm` only.

## First-run preflight

Run these local checks before troubleshooting. They inspect system state but do not change permissions or start services:

```bash
python3 --version
pkg-config --modversion portaudio-2.0
python -c 'import PySide6, sounddevice, evdev; print("Python dependencies: OK")'
printf 'session=%s display=%s wayland=%s\n' "$XDG_SESSION_TYPE" "$DISPLAY" "$WAYLAND_DISPLAY"
```

Check that readable keyboard or controller-button event devices are available:

```bash
python - <<'PY'
from evdev import InputDevice, ecodes, list_devices
devices = []
for path in list_devices():
    try:
        device = InputDevice(path)
        if device.capabilities().get(ecodes.EV_KEY):
            devices.append((path, device.name))
        device.close()
    except OSError:
        pass
print("Readable input devices:", devices or "none")
PY
```

Check the Ollama HTTP service and model list (replace the URL if Ollama is hosted elsewhere):

```bash
curl --fail --show-error http://127.0.0.1:11434/api/tags
```

Wyoming uses TCP rather than HTTP. Test that its ports accept a connection (replace the host/ports with the values configured in VoxComm):

```bash
python - <<'PY'
import socket
for name, host, port in (("Whisper", "127.0.0.1", 10300), ("Piper", "127.0.0.1", 10200)):
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"{name}: reachable")
    except OSError as error:
        print(f"{name}: unavailable ({error})")
PY
```

A reachable port does not guarantee a healthy Wyoming service or a configured model/voice. In **Settings**, use **Load voices** and **Load models**, check the selected microphone, and perform a short push-to-talk test. Piper is optional if speech output is disabled. VoxComm does not save microphone audio by default.

## Current behavior

Open **Settings** from the overlay or tray. Configure the Wyoming Whisper endpoint, STT language, LLM provider/model, and optional Piper endpoint/voice. Ollama model discovery uses `/api/tags`; llama.cpp discovery uses the OpenAI-compatible `/v1/models` endpoint, commonly `http://127.0.0.1:8080/v1`.

Edit **Global** to change the always-active base prompt. Specialty profiles add task-specific context to Global. Selecting a profile and using the general hotkey uses that selected profile; a profile-specific hotkey always uses its bound profile. Profile selection is manual; process-based automatic profile selection is not implemented. The response language is derived from the selected Piper voice where possible. **Whisper recognition language** sets the language passed to each Wyoming `transcribe` request. **Read responses aloud** enables automatic Piper output.

The **Web search** permission is off by default and is configured independently for Global and each specialty profile. When enabled, the model may use DDGS to search external providers; the recognized user request and generated search query leave the machine. Search results are limited to five short snippets and are passed to the model as untrusted reference data. VoxComm does not automatically open result URLs. Time-sensitive queries (for example, weather tomorrow or today's headlines) are searched immediately using the local system date; if the model says it does not know, VoxComm searches once and asks it to answer from the results. If the model still cannot answer, VoxComm displays the results with their source links.

The **Local keyboard input** permission is off by default and is configured independently for Global and every specialty profile. When enabled, the model may request one unambiguous key tap or modifier chord, such as `X` or `Left Alt + N`; VoxComm sends it through Linux `/dev/uinput` to the current foreground application and returns the actual result to the model for a short confirmation. The application rejects unsupported keys and selected system shortcuts, and never holds or repeats a key. This is general-purpose keyboard input, not game detection: it can affect whichever application currently has focus. It requires the optional `evdev` input dependency and write access to `/dev/uinput`. Mouse buttons, wheel, and analog axes are not implemented.

### In-game status with MangoHud

VoxComm publishes its current short status to `${XDG_CACHE_HOME:-~/.cache}/voxcomm/mangohud-status.json`. MangoHud can display it through its `exec` option. Add these lines to `~/.config/MangoHud/MangoHud.conf` (keep existing options; `legacy_layout=0` is required for `exec`):

```ini
legacy_layout=0
custom_text=VoxComm
exec=/absolute/path/to/voxcomm-mangohud-status
font_size_secondary=24
```

Replace the executable path with the output of `command -v voxcomm-mangohud-status`. `font_size_secondary=24` makes VoxComm's `exec` text larger; adjust the value to taste. This also enlarges other secondary MangoHud text. Restart MangoHud/the game after editing. Launch the game with MangoHud enabled, for example with `mangohud %command%` in Steam. MangoHud is a Vulkan/OpenGL overlay, so this status appears only in games where MangoHud itself is active; it is not an independent injector. Status text is limited to 140 characters and expires after 12 seconds. In Gamescope, use MangoHud's documented `--mangoapp` integration instead of the normal layer.

The general push-to-talk binding defaults to F9. Click its field and press a keyboard key or controller button; Esc cancels. Use the adjacent **Remove hotkey** button to clear it. Each specialty profile may have its own **Profile hotkey**. Holding a binding records until release. VoxComm listens to matching `EV_KEY` events across readable `/dev/input` devices, so controller buttons are not tied to a device index. Keyboard presses are observed globally and are not blocked; they also reach the foreground application. A binding can only be assigned once. If input-device access is unavailable, the application reports the issue; pure X11 keyboard sessions can fall back to `pynput`.

In **Tray mode**, clicking the tray icon starts recording and clicking again sends it. The flat tray menu contains recording, settings, replay and exit. Standard Linux tray APIs do not expose reliable press/release events, so the tray uses click-to-toggle rather than hold-to-record. If no system tray is available, VoxComm falls back to overlay mode. The text overlay still appears for status and responses without its buttons, then hides again after the text/audio finishes.

Drag the overlay to move it; VoxComm saves and restores its coordinates. **Reset overlay position** returns it to the top center of the primary display if it has moved off-screen. Under Wayland/XWayland, placement and stacking can still depend on the compositor and game display mode.

## Tool-use boundaries

Arbitrary shell execution is not implemented. Tool availability and safety are enforced by the local dispatcher, not only by prompt instructions:

- Web search is implemented through DDGS; it does not fetch result pages. Keep that boundary explicit unless a separate page-fetching feature is designed with host allowlists, redirect rules, timeouts, response-size limits, and protection for private/local network addresses.
- Check whether the selected Ollama model declares the `tools` capability. A declared capability does not prove that the model reliably selects tools or returns valid arguments; end-to-end tool-call tests are still required.
- Restrict shell access to named, application-defined operations. Never execute arbitrary model-generated shell strings. Destructive or privileged actions should require explicit user confirmation and run only as the unprivileged application user.
- Input injection is separate from keyboard listening. Access to `/dev/input` does not grant write access to `/dev/uinput`; local keyboard input is opt-in per profile and is sent only to the currently focused application.
- Each prompt profile enables its own subset of tools. Disabled tools are omitted from that profile's tool schema and rejected again if the model requests them.
- Informational preflight checks must not grant permissions, run shell commands, send keystrokes, or access websites as a side effect.

## Configuration and diagnostics

Settings are stored as TOML at `${XDG_CONFIG_HOME:-~/.config}/sc-ai-copilot/config.toml`. The historical config directory is retained so existing VoxComm settings are not lost. Do not commit this file: it may contain private prompts, network addresses and device preferences.

To save the latest microphone recording for debugging, set `SC_AI_COPILOT_DEBUG_WAV` when launching:

```bash
SC_AI_COPILOT_DEBUG_WAV=/tmp/voxcomm-last.wav voxcomm
```

Unset the variable to return to the default behavior, which does not save microphone audio.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The offscreen Qt test platform can print harmless platform-plugin warnings. A native Qt teardown crash after tests report `OK` has been observed in some environments; distinguish it from assertion failures using the unittest output.

Project layout:

- `src/voxcomm/app.py`: Qt overlay, tray, settings and interaction flow.
- `src/voxcomm/audio.py`: microphone capture and PCM playback.
- `src/voxcomm/ptt.py`: keyboard and controller-button push-to-talk.
- `src/voxcomm/input_control.py`: validated local keyboard taps via Linux uinput.
- `src/voxcomm/providers/wyoming.py`: Wyoming Whisper and Piper protocol.
- `src/voxcomm/providers/llm.py`: Ollama and llama.cpp clients.
- `src/voxcomm/prompts.py`: prompt assembly and response-language selection.
- `src/voxcomm/config.py`: TOML configuration and persistence.
- `tests/`: unit and offscreen Qt lifecycle tests.
