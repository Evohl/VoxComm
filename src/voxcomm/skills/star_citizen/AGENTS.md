# Star Citizen Skill

## Purpose

- Provide Star Citizen-specific game-log context, announcements, and UEX reference data.

## Ownership

- `events.py` owns game-specific event text and replies; `tool.py` owns UEX tool policy, dispatch, and permission interpretation.
- `gamelog/` owns game-log facts, tracking, reading, executable discovery, and path resolution; `uex.py` owns UEX HTTP requests and result selection.
- `preconditions.py` owns the legacy Star Citizen controller condition names and messages.

## Local Contracts

- `star_citizen_log` enables local log context and announcements; `star_citizen_uex` enables network access to UEX.
- The legacy `star_citizen` permission enables both. The model-facing UEX tool retains the name `star_citizen`.
- UEX-only profiles must not read the game log; disabled UEX must not issue UEX requests.
- UEX data is community-reported reference data, not verified live-server state.
- Legacy `voxcomm.gamelog` imports and submodule patch sites must resolve to the same objects as `voxcomm.skills.star_citizen.gamelog`.
- Legacy `voxcomm.providers.uex` resolves to the same module as `voxcomm.skills.star_citizen.uex`.
- Game-log polling and event draining are synchronized; `GameLogTracker.facts` returns a detached snapshot. Leaving a ship revokes a confirmed pilot seat.
- Raw log viewing uses an independent reader, handles truncation and replacement, and bounds its initial history and buffered bytes without affecting event detection.
- Keep bounded UEX result fields relevant to location, version, price, and route data; resolve exact commodity names before limiting ambiguous matches.
- Game-log announcement overrides are profile- and language-specific; all selectable TTS language codes have translated defaults, with English fallback for unsupported codes.

## Verification

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_star_citizen_skills.py tests/test_gamelog.py tests/test_star_citizen_gamelog_module.py tests/test_star_citizen_tracker_safety.py tests/test_uex.py tests/test_star_citizen_uex_module.py tests/test_star_citizen_uex_results.py tests/test_llm.py`

## Child DOX Index