import threading
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from voxcomm.config import AppConfig
from voxcomm.skills.star_citizen.connection import ship_connection_command
from voxcomm.skills.star_citizen.gamelog import facts as facts_module
from voxcomm.skills.star_citizen.gamelog.facts import GameLogTracker, PILOT_LEFT


@pytest.mark.parametrize("text", ["connect ship", "Connect to my ship.", "bitte verbinde mich mit dem Schiff"])
def test_connect_command(text):
    assert ship_connection_command(text) is True


@pytest.mark.parametrize("text", ["disconnect ship", "disconnect from the ship", "trenne mich vom Schiff"])
def test_disconnect_command(text):
    assert ship_connection_command(text) is False


@pytest.mark.parametrize("text", ["Verbindung zum Schiffscomputer herstellen", "computer connect", "connetion raumschiff"])
def test_keyword_groups_connect_in_any_order(text):
    assert ship_connection_command(text) is True


@pytest.mark.parametrize("connect", ["verbinde", "verbindung", "connect", "connection", "connetion"])
@pytest.mark.parametrize("target", ["ship", "schiff", "raumschiff", "schiffscomputer", "computer"])
def test_requested_default_keywords(connect, target):
    assert ship_connection_command(f"{connect} {target}") is True


def test_custom_keywords_and_ambiguous_commands():
    assert ship_connection_command("koppeln bordnetz", "koppeln", "entkoppeln", "bordnetz") is True
    assert ship_connection_command("entkoppeln bordnetz", "koppeln", "entkoppeln", "bordnetz") is False
    assert ship_connection_command("verbinde und trenne schiff") is None
    assert ship_connection_command("verbinde trenne schiff") is None
    assert ship_connection_command("disconnect spaceship") is None
    assert ship_connection_command("Verbindung zum Schiff trennen") is False
    assert ship_connection_command("disconnect ship connection") is False
    assert ship_connection_command("verbinde verbindung trenne schiff") is None
    assert ship_connection_command("connect ship", "", "disconnect", "ship") is None
    assert ship_connection_command("verbindung schiff", "verbindung", "verbindung", "schiff") is None


def test_keyword_settings_apply_and_round_trip(tmp_path):
    from voxcomm.app import SettingsDialog
    from voxcomm.config import load_config, save_config

    app = QApplication.instance() or QApplication([])
    config = AppConfig(enabled_tools={"__global__": ["star_citizen_log"]})
    dialog = SettingsDialog(config)
    try:
        dialog.ship_connect_keywords.setText("koppeln, verbinden")
        dialog.ship_disconnect_keywords.setText("entkoppeln")
        dialog.ship_target_keywords.setText("bordnetz, schiff")
        dialog.apply(config)
        path = tmp_path / "config.toml"
        save_config(config, path)
        loaded = load_config(path)
        assert ship_connection_command("koppeln bordnetz", loaded.ship_connect_keywords,
                                       loaded.ship_disconnect_keywords, loaded.ship_target_keywords) is True
        assert ship_connection_command("entkoppeln bordnetz", loaded.ship_connect_keywords,
                                       loaded.ship_disconnect_keywords, loaded.ship_target_keywords) is False
        reopened = SettingsDialog(loaded)
        try:
            assert reopened.ship_connect_keywords.text() == "koppeln, verbinden"
            assert reopened.ship_disconnect_keywords.text() == "entkoppeln"
            assert reopened.ship_target_keywords.text() == "bordnetz, schiff"
        finally:
            reopened.close()
    finally:
        dialog.close()


@pytest.mark.parametrize("text", ["do not connect ship", "verbinde nicht mit dem Schiff", "can you connect ship?",
                                  "connect ship and activate weapons", "disconnect keyboard", "ship status"])
def test_unrelated_or_ambiguous_commands_do_not_change_connection(text):
    assert ship_connection_command(text) is None


def test_log_exit_disconnects_even_without_reliable_pilot_entry(monkeypatch, tmp_path):
    join = "You have joined channel 'Crusader C1 Spirit : Player'"
    exit_line = "ClearDriver: Local client node [99] releasing control token for 'CRUS_Spirit_C1_123456' [123456]"
    reader = Mock(read_new_lines=Mock(side_effect=[[join, exit_line], [exit_line]]))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    tracker.poll()
    assert tracker.facts.pilot_seat is False
    assert tracker.set_ship_connection(True) == "Crusader C1 Spirit"
    assert tracker.connected_ship == (True, "Crusader C1 Spirit")
    assert tracker.facts.pilot_seat is True

    tracker.poll()

    assert tracker.connected_ship == (False, "")
    assert tracker.take_events() == [PILOT_LEFT]
    assert tracker.facts.pilot_seat is False


def test_connection_is_rejected_if_the_game_is_not_running():
    tracker = GameLogTracker(executable_finder=lambda: None)
    tracker.poll()
    assert tracker.set_ship_connection(True) is None
    assert tracker.connected_ship == (False, "")
    assert tracker.facts.pilot_seat is None


