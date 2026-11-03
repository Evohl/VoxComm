# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## Project Ownership

- VoxComm is a general desktop voice assistant; game-specific behavior belongs to the corresponding game skill.
- `src/voxcomm/gamelog` preserves legacy imports for the Star Citizen skill's game-log implementation.
- `src/voxcomm/config.py` owns base configuration and persistence; `src/voxcomm/profiles.py` resolves profile-specific settings and permissions.
- `src/voxcomm/app.py` owns the current Qt UI and application orchestration; provider modules own service integrations.
- Settings and tray menus use the active system Qt style; the transparent overlay retains its separate styling.
- The settings dialog uses the window title without a duplicate heading; native tabs fill the available width below the top margin and scroll pages preserve their style's tab background.
- Profile selection and sub-tabs align horizontally with the footer; profile sub-tabs do not repeat their tab names as page headings. Retain meaningful subsection headings.
- User-defined prompt text comes only from the user's profile configuration; never load or create separate persona or prompt files.
- Settings separate general services from profile settings, permissions, controller catalog, and Star Citizen log pages; global controller and announcement options remain visibly identified as applying to all profiles.
- Hide profile module pages when their permissions are disabled. The controller page provides collapsible manual access to all virtual buttons; direct presses bypass catalog preconditions. Delayed presses are cancellable and cancelled when settings close, the profile changes, the controller permission is disabled, or the panel collapses.
- Keep manual hold and delay controls compact; match the delay field's height to the numeric fields in general settings.
- The `Default` profile exposes the existing `ptt_key`; named profile hotkeys remain separate. Keep the `__global__` profile identifier and existing configuration keys unchanged.
- Each profile has an input language, one output language, and an automatic-read setting. Output language controls the model instruction, HTTP TTS language, and Piper voice filtering. Preserve legacy language settings and migrate using explicit response language, then TTS language, then voice or voice filter.
- Server API keys are optional and used as bearer credentials for HTTP LLM, whisper.cpp, and OpenAI-compatible TTS requests and discovery; Wyoming TCP has no API-key support. Preserve existing `ollama_api_key` values.
- General settings fit in the minimum dialog size; profile instructions, the controller catalog, and the Star Citizen event log expand with the window. Show game status outside the event log.
- The Star Citizen log view defaults to recognized events and offers an opt-in unfiltered view limited to 10,000 lines; the raw view must not alter event tracking or bypass profile permissions.
- While an existing profile permits Star Citizen Log, collect observed ship class identifiers and channel names locally for inspection in the log settings; do not retain player names. An existing profile with both Log and UEX permission may use its configured model to propose UEX mappings; label proposals as unverified, preserve manual corrections, and do not use them as confirmed game state.
- Profile hotkeys independently select their profile for each request; the profile selected in settings does not activate or disable other hotkeys.
- At startup, an existing UEX-authorized profile permits one background refresh of the shared local UEX snapshot when its last successful refresh is at least 24 hours old. UEX queries use the snapshot offline; failed refreshes retain the last successful data. Log-only permission never starts UEX access.
- Star Citizen audio-confirmation checkboxes apply to all profiles; automatic announcements use the first alphabetically named profile permitting Star Citizen Log, falling back to Default, independent of the settings selection. Reply text, voice, and language come from that profile.
- Unknown quantum signatures have a separately configurable announcement that includes the detected ship type when available; an unknown identity does not establish proximity or hostility.
- The ship-computer connection uses globally editable connect, disconnect, and ship keyword groups in Star Citizen Log settings; exiting the pilot seat also disconnects via the game log. Voice connection must not assert pilot-seat or ship-presence controller preconditions.
- Announcement replies are edited per event and profile; controller replies are edited in the profile catalog. Both accept the selectable TTS language codes, and existing reply overrides must be retained or migrated without loss.
- Profile permissions default to disabled. Preserve existing configuration identifiers and fail closed for unknown or disabled tools.
- PTT device loss cancels an active recording without submitting it; configuration serialization must round-trip valid Unicode TOML strings.
- The virtual-controller daemon holds an exclusive startup lock and only removes its own socket; timeouts do not establish that an existing socket is stale.

## Verification

- Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` for the complete test suite.

## Child DOX Index

- `src/voxcomm/skills/AGENTS.md`: optional assistant skills, controller and game-specific boundaries.
