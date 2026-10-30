# VoxComm

**VoxComm** is a Linux desktop voice copilot for games and other focused workflows. It combines push-to-talk recording, speech recognition, a chat model, and optional speech output. Speech interfaces and the model provider are selectable independently: Wyoming Whisper or a whisper.cpp HTTP server for recognition, Wyoming Piper or any OpenAI-compatible `/v1/audio/speech` service for speech, and locally hosted Ollama or llama.cpp or Ollama Cloud for chat.

> **Current scope:** VoxComm transcribes microphone audio, asks the configured model for a response, can search the public web when the active prompt profile allows it, and can speak that response. It does not inspect the screen, execute shell commands, or send keyboard or mouse actions.

## Features

- Push-to-talk from an overlay button, a global keyboard or controller-button binding, or tray click-toggle.
- Configurable general hotkey and a separate hotkey for each specialty prompt profile.
- Selectable STT: Wyoming Whisper (default) or a whisper.cpp server over HTTP.
- Selectable TTS: Wyoming Piper (default) or any OpenAI-compatible TTS service (for example `qwentts.cpp`).
- Selectable chat backend: local Ollama, Ollama Cloud with an API key, or llama.cpp.
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

This writes `voxcomm.desktop` and the VoxComm icon beneath `${XDG_DATA_HOME:-~/.local/share}`. Remove those two files to uninstall the launcher. Settings are stored at `${XDG_CONFIG_HOME:-~/.config}/voxcomm/config.toml`; an existing `sc-ai-copilot/config.toml` is migrated on first start. The application command itself is `voxcomm` only.

### systemd user service

To start VoxComm automatically with the graphical user session, save this unit as `packaging/systemd/user/voxcomm.service`. The paths below match a checkout at `~/StudioProjects/SC-AI-Copilot`; adjust `WorkingDirectory` and `ExecStart` if yours is elsewhere.

```ini
[Unit]
Description=VoxComm voice and communications assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=%h/StudioProjects/SC-AI-Copilot
ExecStart=%h/StudioProjects/SC-AI-Copilot/.venv/bin/voxcomm
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=15

[Install]
WantedBy=graphical-session.target
```

From the repository root, link the unit into the user systemd manager and start it:

```bash
systemctl --user link "$PWD/packaging/systemd/user/voxcomm.service"
systemctl --user daemon-reload
systemctl --user enable --now voxcomm.service
```

Check status and logs with `systemctl --user status voxcomm.service` and `journalctl --user -u voxcomm.service`. Use `systemctl --user restart voxcomm.service` to restart it, `systemctl --user stop voxcomm.service` to stop it for the current session, or `systemctl --user disable --now voxcomm.service` to disable autostart and stop it. The unit sends SIGINT on shutdown so VoxComm can close the overlay and its owned virtual controller cleanly.

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

Wyoming uses TCP rather than HTTP. If either interface is set to Wyoming, test that its ports accept a connection (replace the host/ports with the values configured in VoxComm):

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

For HTTP speech services, check the same endpoints VoxComm uses:

```bash
curl --fail --show-error http://127.0.0.1:8081/v1/audio/transcriptions   # whisper.cpp (expects a POST; 200/405 proves it answers)
curl --fail --show-error http://127.0.0.1:8097/v1/audio/voices           # OpenAI-compatible TTS
curl --fail --show-error -H "Authorization: Bearer $OLLAMA_API_KEY" https://ollama.com/api/tags
```

A reachable port does not guarantee a healthy service or a configured model/voice. In **Settings**, use the refresh buttons next to **Stimme** and **Modell**, check the selected microphone, and perform a short push-to-talk test. Speech output is optional and can be left disabled. VoxComm does not save microphone audio by default.

## Current behavior

Open **Settings** from the overlay or tray. Each section picks its own interface:

The interface language can be selected in **Settings** and takes effect after restarting VoxComm. English is the source language; additional translations live in `src/voxcomm/locales/<language>.json` and use the English message text as the key. Add a catalog and register its language in `voxcomm.i18n.SUPPORTED_LANGUAGES` to add another locale.