def test_voice_connection_recognizes_matching_arrival_without_ship_channel(monkeypatch, tmp_path):
    from voxcomm.skills.star_citizen.gamelog.facts import QUANTUM_ARRIVED

    exit_line = "ClearDriver: Local client node [99] releasing control token for 'CRUS_Spirit_C1_912124980711' [912124980711]"
    arrival = (
        "<2026-11-03T12:09:20.286Z> [Notice] <Quantum Drive Arrived - Arrived at Final Destination> "
        "[ItemNavigation][CL][2416] | NOT AUTH | CRUS_Spirit_C1_912124980711[912124980711]|"
        "CSCItemNavigation::OnQuantumDriveArrived|Quantum Drive has arrived at final destination [Team_CGP4][QuantumTravel]"
    )
    reader = Mock(read_new_lines=Mock(side_effect=[[exit_line], [arrival]]))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    tracker.poll()
    assert tracker.facts.in_ship is None
    assert tracker.facts.ship_instance == "912124980711"
    tracker.set_ship_connection(True)

    tracker.poll()

    assert tracker.take_events() == [QUANTUM_ARRIVED]
    assert tracker.facts.in_ship is None


def test_connection_enables_pilot_seat_precondition_without_asserting_ship_presence(monkeypatch, tmp_path):
    from voxcomm.action_catalog import parse_action_catalog
    from voxcomm.skills.controller.tool import _precondition_message

    reader = Mock(read_new_lines=Mock(return_value=[]))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    actions = tuple(parse_action_catalog(
        "[pilot seat yes]\n"
        "Boost On => boost on => Button2 => {EN} Boost on. | {DE} Schub an."
    ))
    tracker.poll()
    assert _precondition_message(actions, {"pilot_seat": tracker.facts.pilot_seat})

    assert tracker.set_ship_connection(True) == ""
    assert tracker.facts.in_ship is None
    assert not _precondition_message(actions, {"pilot_seat": tracker.facts.pilot_seat})
    assert "Pilotensitz: ja" in tracker.prompt_context()
    assert tracker.take_events() == []

    tracker.set_ship_connection(False)
    assert tracker.facts.in_ship is None
    assert tracker.facts.pilot_seat is False
    assert _precondition_message(actions, {"pilot_seat": tracker.facts.pilot_seat})
    assert tracker.take_events() == []


def test_voice_connection_then_log_exit_is_announced_after_speech(monkeypatch, tmp_path):
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    join = "You have joined channel 'C1 Spirit : Player'"
    exit_line = "ClearDriver: Local client node [99] releasing control token for 'CRUS_Spirit_C1_123456' [123456]"
    reader = Mock(read_new_lines=Mock(side_effect=[[join], [exit_line], []]))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    config = AppConfig(special_prompts={"Star Citizen": ""},
                       enabled_tools={"Star Citizen": ["star_citizen_log"]},
                       profile_output_languages={"Star Citizen": "English"})
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay._game_log = tracker
        try:
            with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                  patch("voxcomm.app.transcribe_sync", return_value="connect ship"),
                  patch.object(overlay, "_start_speak") as speak):
                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (True, "C1 Spirit")
                overlay._speech_jobs = 1
                overlay._poll_game_log()
                assert tracker.connected_ship == (False, "")
                speak.assert_not_called()
                overlay._speech_jobs = 0
                overlay._poll_game_log()
                speak.assert_called_once_with("Disconnected from C1 Spirit.", voice_override="", language_override="English")
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


def test_voice_commands_use_requested_profile_and_set_pilot_seat(monkeypatch, tmp_path):
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    reader = Mock(read_new_lines=Mock(return_value=["You have joined channel 'C1 Spirit : Player'"]))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    config = AppConfig(special_prompts={"Star Citizen": ""},
                       enabled_tools={"Star Citizen": ["star_citizen_log", "web_search"]},
                       profile_output_languages={"Star Citizen": "English"},
                       profile_voices={"Star Citizen": "ship-voice"},
                       profile_auto_speak={"Star Citizen": True},
                       ship_connect_keywords="koppeln", ship_disconnect_keywords="entkoppeln",
                       ship_target_keywords="bordnetz")
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay._game_log = tracker
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        try:
            with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                  patch("voxcomm.app.transcribe_sync", side_effect=["koppeln bordnetz", "entkoppeln bordnetz",
                                                                    "koppeln bordnetz", "connect ship"]),
                  patch("voxcomm.app.chat", return_value="No game access") as chat):
                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (True, "C1 Spirit")
                assert tracker.facts.pilot_seat is True
                assert spoken[-1][0] == "Connected to C1 Spirit's ship computer."
                assert spoken[-1][2:] == ("ship-voice", "English")

                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (False, "")
                assert tracker.facts.pilot_seat is False
                assert spoken[-1][0] == "Disconnected from C1 Spirit."
                chat.assert_not_called()

                overlay._capture(threading.Event(), "")
                assert tracker.connected_ship == (False, "")
                chat.assert_called_once()

                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (False, "")
                assert chat.call_count == 2
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()