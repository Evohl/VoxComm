import os
import signal
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLabel, QLineEdit, QMessageBox, QPushButton, QSystemTrayIcon,
    QTabWidget, QToolButton, QWidget,
)

from voxcomm.app import (
    COMPACT_WIDTH, FULL_WIDTH,
    SPEAKING_STATUS, TRANSCRIPT_MAX_HEIGHT, TYPE_INTERVAL_MS,
    HotkeyField, Overlay,
    SettingsDialog,
    configure_application, game_event_reply, game_reply_action, prefer_x11_on_wayland, transcript_segments,
)
from voxcomm.config import AppConfig, reply_override_key
from voxcomm.gamelog.facts import GameEvent, NAV_TARGET_SET, PILOT_LEFT, PILOT_TAKEN, QUANTUM_SIGNATURE_UNKNOWN, SHIP_ENTERED
from voxcomm.theme import rgba, system_accent, system_surface


class GameLogReplyTests(unittest.TestCase):
    def setUp(self):
        self.queue = deque(maxlen=20)

    def test_unknown_signatures_use_each_ship_and_respect_switch(self):
        config = AppConfig(enabled_tools={"__global__": ["star_citizen_log"]})
        overlay = Mock(config=config, _speech_jobs=0, _pending_pilot_left_ship=None, _pending_quantum_signatures=self.queue)
        overlay._game_log.facts.ship = "Own ship"
        overlay._response_language_for_profile.return_value = "English"
        overlay._game_log.take_events.return_value = [GameEvent(QUANTUM_SIGNATURE_UNKNOWN, "Origin 85X"),
                                                      GameEvent(QUANTUM_SIGNATURE_UNKNOWN, "Crusader Spirit C1")]
        Overlay._poll_game_log(overlay)
        overlay._start_speak.assert_called_once_with(
            "Unknown quantum signature detected: Origin 85X. Unknown quantum signature detected: Crusader Spirit C1.",
            voice_override="", language_override="English")
        overlay._start_speak.reset_mock()
        config.game_announce_quantum_signature_unknown = False
        Overlay._poll_game_log(overlay)
        overlay._start_speak.assert_not_called()

    def test_unknown_signature_waits_for_current_speech_and_can_be_cancelled(self):
        config = AppConfig(enabled_tools={"__global__": ["star_citizen_log"]})
        overlay = Mock(config=config, _speech_jobs=1, _pending_pilot_left_ship=None, _pending_quantum_signatures=self.queue)
        overlay._game_log.facts.ship = "C1"
        overlay._response_language_for_profile.return_value = "English"
        overlay._game_log.take_events.side_effect = [[GameEvent(QUANTUM_SIGNATURE_UNKNOWN, "Origin 85X")], []]
        Overlay._poll_game_log(overlay)
        overlay._start_speak.assert_not_called()
        overlay._speech_jobs = 0
        Overlay._poll_game_log(overlay)
        overlay._start_speak.assert_called_once_with("Unknown quantum signature detected: Origin 85X.",
                                                     voice_override="", language_override="English")
        overlay._start_speak.reset_mock()
        overlay._speech_jobs = 1
        overlay._game_log.take_events.side_effect = [[GameEvent(QUANTUM_SIGNATURE_UNKNOWN, "Origin 85X")], []]
        Overlay._poll_game_log(overlay)
        config.game_announce_quantum_signature_unknown = False
        overlay._speech_jobs = 0
        Overlay._poll_game_log(overlay)
        overlay._start_speak.assert_not_called()

    def test_pilot_entry_is_not_announced_but_leaving_still_is(self):
        config = AppConfig(enabled_tools={"__global__": ["star_citizen_log"]})
        overlay = Mock(config=config, _speech_jobs=0, _pending_pilot_left_ship=None, _pending_quantum_signatures=self.queue)
        overlay._game_log.take_events.return_value = [PILOT_TAKEN, PILOT_LEFT]
        overlay._game_log.facts.ship = "C1"
        overlay._response_language_for_profile.return_value = "English"

        Overlay._poll_game_log(overlay)

        overlay._start_speak.assert_called_once_with("Disconnected from C1.", voice_override="", language_override="English")

    def test_announcements_use_the_permitted_profile_not_the_selected_page(self):
        config = AppConfig(
            special_prompts={"Star Citizen": ""},
            enabled_tools={"Star Citizen": ["star_citizen_log"]},
            profile_voices={"Star Citizen": "en-test"},
            profile_output_languages={"Star Citizen": "English"},
            input_reply_overrides={reply_override_key("Star Citizen", game_reply_action(NAV_TARGET_SET), "EN"): "Route set."},
        )
        overlay = Mock(config=config, _speech_jobs=0, _pending_pilot_left_ship=None, _pending_quantum_signatures=self.queue)
        overlay._game_log.take_events.return_value = [NAV_TARGET_SET]
        overlay._game_log.facts.ship = ""
        overlay._response_language_for_profile.side_effect = lambda profile, voice: config.profile_output_languages[profile]

        Overlay._poll_game_log(overlay)

        overlay._game_log.poll.assert_called_once()
        overlay._start_speak.assert_called_once_with("Route set.", voice_override="en-test", language_override="English")

    def test_announcements_choose_one_named_profile_when_several_are_permitted(self):
        config = AppConfig(
            special_prompts={"Zulu": "", "Alpha": ""},
            enabled_tools={"__global__": ["star_citizen_log"], "Zulu": ["star_citizen_log"],
                           "Alpha": ["star_citizen"]},
            profile_voices={"Alpha": "alpha-voice"},
            input_reply_overrides={reply_override_key("Alpha", game_reply_action(NAV_TARGET_SET), "EN"): "Alpha route."},
        )
        overlay = Mock(config=config, _speech_jobs=0, _pending_pilot_left_ship=None, _pending_quantum_signatures=self.queue)
        overlay._game_log.take_events.return_value = [NAV_TARGET_SET]
        overlay._game_log.facts.ship = ""
        overlay._response_language_for_profile.return_value = "English"

        Overlay._poll_game_log(overlay)

        overlay._game_log.poll.assert_called_once()
        overlay._start_speak.assert_called_once_with("Alpha route.", voice_override="alpha-voice", language_override="English")

    def test_announcements_do_not_read_log_without_permission(self):
        config = AppConfig(special_prompts={"Star Citizen": ""}, enabled_tools={"Star Citizen": ["star_citizen_uex"]})
        overlay = Mock(config=config, _speech_jobs=0, _pending_quantum_signatures=self.queue)

        Overlay._poll_game_log(overlay)

        overlay._game_log.poll.assert_not_called()
        overlay._start_speak.assert_not_called()

    def test_game_status_list_includes_version_and_build(self):
        from voxcomm.app import _game_status_lines
        from voxcomm.gamelog.facts import GameFacts

        lines = _game_status_lines(GameFacts(game_version="4.10.2", game_build="12881860"))

        self.assertTrue(any(line.endswith(": 4.10.2") for line in lines))
        self.assertTrue(any(line.endswith(": 12881860") for line in lines))

    def test_default_reply_is_used_without_override(self):
        self.assertEqual(game_event_reply({}, NAV_TARGET_SET, "__global__", "DE"), "Captain, Navziel ausgewählt.")
        self.assertEqual(game_event_reply({}, NAV_TARGET_SET, "__global__", "EN"), "Captain, nav target selected.")

    def test_pilot_seat_confirmation_uses_ship_name_or_fallback(self):
        self.assertEqual(
            game_event_reply({}, PILOT_TAKEN, "__global__", "EN", "Crusader C1 Spirit"),
            "Connected to Crusader C1 Spirit's ship computer.",
        )
        self.assertEqual(
            game_event_reply({}, PILOT_TAKEN, "__global__", "DE"),
            "Mit dem Schiffscomputer von deinem Schiff verbunden.",
        )
        self.assertEqual(
            game_event_reply({}, PILOT_LEFT, "__global__", "EN", "Crusader C1 Spirit"),
            "Disconnected from Crusader C1 Spirit.",
        )
        self.assertEqual(
            game_event_reply({}, SHIP_ENTERED, "__global__", "EN"),
            "Ship detected via Mobiglas, Captain.",
        )

    def test_override_applies_only_to_its_profile_and_language(self):
        overrides = {reply_override_key("Star Citizen", game_reply_action(NAV_TARGET_SET), "EN"): "Route set."}
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "Star Citizen", "EN"), "Route set.")
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "Star Citizen", "DE"), "Captain, Navziel ausgewählt.")
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "__global__", "EN"), "Captain, nav target selected.")

    def test_additional_languages_and_default_fallback(self):
        overrides = {reply_override_key("Star Citizen", game_reply_action(NAV_TARGET_SET), "ES"): "Destino fijado."}
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "Star Citizen", "ES"), "Destino fijado.")
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "__global__", "ES"), "Objetivo de navegación seleccionado.")
        self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "__global__", "XX"), "Captain, nav target selected.")

    def test_every_tts_language_selects_its_announcement(self):
        from voxcomm.action_catalog import reply_language_tag
        from voxcomm.prompts import VOICE_LANGUAGE_NAMES
        overrides = {reply_override_key("Star Citizen", game_reply_action(NAV_TARGET_SET), code.upper()): label
                     for code, label in VOICE_LANGUAGE_NAMES.items()}
        for code, label in VOICE_LANGUAGE_NAMES.items():
            with self.subTest(language=label):
                self.assertEqual(game_event_reply(overrides, NAV_TARGET_SET, "Star Citizen", reply_language_tag(label)),
                                 label)

    def test_every_language_has_translated_defaults_and_local_ship_name(self):
        from voxcomm.action_catalog import parse_replies, format_replies
        from voxcomm.prompts import VOICE_LANGUAGE_NAMES
        from voxcomm.skills.star_citizen.events import GAME_EVENT_TEXT, game_reply_defaults
        for event in GAME_EVENT_TEXT:
            defaults = game_reply_defaults(event)
            self.assertEqual(set(defaults), {code.upper() for code in VOICE_LANGUAGE_NAMES})
            self.assertEqual(parse_replies(format_replies(defaults)), defaults)
            for code in VOICE_LANGUAGE_NAMES:
                with self.subTest(event=event, language=code):
                    result = game_event_reply({}, event, "__global__", code.upper(), "Testship")
                    self.assertNotIn("{ship}", result)
                    self.assertEqual(result, defaults[code.upper()].replace("{ship}", "Testship"))
                    if code not in {"en", "de", "fr"}:
                        self.assertNotEqual(defaults[code.upper()], defaults["EN"])
                    self.assertNotIn("{ship}", game_event_reply({}, event, "__global__", code.upper()))


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._config_patch = patch("voxcomm.app.load_config", return_value=AppConfig())
        self._config_patch.start()

    def tearDown(self):
        self._config_patch.stop()

    def test_ship_collection_requires_enabled_log_profile(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.ships.ShipLogCollector") as collector:
            overlay = Overlay()
            overlay._game_log = Mock(path=Path("/tmp/Game.log"))
            overlay._game_log.take_events.return_value = []
            overlay._game_log.connected_ship = (False, "")
            overlay._poll_game_log()
            collector.return_value.scan.assert_not_called()
            overlay.config.enabled_tools = {"__global__": ["star_citizen_log"]}
            overlay._poll_game_log()
            collector.return_value.scan.assert_called_once_with(Path("/tmp/Game.log"))
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_observed_ship_list_is_separate_and_closes_without_log_permission(self):
        from PySide6.QtWidgets import QPlainTextEdit, QTableWidget

        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}))
        with patch("voxcomm.app.ships.list_names", return_value=[
            ("class", "CRUS_Spirit_C1"), ("channel", "Crusader C1 Spirit"),
        ]):
            dialog.observed_ships_button.click()
        view = dialog._ships_dialog
        self.assertIsNotNone(view)
        self.assertEqual(view.findChild(QTableWidget, "observed_ships_table").item(0, 0).text(), "CRUS_Spirit_C1")
        self.assertEqual(view.findChild(QPlainTextEdit, "observed_ships_channel").toPlainText(), "Crusader C1 Spirit")
        dialog.star_citizen.setChecked(False)
        self.assertFalse(view.isVisible())
        dialog.deleteLater()

    def test_ship_mapping_table_shows_uex_suggestion_and_allows_manual_correction(self):
        from PySide6.QtWidgets import QPushButton, QTableWidget
        from voxcomm.skills.star_citizen import database
        from voxcomm.skills.star_citizen.gamelog import ships

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"XDG_DATA_HOME": root}):
            database.replace_snapshot({"vehicles": [
                {"id": 4, "name": "C1 Spirit", "company_name": "Crusader Industries"},
                {"id": 5, "name": "85X", "company_name": "Origin Jumpworks"},
            ]})
            ships.collect_lines(["| CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::GetRoute|"])
            ships.save_match("CRUS_Spirit_C1", {"id": 4, "name": "C1 Spirit", "manufacturer": "Crusader Industries"}, "llm")
            dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen"]}))
            dialog.observed_ships_button.click()
            view = dialog._ships_dialog
            table = view.findChild(QTableWidget, "observed_ships_table")
            self.assertEqual([table.item(0, col).text() for col in range(4)],
                             ["CRUS_Spirit_C1", "Crusader Industries", "C1 Spirit", "LLM suggestion"])
            table.selectRow(0)
            with patch("voxcomm.app.QInputDialog.getItem", return_value=("Origin Jumpworks - 85X [5]", True)):
                button = next(widget for widget in view.findChildren(QPushButton) if widget.text() == "Change mapping")
                button.click()
            self.assertEqual([table.item(0, col).text() for col in range(4)],
                             ["CRUS_Spirit_C1", "Origin Jumpworks", "85X", "Manual"])
            self.assertEqual(ships.list_matches()["CRUS_Spirit_C1"]["source"], "manual")
            dialog.close()
            dialog.deleteLater()

    def test_uex_cache_refresh_at_startup_requires_an_existing_authorized_profile(self):
        app = QApplication.instance() or QApplication([])
        cases = (
            (AppConfig(), 0),
            (AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}), 0),
            (AppConfig(special_prompts={"Current": ""}, enabled_tools={
                "Current": ["star_citizen_log"], "Deleted": ["star_citizen_uex", "star_citizen"],
            }), 0),
            (AppConfig(enabled_tools={"__global__": ["star_citizen_uex"]}), 1),
            (AppConfig(enabled_tools={"__global__": ["star_citizen"]}), 1),
            (AppConfig(special_prompts={"Current": ""}, enabled_tools={"Current": ["star_citizen_uex"]}), 1),
            (AppConfig(special_prompts={"Current": ""}, enabled_tools={"Current": ["star_citizen"]}), 1),
            (AppConfig(special_prompts={"Current": ""}, enabled_tools={
                "__global__": ["star_citizen_uex"], "Current": ["star_citizen"],
            }), 1),
        )
        for config, expected_calls in cases:
            with self.subTest(config=config), patch("voxcomm.app.load_config", return_value=config), \
                    patch("voxcomm.app.PttListener"), \
                    patch("voxcomm.app.database.start_refresh_if_due", create=True) as refresh:
                overlay = Overlay()
                self.assertEqual(refresh.call_count, expected_calls)
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()

    def test_uex_cache_refresh_is_not_repeated_when_settings_change(self):
        app = QApplication.instance() or QApplication([])
        for initially_authorized in (False, True):
            config = AppConfig(enabled_tools={"__global__": ["star_citizen_uex"]} if initially_authorized else {})
            with self.subTest(initially_authorized=initially_authorized), \
                    patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"), \
                    patch("voxcomm.app.save_config"), \
                    patch("voxcomm.app.database.start_refresh_if_due", create=True) as refresh:
                overlay = Overlay()
                config.enabled_tools = {} if initially_authorized else {"__global__": ["star_citizen_uex"]}
                dialog = Mock()
                dialog.apply.return_value = config
                overlay._save_settings(dialog)
                overlay._save_settings(dialog)
                self.assertEqual(refresh.call_count, int(initially_authorized))
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()

    def test_settings_controls_use_native_style_and_editor_frames(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        self.assertEqual(dialog.styleSheet(), "")
        for widget in (dialog.profile_model, dialog.sample_rate, dialog.key_test, dialog.profile_model_refresh):
            self.assertEqual(widget.style(), app.style())
            self.assertEqual(widget.styleSheet(), "")
        for editor in (dialog.special_prompt, dialog.input_action_catalog, dialog.game_log_view):
            editor.ensurePolished()
            self.assertNotEqual(editor.frameShape(), QFrame.Shape.NoFrame)
            self.assertEqual(editor.style(), app.style())
            self.assertEqual(editor.styleSheet(), "")

        dialog.deleteLater()

    def test_controller_action_catalog_is_a_preserved_multiline_text_input(self):
        config = AppConfig(
            special_prompts={"Star Citizen": "Ship operations"},
            input_action_catalogs={"Star Citizen": "Self Destruct => self destruct => Button11 => Ausgeloest."},
            active_special_prompt="Star Citizen",
        )
        dialog = SettingsDialog(config)

        self.assertEqual(
            dialog.input_action_catalog.toPlainText(),
            "Self Destruct => self destruct => Button11 => Ausgeloest.",
        )
        dialog.input_action_catalog.appendPlainText(
            "Mining Mode => mining mode => Button1 => Bergbau-Modus."
        )
        dialog._store_input_action_catalog()
        self.assertIn("Button1", dialog.input_action_catalogs["Star Citizen"])
        dialog.deleteLater()

    def test_accepting_dialog_does_not_quit_tool_window_app(self):
        app = QApplication.instance() or QApplication([])
        restore_sigint_handler = configure_application(app)
        overlay = QWidget()
        overlay.setWindowFlags(Qt.WindowType.Tool)
        overlay.show()
        dialog = QDialog(overlay)
        quit_events = []
        app.aboutToQuit.connect(lambda: quit_events.append(True))
        QTimer.singleShot(0, dialog.accept)

        result = dialog.exec()

        self.assertEqual(result, QDialog.DialogCode.Accepted)
        self.assertFalse(quit_events)
        self.assertFalse(app.quitOnLastWindowClosed())
        overlay.close()
        restore_sigint_handler()

    def test_sigint_during_dialog_closes_dialog_without_keyboard_interrupt(self):
        app = QApplication.instance() or QApplication([])
        restore_sigint_handler = configure_application(app)
        dialog = QDialog()
        QTimer.singleShot(0, lambda: signal.raise_signal(signal.SIGINT))

        try:
            result = dialog.exec()
        finally:
            restore_sigint_handler()

        self.assertEqual(result, QDialog.DialogCode.Rejected)

    def test_rapid_ptt_cycles_use_independent_capture_stop_events(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener") as listener_class:
            with patch("voxcomm.app.threading.Thread") as thread_class:
                overlay = Overlay()
                overlay._ptt_event("pressed", "")
                first_stop = thread_class.call_args.kwargs["args"][0]
                overlay._ptt_event("released", "")
                overlay._ptt_event("pressed", "")
                second_stop = thread_class.call_args.kwargs["args"][0]

                self.assertIsNot(first_stop, second_stop)
                self.assertTrue(first_stop.is_set())
                self.assertFalse(second_stop.is_set())
                overlay.listener.stop()
                overlay.deleteLater()

    def test_new_request_ignores_late_messages_from_previous_request(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread") as thread_class:
            overlay = Overlay()
            previous_request = threading.Event()
            with overlay._request_cancellations_lock:
                overlay._request_cancellations.add(previous_request)

            overlay._ptt_event("pressed", "")
            new_request = thread_class.call_args.kwargs["args"][2]

            self.assertTrue(previous_request.is_set())
            self.assertFalse(new_request.is_set())
            overlay._show_request_message("Stale response", "You: How does the cow sound?\n", previous_request)
            self.assertIn("Recording", overlay.status.text())

            overlay._show_request_message("Thinking …", "You: Weather tomorrow?\n", new_request)
            self.assertEqual(overlay._typing_text, "You: Weather tomorrow?\n")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_activity_replaces_thinking_status_and_ignores_cancelled_requests(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            active_request = threading.Event()
            cancelled_request = threading.Event()
            cancelled_request.set()

            overlay._show_activity("Web search: weather", active_request)
            self.assertEqual(overlay.status.text(), "Web search: weather")
            overlay._show_activity("Stale activity", cancelled_request)
            self.assertEqual(overlay.status.text(), "Web search: weather")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_new_recording_clears_previous_transcript_and_stops_typing(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay._show_message("Ready · response generated locally", "You: How does the cow sound?\nAI: Moo")
            overlay._type_next_character()

            overlay._ptt_event("pressed", "")

            self.assertFalse(overlay._type_timer.isActive())
            self.assertEqual(overlay.transcript.toPlainText(), "")
            overlay._show_message("Thinking …", "You: Weather tomorrow?\n")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertEqual(overlay.transcript.toPlainText(), "You: Weather tomorrow?\n")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_tray_click_toggles_recording_and_menu_contains_requested_actions(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread") as thread_class:
            overlay = Overlay()
            overlay._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(overlay._recording)
            self.assertEqual(overlay.tray_toggle_action.text(), "Send recording")
            first_stop = thread_class.call_args.kwargs["args"][0]

            overlay._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertFalse(overlay._recording)
            self.assertTrue(first_stop.is_set())
            self.assertEqual(overlay.tray_toggle_action.text(), "Start recording")
            self.assertEqual(
                [action.text() for action in overlay.tray_menu.actions() if not action.isSeparator()],
                ["Start recording", "Settings …", "Read last response aloud again", "Quit"],
            )
            self.assertFalse(any(action.menu() for action in overlay.tray_menu.actions()))
            overlay.listener.stop()
            overlay.deleteLater()

    def test_tray_mode_falls_back_to_overlay_without_system_tray(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.load_config", return_value=AppConfig(tray_mode=True)):
            with patch("voxcomm.app.PttListener"):
                with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
                    overlay = Overlay()
                    self.assertFalse(overlay.config.tray_mode)
                    self.assertTrue(overlay.isVisible())
                    overlay.listener.stop()
                    overlay.deleteLater()

    def test_tray_mode_shows_text_overlay_without_buttons_then_hides_it(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.load_config", return_value=AppConfig(tray_mode=True)):
            with patch("voxcomm.app.PttListener"):
                with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
                    overlay = Overlay()
                    self.assertFalse(overlay.isVisible())
                    self.assertTrue(all(button.isHidden() for button in (overlay.mic, overlay.say, overlay.settings_button)))

                    overlay._show_message("Aufnahme läuft", "")
                    self.assertTrue(overlay.isVisible())
                    overlay._clear_faded_text()
                    self.assertFalse(overlay.isVisible())

                    overlay._show_message("Antwort", "A.I.: Test")

                    self.assertTrue(overlay.isVisible())
                    self.assertTrue(all(button.isHidden() for button in (overlay.mic, overlay.say, overlay.settings_button)))
                    self.assertEqual(overlay.width(), FULL_WIDTH)
                    self.assertEqual(overlay.transcript.width(), FULL_WIDTH - 24)
                    overlay._clear_faded_text()
                    self.assertFalse(overlay.isVisible())
                    overlay.listener.stop()
                    overlay.tray_icon.hide()
                    overlay.deleteLater()

    def test_wayland_session_defaults_to_x11_unless_platform_is_set(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}, clear=True):
            prefer_x11_on_wayland()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "xcb")
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0", "QT_QPA_PLATFORM": "wayland"}, clear=True):
            prefer_x11_on_wayland()
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "wayland")
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            prefer_x11_on_wayland()
            self.assertNotIn("QT_QPA_PLATFORM", os.environ)

    def test_hotkey_field_captures_pressed_key_and_cancel_keeps_value(self):
        app = QApplication.instance() or QApplication([])
        field = HotkeyField("f9", clearable=True)
        changes = []
        field.changed.connect(changes.append)

        with patch("voxcomm.app.capture_input", return_value="PAGEUP"):
            field.button.click()
            self.assertTrue(field.button.capturing)
            for _ in range(100):
                app.processEvents()
                if changes:
                    break
                QTest.qWait(10)
        self.assertEqual(field.value(), "pageup")
        self.assertEqual(field.button.text(), "PAGEUP")
        self.assertEqual(changes, ["pageup"])
        self.assertIsNotNone(field.clear_button)

        with patch("voxcomm.app.capture_input", return_value=None):
            field.button.click()
            for _ in range(10):
                app.processEvents()
                QTest.qWait(10)
        self.assertEqual(field.value(), "pageup")
        field.clear_button.click()
        self.assertEqual(field.value(), "")
        self.assertEqual(changes, ["pageup", ""])
        field.deleteLater()

    def test_hotkey_field_reports_missing_keyboard_access(self):
        app = QApplication.instance() or QApplication([])
        field = HotkeyField("f9")
        errors = []
        field.error.connect(errors.append)

        with patch("voxcomm.app.capture_input", side_effect=PermissionError("kein Zugriff")):
            field.button.click()
            for _ in range(100):
                app.processEvents()
                if errors:
                    break
                QTest.qWait(10)
        self.assertEqual(errors, ["kein Zugriff"])
        self.assertFalse(field.button.capturing)
        self.assertEqual(field.value(), "f9")
        field.deleteLater()

    def test_profile_hotkeys_are_stored_renamed_removed_and_conflict_checked(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(ptt_key="F9", special_prompts={"Star Citizen": "a", "Elite": "b"},
                   special_hotkeys={"Elite": "f11", "Gone": "f12"},
                   profile_models={"Star Citizen": "ship-model"},
                   profile_voices={"Star Citizen": "ship-voice"})
        dialog = SettingsDialog(config)

        self.assertTrue(dialog.profile_hotkey.isEnabled())
        self.assertEqual(dialog.profile_hotkey.value(), "F9")
        self.assertEqual(dialog.special_hotkeys, {"Elite": "f11"})

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertTrue(dialog.profile_hotkey.isEnabled())
        self.assertEqual(dialog.profile_hotkey.value(), "")
        dialog.profile_hotkey._apply("f9")
        self.assertEqual(dialog.profile_hotkey.value(), "")
        self.assertIn("Default", dialog.discovery_status.text())
        dialog.profile_hotkey._apply("f11")
        self.assertEqual(dialog.profile_hotkey.value(), "")
        self.assertIn("Elite", dialog.discovery_status.text())
        dialog.profile_hotkey._apply("f10")
        self.assertEqual(dialog.special_hotkeys["Star Citizen"], "f10")

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        dialog.profile_hotkey._apply("f10")
        self.assertEqual(dialog.profile_hotkey.value(), "F9")
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))

        with patch("voxcomm.app.QInputDialog.getText", return_value=("SC", True)):
            dialog._rename_special_profile()
        self.assertEqual(dialog.special_hotkeys["SC"], "f10")
        self.assertNotIn("Star Citizen", dialog.special_hotkeys)
        self.assertEqual(dialog._profile_models["SC"], "ship-model")
        self.assertEqual(dialog._profile_voices["SC"], "ship-voice")
        self.assertEqual(dialog.profile_hotkey.value(), "f10")

        with patch("voxcomm.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog._remove_special_profile()
        self.assertNotIn("SC", dialog.special_hotkeys)
        self.assertNotIn("SC", dialog._profile_models)
        self.assertNotIn("SC", dialog._profile_voices)

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Elite"))
        dialog.profile_hotkey.clear_button.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.special_hotkeys, {})
        self.assertEqual(updated.ptt_key, "F9")
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertIsNotNone(dialog.profile_hotkey.clear_button)
        dialog.profile_hotkey.clear_button.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.ptt_key, "")
        self.assertFalse(hasattr(dialog, "device"))
        dialog.deleteLater()

    def test_default_profile_hotkey_is_editable_and_routes_to_default(self):
        from voxcomm.ptt import PttListener
        app = QApplication.instance() or QApplication([])
        config = AppConfig(ptt_key="F9", special_prompts={"Work": ""}, special_hotkeys={"Work": "f10"})
        dialog = SettingsDialog(config)
        self.assertEqual(dialog.special_profile.currentText(), "Default")
        self.assertEqual(dialog.profile_hotkey.value(), "F9")
        dialog.profile_hotkey._apply("f12")
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Work"))
        dialog.profile_hotkey._apply("f12")
        self.assertEqual(dialog.profile_hotkey.value(), "f10")
        updated = dialog.apply(config)
        self.assertEqual(updated.ptt_key, "f12")
        listener = PttListener(updated.ptt_key, lambda *_event: None,
                               {key: name for name, key in updated.special_hotkeys.items()})
        self.assertEqual(listener._key_profiles(), {"f12": "", "f10": "Work"})
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertEqual(dialog.profile_hotkey.value(), "f12")
        dialog.deleteLater()

    def test_profile_hotkey_selects_prompt_profile_for_recording(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            special_prompts={"Star Citizen": "Ship context"},
            special_hotkeys={"Star Citizen": "f10"},
            profile_models={"__global__": "global-model", "Star Citizen": "ship-model"},
            profile_voices={"__global__": "global-voice", "Star Citizen": "de_DE-ship"},
            profile_thinking={"Star Citizen": "high"},
            profile_response_languages={"Star Citizen": "German"},
            active_special_prompt="Star Citizen",
            enabled_tools={"__global__": ["web_search"]},
        )
        with patch("voxcomm.app.load_config", return_value=config):
            with patch("voxcomm.app.PttListener") as listener_class:
                with patch("voxcomm.app.threading.Thread") as thread_class:
                    overlay = Overlay()
                    self.assertEqual(listener_class.call_args.args[-1], {"f10": "Star Citizen"})

                    overlay._ptt_event("pressed", "Star Citizen")

                    self.assertEqual(thread_class.call_args.kwargs["args"][1], "Star Citizen")
                    self.assertIn("Star Citizen", overlay.status.text())
                    overlay.listener.stop()
                    overlay.tray_icon.hide()
                    overlay.deleteLater()

        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            requests = []
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Hallo"),
                patch("voxcomm.app.chat", side_effect=lambda *a, **k: requests.append((a, k)) or "Antwort"),
            ):
                overlay._capture(threading.Event(), "Star Citizen")
                self.assertEqual(overlay._last_response_voice, "de_DE-ship")
                overlay._capture(threading.Event(), "")
            self.assertIn("Ship context", requests[0][1]["system_prompt"])
            self.assertEqual(requests[0][0][2], "ship-model")
            self.assertEqual(requests[0][1]["thinking"], "high")
            self.assertIn("German", requests[0][1]["system_prompt"])
            self.assertEqual(requests[0][1]["enabled_tools"], [])
            self.assertNotIn("Ship context", requests[1][1]["system_prompt"])
            self.assertEqual(requests[1][1]["enabled_tools"], ["web_search"])
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_general_ptt_ignores_active_prompt_profile(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            special_prompts={"Star Citizen": "Ship context"},
            active_special_prompt="Star Citizen",
        )
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            with patch("voxcomm.app.threading.Thread") as thread_class:
                overlay = Overlay()

                overlay._ptt_event("pressed", "")

                self.assertEqual(thread_class.call_args.kwargs["args"][1], "")
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()

    def test_short_ptt_capture_is_discarded_before_transcription(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with patch("voxcomm.app.record_until_released", return_value=b"\0" * 6368):
                with patch("voxcomm.app.transcribe_sync") as transcribe:
                    with patch("voxcomm.app.chat") as chat:
                        overlay._capture(threading.Event())

            transcribe.assert_not_called()
            chat.assert_not_called()
            self.assertEqual(overlay.status.text(), "Ready · recording too short")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_sub_200ms_ptt_tap_cancels_active_request_and_visible_output(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            active_request = threading.Event()
            with overlay._request_cancellations_lock:
                overlay._request_cancellations.add(active_request)
            overlay._show_message("Answer", "AI: current output")
            self.assertTrue(overlay._type_timer.isActive())

            with patch("voxcomm.app.time.monotonic", side_effect=[1.0, 1.199]):
                overlay._ptt_event("pressed", "")
                overlay._ptt_event("released", "")

            self.assertTrue(active_request.is_set())
            self.assertFalse(overlay._type_timer.isActive())
            self.assertEqual(overlay.transcript.toPlainText(), "")
            self.assertEqual(overlay.status.text(), "Ready · request cancelled")
            overlay._show_request_message("Stale response", "AI: do not show", active_request)
            self.assertEqual(overlay.status.text(), "Ready · request cancelled")
            self.assertEqual(overlay.transcript.toPlainText(), "")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_overlay_restores_saved_position(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(overlay_x=140, overlay_y=160)
        with patch("voxcomm.app.load_config", return_value=config):
            with patch("voxcomm.app.PttListener"), patch("voxcomm.app.save_overlay_position") as save_position:
                overlay = Overlay()
                self.assertEqual(overlay.pos(), QPoint(140, 160))
                self.assertTrue(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                self.assertFalse(save_position.called)
                overlay.move(180, 200)
                self.assertTrue(overlay._position_save_timer.isActive())
                overlay._position_save_timer.stop()
                overlay._save_overlay_position()
                self.assertEqual((config.overlay_x, config.overlay_y), (180, 200))
                save_position.assert_called_once_with(180, 200)
                overlay.listener.stop()
                overlay.deleteLater()

    def test_overlay_reset_button_returns_window_to_visible_default(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(overlay_x=50000, overlay_y=50000)
        with patch("voxcomm.app.load_config", return_value=config):
            with patch("voxcomm.app.PttListener"), patch("voxcomm.app.save_overlay_position") as save_position:
                overlay = Overlay()
                area = QGuiApplication.primaryScreen().availableGeometry()
                expected = QPoint(area.left() + (area.width() - overlay.width()) // 2, area.top() + 12)
                self.assertEqual(overlay.pos(), expected)
                dialog = SettingsDialog(config, overlay)
                self.assertTrue(dialog.reset_position.isEnabled())
                dialog.reset_position.click()
                self.assertEqual((config.overlay_x, config.overlay_y), (expected.x(), expected.y()))
                save_position.assert_called_with(expected.x(), expected.y())
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()
                dialog.deleteLater()

    def test_overlay_is_translucent_and_hides_after_completed_answer(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(overlay.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(overlay._hide_timer.interval(), 10_000)
            panel = overlay.centralWidget()
            self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertIn(rgba(system_surface(), 170), panel.styleSheet())
            self.assertIn(system_accent().name(), panel.styleSheet())
            self.assertFalse(overlay.transcript.viewport().autoFillBackground())
            self.assertTrue(overlay.transcript.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground))
            self.assertIn("QAbstractScrollArea::viewport", panel.styleSheet())
            for button in (overlay.mic, overlay.say, overlay.settings_button):
                self.assertFalse(button.icon().isNull())
                self.assertEqual(button.text(), "")

            overlay.show()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Test?\nA.I.: Antwort")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertTrue(overlay._hide_timer.isActive())
            overlay._hide_timer.timeout.emit()
            self.assertTrue(overlay._fade_animation.state())
            overlay._fade_animation.setCurrentTime(overlay._fade_animation.duration())
            self.assertTrue(overlay.isVisible())
            self.assertTrue(overlay.mic.isVisible())
            self.assertTrue(overlay.say.isVisible())
            self.assertTrue(overlay.settings_button.isVisible())
            self.assertEqual(overlay.transcript.toPlainText(), "")
            self.assertEqual(overlay.status.text(), "")
            self.assertFalse(overlay._text_container.isVisible())
            self.assertEqual(overlay.width(), COMPACT_WIDTH)
            self.assertLessEqual(overlay.height(), 64)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_overlay_reapplies_system_accent_after_theme_change(self):
        app = QApplication.instance() or QApplication([])
        accent = QColor("#29c5fe")
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.system_accent", return_value=accent):
            overlay = Overlay()
            overlay.changeEvent(QEvent(QEvent.Type.ApplicationPaletteChange))

            self.assertIn(accent.name(), overlay._panel.styleSheet())
            self.assertEqual(overlay._transcript_formats["ai_label"].foreground().color(), accent)

            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_overlay_starts_compact_and_grows_with_output(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.load_config", return_value=AppConfig()):
            with patch("voxcomm.app.PttListener"):
                overlay = Overlay()
                overlay.show()
                self.assertEqual(overlay.width(), COMPACT_WIDTH)
                self.assertFalse(overlay._text_container.isVisible())

                overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Test?\nA.I.: Antwort")
                overlay._type_next_character()

                self.assertEqual(overlay.width(), FULL_WIDTH)
                self.assertTrue(overlay._text_container.isVisible())
                overlay.listener.stop()
                overlay.deleteLater()

    def test_stale_hide_timeout_does_not_fade_while_output_is_typing(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay.show()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "A.I.: " + "Lange Antwort " * 20)
            overlay._start_speak("Lange Antwort")
            overlay._hide_timer.start()

            overlay._hide_timer.timeout.emit()
            overlay._hide_timer.stop()

            self.assertTrue(overlay._type_timer.isActive())
            self.assertNotEqual(overlay._fade_animation.state(), QAbstractAnimation.State.Running)
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertFalse(overlay._hide_timer.isActive())

            overlay._hide_timer.start()
            overlay._hide_timer.timeout.emit()
            overlay._hide_timer.stop()
            self.assertNotEqual(overlay._fade_animation.state(), QAbstractAnimation.State.Running)

            overlay.events.speech_finished.emit()
            self.assertTrue(overlay._hide_timer.isActive())
            overlay.listener.stop()
            overlay.deleteLater()

    def test_status_only_message_hides_again_and_shrinks(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            overlay.show()
            overlay._show_message("Bereit · keine Sprache erkannt", "")
            self.assertEqual(overlay.width(), FULL_WIDTH)
            self.assertTrue(overlay._hide_timer.isActive())

            overlay._hide_timer.timeout.emit()
            overlay._fade_animation.setCurrentTime(overlay._fade_animation.duration())

            self.assertEqual(overlay.width(), COMPACT_WIDTH)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_play_shows_last_text_again_and_hides_after_audio(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay.show()
            full_text = "You: Test?\nAI: Answer"
            overlay._show_message("Ready · response generated locally", full_text)
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            overlay._hide_timer.timeout.emit()
            overlay._fade_animation.setCurrentTime(overlay._fade_animation.duration())
            self.assertEqual(overlay.width(), COMPACT_WIDTH)

            overlay.say.click()

            self.assertEqual(overlay.width(), FULL_WIDTH)
            self.assertEqual(overlay.status.text(), SPEAKING_STATUS)
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertEqual(overlay.transcript.toPlainText(), full_text)
            self.assertFalse(overlay._hide_timer.isActive())

            overlay.events.speech_finished.emit()

            self.assertTrue(overlay._hide_timer.isActive())
            overlay.listener.stop()
            overlay.deleteLater()

    def test_new_recording_stops_and_cancels_active_speech(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay._start_speak("Gesprochene Antwort")
            speech_cancel = overlay._speech_cancel

            overlay._ptt_event("pressed", "")

            self.assertTrue(speech_cancel.is_set())
            self.assertTrue(overlay._recording)
            self.assertEqual(overlay.status.text().split(" · ")[0], "Recording")
            overlay.listener.stop()
            overlay.deleteLater()

    def test_cancelled_speech_does_not_start_playback_after_generation(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            cancel_event = threading.Event()
            cancel_event.set()
            completed = []
            overlay.events.speech_finished.connect(lambda: completed.append(True))

            with patch("voxcomm.app.speak_stream_sync", return_value=iter([(b"audio", 22050)])):
                with patch("voxcomm.audio.sd.OutputStream") as output_stream:
                    overlay._speak("Antwort", cancel_event)

            output_stream.assert_not_called()
            self.assertEqual(completed, [True])
            overlay.listener.stop()
            overlay.deleteLater()

    def test_auto_speak_starts_with_first_sentence_while_llm_is_still_streaming(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(auto_speak=True, sample_rate=16000)
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            seen = {}

            def fake_chat(*_args, on_text=None, **_kwargs):
                on_text("Das Schiff ist bereit für den Start. Die Trieb")
                seen["speech_started"] = thread_class.called
                on_text("werke laufen stabil und ruhig.")
                return "Das Schiff ist bereit für den Start. Die Triebwerke laufen stabil und ruhig."

            with (
                patch("voxcomm.app.threading.Thread") as thread_class,
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Status"),
                patch("voxcomm.app.chat", side_effect=fake_chat),
            ):
                overlay._capture(threading.Event())

            self.assertTrue(seen["speech_started"])
            source = thread_class.call_args.kwargs["args"][0]
            self.assertEqual(list(source), [
                "Das Schiff ist bereit für den Start.", "Die Triebwerke laufen stabil und ruhig."])
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_non_streaming_answer_is_still_spoken_once_when_chat_does_not_stream(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(auto_speak=True, sample_rate=16000)
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with (
                patch("voxcomm.app.threading.Thread") as thread_class,
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Status"),
                patch("voxcomm.app.chat", return_value="Kurze Antwort"),
            ):
                overlay._capture(threading.Event())

            source = thread_class.call_args.kwargs["args"][0]
            self.assertEqual(list(source), ["Kurze Antwort"])
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_web_search_profile_speaks_only_the_final_answer(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(auto_speak=True, sample_rate=16000, enabled_tools={"__global__": ["web_search"]})
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with (
                patch("voxcomm.app.threading.Thread") as thread_class,
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Wetter"),
                patch("voxcomm.app.chat", return_value="Morgen wird es sonnig.") as chat,
            ):
                overlay._capture(threading.Event())

            self.assertIsNone(chat.call_args.kwargs["on_text"])
            self.assertEqual(thread_class.call_args.kwargs["args"][0], "Morgen wird es sonnig.")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_answer_types_slowly_and_scrolls_when_long(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            overlay.show()
            long_text = "Du: Frage\nA.I.: " + "\n".join(f"Zeile {number}" for number in range(60))
            overlay._show_message("Bereit · Antwort lokal erzeugt", long_text)

            overlay._type_next_character()
            self.assertEqual(overlay._typing_index, 1)
            self.assertEqual(overlay._type_timer.interval(), TYPE_INTERVAL_MS)
            self.assertGreaterEqual(TYPE_INTERVAL_MS, 20)

            while overlay._type_timer.isActive():
                overlay._type_next_character()
            app.processEvents()

            scroll_bar = overlay.transcript.verticalScrollBar()
            self.assertEqual(overlay.transcript.height(), TRANSCRIPT_MAX_HEIGHT)
            self.assertGreater(scroll_bar.maximum(), 0)
            self.assertEqual(scroll_bar.value(), scroll_bar.maximum())
            self.assertLessEqual(overlay.height(), TRANSCRIPT_MAX_HEIGHT + 80)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_overlay_hides_only_after_audio_output_finished(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay.show()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Test?\nA.I.: Antwort")
            overlay._start_speak("Antwort")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertFalse(overlay._hide_timer.isActive())

            overlay.events.speech_finished.emit()

            self.assertTrue(overlay._hide_timer.isActive())
            self.assertEqual(overlay._hide_timer.interval(), 10_000)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_overlay_waits_for_typing_when_audio_finishes_first(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay.show()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Test?\nA.I.: Antwort")
            overlay._start_speak("Antwort")
            overlay.events.speech_finished.emit()
            self.assertFalse(overlay._hide_timer.isActive())

            while overlay._type_timer.isActive():
                overlay._type_next_character()

            self.assertTrue(overlay._hide_timer.isActive())
            overlay.listener.stop()
            overlay.deleteLater()

    def test_ai_output_is_labeled_and_colored_apart_from_user_input(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            overlay.show()
            text = "You: Question?\nAI: Answer"
            overlay._show_message("Ready · response generated locally", text)
            while overlay._type_timer.isActive():
                overlay._type_next_character()

            self.assertEqual(overlay.transcript.toPlainText(), text)
            self.assertEqual(overlay._last_response, "Answer")

            def color_at(index):
                cursor = overlay.transcript.textCursor()
                cursor.setPosition(index + 1)
                return cursor.charFormat().foreground().color().name()

            user_color = color_at(text.index("Question"))
            ai_label_color = color_at(text.index("AI:"))
            ai_color = color_at(text.index("Answer"))
            self.assertEqual(ai_label_color, system_accent().name())
            self.assertNotEqual(user_color, ai_color)
            self.assertNotEqual(user_color, ai_label_color)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_transcript_segments_reassemble_original_text(self):
        for text in ("You: Question?\nAI: Answer", "You: Question?\n", "Plain text", "You: AI: without line break"):
            self.assertEqual("".join(chunk for chunk, _ in transcript_segments(text)), text)
        self.assertEqual(
            [kind for _, kind in transcript_segments("You: Question?\nAI: Answer")],
            ["user_label", "user", "ai_label", "ai"],
        )

    def test_drag_handle_moves_overlay_when_system_move_is_unavailable(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.save_config"):
            overlay = Overlay()
            overlay.show()
            overlay.status.clear()
            overlay.transcript.clear()
            app.processEvents()
            start = overlay.pos()
            window_handle = overlay.windowHandle()
            with patch.object(window_handle, "startSystemMove", return_value=False):
                press = Mock()
                press.button.return_value = Qt.MouseButton.LeftButton
                press.globalPosition.return_value = QPointF(start.x() + 10, start.y() + 10)
                overlay._drag_surface.mousePressEvent(press)

                move = Mock()
                move.buttons.return_value = Qt.MouseButton.LeftButton
                move.globalPosition.return_value = QPointF(start.x() + 40, start.y() + 30)
                overlay._drag_surface.mouseMoveEvent(move)

            self.assertEqual(overlay.pos(), start + QPoint(30, 20))
            QTest.mouseRelease(overlay._drag_surface, Qt.MouseButton.LeftButton)
            self.assertTrue(overlay._position_save_timer.isActive())
            overlay._position_save_timer.stop()
            overlay._save_overlay_position()
            self.assertEqual((overlay.config.overlay_x, overlay.config.overlay_y), (overlay.x(), overlay.y()))
            overlay.listener.stop()
            overlay.deleteLater()

    def test_settings_dialog_is_split_into_sections(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        titles = [label.text() for label in dialog.findChildren(QLabel, "sectionTitle")]
        self.assertEqual(
            sorted(titles),
            sorted(["APPLICATION", "SPEECH RECOGNITION", "LANGUAGE MODEL", "SPEECH OUTPUT"]),
        )
        for index in range(dialog.profile_tabs.count()):
            self.assertEqual(dialog.profile_tabs.widget(index).findChildren(QLabel, "sectionTitle"), [])
        self.assertTrue(dialog.audio_confirmation_heading.text())
        sections = dialog.findChildren(QFrame, "section")
        self.assertEqual(len(sections), 8)
        self.assertEqual(dialog.input_action_catalog.toPlainText(), "")
        self.assertTrue(dialog.action_catalog_section.isHidden())
        self.assertEqual(dialog.style(), app.style())
        self.assertEqual(dialog.findChild(QPushButton, "primary").text(), "Save")
        for field in (dialog.stt, dialog.tts, dialog.llm_url, dialog.profile_hotkey, dialog.special_prompt):
            self.assertTrue(any(section.isAncestorOf(field) for section in sections))

    def test_section_headings_follow_system_accent(self):
        app = QApplication.instance() or QApplication([])
        first_accent = QColor("#278754")
        second_accent = QColor("#cd5148")
        with patch("voxcomm.app.system_accent", return_value=first_accent) as accent:
            dialog = SettingsDialog(AppConfig(ui_language="de"))
            dialog._lookup_thinking = lambda: None
            dialog.show()
            app.processEvents()
            headings = dialog.findChildren(QLabel, "sectionTitle")
            self.assertIn("ANWENDUNG", [heading.text() for heading in headings])
            self.assertTrue(headings)
            for heading in headings:
                self.assertEqual(heading.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText), first_accent)
                self.assertEqual(heading.styleSheet(), "")
            application_heading = next(heading for heading in headings if heading.text() == "ANWENDUNG")
            image = application_heading.grab().toImage()
            self.assertTrue(any(
                abs(color.red() - first_accent.red()) < 25
                and abs(color.green() - first_accent.green()) < 25
                and abs(color.blue() - first_accent.blue()) < 25
                for x in range(image.width()) for y in range(image.height())
                if (color := image.pixelColor(x, y)).alpha() > 0
            ))
            accent.return_value = second_accent
            dialog.changeEvent(QEvent(QEvent.Type.ApplicationPaletteChange))
            for heading in headings:
                self.assertEqual(heading.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText), second_accent)
            self.assertEqual(dialog.styleSheet(), "")
            dialog.close()
            dialog.deleteLater()

    def test_settings_dialog_loads_the_configured_translation(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(ui_language="de")
        dialog = SettingsDialog(config)

        self.assertTrue(dialog.windowTitle().startswith("Einstellungen"))
        self.assertEqual(dialog.web_search.text(), "Websuche erlauben")
        self.assertEqual([dialog.settings_tabs.tabText(index) for index in range(dialog.settings_tabs.count())],
                         ["Allgemeine Einstellungen", "Profile"])
        self.assertEqual(dialog.profile_tabs.tabText(0), "Profileinstellungen")
        self.assertEqual(dialog.profile_tabs.tabText(1), "Skills und Tools")
        self.assertEqual(dialog.apply(config).ui_language, "de")

    def test_profile_navigation_follows_each_profiles_permissions(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": "Ship context"},
                           enabled_tools={"Star Citizen": ["input_control", "star_citizen_log"]})
        dialog = SettingsDialog(config)
        dialog.settings_tabs.setCurrentIndex(1)
        self.assertEqual([dialog.profile_tabs.tabText(index) for index in range(dialog.profile_tabs.count())],
                         ["Profile settings", "Skills and tools", "VoxComm Controller", "Star Citizen log"])
        self.assertFalse(dialog.profile_tabs.isTabVisible(2))
        self.assertFalse(dialog.profile_tabs.isTabVisible(3))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertTrue(dialog.profile_tabs.isTabVisible(2))
        self.assertTrue(dialog.profile_tabs.isTabVisible(3))
        dialog.profile_tabs.setCurrentIndex(2)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertFalse(dialog.profile_tabs.isTabVisible(2))
        self.assertFalse(dialog.profile_tabs.isTabVisible(3))
        self.assertNotEqual(dialog.profile_tabs.currentIndex(), 2)
        dialog.profile_tabs.setCurrentIndex(1)
        dialog.input_control.setChecked(True)
        self.assertTrue(dialog.profile_tabs.isTabVisible(2))
        self.assertEqual(dialog.apply(config).enabled_tools["__global__"], ["input_control"])
        dialog.deleteLater()

    def _wait_for_controller_signal(self, app, spy, count):
        for _ in range(100):
            app.processEvents()
            if spy.count() >= count:
                app.processEvents()
                return
            QTest.qWait(10)
        self.fail("Controller result signal was not received")

    def test_manual_controller_buttons_send_tap_and_hold_without_blocking_ui(self):
        from voxcomm.gamepad import BUTTON_COUNT

        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["input_control"]}))
        self.assertTrue(dialog.manual_controller_panel.isHidden())
        self.assertEqual(len(dialog.manual_buttons), BUTTON_COUNT)
        dialog.manual_controller_toggle.click()
        self.assertFalse(dialog.manual_controller_panel.isHidden())
        spy = QSignalSpy(dialog.discovery.controller_pressed)
        with patch("voxcomm.gamepad.press_gamepad_button", return_value="Button100") as press:
            dialog.manual_buttons[99].click()
            self._wait_for_controller_signal(app, spy, 1)
            press.assert_called_once_with("Button100", hold=False)
            self.assertEqual(dialog.manual_status.text(), "Pressed Button100")
        dialog.manual_hold.setChecked(True)
        with patch("voxcomm.gamepad.press_gamepad_button", return_value="Button1") as press:
            dialog.manual_buttons[0].click()
            self._wait_for_controller_signal(app, spy, 2)
            press.assert_called_once_with("Button1", hold=True)
        dialog.deleteLater()

    def test_manual_controller_waits_for_active_press_without_queuing_another(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["input_control"]}))
        started = threading.Event()
        release = threading.Event()
        spy = QSignalSpy(dialog.discovery.controller_pressed)

        def press_button(button, hold=False):
            started.set()
            release.wait(2)
            return button

        with patch("voxcomm.gamepad.press_gamepad_button", side_effect=press_button) as press:
            try:
                dialog.manual_buttons[0].click()
                self.assertTrue(started.wait(1))
                self.assertFalse(dialog.manual_grid.isEnabled())
                dialog.manual_buttons[1].click()
                self.assertEqual(press.call_count, 1)
            finally:
                release.set()
            self._wait_for_controller_signal(app, spy, 1)
            self.assertTrue(dialog.manual_grid.isEnabled())
        dialog.deleteLater()

    def test_manual_controller_delayed_press_and_cancellation(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Other": ""}, enabled_tools={"__global__": ["input_control"]})
        dialog = SettingsDialog(config)
        dialog.manual_controller_toggle.click()
        dialog.manual_delay.setValue(2)
        with patch("voxcomm.gamepad.press_gamepad_button", return_value="Button5") as press:
            dialog.manual_buttons[4].click()
            self.assertTrue(dialog._manual_timer.isActive())
            self.assertFalse(dialog.manual_grid.isEnabled())
            dialog.manual_cancel.click()
            self.assertFalse(dialog._manual_timer.isActive())
            self.assertTrue(dialog.manual_grid.isEnabled())
            press.assert_not_called()
            dialog.manual_buttons[4].click()
            dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Other"))
            self.assertFalse(dialog._manual_timer.isActive())
            press.assert_not_called()
            dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
            dialog.manual_buttons[4].click()
            dialog.input_control.setChecked(False)
            self.assertFalse(dialog._manual_timer.isActive())
            press.assert_not_called()
            dialog.input_control.setChecked(True)
            dialog.manual_buttons[4].click()
            dialog.manual_controller_toggle.click()
            self.assertFalse(dialog._manual_timer.isActive())
            press.assert_not_called()
            dialog.manual_controller_toggle.click()
            dialog.manual_buttons[4].click()
            spy = QSignalSpy(dialog.discovery.controller_pressed)
            dialog._manual_timer.start(1)
            self._wait_for_controller_signal(app, spy, 1)
            press.assert_called_once_with("Button5", hold=False)
        dialog.manual_buttons[4].click()
        dialog.close()
        self.assertFalse(dialog._manual_timer.isActive())
        dialog.deleteLater()

    def test_manual_controller_reports_daemon_errors(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(ui_language="de", enabled_tools={"__global__": ["input_control"]}))
        spy = QSignalSpy(dialog.discovery.controller_pressed)
        with patch("voxcomm.gamepad.press_gamepad_button", side_effect=OSError("not running")):
            dialog.manual_buttons[0].click()
            self._wait_for_controller_signal(app, spy, 1)
        self.assertIn("Controller-Fehler: not running", dialog.manual_status.text())
        self.assertTrue(dialog.manual_grid.isEnabled())
        dialog.deleteLater()

    def test_expanded_manual_controller_fits_minimum_dialog_width(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(ui_language="de", enabled_tools={"__global__": ["input_control"]}))
        dialog._lookup_thinking = lambda: None
        dialog.resize(1040, 720)
        dialog.show()
        dialog.settings_tabs.setCurrentIndex(1)
        dialog.profile_tabs.setCurrentIndex(2)
        dialog.manual_controller_toggle.click()
        app.processEvents()
        scroll = dialog.profile_tabs.currentWidget()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(dialog.manual_delay.height(), dialog.sample_rate.height())
        self.assertGreater(dialog.manual_delay.x(), dialog.manual_hold.geometry().right())
        self.assertGreater(dialog.manual_buttons[99].width(), 0)
        self.assertEqual(dialog.manual_buttons[99].text(), "Button100")
        dialog.close()
        dialog.deleteLater()

    def test_announcement_fields_keep_profile_specific_languages(self):
        app = QApplication.instance() or QApplication([])
        key = reply_override_key("Star Citizen", game_reply_action(NAV_TARGET_SET), "ES")
        config = AppConfig(special_prompts={"Star Citizen": ""}, input_reply_overrides={key: "Destino anterior."})
        dialog = SettingsDialog(config)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        field = dialog._game_reply_fields[NAV_TARGET_SET]
        self.assertIn("{ES} Destino anterior.", field.text())
        self.assertIn("{FR}", field.text())
        self.assertTrue(field.text().startswith("{EN}"))
        self.assertEqual(field.cursorPosition(), 0)
        from voxcomm.action_catalog import parse_replies
        from voxcomm.prompts import VOICE_LANGUAGE_NAMES
        self.assertEqual(set(parse_replies(field.text())), {code.upper() for code in VOICE_LANGUAGE_NAMES})
        field.setText("{EN} Destination | {DE} Ziel | {ES} Destino | {CZ} Cíl")
        field.textEdited.emit(field.text())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertNotIn("{ES} Destino anterior.", field.text())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertIn("{CZ} Cíl", field.text())
        updated = dialog.apply(config)
        self.assertEqual(updated.input_reply_overrides[key], "Destino")
        self.assertEqual(game_event_reply(updated.input_reply_overrides, NAV_TARGET_SET, "Star Citizen", "CS"), "Cíl")
        dialog.deleteLater()

    def test_unknown_signature_checkbox_and_profile_reply_are_saved(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": ""}, enabled_tools={"Star Citizen": ["star_citizen_log"]})
        dialog = SettingsDialog(config)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog._announce_checks[QUANTUM_SIGNATURE_UNKNOWN].setChecked(False)
        field = dialog._game_reply_fields[QUANTUM_SIGNATURE_UNKNOWN]
        field.setText("{EN} Contact: {ship}.")
        field.textEdited.emit(field.text())
        updated = dialog.apply(config)
        self.assertFalse(updated.game_announce_quantum_signature_unknown)
        self.assertEqual(game_event_reply(updated.input_reply_overrides, QUANTUM_SIGNATURE_UNKNOWN,
                                          "Star Citizen", "EN", "Origin 85X"), "Contact: Origin 85X.")
        self.assertEqual(game_event_reply(updated.input_reply_overrides, QUANTUM_SIGNATURE_UNKNOWN,
                                          "__global__", "EN", "Origin 85X"),
                         "Unknown quantum signature detected: Origin 85X.")
        dialog.deleteLater()

    def test_invalid_announcement_draft_survives_switch_and_blocks_save(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(special_prompts={"Star Citizen": ""},
                                          enabled_tools={"Star Citizen": ["star_citizen_log"]}))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        field = dialog._game_reply_fields[NAV_TARGET_SET]
        field.setText("{ES} ")
        field.textEdited.emit(field.text())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        saved = []
        dialog.save_requested.connect(lambda: saved.append(True))
        dialog._request_save()
        self.assertEqual(saved, [])
        self.assertEqual(dialog.special_profile.currentData(), "Star Citizen")
        self.assertEqual(field.text(), "{ES} ")
        self.assertIn("Invalid announcement reply", dialog.discovery_status.text())
        field.setText("{ES} Destino")
        field.textEdited.emit(field.text())
        dialog._request_save()
        self.assertEqual(saved, [True])
        dialog.deleteLater()

    def test_invalid_announcement_can_be_recovered_after_log_is_disabled(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}))
        field = dialog._game_reply_fields[NAV_TARGET_SET]
        field.setText("{ES}")
        field.textEdited.emit(field.text())
        dialog.star_citizen.setChecked(False)
        saved = []
        dialog.save_requested.connect(lambda: saved.append(True))
        dialog._request_save()
        self.assertEqual(saved, [])
        self.assertEqual(dialog.profile_tabs.currentIndex(), 1)
        self.assertIn("Enable Star Citizen Log", dialog.discovery_status.text())
        dialog.star_citizen.setChecked(True)
        dialog._request_save()
        self.assertEqual(dialog.profile_tabs.currentIndex(), 3)
        field.setText("{ES} Destino")
        field.textEdited.emit(field.text())
        dialog._request_save()
        self.assertEqual(saved, [True])
        dialog.deleteLater()

    def test_controller_legacy_replies_move_into_catalog_and_remain_editable(self):
        from voxcomm.action_catalog import parse_action_catalog
        app = QApplication.instance() or QApplication([])
        key = reply_override_key("Star Citizen", "Gear", "EN")
        other = reply_override_key("Star Citizen", "Missing", "DE")
        config = AppConfig(special_prompts={"Star Citizen": ""},
                           active_special_prompt="Star Citizen",
                           input_action_catalogs={"Star Citizen": "Gear => gear => Button1 => {EN} Old"},
                           input_reply_overrides={key: "Ready | now", other: "Preserve me"})
        dialog = SettingsDialog(config)
        self.assertFalse(hasattr(dialog, "reply_action"))
        self.assertEqual(parse_action_catalog(dialog.input_action_catalog.toPlainText())[0]["replies"]["EN"],
                         "Ready | now")
        self.assertFalse(dialog.catalog_warning.isHidden())
        dialog.input_action_catalog.setPlainText("Gear => gear => Button1 => {EN} Edited | {ES} Listo")
        updated = dialog.apply(config)
        self.assertNotIn(key, updated.input_reply_overrides)
        self.assertEqual(updated.input_reply_overrides[other], "Preserve me")
        replies = parse_action_catalog(updated.input_action_catalogs["Star Citizen"])[0]["replies"]
        self.assertEqual(replies, {"EN": "Edited", "ES": "Listo"})
        dialog.deleteLater()

    def test_controller_legacy_replies_wait_for_invalid_catalog_repair(self):
        app = QApplication.instance() or QApplication([])
        key = reply_override_key("__global__", "Gear", "DE")
        config = AppConfig(input_action_catalogs={"__global__": "invalid"}, input_reply_overrides={key: "Bereit"})
        dialog = SettingsDialog(config)
        self.assertFalse(dialog.catalog_warning.isHidden())
        self.assertEqual(dialog.input_reply_overrides[key], "Bereit")
        dialog.input_action_catalog.setPlainText("Gear => gear => Button1 => {DE} Alt")
        updated = dialog.apply(config)
        self.assertNotIn(key, updated.input_reply_overrides)
        self.assertIn("{DE} Bereit", updated.input_action_catalogs["__global__"])
        dialog.deleteLater()

    def test_settings_scroll_at_compact_desktop_size(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(ui_language="de"))
        dialog._lookup_thinking = lambda: None
        dialog.resize(880, 600)
        dialog.show()
        app.processEvents()
        general_scroll = dialog.settings_tabs.widget(0)
        self.assertEqual(general_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(general_scroll.verticalScrollBar().maximum(), 0)
        dialog.settings_tabs.setCurrentIndex(1)
        dialog.profile_tabs.setCurrentIndex(1)
        app.processEvents()
        self.assertEqual(dialog.style(), app.style())
        dialog.close()
        dialog.deleteLater()

    def test_settings_tabs_fill_dialog_without_redundant_title(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())
        dialog._lookup_thinking = lambda: None
        dialog.show()
        app.processEvents()

        self.assertIsNone(dialog.findChild(QLabel, "dialogTitle"))
        self.assertFalse(dialog.settings_tabs.documentMode())
        self.assertFalse(dialog.profile_tabs.documentMode())
        for scroll in (dialog.settings_tabs.widget(0), *(dialog.profile_tabs.widget(index)
                                                       for index in range(dialog.profile_tabs.count()))):
            self.assertFalse(scroll.viewport().autoFillBackground())
            self.assertFalse(scroll.widget().autoFillBackground())
        self.assertEqual(dialog.settings_tabs.geometry().left(), 0)
        self.assertEqual(dialog.settings_tabs.geometry().top(), 12)
        self.assertEqual(dialog.settings_tabs.width(), dialog.width())
        self.assertTrue(dialog.windowTitle())
        save = dialog.findChild(QPushButton, "primary")
        self.assertIsNone(dialog.findChild(QFrame, "settingsFooterSeparator"))
        self.assertGreaterEqual(save.geometry().top() - dialog.settings_tabs.geometry().bottom(), 12)
        for section in dialog.findChildren(QFrame, "section"):
            self.assertEqual(section.frameShape(), QFrame.Shape.NoFrame)
        dialog.settings_tabs.setCurrentIndex(1)
        app.processEvents()
        self.assertTrue(dialog.settings_tabs.documentMode())
        self.assertFalse(dialog.profile_tabs.documentMode())
        profile_page = dialog.settings_tabs.currentWidget()
        self.assertGreaterEqual(dialog.special_profile.mapTo(profile_page, QPoint(0, 0)).y(), 12)
        profile_header = profile_page.layout().itemAt(0).layout()
        profile_label = profile_header.itemAt(0).widget()
        self.assertEqual(profile_label.mapTo(dialog, QPoint(0, 0)).x(),
                 dialog.discovery_status.mapTo(dialog, QPoint(0, 0)).x())
        self.assertEqual(dialog.remove_profile.mapTo(dialog, QPoint(dialog.remove_profile.width(), 0)).x(),
                 save.mapTo(dialog, QPoint(save.width(), 0)).x())
        self.assertEqual(dialog.profile_tabs.mapTo(dialog, QPoint(0, 0)).x(),
             dialog.discovery_status.mapTo(dialog, QPoint(0, 0)).x())
        self.assertEqual(dialog.profile_tabs.mapTo(dialog, QPoint(dialog.profile_tabs.width(), 0)).x(),
             save.mapTo(dialog, QPoint(save.width(), 0)).x())
        dialog.settings_tabs.setCurrentIndex(0)
        self.assertFalse(dialog.settings_tabs.documentMode())
        dialog.close()
        dialog.deleteLater()

    def test_settings_scroll_page_shows_native_tab_background(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())
        dialog._lookup_thinking = lambda: None
        dialog.show()
        dialog.settings_tabs.setCurrentIndex(1)
        app.processEvents()
        page = dialog.profile_tabs.currentWidget().widget()
        page_point = page.mapTo(dialog, QPoint(5, 5))
        page_color = dialog.grab().toImage().pixelColor(page_point)

        reference = QTabWidget(dialog)
        reference.setGeometry(0, 0, 300, 180)
        reference_page = QWidget()
        reference.addTab(reference_page, "Reference")
        reference.show()
        reference.raise_()
        app.processEvents()
        reference_point = reference_page.mapTo(dialog, QPoint(5, 5))
        reference_color = dialog.grab().toImage().pixelColor(reference_point)

        self.assertEqual(page_color, reference_color)
        dialog.close()
        dialog.deleteLater()

    def test_settings_editors_expand_with_the_dialog(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["input_control", "star_citizen_log"]}))
        dialog._lookup_thinking = lambda: None
        dialog.resize(1040, 720)
        dialog.show()
        dialog.settings_tabs.setCurrentIndex(1)
        minimum_sizes = {}
        editors = ((0, dialog.special_prompt), (2, dialog.input_action_catalog), (3, dialog.game_log_view))
        for index, editor in editors:
            dialog.profile_tabs.setCurrentIndex(index)
            app.processEvents()
            minimum_sizes[index] = editor.size()
        dialog.resize(1380, 1050)
        for index, editor in editors:
            dialog.profile_tabs.setCurrentIndex(index)
            app.processEvents()
            self.assertGreater(editor.width(), minimum_sizes[index].width())
            self.assertGreater(editor.height(), minimum_sizes[index].height())
        dialog.close()
        dialog.deleteLater()

    def test_audio_confirmation_labels_and_scope(self):
        app = QApplication.instance() or QApplication([])
        for language in ("en", "de"):
            with self.subTest(language=language):
                dialog = SettingsDialog(AppConfig(ui_language=language))
                expected = "Audio-Bestätigung (alle Profile)" if language == "de" else "Audio confirmation (all profiles)"
                self.assertEqual(dialog.audio_confirmation_heading.text(), expected)
                for checkbox in dialog._announce_checks.values():
                    self.assertNotIn("Announce:", checkbox.text())
                    self.assertNotIn("Ansage:", checkbox.text())
                    self.assertEqual(checkbox.toolTip(), dialog.audio_confirmation_heading.toolTip())
                self.assertTrue(dialog.audio_confirmation_heading.toolTip())
                dialog.deleteLater()

    def test_game_status_is_separate_from_indented_event_log(self):
        from voxcomm.gamelog import GameFacts
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}))
        dialog._game_log = Mock(facts=GameFacts(game_version="4.10.2", ship="C1"))
        dialog._game_log.take_events.return_value = [NAV_TARGET_SET]
        dialog._refresh_game_log()
        self.assertIn("4.10.2", dialog.game_status_view.text())
        self.assertIn("C1", dialog.game_status_view.text())
        self.assertNotIn("4.10.2", dialog.game_log_view.toPlainText())
        self.assertIn("Nav target selected", dialog.game_log_view.toPlainText())
        self.assertEqual(dialog.game_log_section.form.labelForField(dialog.game_log_view).text(), "Star Citizen Log:")
        dialog.deleteLater()

    def test_game_log_unfiltered_shows_recent_lines_and_restores_events(self):
        from voxcomm.gamelog import GameLogTracker
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "Game.log"
            path.write_text("".join(f"raw line {number}\n" for number in range(10010)), encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}))
            dialog._game_log_timer.stop()
            dialog._game_log = GameLogTracker(executable_finder=lambda: executable)
            self.assertFalse(dialog.game_log_unfiltered.isChecked())
            dialog._refresh_game_log()
            self.assertEqual(dialog.game_log_view.toPlainText(), "")
            dialog.game_log_unfiltered.setChecked(True)
            self._wait_for_raw_game_log(app, dialog)
            lines = dialog.game_log_view.toPlainText().splitlines()
            self.assertEqual(len(lines), 10000)
            self.assertEqual((lines[0], lines[-1]), ("raw line 10", "raw line 10009"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write("new raw line\nYou have joined channel 'C1 : Player'\n"
                             "ClearDriver: Local client node [99] releasing control token for 'CRUS_Spirit_C1_123456' [123456]\n"
                             "| CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::CalculateRoute|Successfully calculated route to ArcCorp fuel estimate 42.0\n")
            dialog._refresh_game_log()
            self._wait_for_raw_game_log(app, dialog)
            lines = dialog.game_log_view.toPlainText().splitlines()
            self.assertEqual(len(lines), 10000)
            self.assertEqual(lines[0], "raw line 14")
            self.assertIn("new raw line", lines)
            path.write_text("new session\n", encoding="utf-8")
            dialog._refresh_game_log()
            self._wait_for_raw_game_log(app, dialog)
            self.assertEqual(dialog.game_log_view.toPlainText(), "new session")
            dialog.game_log_unfiltered.setChecked(False)
            self.assertIn("Nav target selected", dialog.game_log_view.toPlainText())
            self.assertNotIn("new raw line", dialog.game_log_view.toPlainText())
            dialog.deleteLater()

    def _wait_for_raw_game_log(self, app, dialog):
        for _ in range(200):
            app.processEvents()
            if not dialog._raw_log_busy:
                return
            QTest.qWait(10)
        self.fail("Raw game log read did not finish")

    def test_game_log_discards_raw_read_when_permission_is_revoked(self):
        from voxcomm.gamelog import GameLogTracker
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "Game.log"
            path.write_text("private line\n", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            dialog = SettingsDialog(AppConfig(enabled_tools={"__global__": ["star_citizen_log"]}))
            dialog._game_log_timer.stop()
            dialog._game_log = GameLogTracker(executable_finder=lambda: executable)
            started = threading.Event()
            release = threading.Event()

            def read_lines(_reader):
                started.set()
                release.wait(2)
                return True, ["private line"]

            with patch("voxcomm.app.RecentLogReader.read_new_lines", autospec=True, side_effect=read_lines):
                try:
                    dialog.game_log_unfiltered.setChecked(True)
                    self.assertTrue(started.wait(1))
                    dialog.star_citizen.setChecked(False)
                finally:
                    release.set()
                self._wait_for_raw_game_log(app, dialog)
            self.assertEqual(dialog.game_log_view.toPlainText(), "")
            dialog.deleteLater()

    def test_general_settings_fit_minimum_for_all_ui_languages_and_providers(self):
        app = QApplication.instance() or QApplication([])
        for language in ("en", "de"):
            for provider in ("ollama", "ollama-cloud", "llama.cpp"):
                with self.subTest(language=language, provider=provider):
                    dialog = SettingsDialog(AppConfig(ui_language=language, llm_provider=provider))
                    dialog._lookup_thinking = lambda: None
                    dialog.resize(dialog.minimumSize())
                    dialog.show()
                    app.processEvents()
                    scroll = dialog.settings_tabs.widget(0)
                    self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                    self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                    if provider == "ollama-cloud":
                        self.assertTrue(dialog.llm_section.form.isRowVisible(dialog.key_row))
                    dialog.close()
                    dialog.deleteLater()

    def test_profile_buttons_preserve_profile_data_through_rename(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig()
        dialog = SettingsDialog(config)
        dialog.settings_tabs.setCurrentIndex(1)
        with patch("voxcomm.app.QInputDialog.getText", return_value=("Work", True)):
            dialog.add_profile.click()
        self.assertEqual(dialog.special_profile.currentData(), "Work")
        dialog.special_prompt.setPlainText("Work instructions")
        dialog.profile_model.setCurrentText("work-model")
        dialog.profile_tabs.setCurrentIndex(1)
        dialog.web_search.setChecked(True)
        dialog.input_control.setChecked(True)
        dialog.profile_tabs.setCurrentIndex(2)
        catalog = "Open => open => Button1 => {EN} Open"
        dialog.input_action_catalog.setPlainText(catalog)
        with patch("voxcomm.app.QInputDialog.getText", return_value=("Office", True)):
            dialog.rename_profile.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.special_prompts, {"Office": "Work instructions"})
        self.assertEqual(updated.profile_models, {"Office": "work-model"})
        self.assertEqual(updated.enabled_tools, {"Office": ["web_search", "input_control"]})
        self.assertEqual(updated.input_action_catalogs, {"Office": catalog})
        self.assertEqual(updated.active_special_prompt, "Office")
        dialog.deleteLater()

    def test_settings_action_reuses_the_existing_dialog(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with patch("voxcomm.app.SettingsDialog") as dialog_class:
                overlay._settings()
                overlay._settings()

            dialog_class.assert_called_once_with(overlay.config, reset_position_callback=overlay.reset_overlay_position)
            self.assertEqual(dialog_class.return_value.show.call_count, 2)
            self.assertEqual(dialog_class.return_value.raise_.call_count, 2)
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_overlay_position_can_be_reset_from_tray_settings(self):
        app = QApplication.instance() or QApplication([])
        with (patch("voxcomm.app.load_config", return_value=AppConfig(tray_mode=True, overlay_x=400, overlay_y=300)),
              patch("voxcomm.app.PttListener"),
              patch("voxcomm.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True),
              patch("voxcomm.app.save_overlay_position") as save_position):
            overlay = Overlay()
            overlay._settings()
            dialog = overlay._settings_dialog
            self.assertTrue(dialog.reset_position.isEnabled())
            dialog.reset_position.click()
            save_position.assert_called_once_with(overlay.x(), overlay.y())
            self.assertEqual((overlay.config.overlay_x, overlay.config.overlay_y), (overlay.x(), overlay.y()))
            dialog.close()
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_auto_read_is_independent_per_profile(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(auto_speak=True, special_prompts={"Work": ""})
        dialog = SettingsDialog(config)
        self.assertTrue(dialog.auto_speak.isChecked())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Work"))
        self.assertTrue(dialog.auto_speak.isChecked())
        dialog.auto_speak.setChecked(False)
        with patch("voxcomm.app.QInputDialog.getText", return_value=("Office", True)):
            dialog._rename_special_profile()
        updated = dialog.apply(config)
        self.assertEqual(updated.profile_auto_speak, {"__global__": True, "Office": False})
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertTrue(dialog.auto_speak.isChecked())
        dialog.deleteLater()

    def test_saving_settings_keeps_the_dialog_open(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            with patch("voxcomm.app.save_config"):
                overlay = Overlay()
                overlay._settings()
                dialog = overlay._settings_dialog
                dialog.auto_speak.setChecked(True)
                dialog.findChild(QPushButton, "primary").click()

                self.assertTrue(dialog.isVisible())
                self.assertTrue(overlay.config.auto_speak)
                self.assertIs(overlay._settings_dialog, dialog)

                dialog.close()
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()

    def test_app_starts_and_stops_only_its_own_gamepad_daemon(self):
        from voxcomm.app import Overlay

        overlay = Mock()
        overlay._gamepad_owned = False
        with patch("voxcomm.gamepad.status", side_effect=OSError("not running")):
            with patch("voxcomm.gamepad.start", return_value="VoxComm controller started (100 buttons)") as start:
                Overlay._start_gamepad(overlay)

        self.assertTrue(overlay._gamepad_owned)
        start.assert_called_once_with()

        with patch("voxcomm.gamepad.stop") as stop:
            Overlay._stop_gamepad(overlay)

        self.assertFalse(overlay._gamepad_owned)
        stop.assert_called_once_with()

    def test_app_does_not_claim_or_stop_an_already_running_gamepad(self):
        from voxcomm.app import Overlay

        overlay = Mock()
        overlay._gamepad_owned = False
        with patch("voxcomm.gamepad.status", return_value="VoxComm: running, 100 buttons"):
            with patch("voxcomm.gamepad.start") as start:
                Overlay._start_gamepad(overlay)
        start.assert_not_called()

        with patch("voxcomm.gamepad.stop") as stop:
            Overlay._stop_gamepad(overlay)
        stop.assert_not_called()

    def test_missing_optional_gamepad_dependency_does_not_abort_app_startup(self):
        from voxcomm.app import Overlay

        overlay = Mock()
        with patch.dict("sys.modules", {"voxcomm.gamepad": None}):
            Overlay._start_gamepad(overlay)

        overlay._set_status.assert_called_once()
        self.assertIn("Controller unavailable", overlay._set_status.call_args.args[0])

    def test_discovery_buttons_are_icon_only_with_tooltips(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        self.assertEqual(dialog.styleSheet(), "")
        for button in (dialog.profile_voice_refresh, dialog.profile_model_refresh):
            self.assertIsInstance(button, QToolButton)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.text(), "")
            self.assertIn("Load", button.toolTip())
            self.assertEqual(button.styleSheet(), "")

    def test_discovery_buttons_fall_back_when_theme_icon_is_missing(self):
        app = QApplication.instance() or QApplication([])
        with patch.object(QIcon, "fromTheme", return_value=QIcon()) as from_theme:
            dialog = SettingsDialog(AppConfig())

        self.assertEqual(from_theme.call_count, 2)
        for button in (dialog.profile_voice_refresh, dialog.profile_model_refresh):
            self.assertFalse(button.icon().isNull())
            self.assertFalse(button.icon().pixmap(button.iconSize()).isNull())
        dialog.deleteLater()

    def test_discovery_button_renders_its_icon(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())
        dialog._lookup_thinking = lambda: None
        dialog.settings_tabs.setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        button = dialog.profile_model_refresh
        with_icon = button.grab().toImage()
        button.setIcon(QIcon())
        app.processEvents()
        without_icon = button.grab().toImage()

        self.assertTrue(any(with_icon.pixel(x, y) != without_icon.pixel(x, y)
                            for x in range(with_icon.width()) for y in range(with_icon.height())))
        dialog.close()
        dialog.deleteLater()

    def test_settings_and_tray_use_native_style_with_overlay_parent(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            dialog = SettingsDialog(AppConfig(), overlay)

            for widget in (dialog, dialog.profile_model, dialog.special_prompt, overlay.tray_menu):
                self.assertEqual(widget.styleSheet(), "")
                self.assertEqual(widget.style(), app.style())

            dialog.deleteLater()
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_settings_save_global_prompt_and_auto_speak(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": "Ship context"})
        dialog = SettingsDialog(config)

        self.assertEqual(dialog.special_profile.itemText(0), "Default")
        dialog.web_search.setChecked(True)
        dialog.special_prompt.setPlainText("Editable global instructions")
        dialog.auto_speak.setChecked(True)
        dialog.tray_mode.setChecked(True)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog.special_prompt.setPlainText("Edited ship context")

        updated = dialog.apply(config)

        self.assertEqual(updated.global_prompt, "Editable global instructions")
        self.assertEqual(updated.special_prompts["Star Citizen"], "Edited ship context")
        self.assertTrue(updated.auto_speak)
        self.assertTrue(updated.tray_mode)
        self.assertEqual(updated.active_special_prompt, "Star Citizen")
        self.assertEqual(updated.enabled_tools, {"__global__": ["web_search"]})

    def test_web_search_permission_is_independent_per_prompt_profile(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(special_prompts={"Star Citizen": "Ship context"}))

        self.assertFalse(dialog.web_search.isChecked())
        dialog.web_search.setChecked(True)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertFalse(dialog.web_search.isChecked())
        dialog.web_search.setChecked(True)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertTrue(dialog.web_search.isChecked())

        updated = dialog.apply(AppConfig(special_prompts={"Star Citizen": "Ship context"}))

        self.assertEqual(updated.enabled_tools, {
            "__global__": ["web_search"],
            "Star Citizen": ["web_search"],
        })

    def test_star_citizen_log_permission_keeps_legacy_key_and_is_profile_scoped(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": "Ship context"},
                           enabled_tools={"__global__": ["star_citizen"]})
        dialog = SettingsDialog(config)

        self.assertEqual(dialog.star_citizen.text(), "Star Citizen Log")
        self.assertTrue(dialog.star_citizen.isChecked())
        self.assertTrue(dialog.star_citizen_uex.isChecked())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertFalse(dialog.star_citizen.isChecked())
        self.assertFalse(dialog.star_citizen_uex.isChecked())
        dialog.star_citizen.setChecked(True)

        updated = dialog.apply(config)

        self.assertEqual(updated.enabled_tools, {
            "__global__": ["star_citizen"],
            "Star Citizen": ["star_citizen_log"],
        })

    def test_mangohud_status_includes_spoken_text_and_ai_reply(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.publish_status") as publish:
            overlay = Overlay()
            overlay._show_message("Ready · response generated locally", 'You: Flight ready\nAI: "Right Alt + R sent."')

            self.assertEqual(publish.call_args.args[0], 'You: Flight ready\nAI: "Right Alt + R sent."')
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_mangohud_keeps_reply_visible_while_piper_speaks(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.publish_status") as publish:
            with patch("voxcomm.app.threading.Thread"):
                overlay = Overlay()
                expected = 'You: Flight ready\nAI: "Right Alt + R sent."'
                overlay._show_message("Ready · response generated locally", 'You: Flight ready\nAI: "Right Alt + R sent."')

                overlay._start_speak('"Right Alt + R gesendet."')
                self.assertEqual(publish.call_args.args[0], expected)

                overlay._show_message("Ready · press PTT to speak", "")
                self.assertEqual(publish.call_args.args[0], expected)
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()

    def test_input_control_permission_is_independent_per_prompt_profile(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(special_prompts={"Star Citizen": "Ship context"}))

        self.assertFalse(dialog.input_control.isChecked())
        self.assertFalse(dialog.action_catalog_section.isVisible())
        dialog.input_control.setChecked(True)
        self.assertFalse(dialog.action_catalog_section.isHidden())
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertFalse(dialog.input_control.isChecked())
        self.assertTrue(dialog.action_catalog_section.isHidden())
        dialog.input_control.setChecked(True)
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertTrue(dialog.input_control.isChecked())
        self.assertFalse(dialog.action_catalog_section.isHidden())

        updated = dialog.apply(AppConfig(special_prompts={"Star Citizen": "Ship context"}))

        self.assertEqual(updated.enabled_tools, {
            "__global__": ["input_control"],
            "Star Citizen": ["input_control"],
        })

    def test_action_catalog_is_saved_per_profile(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(special_prompts={"Star Citizen": "Ship context"}))
        dialog.input_action_catalog.setPlainText(
            "Flight Systems On => flight ready => Button1 => Flight Systems on\n"
            "Flight Systems Off => flight systems off => Button2 => Flight Systems off"
        )
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog.input_action_catalog.setPlainText(
            "Landing Gear On => gear down => Button3 => Gear extended\n"
            "Landing Gear Off => gear up => Button4 => Gear retracted"
        )

        updated = dialog.apply(AppConfig(special_prompts={"Star Citizen": "Ship context"}))

        self.assertEqual(updated.input_action_catalogs, {
            "__global__": (
                "Flight Systems On => flight ready => Button1 => Flight Systems on\n"
                "Flight Systems Off => flight systems off => Button2 => Flight Systems off"
            ),
            "Star Citizen": (
                "Landing Gear On => gear down => Button3 => Gear extended\n"
                "Landing Gear Off => gear up => Button4 => Gear retracted"
            ),
        })

    def test_reasoning_mode_is_saved_directly_per_profile(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(
            special_prompts={"Star Citizen": "Ship context"},
            profile_thinking={"__global__": "medium"},
        ))
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        dialog.profile_thinking.setCurrentIndex(dialog.profile_thinking.findData("low"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog.profile_thinking.setCurrentIndex(dialog.profile_thinking.findData("high"))

        updated = dialog.apply(AppConfig(special_prompts={"Star Citizen": "Ship context"},
                         profile_thinking={"__global__": "medium"}))

        self.assertEqual(updated.profile_thinking, {"__global__": "low", "Star Citizen": "high"})
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertEqual(dialog.profile_thinking.currentData(), "low")

    def test_model_and_voice_are_independent_direct_profile_settings(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(
            profile_models={"__global__": "global-model"},
            profile_voices={"__global__": "global-voice"},
            special_prompts={"Star Citizen": "Ship context", "Work": "Work context"},
        ))
        self.assertTrue(dialog.profile_model.isEnabled())
        self.assertTrue(dialog.profile_voice.isEnabled())
        self.assertEqual(dialog.profile_voice.currentText(), "global-voice")
        dialog.profile_voice.setCurrentText("de_DE-global")

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertTrue(dialog.profile_model.isEnabled())
        self.assertTrue(dialog.profile_voice.isEnabled())
        dialog.profile_model.setCurrentText("qwen3:8b")
        dialog.profile_voice.setCurrentText("de_DE-thorsten-high")
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Work"))
        self.assertEqual(dialog.profile_model.currentText(), "")
        self.assertEqual(dialog.profile_voice.currentText(), "")
        dialog.profile_model.setCurrentText("llama3.2")
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("English"))
        updated = dialog.apply(AppConfig(
            profile_models={"__global__": "global-model"},
            profile_voices={"__global__": "global-voice"},
            special_prompts={"Star Citizen": "Ship context", "Work": "Work context"},
        ))

        self.assertEqual(updated.profile_models, {
            "__global__": "global-model",
            "Star Citizen": "qwen3:8b",
            "Work": "llama3.2",
        })
        self.assertEqual(updated.profile_voices, {
            "__global__": "de_DE-global",
            "Star Citizen": "de_DE-thorsten-high",
        })
        self.assertEqual(updated.profile_output_languages["Work"], "English")

    def test_language_settings_migrate_switch_and_follow_profile_rename(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(stt_language="fr", tts_language="French", voice_language="fr",
                           special_prompts={"Work": ""})
        dialog = SettingsDialog(config)
        self.assertTrue(dialog.profile_settings_section.isAncestorOf(dialog.stt_language))
        self.assertTrue(dialog.profile_settings_section.isAncestorOf(dialog.profile_response_language))
        self.assertFalse(hasattr(dialog, "tts_language"))
        self.assertFalse(hasattr(dialog, "voice_language"))
        dialog.stt_language.setCurrentIndex(dialog.stt_language.findData("en"))
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("English"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Work"))
        self.assertEqual((dialog.stt_language.currentData(), dialog.profile_response_language.currentData()),
                 ("fr", "French"))
        dialog.stt_language.setCurrentIndex(dialog.stt_language.findData("auto"))
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("German"))
        with patch("voxcomm.app.QInputDialog.getText", return_value=("Office", True)):
            dialog._rename_special_profile()
        updated = dialog.apply(config)
        self.assertEqual(updated.profile_stt_languages, {"__global__": "en", "Office": "auto"})
        self.assertEqual(updated.profile_tts_languages, {"__global__": "French", "Office": "French"})
        self.assertEqual(updated.profile_voice_languages, {"__global__": "fr", "Office": "fr"})
        self.assertEqual(updated.profile_output_languages, {"__global__": "English", "Office": "German"})
        self.assertEqual((updated.stt_language, updated.tts_language, updated.voice_language),
                         ("en", "French", "fr"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertEqual((dialog.stt_language.currentData(), dialog.profile_response_language.currentData()),
                 ("en", "English"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Office"))
        with patch("voxcomm.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog._remove_special_profile()
        updated = dialog.apply(config)
        self.assertNotIn("Office", updated.profile_stt_languages)
        self.assertNotIn("Office", updated.profile_tts_languages)
        self.assertNotIn("Office", updated.profile_voice_languages)
        dialog.deleteLater()

    def test_new_profile_languages_use_independent_defaults(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(stt_language="fr", tts_language="French", voice_language="fr")
        dialog = SettingsDialog(config)
        with patch("voxcomm.app.QInputDialog.getText", return_value=("Work", True)):
            dialog._add_special_profile()
        self.assertEqual((dialog.stt_language.currentData(), dialog.profile_response_language.currentData()),
                 ("de", "English"))
        updated = dialog.apply(config)
        self.assertEqual(updated.profile_stt_languages["Work"], "de")
        self.assertEqual(updated.profile_tts_languages["Work"], "")
        self.assertEqual(updated.profile_voice_languages["Work"], "")
        dialog.deleteLater()

    def test_voice_language_filter_follows_the_selected_profile(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Work": ""},
                   profile_output_languages={"__global__": "German", "Work": "English"},
                           profile_voices={"__global__": "de_DE-thorsten", "Work": "en_US-amy"})
        dialog = SettingsDialog(config)
        dialog._available_voices = ["de_DE-thorsten", "en_US-amy"]
        dialog._refresh_voice_options()
        self.assertEqual(dialog.profile_voice.currentText(), "de_DE-thorsten")
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Work"))
        self.assertEqual(dialog.profile_response_language.currentData(), "English")
        self.assertEqual(dialog.profile_voice.currentText(), "en_US-amy")
        self.assertEqual([dialog.profile_voice.itemText(index) for index in range(dialog.profile_voice.count())],
                         ["en_US-amy"])
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertEqual(dialog.profile_voice.currentText(), "de_DE-thorsten")
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("English"))
        self.assertEqual([dialog.profile_voice.itemText(index) for index in range(dialog.profile_voice.count())],
                 ["en_US-amy"])
        dialog.deleteLater()

    def test_legacy_output_language_alias_filters_piper_voices(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(profile_response_languages={"__global__": "Deutsch"}))
        dialog._available_voices = ["de_DE-thorsten", "en_US-amy"]
        dialog._refresh_voice_options()
        self.assertEqual(dialog.profile_response_language.currentData(), "Deutsch")
        self.assertEqual([dialog.profile_voice.itemText(index) for index in range(dialog.profile_voice.count())],
                         ["de_DE-thorsten"])
        dialog.deleteLater()

    def test_profile_voice_is_used_for_speech_output_without_global_fallback(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.load_config", return_value=AppConfig(
                profile_voices={"__global__": "global-voice"})):
            with patch("voxcomm.app.PttListener"):
                overlay = Overlay()
                with patch("voxcomm.app.speak_stream_sync", return_value=[b"audio"]) as speak:
                    self.assertEqual(list(overlay._speech_chunks(["Hallo"], "profile-voice")), [b"audio"])

                speak.assert_called_once_with("Hallo", "tcp://127.0.0.1:10200", "profile-voice")
                overlay.listener.stop()
                overlay.tray_icon.hide()
                overlay.deleteLater()


class ProviderSelectionTests(unittest.TestCase):
    def test_api_keys_are_optional_masked_and_saved_per_server(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())
        for field in (dialog.api_key, dialog.stt_api_key, dialog.tts_api_key):
            self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse(dialog.stt_api_key.isEnabled())
        self.assertFalse(dialog.tts_api_key.isEnabled())
        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("whisper.cpp"))
        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))
        self.assertTrue(dialog.stt_api_key.isEnabled())
        self.assertTrue(dialog.tts_api_key.isEnabled())
        dialog.api_key.setText(" llm ")
        dialog.stt_api_key.setText(" stt ")
        dialog.tts_api_key.setText(" tts ")
        config = dialog.apply(AppConfig())
        self.assertEqual((config.ollama_api_key, config.stt_api_key, config.tts_api_key), ("llm", "stt", "tts"))
        dialog.deleteLater()

    def test_discovery_uses_the_matching_server_key(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(tts_provider="openai", ollama_api_key="llm-key", tts_api_key="tts-key"))
        with (patch("voxcomm.app.threading.Thread") as thread,
              patch("voxcomm.app.list_models", return_value=[]) as models,
              patch("voxcomm.app.openai_tts.list_voices", return_value=[]) as voices):
            dialog._discover("models")
            thread.call_args.kwargs["target"]()
            models.assert_called_once_with("ollama", dialog.llm_url.text(), "llm-key")
            dialog._discover("voices")
            thread.call_args.kwargs["target"]()
            voices.assert_called_once_with(dialog.tts.text(), "tts-key")
        dialog.deleteLater()

    def test_cloud_provider_shows_api_key_row_and_swaps_url(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(llm_provider="ollama-cloud", ollama_api_key="k",
                                          llm_url="http://127.0.0.1:11434"))

        self.assertTrue(dialog.llm_section.form.isRowVisible(dialog.key_row))
        self.assertEqual(dialog.llm_url.text(), "https://ollama.com")
        self.assertEqual(dialog.provider.currentData(), "ollama-cloud")
        self.assertEqual(dialog.api_key.text(), "k")
        self.assertEqual(dialog.api_key.echoMode(), QLineEdit.EchoMode.Password)

        dialog.provider.setCurrentIndex(dialog.provider.findData("ollama"))

        self.assertTrue(dialog.llm_section.form.isRowVisible(dialog.key_row))
        self.assertTrue(dialog.key_test.isHidden())
        self.assertEqual(dialog.llm_url.text(), "http://127.0.0.1:11434")

    def test_cloud_models_are_loaded_from_cache_without_http(self):
        app = QApplication.instance() or QApplication([])
        with (
            patch("voxcomm.app.cloud.cached_models", return_value=(["m1", "m2"], time.time())),
            patch("voxcomm.app.cloud.list_cloud_models") as listing,
        ):
            dialog = SettingsDialog(AppConfig(llm_provider="ollama-cloud", profile_models={"__global__": "m2"}))

        listing.assert_not_called()
        self.assertEqual([dialog.profile_model.itemText(index) for index in range(dialog.profile_model.count())], ["m1", "m2"])
        self.assertEqual(dialog.profile_model.currentText(), "m2")
        self.assertIn("cache", dialog.discovery_status.text())

    def test_stale_cloud_cache_triggers_refresh(self):
        app = QApplication.instance() or QApplication([])
        stale = time.time() - 2 * 24 * 60 * 60
        with (
            patch("voxcomm.app.cloud.cached_models", return_value=(["old"], stale)),
            patch("voxcomm.app.threading.Thread") as thread_class,
        ):
            SettingsDialog(AppConfig(llm_provider="ollama-cloud"))

        thread_class.assert_called_once()

    def test_apply_carries_new_provider_and_key_fields(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(profile_voices={"__global__": "de_DE-thorsten-low"}))
        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("whisper.cpp"))
        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("English"))
        dialog.provider.setCurrentIndex(dialog.provider.findData("ollama-cloud"))
        dialog.api_key.setText(" secret ")
        dialog.profile_voice.setCurrentText("data")

        updated = dialog.apply(AppConfig())

        self.assertEqual(updated.stt_provider, "whisper.cpp")
        self.assertEqual(updated.tts_provider, "openai")
        self.assertEqual(updated.profile_output_languages["__global__"], "English")
        self.assertEqual(updated.ollama_api_key, "secret")
        self.assertEqual(updated.llm_provider, "ollama-cloud")
        self.assertEqual(dialog.profile_response_language.currentData(), "English")

    def test_openai_interface_hides_voice_language_and_swaps_url(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(tts_url="tcp://127.0.0.1:10200"))

        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))

        self.assertEqual(dialog.tts.text(), "http://127.0.0.1:8097")
        self.assertFalse(hasattr(dialog, "voice_language"))

        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("wyoming"))

        self.assertEqual(dialog.tts.text(), "tcp://127.0.0.1:10200")
        self.assertFalse(hasattr(dialog, "voice_language"))

    def test_profile_response_language_does_not_fall_back_to_global_tts_language(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(
            tts_provider="openai",
            tts_language="German",
            profile_voices={"__global__": "data"},
        ))

        self.assertEqual(dialog.profile_response_language.currentData(), "German")
        dialog.profile_response_language.setCurrentIndex(
            dialog.profile_response_language.findData("French"))
        self.assertEqual(dialog.profile_response_language.currentData(), "French")

        dialog.deleteLater()

    def test_stt_interface_url_default_swap(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("whisper.cpp"))
        self.assertEqual(dialog.stt.text(), "http://127.0.0.1:8081")

        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("wyoming"))
        self.assertEqual(dialog.stt.text(), "tcp://127.0.0.1:10300")

    def test_custom_urls_are_never_overwritten(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(stt_url="http://127.0.0.1:8081/v1/audio/transcriptions",
                                          tts_url="http://127.0.0.1:9999/custom"))

        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("whisper.cpp"))
        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))

        self.assertEqual(dialog.stt.text(), "http://127.0.0.1:8081/v1/audio/transcriptions")
        self.assertEqual(dialog.tts.text(), "http://127.0.0.1:9999/custom")

    def test_key_test_reports_valid_and_invalid_key(self):
        app = QApplication.instance() or QApplication([])
        with (
            patch("voxcomm.app.cloud.cached_models", return_value=(["m"], time.time())),
            patch("voxcomm.app.threading.Thread") as thread_class,
        ):
            dialog = SettingsDialog(AppConfig(llm_provider="ollama-cloud", ollama_api_key="k"))

            dialog.key_test.click()
            self.assertFalse(dialog.key_test.isEnabled())
            self.assertIn("Verifying", dialog.discovery_status.text())
            thread_class.assert_called()
            self.assertEqual(thread_class.call_args.kwargs["target"].__name__, "run")

        dialog.discovery.discovery.emit("key", [], "")
        self.assertTrue(dialog.key_test.isEnabled())
        self.assertEqual(dialog.discovery_status.text(), "API key is valid")

        dialog.discovery.discovery.emit("key", [], "401")
        self.assertIn("Invalid API key", dialog.discovery_status.text())

    def test_openai_voice_listing_is_not_language_filtered(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.cloud.cached_models", return_value=([], 0.0)):
            with patch("voxcomm.app.threading.Thread"):
                dialog = SettingsDialog(AppConfig(tts_provider="openai", llm_provider="ollama"))
        dialog._available_voices = ["data"]
        dialog.profile_response_language.setCurrentIndex(dialog.profile_response_language.findData("German"))

        dialog._refresh_voice_options()

        self.assertEqual(dialog.profile_voice.currentText(), "data")

    def test_overlay_dispatches_to_local_backends(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(stt_provider="whisper.cpp", tts_provider="openai",
                           stt_url="http://127.0.0.1:8081", tts_url="http://127.0.0.1:8097",
                           profile_voices={"__global__": "data"}, tts_language="German", sample_rate=16000)
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.whisper_cpp.transcribe", return_value="Hallo") as whisper,
                patch("voxcomm.app.transcribe_sync") as wyoming,
                patch("voxcomm.app.chat", return_value="Antwort"),
            ):
                overlay._capture(threading.Event())

            whisper.assert_called_once_with(b"\0" * 9600, "http://127.0.0.1:8081", 16000, "de")
            wyoming.assert_not_called()

            with (
                patch("voxcomm.app.openai_tts.speak_stream", return_value=iter([(b"\0\0", 24000)])) as openai_speak,
                patch("voxcomm.app.speak_stream_sync") as piper,
                patch("voxcomm.app.play_stream", side_effect=lambda chunks, _cancel: list(chunks)),
            ):
                overlay._speak("Antwort", threading.Event())

            openai_speak.assert_called_once_with("Antwort", "http://127.0.0.1:8097", "data", "German")
            piper.assert_not_called()
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_cloud_key_is_passed_to_chat_and_voice_is_not_a_language(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(llm_provider="ollama-cloud", llm_url="https://ollama.com",
                           ollama_api_key="secret", tts_provider="openai",
                           profile_voices={"__global__": "data"},
                           profile_response_languages={"__global__": "Deutsch"})
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            requests = []
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Hallo"),
                patch("voxcomm.app.chat", side_effect=lambda *a, **k: requests.append(k) or "Antwort"),
            ):
                overlay._capture(threading.Event())

            self.assertEqual(requests[0]["api_key"], "secret")
            self.assertIn("Deutsch", requests[0]["system_prompt"])
            self.assertNotIn("DATA", requests[0]["system_prompt"])
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_thinking_setting_is_passed_to_chat(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(profile_thinking={"__global__": "off"})
        with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Hallo"),
                patch("voxcomm.app.chat", return_value="Antwort") as chat,
            ):
                overlay._capture(threading.Event())

            self.assertEqual(chat.call_args.kwargs["thinking"], "off")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_thinking_choices_follow_the_models_reported_controls(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(profile_models={"__global__": "gemma4"},
                          profile_thinking={"__global__": "off"}))

        self.assertEqual(dialog.profile_thinking.currentData(), "off")
        self.assertEqual(dialog.profile_thinking.currentText(), "Off")
        dialog._apply_thinking_options(dialog._thinking_token, ["off", "on"])
        self.assertEqual([dialog.profile_thinking.itemText(i) for i in range(dialog.profile_thinking.count())],
                         ["Model default", "Off", "On"])
        self.assertEqual(dialog.profile_thinking.currentData(), "off")

        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        self.assertEqual([dialog.profile_thinking.itemData(i) for i in range(dialog.profile_thinking.count())],
                         ["", "low", "medium", "high"])
        self.assertEqual(dialog.profile_thinking.currentData(), "")
        self.assertEqual(dialog.apply(AppConfig()).profile_thinking, {"__global__": "off"})

        dialog.profile_thinking.setCurrentIndex(dialog.profile_thinking.findData("high"))
        dialog.profile_thinking.activated.emit(dialog.profile_thinking.currentIndex())
        self.assertEqual(dialog.apply(AppConfig()).profile_thinking, {"__global__": "high"})
        dialog.deleteLater()

    def test_model_without_thinking_controls_disables_the_choice_but_keeps_the_setting(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(profile_models={"__global__": "deepseek-v2"},
                          profile_thinking={"__global__": "high"}))

        dialog._apply_thinking_options(dialog._thinking_token, [])

        self.assertFalse(dialog.profile_thinking.isEnabled())
        self.assertEqual(dialog.profile_thinking.currentText(), "Not controlled by model")
        self.assertEqual(dialog.apply(AppConfig()).profile_thinking, {"__global__": "high"})
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        self.assertTrue(dialog.profile_thinking.isEnabled())
        self.assertEqual(dialog.profile_thinking.currentData(), "high")
        dialog.deleteLater()

    def test_stale_thinking_answer_for_a_previous_model_is_ignored(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(profile_models={"__global__": "a"}))
        with patch("voxcomm.app.threading.Thread"):
            dialog._lookup_thinking()
            stale = dialog._thinking_token
            dialog._lookup_thinking()

        dialog._apply_thinking_options(stale, ["low", "high"])

        self.assertEqual(dialog.profile_thinking.count(), 1)
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "high"])
        self.assertEqual(dialog.profile_thinking.count(), 3)
        dialog.deleteLater()

    def test_thinking_lookup_runs_off_the_ui_thread_with_current_connection_values(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(llm_provider="ollama", llm_url="http://h:11434",
                          profile_models={"__global__": "gpt-oss"}))
        received = []
        dialog.discovery.thinking.connect(lambda token, options: received.append((token, options)))

        with patch("voxcomm.app.thinking_options", return_value=["low", "medium", "high"]) as lookup:
            dialog._lookup_thinking()
            for _ in range(100):
                app.processEvents()
                if received:
                    break
                QTest.qWait(10)

        lookup.assert_any_call("ollama", "http://h:11434", "gpt-oss", "")
        self.assertEqual(received, [(dialog._thinking_token, ["low", "medium", "high"])])
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()