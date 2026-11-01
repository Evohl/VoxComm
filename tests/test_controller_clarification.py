from unittest.mock import patch

from voxcomm.providers.llm import _CLARIFICATIONS, chat


CATALOG = (
    "Lights On => lights + on => Button1 => {EN} Lights on\n"
    "Lights Off => lights + off => Button2 => {EN} Lights off"
)


def test_clarifications_are_scoped_to_profile_and_negation_clears_them():
    _CLARIFICATIONS.clear()
    with patch("voxcomm.providers.llm._request", return_value={"content": "On or off?"}) as request:
        chat("ollama", "http://localhost", "model", "lights", enabled_tools=["input_control"],
             controller_mode="llm", input_actions=CATALOG, profile_key="one")
        chat("ollama", "http://localhost", "model", "lights", enabled_tools=["input_control"],
             controller_mode="llm", input_actions=CATALOG, profile_key="two")
        assert "Earlier request:" not in request.call_args.args[3][-1]["content"]
        chat("ollama", "http://localhost", "model", "nein", enabled_tools=["input_control"],
             controller_mode="llm", input_actions=CATALOG, profile_key="one")
        assert ("one", CATALOG) not in _CLARIFICATIONS
        assert ("two", CATALOG) in _CLARIFICATIONS
    _CLARIFICATIONS.clear()