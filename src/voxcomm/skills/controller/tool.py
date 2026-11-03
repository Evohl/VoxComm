from __future__ import annotations

from voxcomm.action_catalog import REQUIREMENT_TEXT, UNKNOWN_STATE_TEXT, action_reply, reply_language_tag, unmet_requirement
from voxcomm.i18n import tr_for_language


INPUT_CONTROL_TOOL = {
    "name": "input_control",
    "description": (
        "Execute exactly one configured VoxComm controller action from the active profile. Never invent buttons "
        "or use keyboard input. If the requested action is not configured or is "
        "ambiguous, ask one short clarifying question."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "A configured action name from the active profile"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _action_keyword_summary(actions: list[dict[str, object]]) -> str:
    return "; ".join(
        f"{action['name']} = " + " + ".join(" | ".join(group) for group in action["keywords"])
        for action in actions
    )


def _input_control_tool(actions: list[dict[str, object]]) -> dict:
    tool = {
        "name": INPUT_CONTROL_TOOL["name"],
        "description": INPUT_CONTROL_TOOL["description"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [action["name"] for action in actions],
                    "description": (
                        "Choose one configured controller action by name. Keyword groups per action (+ separates "
                        "groups, | separates alternatives). An action matches when every group has an alternative in "
                        "the request, in any language or inflection: " + _action_keyword_summary(actions)
                    ),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    }
    return tool


def _precondition_message(actions: tuple[dict[str, object], ...], state: dict[str, bool | None],
                          reply_language: str = "") -> str:
    for action in actions:
        requirement = unmet_requirement(action, state)
        if requirement is not None:
            if state.get(requirement[0]) is None:
                return tr_for_language(UNKNOWN_STATE_TEXT[requirement[0]], reply_language_tag(reply_language))
            return tr_for_language(REQUIREMENT_TEXT[requirement], reply_language_tag(reply_language))
    return ""


def _press_actions(actions: tuple[dict[str, object], ...], reply_language: str, *, send, cancelled=lambda: False) -> str:
    replies = []
    for action in actions:
        if cancelled():
            return ""
        try:
            send(action["button"], hold=bool(action["hold"]))
        except Exception as error:
            return tr_for_language("Captain, controller input was not sent: {error}",
                                   reply_language_tag(reply_language), error=str(error)[:300])
        replies.append(action_reply(action, reply_language))
    if cancelled():
        return ""
    return " ".join(replies)


def execute_input_control(action_name: object, actions: list[dict[str, object]], reply_language: str, *, send) -> dict:
    action = next((item for item in actions if item["name"] == action_name), None)
    if action is None:
        return {"ok": False, "error": "Choose a configured controller action"}
    try:
        send(action["button"], hold=bool(action.get("hold")))
        return {"ok": True, "action": action["name"], "reply": action_reply(action, reply_language)}
    except Exception as error:
        return {"ok": False, "error": str(error)[:300]}