| Section | Interface | URL |
| --- | --- | --- |
| Spracherkennung | Wyoming (default) | `tcp://host:port`, e.g. `tcp://127.0.0.1:10300` |
| Spracherkennung | whisper.cpp server | server base URL (`/inference` is appended) or the full `/v1/audio/transcriptions` path, e.g. `http://127.0.0.1:8081` |
| Sprachausgabe | Wyoming Piper (default) | `tcp://host:port`, e.g. `tcp://127.0.0.1:10200` |
| Sprachausgabe | OpenAI-compatible | base URL; voices are listed from `GET /v1/audio/voices` and speech is fetched from `POST /v1/audio/speech` |
| Sprachmodell | Ollama (local) | `http://127.0.0.1:11434`, models from `/api/tags` |
| Sprachmodell | Ollama Cloud | `https://ollama.com`, models from `/api/tags`, chat from `/api/chat` |
| Sprachmodell | llama.cpp | OpenAI-compatible base URL, commonly `http://127.0.0.1:8080/v1`, models from `/v1/models` |

An OpenAI-compatible TTS service such as `qwentts.cpp` commonly runs on `http://127.0.0.1:8097` and returns 24 kHz mono WAV. Voice names of such services (for example `data`) carry no language prefix, so the **TTS-Sprache** selector is sent to the service directly and also becomes the response language; for Wyoming/Piper the response language is derived from the selected Piper voice instead.

