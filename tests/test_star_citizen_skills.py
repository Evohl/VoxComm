import json
import threading
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from voxcomm.app import Overlay, SettingsDialog
from voxcomm.config import AppConfig, load_config, save_config
from voxcomm.gamelog.facts import GameFacts, SHIP_ENTERED
from voxcomm.i18n import set_language
from voxcomm.providers.llm import _execute_tool, chat
from voxcomm.skills.star_citizen.tool import allows_game_log, allows_uex


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("tools", "log_enabled", "uex_enabled"),
    [
        ([], False, False),
        (["star_citizen"], True, True),
        (["star_citizen_log"], True, False),
        (["star_citizen_uex"], False, True),
        (["star_citizen_log", "star_citizen_uex"], True, True),
        (["unknown_skill"], False, False),
    ],
)
def test_star_citizen_permission_combinations(tools, log_enabled, uex_enabled):
    assert allows_game_log(tools) is log_enabled
    assert allows_uex(tools) is uex_enabled


@pytest.mark.parametrize(
    ("tools", "log_enabled", "uex_enabled"),
    [
        ([], False, False),
        (["star_citizen"], True, True),
        (["star_citizen_log"], True, False),
        (["star_citizen_uex"], False, True),
        (["star_citizen_log", "star_citizen_uex"], True, True),
    ],
)
def test_settings_permissions_round_trip(application, tmp_path, tools, log_enabled, uex_enabled):
    config = AppConfig(enabled_tools={"__global__": tools} if tools else {})
    dialog = SettingsDialog(config)
    assert dialog.star_citizen.isChecked() is log_enabled
    assert dialog.star_citizen_uex.isChecked() is uex_enabled
    updated = dialog.apply(config)
    path = tmp_path / "config.toml"
    save_config(updated, path)
    loaded = load_config(path)
    reopened = SettingsDialog(loaded)
    assert reopened.star_citizen.isChecked() is log_enabled
    assert reopened.star_citizen_uex.isChecked() is uex_enabled
    assert loaded.enabled_tools.get("__global__", []) == tools
    dialog.close()
    reopened.close()


def test_settings_switch_profiles_and_preserve_unknown_permissions(application, tmp_path):
    config = AppConfig(
        special_prompts={"Log": "", "UEX": "", "Both": "", "Neither": ""},
        enabled_tools={
            "__global__": ["star_citizen", "future_global"],
            "Log": ["star_citizen_log", "web_search", "future_log"],
            "UEX": ["star_citizen_uex", "input_control", "future_uex"],
            "Both": ["star_citizen_log", "star_citizen_uex"],
            "Neither": ["future_neither"],
            "Unlisted": ["future_unlisted"],
        },
    )
    dialog = SettingsDialog(config)
    for profile, expected in (
        ("Log", (True, False, True, False)),
        ("UEX", (False, True, False, True)),
        ("Both", (True, True, False, False)),
        ("Neither", (False, False, False, False)),
        ("__global__", (True, True, False, False)),
    ):
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData(profile))
        assert tuple(check.isChecked() for check in (
            dialog.star_citizen, dialog.star_citizen_uex, dialog.web_search, dialog.input_control,
        )) == expected
    dialog.star_citizen_uex.setChecked(False)
    dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("UEX"))
    dialog.star_citizen.setChecked(True)
    updated = dialog.apply(config)
    path = tmp_path / "config.toml"
    save_config(updated, path)
    loaded = load_config(path)
    assert loaded.enabled_tools == {
        "__global__": ["star_citizen_log", "future_global"],
        "Log": ["web_search", "star_citizen_log", "future_log"],
        "UEX": ["input_control", "star_citizen_log", "star_citizen_uex", "future_uex"],
        "Both": ["star_citizen_log", "star_citizen_uex"],
        "Neither": ["future_neither"],
        "Unlisted": ["future_unlisted"],
    }
    reopened = SettingsDialog(loaded)
    assert reopened.star_citizen.isChecked()
    assert reopened.star_citizen_uex.isChecked()
    reopened.special_profile.setCurrentIndex(reopened.special_profile.findData("__global__"))
    assert reopened.star_citizen.isChecked()
    assert not reopened.star_citizen_uex.isChecked()
    dialog.close()
    reopened.close()


def test_settings_german_labels_and_tooltips(application):
    set_language("de")
    try:
        dialog = SettingsDialog(AppConfig(ui_language="de"))
        assert dialog.star_citizen.text() == "Star Citizen Log"
        assert dialog.star_citizen_uex.text() == "Star Citizen UEX"
        assert "Game.log" in dialog.star_citizen.toolTip()
        assert "Erlaubt diesem Profil" in dialog.star_citizen_uex.toolTip()
        dialog.close()
    finally:
        set_language("en")


