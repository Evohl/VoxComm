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
    assert tracker.facts.pilot_seat is False

    tracker.poll()

    assert tracker.connected_ship == (False, "")
    assert tracker.take_events() == [PILOT_LEFT]
    assert tracker.facts.pilot_seat is False


def test_connection_is_rejected_if_the_game_is_not_running():
    tracker = GameLogTracker(executable_finder=lambda: None)
    tracker.poll()
    assert tracker.set_ship_connection(True) is None
    assert tracker.connected_ship == (False, "")


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


def test_voice_commands_use_requested_profile_and_preserve_pilot_safety(monkeypatch, tmp_path):
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
                       profile_auto_speak={"Star Citizen": True})
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay._game_log = tracker
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        try:
            with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                  patch("voxcomm.app.transcribe_sync", side_effect=["connect ship", "disconnect ship", "connect ship"]),
                  patch("voxcomm.app.chat", return_value="No game access") as chat):
                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (True, "C1 Spirit")
                assert tracker.facts.pilot_seat is None
                assert spoken[-1][0] == "Connected to C1 Spirit's ship computer."
                assert spoken[-1][2:] == ("ship-voice", "English")

                overlay._capture(threading.Event(), "Star Citizen")
                assert tracker.connected_ship == (False, "")
                assert spoken[-1][0] == "Disconnected from C1 Spirit."
                chat.assert_not_called()

                overlay._capture(threading.Event(), "")
                assert tracker.connected_ship == (False, "")
                chat.assert_called_once()
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()