Edit **Global** to change the always-active base prompt. Specialty profiles add task-specific context to Global. Selecting a profile and using the general hotkey uses that selected profile; a profile-specific hotkey always uses its bound profile. Profile selection is manual; process-based automatic profile selection is not implemented. **Erkennungssprache** sets the language passed to each recognition request (Wyoming `transcribe` or the whisper.cpp `language` field; *Automatisch* forwards `auto`). **Read responses aloud** enables automatic speech output. Replies are spoken sentence by sentence while the model is still writing (except in profiles with web search, which speak the finished answer). **Denkmodus** (Ollama and Ollama Cloud) offers exactly the thinking controls the selected model reports through `/api/show` (for example *Aus*/*An*, or *Niedrig*/*Mittel*/*Hoch*) and is disabled for models without such controls; *Modell-Standard* sends nothing. For reasoning models, *Aus* usually cuts the wait before the first word from tens of seconds to well under one.

The **Web search** permission is off by default and is configured independently for Global and each specialty profile. When enabled and the provider is `ollama-cloud`, searches run through Ollama's `/api/web_search`; with any other provider they use DDGS. Either way the recognized user request and generated search query leave the machine, results are limited to five short snippets, and they reach the model as untrusted reference data — the boundary described under [Tool-use boundaries](#tool-use-boundaries) is unchanged. Search results are limited to five short snippets and are passed to the model as untrusted reference data. VoxComm does not automatically open result URLs. Time-sensitive queries (for example, weather tomorrow or today's headlines) are searched immediately using the local system date; if the model says it does not know, VoxComm searches once and asks it to answer from the results. If the model still cannot answer, VoxComm displays the results with their source links.

The **VoxComm controller actions** permission is off by default and is configured independently for Global and every specialty profile. When enabled, the model can select only a named action from that profile's On/Off catalog; it cannot send keyboard input or invent button numbers. Each action sends one tap to the virtual VoxComm controller and reports which configured button was sent, without claiming that the game state changed. Catalog lines use `Action Name On/Off => keyword|alias => ButtonN => short reply`; every action must have a matching On and Off entry. For example:

```text
Flight Systems On => flight ready|systeme ein => Button1 => Flight Systems eingeschaltet
Flight Systems Off => flight systems off|systeme aus => Button2 => Flight Systems ausgeschaltet
```

Existing keyboard-style catalog entries must be replaced with this controller format. The feature requires the optional `evdev` input dependency, a running virtual controller, and write access to `/dev/uinput`.

### Virtual button controller

On Linux, install the optional input dependency with `pip install -e '.[input]'`, then start VoxComm's virtual controller with `voxcomm-gamepad start`. It appears as **VoxComm** and exposes `Button1` through `Button100` as button-only events, with no sticks or axes. Use `voxcomm-gamepad press Button37` to test a button, or `voxcomm-gamepad press Button37 --delay 2` to wait two seconds before pressing. `voxcomm-gamepad status` checks the device and `voxcomm-gamepad stop` removes it. The controller runs only while started and requires write permission for `/dev/uinput`. If needed, an administrator can grant access to the `input` group with a udev rule such as `KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"` in `/etc/udev/rules.d/99-uinput.rules`, then reload udev and reconnect the user session. Bind each On and Off command to its own numbered button in the game's controller settings, then add those button assignments to the active profile's action catalog.

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

- Web search is implemented through DDGS, or through Ollama Cloud's `/api/web_search` when that provider is selected; neither fetches result pages. Keep that boundary explicit unless a separate page-fetching feature is designed with host allowlists, redirect rules, timeouts, response-size limits, and protection for private/local network addresses.
- Check whether the selected model declares the `tools` capability. Ollama Cloud models report their capabilities in `/api/tags`, and a cloud model may also request tools VoxComm does not offer (for example its own `open_url`); such calls are answered with `Tool is not enabled`. A declared capability does not prove that the model reliably selects tools or returns valid arguments; end-to-end tool-call tests are still required.
- Restrict shell access to named, application-defined operations. Never execute arbitrary model-generated shell strings. Destructive or privileged actions should require explicit user confirmation and run only as the unprivileged application user.
- Input injection is separate from keyboard listening. Access to `/dev/input` does not grant write access to `/dev/uinput`; local keyboard input is opt-in per profile and is sent only to the currently focused application.
- Each prompt profile enables its own subset of tools. Disabled tools are omitted from that profile's tool schema and rejected again if the model requests them.
- Informational preflight checks must not grant permissions, run shell commands, send keystrokes, or access websites as a side effect.

## Configuration and diagnostics

Settings are stored as TOML at `${XDG_CONFIG_HOME:-~/.config}/voxcomm/config.toml`. An existing `${XDG_CONFIG_HOME:-~/.config}/sc-ai-copilot/config.toml` is migrated there on first start. The file is written atomically with mode `0600` because it can contain `ollama_api_key`. Do not commit this file: it may contain private prompts, network addresses, an API key and device preferences.

Ollama Cloud needs an API key from <https://ollama.com/settings/keys>, entered under **Sprachmodell → API-Schlüssel** (visible only when *Anbieter* is *Ollama Cloud*) and stored as `ollama_api_key`. **Schlüssel prüfen** verifies it against `https://ollama.com/api/web_search`. The model list endpoint `/api/tags` is public, but `/api/chat` and `/api/web_search` require `Authorization: Bearer <key>`. Cloud model discovery caches its result in `${XDG_CACHE_HOME:-~/.cache}/voxcomm/ollama-cloud-models.json` for 24 hours; the cache is reused when Settings opens and the refresh button always re-fetches. When this provider is active, prompts, transcriptions and search queries are sent to ollama.com and leave the machine.

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
- `src/voxcomm/audio.py`: microphone capture and streaming PCM playback.
- `src/voxcomm/streaming.py`: sentence splitting so replies are spoken while the model is still writing.
- `src/voxcomm/ptt.py`: keyboard and controller-button push-to-talk.
- `src/voxcomm/input_control.py`: validated local keyboard taps via Linux uinput.
- `src/voxcomm/providers/wyoming.py`: Wyoming Whisper and Piper protocol.
- `src/voxcomm/providers/whisper_cpp.py`: whisper.cpp HTTP speech recognition.
- `src/voxcomm/providers/openai_tts.py`: OpenAI-compatible speech output.
- `src/voxcomm/providers/llm.py`: Ollama, Ollama Cloud and llama.cpp clients.
- `src/voxcomm/providers/cloud.py`: Ollama Cloud API key, model discovery cache and web search.
- `src/voxcomm/providers/web.py`: DDGS web search.
- `src/voxcomm/prompts.py`: prompt assembly and response-language selection.
- `src/voxcomm/config.py`: TOML configuration and persistence.
- `tests/`: unit and offscreen Qt lifecycle tests.