def test_settings_log_viewer_only_polls_when_log_is_enabled(application):
    dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_uex"]}))
    dialog._game_log = Mock()
    dialog._game_log.facts = GameFacts()
    dialog._game_log.take_events.return_value = []
    dialog._refresh_game_log()
    dialog._game_log.poll.assert_not_called()
    dialog.star_citizen.setChecked(True)
    dialog._refresh_game_log()
    dialog._game_log.poll.assert_called_once_with()
    dialog.star_citizen.setChecked(False)
    dialog._refresh_game_log()
    dialog._game_log.poll.assert_called_once_with()
    dialog.close()


@pytest.mark.parametrize(
    ("log_enabled", "uex_enabled", "expected"),
    [
        (False, False, []),
        (True, False, ["star_citizen_log"]),
        (False, True, ["star_citizen_uex"]),
        (True, True, ["star_citizen"]),
    ],
)
def test_changing_legacy_permissions_removes_unchecked_access(application, log_enabled, uex_enabled, expected):
    config = AppConfig(enabled_tools={"__global__": ["star_citizen"]})
    dialog = SettingsDialog(config)
    dialog.star_citizen.setChecked(log_enabled)
    dialog.star_citizen_uex.setChecked(uex_enabled)
    assert dialog.apply(config).enabled_tools.get("__global__", []) == expected
    dialog.close()


@pytest.mark.parametrize(
    ("tools", "offered"),
    [
        ([], []),
        (["star_citizen_log"], []),
        (["star_citizen_uex"], ["star_citizen"]),
        (["star_citizen"], ["star_citizen"]),
        (["star_citizen_log", "star_citizen_uex", "unknown_skill"], ["star_citizen"]),
        (["web_search", "unknown_skill"], ["web_search"]),
        (["star_citizen_uex", "web_search"], ["star_citizen"]),
    ],
)
def test_chat_offers_only_authorized_tools(tools, offered):
    with patch("voxcomm.providers.llm._request", return_value={"content": "Done"}) as request:
        with patch("voxcomm.providers.llm.query_uex") as uex, patch("voxcomm.providers.llm.search_web") as search:
            chat("ollama", "http://localhost", "model", "Find Levski", enabled_tools=tools)
    definitions = request.call_args.args[4] or []
    assert [tool["name"] for tool in definitions] == offered
    uex.assert_not_called()
    search.assert_not_called()


@pytest.mark.parametrize("tools", [[], ["star_citizen_log"], ["unknown_skill"]])
def test_untrusted_uex_call_cannot_bypass_permission(tools):
    call = {"function": {"name": "star_citizen", "arguments": {"operation": "terminals", "query": "Levski"}}}
    uex = Mock()
    result = json.loads(_execute_tool(call, set(tools), uex=uex))
    assert result == {"error": "Tool is not enabled"}
    uex.assert_not_called()


@pytest.mark.parametrize("tools", [["star_citizen"], ["star_citizen_uex"]])
def test_legacy_and_granular_uex_calls_execute(tools):
    call = {"function": {"name": "star_citizen", "arguments": {"operation": "terminals", "query": "Levski"}}}
    uex = Mock(return_value={"status": "ok"})
    assert json.loads(_execute_tool(call, set(tools), uex=uex)) == {"status": "ok"}
    uex.assert_called_once_with("terminals", "Levski", "")


def test_chat_rejects_unoffered_uex_call_with_other_tool_enabled():
    call = {"function": {"name": "star_citizen", "arguments": {"operation": "terminals", "query": "Levski"}}}
    requests = []

    def request(*args, **kwargs):
        requests.append(deepcopy(args[3]))
        return {"tool_calls": [call]} if len(requests) == 1 else {"content": "Done"}

    with patch("voxcomm.providers.llm._request", side_effect=request), patch("voxcomm.providers.llm.query_uex") as uex:
        chat("ollama", "http://localhost", "model", "Find Levski", enabled_tools=["web_search", "star_citizen_log"])
    assert json.loads(requests[1][-1]["content"]) == {"error": "Tool is not enabled"}
    uex.assert_not_called()


def test_chat_runs_granular_uex_tool_without_web_search():
    call = {"function": {"name": "star_citizen", "arguments": {"operation": "terminals", "query": "Levski"}}}
    requests = []

    def request(*args, **kwargs):
        requests.append(deepcopy(args[3]))
        return {"tool_calls": [call]} if len(requests) == 1 else {"content": "Found"}

    with (patch("voxcomm.providers.llm._request", side_effect=request),
          patch("voxcomm.providers.llm.query_uex", return_value={"status": "ok"}) as uex,
          patch("voxcomm.providers.llm.search_web") as search):
        assert chat("ollama", "http://localhost", "model", "Find Levski",
                    enabled_tools=["star_citizen_uex", "web_search"]) == "Found"
    uex.assert_called_once_with("terminals", "Levski", "")
    search.assert_not_called()
    assert json.loads(requests[1][-1]["content"]) == {"status": "ok"}


