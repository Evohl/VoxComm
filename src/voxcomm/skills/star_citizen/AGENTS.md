# Star Citizen Skill

## Purpose

- Provide Star Citizen-specific game-log context, announcements, and UEX reference data.

## Ownership

- `events.py` owns game-specific event text and replies; `tool.py` owns UEX tool policy, dispatch, and permission interpretation.
- `connection.py` recognizes explicit ship-computer connection and disconnection voice commands.
- `gamelog/` owns game-log facts, tracking, reading, executable discovery, and path resolution.
- `gamelog/ships.py` owns the independent incremental ship-name collector and its persistent local inventory.
- `gamelog/ship_matching.py` owns bounded UEX candidate selection and asynchronous profile-authorized model suggestions.
- `database.py` owns the UEX importer, refresh schedule, and atomic SQLite snapshots; `database_query.py` owns bounded offline searches and price joins; `uex.py` owns the compatible tool interface and result compaction.
- `preconditions.py` owns the legacy Star Citizen controller condition names and messages.

## Local Contracts

- `star_citizen_log` enables local log context and announcements; `star_citizen_uex` enables the UEX snapshot and its startup refresh.
- The legacy `star_citizen` permission enables both. The model-facing UEX tool retains the name `star_citizen`.
- UEX-only profiles must not read the game log; disabled UEX must not issue UEX requests.
- Startup refreshes run in the background only when an existing profile allows UEX and the last successful refresh is at least 24 hours old; settings changes alone do not trigger downloads. A failed or interrupted refresh keeps the prior snapshot.
- Model queries read one consistent local snapshot without network fallback, report its timestamp and freshness, and distinguish an unavailable snapshot from a search with no matches.
- UEX data is untrusted community-reported reference data, not verified live-server state. Trade comparisons are indicative gross price spreads, not verified routes, stock, or profit; keep terminal and price version provenance separate.
- LLM mappings are unverified suggestions, not game-log facts or automatic ship-name replacements. Offer only UEX snapshot candidates, validate returned IDs against those candidates, allow manual correction, and preserve manual choices against background updates.
- Automatic model matching requires one existing profile with both Log and UEX permission and a configured model. Send only collected class names and UEX candidates to that model, recheck permissions before storing results, and never initiate UEX downloads from matching.
- Legacy `voxcomm.gamelog` imports and submodule patch sites must resolve to the same objects as `voxcomm.skills.star_citizen.gamelog`.
- Legacy `voxcomm.providers.uex` resolves to the same module as `voxcomm.skills.star_citizen.uex`.
- Game-log polling and event draining are synchronized; `GameLogTracker.facts` returns a detached snapshot. Leaving a ship revokes a confirmed pilot seat.
- Ship-computer connection is a logical voice-controlled state requiring Star Citizen Log permission and a running game; it never confirms pilot-seat or ship-presence preconditions. An explicit pilot-seat exit in the log clears this state and remains announceable even when seat entry was not detected.
- Voice commands require a whole-word match from the global ship keyword list and one from the connect or disconnect list. The lists are comma-separated, editable in settings, and may be empty; reject negated, questioned, compound, or conflicting commands. A generic connection noun does not conflict with an explicit disconnect keyword.
- Navigation is attributed only to the exact ship instance last identified by a local pilot-seat exit while its ship channel remains active; clear the association on channel exit, ship change, game stop, or log reset. This is not proof of current control.
- Quantum arrivals without a matching known instance produce an independently switchable unknown-signature announcement with the log's ship type; they do not mutate own navigation. An unknown signature may be the player's own ship and does not prove distance, ownership, or hostility. Preserve each queued announcement's ship type and profile-specific reply overrides.
- Pilot-seat entry never automatically announces a connection; explicit voice connection uses the profile's pilot-entry reply. Pilot-seat exit and voice disconnection use the pilot-exit reply. Preserve profile-specific reply overrides.
- Raw log viewing uses an independent reader, handles truncation and replacement, and bounds its initial history and buffered bytes without affecting event detection.
- The ship collector runs only through the existing profile-authorized log polling path. It scans from the start in bounded chunks, handles partial lines and log replacement, and persists unique raw classes and channel names separately without instance IDs or player names. Only recognized vehicle and channel log patterns are collected.
- Keep bounded UEX result fields relevant to location, version, price, and route data; resolve exact commodity names before limiting ambiguous matches.
- Game-log announcement overrides are profile- and language-specific; all selectable TTS language codes have translated defaults, with English fallback for unsupported codes.

## Verification

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_star_citizen_skills.py tests/test_gamelog.py tests/test_star_citizen_log_ships.py tests/test_star_citizen_ship_matching.py tests/test_star_citizen_gamelog_module.py tests/test_star_citizen_tracker_safety.py tests/test_uex.py tests/test_star_citizen_uex_module.py tests/test_star_citizen_uex_results.py tests/test_star_citizen_database.py tests/test_star_citizen_database_query.py tests/test_llm.py tests/test_app_lifecycle.py`

## Child DOX Index