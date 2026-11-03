# Controller Skill

## Purpose

- Offer configured virtual-controller actions independently of a specific game.

## Ownership

- `tool.py` owns action tool definitions, action execution, and precondition reporting.
- The existing action catalog and virtual device remain in `voxcomm.action_catalog` and `voxcomm.gamepad`.

## Local Contracts

- Only configured controller actions can be sent; never invent buttons or send keyboard input for AI controller actions.
- Preserve tap and hold behavior, profile-local catalogs, and state preconditions.
- Catalog replies support the selectable TTS language codes; legacy reply overrides must remain effective or be migrated into their catalog entries.
- Scripted controller warnings use the requesting profile's output language, independently of the UI language. Built-in translations are available in English and German; other languages fall back to English.
- Recognized questions and negations must not send controller input, including through model tool calls; lexical detection is not a complete intent classifier.
- Check cancellation before model tool dispatch and between scripted actions. An input already sent cannot be recalled.
- Pending model clarifications are scoped to the requesting profile and action catalog; a recognized negation clears the pending clarification.
- Legacy catalog syntax uses Star Citizen precondition definitions; do not assume these describe another game's state.

## Verification

- Use temporary XDG directories as required by the root verification rule.
- `.venv/bin/python -m pytest -q tests/test_controller_skill.py tests/test_action_catalog.py tests/test_llm.py`

## Child DOX Index