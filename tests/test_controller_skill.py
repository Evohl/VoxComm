import threading
import unittest
from unittest.mock import Mock, call

from voxcomm.action_catalog import parse_action_catalog
from voxcomm.i18n import current_language, set_language, tr
from voxcomm.skills.controller.tool import (
    INPUT_CONTROL_TOOL,
    _input_control_tool,
    _precondition_message,
    _press_actions,
    execute_input_control,
)


class ControllerSkillTests(unittest.TestCase):
    def setUp(self):
        self.actions = parse_action_catalog(
            "Lights On => lights|lamps + on => Button1 => {EN} Lights on. | {DE} Licht an.\n"
            "[pilot seat yes]\n"
            "Boost On => boost on => Button2 hold => {EN} Boost on. | {DE} Schub an."
        )
        self.send = Mock()

    def test_schema_lists_configured_actions_and_keyword_groups(self):
        tool = _input_control_tool(self.actions)
        self.assertEqual(tool["name"], INPUT_CONTROL_TOOL["name"])
        action = tool["parameters"]["properties"]["action"]
        self.assertEqual(action["enum"], ["Lights On", "Boost On"])
        self.assertIn("Lights On = lights | lamps + on", action["description"])

    def test_tool_executes_only_configured_action_and_returns_localized_reply(self):
        result = execute_input_control("Boost On", self.actions, "German", send=self.send)
        self.assertEqual(result, {"ok": True, "action": "Boost On", "reply": "Schub an."})
        self.send.assert_called_once_with("Button2", hold=True)

    def test_tool_rejects_unknown_action_without_sending(self):
        result = execute_input_control("Unknown", self.actions, "English", send=self.send)
        self.assertEqual(result, {"ok": False, "error": "Choose a configured controller action"})
        self.send.assert_not_called()

    def test_tool_reports_send_errors(self):
        self.send.side_effect = RuntimeError("x" * 400)
        result = execute_input_control("Lights On", self.actions, "English", send=self.send)
        self.assertEqual(result, {"ok": False, "error": "x" * 300})

    def test_preconditions_distinguish_unknown_contradicted_and_satisfied(self):
        actions = tuple(self.actions)
        self.assertEqual(
            _precondition_message(actions, {}),
            "Captain, pilot-seat status is unknown. I am waiting for Star Citizen telemetry before enabling ship controls.",
        )
        self.assertEqual(
            _precondition_message(actions, {"pilot_seat": False}),
            "Captain, ship controls are unavailable until you take the pilot seat.",
        )
        self.assertEqual(_precondition_message(actions, {"pilot_seat": True}), "")
        self.send.assert_not_called()

    def test_precondition_language_is_independent_of_ui_language(self):
        set_language("de")
        self.addCleanup(set_language, "en")
        self.assertEqual(
            _precondition_message(tuple(self.actions), {"pilot_seat": False}, "English"),
            "Captain, ship controls are unavailable until you take the pilot seat.",
        )
        self.assertEqual(current_language(), "de")
        self.assertEqual(tr("Settings"), "Einstellungen")

        set_language("en")
        self.assertEqual(
            _precondition_message(tuple(self.actions), {"pilot_seat": False}, "German"),
            "Captain, Schiffssteuerung nicht verfügbar. Nimm zuerst den Pilotensitz ein.",
        )
        self.assertIn("status is unknown", _precondition_message(tuple(self.actions), {}, "French"))

    def test_script_actions_send_in_order_and_join_replies(self):
        result = _press_actions(tuple(self.actions), "English", send=self.send)
        self.assertEqual(result, "Lights on. Boost on.")
        self.assertEqual(self.send.call_args_list, [call("Button1", hold=False), call("Button2", hold=True)])

    def test_script_actions_stop_on_send_failure(self):
        self.send.side_effect = RuntimeError("unavailable")
        result = _press_actions(tuple(self.actions), "English", send=self.send)
        self.assertEqual(result, "Captain, controller input was not sent: unavailable")
        self.send.assert_called_once_with("Button1", hold=False)

    def test_send_failure_uses_reply_language_not_ui_language(self):
        set_language("de")
        self.addCleanup(set_language, "en")
        self.send.side_effect = RuntimeError("unavailable")
        self.assertEqual(_press_actions(tuple(self.actions), "English", send=self.send),
                         "Captain, controller input was not sent: unavailable")
        self.assertEqual(current_language(), "de")

    def test_script_actions_stop_when_cancelled_during_first_press(self):
        cancelled = threading.Event()
        self.send.side_effect = lambda *args, **kwargs: cancelled.set()
        result = _press_actions(tuple(self.actions), "English", send=self.send, cancelled=cancelled.is_set)
        self.assertEqual(result, "")
        self.send.assert_called_once_with("Button1", hold=False)


if __name__ == "__main__":
    unittest.main()