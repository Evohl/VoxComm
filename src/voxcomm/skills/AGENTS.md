# Assistant Skills

## Purpose

- Isolate optional assistant capabilities from base settings, profile resolution, and provider transport.

## Ownership

- Each skill owns its domain-specific rules and integrations; common orchestration and profile persistence remain outside this package.
- New game-specific behavior belongs in that game's skill, not in unrelated games or the generic controller.

## Local Contracts

- Skills are independently enabled per profile. Unknown permission identifiers are preserved but do not grant executable tools.
- Existing configuration identifiers and tool-call names remain compatible unless a migration is explicitly implemented and tested.
- Keep tool permission checks at the dispatch boundary and test disabled capabilities as well as enabled ones.

## Verification

- Use temporary XDG directories as required by the root verification rule.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_controller_skill.py tests/test_star_citizen_skills.py tests/test_llm.py`

## Child DOX Index

- `controller/AGENTS.md`: virtual controller actions.
- `star_citizen/AGENTS.md`: Star Citizen log and UEX subskills.