def test_controller_web_and_uex_tool_selection_are_independent():
        catalog = "Lights On => lights => Button1 => {EN} Lights on"
        with (patch("voxcomm.providers.llm._request", return_value={"content": "Done"}) as request,
                    patch("voxcomm.providers.llm.thinking_options", return_value=[]),
                    patch("voxcomm.providers.llm.search_web") as search):
                chat("ollama", "http://localhost", "model", "Find Levski",
                         enabled_tools=["input_control", "star_citizen_uex", "web_search", "unknown_skill"],
                         input_actions=catalog, controller_mode="llm")
        assert [tool["name"] for tool in request.call_args.args[4]] == ["star_citizen", "input_control"]
        search.assert_not_called()


@pytest.mark.parametrize("tools", [[], ["star_citizen_uex"], ["unknown_skill"]])
def test_active_profile_without_log_never_polls_or_announces(tools):
    overlay = Mock()
    overlay.config = AppConfig(special_prompts={"Other": ""}, active_special_prompt="Other",
                               enabled_tools={"__global__": ["star_citizen_log"], "Other": tools})
    overlay._active_profile.return_value = "Other"
    Overlay._poll_game_log(overlay)
    overlay._game_log.poll.assert_not_called()
    overlay._start_speak.assert_not_called()


@pytest.mark.parametrize("permission", ["star_citizen", "star_citizen_log"])
def test_active_log_permission_polls_and_announces(permission):
    overlay = Mock()
    overlay.config = AppConfig(enabled_tools={"__global__": [permission]})
    overlay._active_profile.return_value = "__global__"
    overlay._active_profile_voice.return_value = "voice"
    overlay._response_language_for_profile.return_value = "English"
    overlay._game_log.take_events.return_value = [SHIP_ENTERED]
    overlay._game_log.facts.ship = "Ship"
    overlay._speech_jobs = []
    Overlay._poll_game_log(overlay)
    overlay._game_log.poll.assert_called_once_with()
    overlay._start_speak.assert_called_once()


@pytest.mark.parametrize(
    ("tools", "log_enabled"),
    [([], False), (["star_citizen_uex"], False), (["star_citizen_log"], True), (["star_citizen"], True)],
)
def test_capture_uses_log_only_with_log_permission(application, tools, log_enabled):
    config = AppConfig(enabled_tools={"__global__": tools}, profile_models={"__global__": "model"})
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        tracker = Mock()
        tracker.prompt_context.return_value = "PRIVATE GAME LOG CONTEXT"
        tracker.facts = GameFacts(in_ship=True, pilot_seat=True)
        overlay._game_log = tracker
        with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
              patch("voxcomm.app.transcribe_sync", return_value="Hello"),
              patch("voxcomm.app.chat", return_value="Done") as chat_mock):
            overlay._capture(threading.Event())
        request = chat_mock.call_args.kwargs
        assert ("PRIVATE GAME LOG CONTEXT" in request["system_prompt"]) is log_enabled
        assert request["game_state"] == ({"in_ship": True, "pilot_seat": True} if log_enabled else None)
        assert tracker.prompt_context.call_count == int(log_enabled)
        overlay.listener.stop()
        overlay.tray_icon.hide()
        overlay.deleteLater()


def test_capture_resolves_log_permission_from_requested_profile(application):
    config = AppConfig(
        special_prompts={"UEX": ""},
        active_special_prompt="UEX",
        enabled_tools={"__global__": ["star_citizen_log"], "UEX": ["star_citizen_uex"]},
    )
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay._game_log = Mock(facts=GameFacts(in_ship=True, pilot_seat=True))
        overlay._game_log.prompt_context.return_value = "PRIVATE GAME LOG CONTEXT"
        with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
              patch("voxcomm.app.transcribe_sync", return_value="Hello"),
              patch("voxcomm.app.chat", return_value="Done") as chat_mock):
            overlay._capture(threading.Event(), "UEX")
            overlay._capture(threading.Event(), "")
        first, second = [call.kwargs for call in chat_mock.call_args_list]
        assert first["game_state"] is None
        assert "PRIVATE GAME LOG CONTEXT" not in first["system_prompt"]
        assert second["game_state"] == {"in_ship": True, "pilot_seat": True}
        assert "PRIVATE GAME LOG CONTEXT" in second["system_prompt"]
        overlay._game_log.prompt_context.assert_called_once_with()
        overlay.listener.stop()
        overlay.tray_icon.hide()
        overlay.deleteLater()


def test_controller_requires_known_log_state_without_permission(application):
    config = AppConfig(
        enabled_tools={"__global__": ["input_control", "star_citizen_uex"]},
        controller_mode="script",
        input_action_catalogs={"__global__": "[pilot seat yes]\nQuantum Mode => quantum => Button5 => {EN} Done"},
    )
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay._game_log = Mock(facts=GameFacts(in_ship=True, pilot_seat=True))
        with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
              patch("voxcomm.app.transcribe_sync", return_value="quantum"),
              patch("voxcomm.providers.llm.press_gamepad_button") as press):
            overlay._capture(threading.Event())
        press.assert_not_called()
        overlay._game_log.prompt_context.assert_not_called()
        assert "pilot" in overlay._last_response.lower()
        overlay.listener.stop()
        overlay.tray_icon.hide()
        overlay.deleteLater()