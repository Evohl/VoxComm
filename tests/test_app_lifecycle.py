import os
import signal
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLabel, QLineEdit, QMessageBox, QPushButton, QSystemTrayIcon,
    QTableWidgetItem, QToolButton, QWidget,
)

from voxcomm.app import (
    COMPACT_WIDTH, FULL_WIDTH,
    SPEAKING_STATUS, TRANSCRIPT_MAX_HEIGHT, TYPE_INTERVAL_MS,
    HotkeyField, Overlay,
    SettingsDialog,
    configure_application, prefer_x11_on_wayland, transcript_segments,
)
from voxcomm.config import AppConfig
from voxcomm.theme import rgba, system_accent, system_surface


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._config_patch = patch("voxcomm.app.load_config", return_value=AppConfig())
        self._config_patch.start()

    def tearDown(self):
        self._config_patch.stop()

    def test_settings_refresh_rows_have_spacing_and_numeric_controls_are_compact(self):
        dialog = SettingsDialog(AppConfig())

        self.assertEqual(dialog._voice_row.spacing(), 10)
        self.assertEqual(dialog._model_row.spacing(), 10)
        self.assertEqual(dialog.sample_rate.height(), 30)
        self.assertEqual(dialog.overlay_hide_seconds.height(), 30)

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
            overlay._show_request_message("Veraltete Antwort", "Du: Wie macht die Kuh?\n", previous_request)
            self.assertIn("Aufnahme läuft", overlay.status.text())

            overlay._show_request_message("Denke nach …", "Du: Wetter morgen?\n", new_request)
            self.assertEqual(overlay._typing_text, "Du: Wetter morgen?\n")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_new_recording_clears_previous_transcript_and_stops_typing(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread"):
            overlay = Overlay()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Wie macht die Kuh?\nA.I.: Muh")
            overlay._type_next_character()

            overlay._ptt_event("pressed", "")

            self.assertFalse(overlay._type_timer.isActive())
            self.assertEqual(overlay.transcript.toPlainText(), "")
            overlay._show_message("Denke nach …", "Du: Wetter morgen?\n")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertEqual(overlay.transcript.toPlainText(), "Du: Wetter morgen?\n")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_tray_click_toggles_recording_and_menu_contains_requested_actions(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.threading.Thread") as thread_class:
            overlay = Overlay()
            overlay._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(overlay._recording)
            self.assertEqual(overlay.tray_toggle_action.text(), "Aufnahme senden")
            first_stop = thread_class.call_args.kwargs["args"][0]

            overlay._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertFalse(overlay._recording)
            self.assertTrue(first_stop.is_set())
            self.assertEqual(overlay.tray_toggle_action.text(), "Aufnahme starten")
            self.assertEqual(
                [action.text() for action in overlay.tray_menu.actions() if not action.isSeparator()],
                ["Aufnahme starten", "Einstellungen …", "Letzte Antwort erneut vorlesen", "Beenden"],
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
                           special_hotkeys={"Elite": "f11", "Gone": "f12"})
        dialog = SettingsDialog(config)

        self.assertFalse(dialog.profile_hotkey.isEnabled())
        self.assertEqual(dialog.special_hotkeys, {"Elite": "f11"})

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        self.assertTrue(dialog.profile_hotkey.isEnabled())
        self.assertEqual(dialog.profile_hotkey.value(), "")
        dialog.profile_hotkey._apply("f9")
        self.assertEqual(dialog.profile_hotkey.value(), "")
        self.assertIn("Push-to-talk", dialog.discovery_status.text())
        dialog.profile_hotkey._apply("f11")
        self.assertEqual(dialog.profile_hotkey.value(), "")
        self.assertIn("Elite", dialog.discovery_status.text())
        dialog.profile_hotkey._apply("f10")
        self.assertEqual(dialog.special_hotkeys["Star Citizen"], "f10")

        dialog.key._apply("f10")
        self.assertEqual(dialog.key.value(), "F9")

        with patch("voxcomm.app.QInputDialog.getText", return_value=("SC", True)):
            dialog._rename_special_profile()
        self.assertEqual(dialog.special_hotkeys["SC"], "f10")
        self.assertNotIn("Star Citizen", dialog.special_hotkeys)
        self.assertEqual(dialog.profile_hotkey.value(), "f10")

        with patch("voxcomm.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog._remove_special_profile()
        self.assertNotIn("SC", dialog.special_hotkeys)

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Elite"))
        dialog.profile_hotkey.clear_button.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.special_hotkeys, {})
        self.assertEqual(updated.ptt_key, "F9")
        self.assertIsNotNone(dialog.key.clear_button)
        dialog.key.clear_button.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.ptt_key, "")
        self.assertFalse(hasattr(dialog, "device"))
        dialog.deleteLater()

    def test_profile_hotkey_selects_prompt_profile_for_recording(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            special_prompts={"Star Citizen": "Ship context"},
            special_hotkeys={"Star Citizen": "f10"},
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
                patch("voxcomm.app.chat", side_effect=lambda *a, **k: requests.append(k) or "Antwort"),
            ):
                overlay._capture(threading.Event(), "Star Citizen")
                overlay._capture(threading.Event(), "")
            self.assertIn("Ship context", requests[0]["system_prompt"])
            self.assertEqual(requests[0]["enabled_tools"], [])
            self.assertNotIn("Ship context", requests[1]["system_prompt"])
            self.assertEqual(requests[1]["enabled_tools"], ["web_search"])
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
            self.assertEqual(overlay.status.text(), "Bereit · Aufnahme zu kurz")
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
            overlay._show_message("Antwort", "A.I.: laufende Ausgabe")
            self.assertTrue(overlay._type_timer.isActive())

            with patch("voxcomm.app.time.monotonic", side_effect=[1.0, 1.199]):
                overlay._ptt_event("pressed", "")
                overlay._ptt_event("released", "")

            self.assertTrue(active_request.is_set())
            self.assertFalse(overlay._type_timer.isActive())
            self.assertEqual(overlay.transcript.toPlainText(), "")
            self.assertEqual(overlay.status.text(), "Bereit · Anfrage abgebrochen")
            overlay._show_request_message("Veraltete Antwort", "A.I.: nicht anzeigen", active_request)
            self.assertEqual(overlay.status.text(), "Bereit · Anfrage abgebrochen")
            self.assertEqual(overlay.transcript.toPlainText(), "")
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_overlay_restores_saved_position(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(overlay_x=140, overlay_y=160)
        with patch("voxcomm.app.load_config", return_value=config):
            with patch("voxcomm.app.PttListener"), patch("voxcomm.app.save_config") as save_config:
                overlay = Overlay()
                self.assertEqual(overlay.pos(), QPoint(140, 160))
                self.assertTrue(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                self.assertFalse(save_config.called)
                overlay.move(180, 200)
                self.assertTrue(overlay._position_save_timer.isActive())
                overlay._position_save_timer.stop()
                overlay._save_overlay_position()
                self.assertEqual((config.overlay_x, config.overlay_y), (180, 200))
                self.assertTrue(save_config.called)
                overlay.listener.stop()
                overlay.deleteLater()

    def test_overlay_reset_button_returns_window_to_visible_default(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(overlay_x=50000, overlay_y=50000)
        with patch("voxcomm.app.load_config", return_value=config):
            with patch("voxcomm.app.PttListener"), patch("voxcomm.app.save_config") as save_config:
                overlay = Overlay()
                area = QGuiApplication.primaryScreen().availableGeometry()
                expected = QPoint(area.left() + (area.width() - overlay.width()) // 2, area.top() + 12)
                self.assertEqual(overlay.pos(), expected)
                dialog = SettingsDialog(config, overlay)
                self.assertTrue(dialog.reset_position.isEnabled())
                dialog.reset_position.click()
                self.assertEqual((config.overlay_x, config.overlay_y), (expected.x(), expected.y()))
                self.assertTrue(save_config.called)
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
            full_text = "Du: Test?\nA.I.: Antwort"
            overlay._show_message("Bereit · Antwort lokal erzeugt", full_text)
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
            self.assertEqual(overlay.status.text().split(" · ")[0], "Aufnahme läuft")
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
            text = "Du: Frage?\nA.I.: Antwort"
            overlay._show_message("Bereit · Antwort lokal erzeugt", text)
            while overlay._type_timer.isActive():
                overlay._type_next_character()

            self.assertEqual(overlay.transcript.toPlainText(), text)
            self.assertEqual(overlay._last_response, "Antwort")

            def color_at(index):
                cursor = overlay.transcript.textCursor()
                cursor.setPosition(index + 1)
                return cursor.charFormat().foreground().color().name()

            user_color = color_at(text.index("Frage"))
            ai_label_color = color_at(text.index("A.I."))
            ai_color = color_at(text.index("Antwort"))
            self.assertEqual(ai_label_color, system_accent().name())
            self.assertNotEqual(user_color, ai_color)
            self.assertNotEqual(user_color, ai_label_color)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_transcript_segments_reassemble_original_text(self):
        for text in ("Du: Frage?\nA.I.: Antwort", "Du: Frage?\n", "Nur Text", "Du: A.I.: ohne Zeilenumbruch"):
            self.assertEqual("".join(chunk for chunk, _ in transcript_segments(text)), text)
        self.assertEqual(
            [kind for _, kind in transcript_segments("Du: Frage?\nA.I.: Antwort")],
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

    def test_settings_dialog_is_split_into_themed_sections(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        titles = [label.text() for label in dialog.findChildren(QLabel, "sectionTitle")]
        self.assertEqual(
            sorted(titles),
            sorted(["EINGABE", "SPRACHERKENNUNG", "SPRACHMODELL", "SPRACHAUSGABE", "PROMPT", "VOXCOMM-CONTROLLER"]),
        )
        sections = dialog.findChildren(QFrame, "section")
        self.assertEqual(len(sections), 6)
        self.assertEqual(dialog.input_action_catalog.rowCount(), 100)
        self.assertTrue(dialog.action_catalog_section.isHidden())
        self.assertIn(system_accent().name(), dialog.styleSheet())
        self.assertEqual(dialog.findChild(QPushButton, "primary").text(), "Speichern")
        for field in (dialog.stt, dialog.tts, dialog.llm_url, dialog.key, dialog.special_prompt):
            self.assertTrue(any(section.isAncestorOf(field) for section in sections))

    def test_settings_dialog_is_not_parented_to_the_overlay(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"):
            overlay = Overlay()
            with patch("voxcomm.app.SettingsDialog") as dialog_class:
                dialog_class.return_value.exec.return_value = 0
                overlay._settings()

            dialog_class.assert_called_once_with(overlay.config)
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
        self.assertIn("Controller nicht verfügbar", overlay._set_status.call_args.args[0])

    def test_discovery_buttons_are_icon_only_with_tooltips(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig())

        for button in (dialog.voice_refresh, dialog.model_refresh):
            self.assertIsInstance(button, QToolButton)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.text(), "")
            self.assertIn("laden", button.toolTip())
            self.assertEqual(button.width(), button.height())

    def test_settings_save_global_prompt_and_auto_speak(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": "Ship context"})
        dialog = SettingsDialog(config)

        self.assertEqual(dialog.special_profile.itemText(0), "Global")
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

    def test_mangohud_status_includes_spoken_text_and_ai_reply(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.publish_status") as publish:
            overlay = Overlay()
            overlay._show_message("Bereit · Antwort lokal erzeugt", 'Du: Flight ready\nA.I.: "Right Alt + R gesendet."')

            self.assertEqual(publish.call_args.args[0], 'Du: Flight ready\nA.I.: "Right Alt + R gesendet."')
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_mangohud_keeps_reply_visible_while_piper_speaks(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.PttListener"), patch("voxcomm.app.publish_status") as publish:
            with patch("voxcomm.app.threading.Thread"):
                overlay = Overlay()
                expected = 'Du: Flight ready\nA.I.: "Right Alt + R gesendet."'
                overlay._show_message("Bereit · Antwort lokal erzeugt", 'Du: Flight ready\nA.I.: "Right Alt + R gesendet."')

                overlay._start_speak('"Right Alt + R gesendet."')
                self.assertEqual(publish.call_args.args[0], expected)

                overlay._show_message("Bereit · PTT zum Sprechen", "")
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
        dialog.input_action_catalog.setItem(0, 1, QTableWidgetItem("Flight Systems On"))
        dialog.input_action_catalog.setItem(0, 2, QTableWidgetItem("flight ready"))
        dialog.input_action_catalog.setItem(0, 3, QTableWidgetItem("Flight Systems on"))
        dialog.input_action_catalog.setItem(1, 1, QTableWidgetItem("Flight Systems Off"))
        dialog.input_action_catalog.setItem(1, 2, QTableWidgetItem("flight systems off"))
        dialog.input_action_catalog.setItem(1, 3, QTableWidgetItem("Flight Systems off"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog.input_action_catalog.setItem(2, 1, QTableWidgetItem("Landing Gear On"))
        dialog.input_action_catalog.setItem(2, 2, QTableWidgetItem("gear down"))
        dialog.input_action_catalog.setItem(2, 3, QTableWidgetItem("Gear extended"))
        dialog.input_action_catalog.setItem(3, 1, QTableWidgetItem("Landing Gear Off"))
        dialog.input_action_catalog.setItem(3, 2, QTableWidgetItem("gear up"))
        dialog.input_action_catalog.setItem(3, 3, QTableWidgetItem("Gear retracted"))

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

    def test_thinking_override_is_saved_per_prompt_profile(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(
            special_prompts={"Star Citizen": "Ship context"},
            llm_thinking="medium",
        ))
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        dialog.profile_thinking.setCurrentIndex(dialog.profile_thinking.findData("low"))
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Star Citizen"))
        dialog.profile_thinking.setCurrentIndex(dialog.profile_thinking.findData("high"))

        updated = dialog.apply(AppConfig(special_prompts={"Star Citizen": "Ship context"}, llm_thinking="medium"))

        self.assertEqual(updated.llm_thinking_overrides, {"__global__": "low", "Star Citizen": "high"})
        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("__global__"))
        self.assertEqual(dialog.profile_thinking.currentData(), "low")


class ProviderSelectionTests(unittest.TestCase):
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

        self.assertFalse(dialog.llm_section.form.isRowVisible(dialog.key_row))
        self.assertEqual(dialog.llm_url.text(), "http://127.0.0.1:11434")

    def test_cloud_models_are_loaded_from_cache_without_http(self):
        app = QApplication.instance() or QApplication([])
        with (
            patch("voxcomm.app.cloud.cached_models", return_value=(["m1", "m2"], time.time())),
            patch("voxcomm.app.cloud.list_cloud_models") as listing,
        ):
            dialog = SettingsDialog(AppConfig(llm_provider="ollama-cloud", llm_model="m2"))

        listing.assert_not_called()
        self.assertEqual([dialog.model.itemText(index) for index in range(dialog.model.count())], ["m1", "m2"])
        self.assertEqual(dialog.model.currentText(), "m2")
        self.assertIn("Zwischenspeicher", dialog.discovery_status.text())

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
        dialog = SettingsDialog(AppConfig(tts_voice="de_DE-thorsten-low"))
        dialog.stt_provider.setCurrentIndex(dialog.stt_provider.findData("whisper.cpp"))
        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))
        dialog.tts_language.setCurrentIndex(dialog.tts_language.findData("English"))
        dialog.provider.setCurrentIndex(dialog.provider.findData("ollama-cloud"))
        dialog.api_key.setText(" secret ")
        dialog.voice.setCurrentText("data")

        updated = dialog.apply(AppConfig())

        self.assertEqual(updated.stt_provider, "whisper.cpp")
        self.assertEqual(updated.tts_provider, "openai")
        self.assertEqual(updated.tts_language, "English")
        self.assertEqual(updated.ollama_api_key, "secret")
        self.assertEqual(updated.llm_provider, "ollama-cloud")
        self.assertEqual(updated.response_language, "English")

    def test_openai_interface_hides_voice_language_and_swaps_url(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(tts_url="tcp://127.0.0.1:10200"))

        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("openai"))

        self.assertEqual(dialog.tts.text(), "http://127.0.0.1:8097")
        self.assertFalse(dialog.tts_section.form.isRowVisible(dialog.voice_language))

        dialog.tts_provider.setCurrentIndex(dialog.tts_provider.findData("wyoming"))

        self.assertEqual(dialog.tts.text(), "tcp://127.0.0.1:10200")
        self.assertTrue(dialog.tts_section.form.isRowVisible(dialog.voice_language))

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
            self.assertIn("Prüfe", dialog.discovery_status.text())
            thread_class.assert_called()
            self.assertEqual(thread_class.call_args.kwargs["target"].__name__, "run")

        dialog.discovery.discovery.emit("key", [], "")
        self.assertTrue(dialog.key_test.isEnabled())
        self.assertEqual(dialog.discovery_status.text(), "API-Schlüssel gültig")

        dialog.discovery.discovery.emit("key", [], "401")
        self.assertIn("API-Schlüssel ungültig", dialog.discovery_status.text())

    def test_openai_voice_listing_is_not_language_filtered(self):
        app = QApplication.instance() or QApplication([])
        with patch("voxcomm.app.cloud.cached_models", return_value=([], 0.0)):
            with patch("voxcomm.app.threading.Thread"):
                dialog = SettingsDialog(AppConfig(tts_provider="openai", llm_provider="ollama"))
        dialog._available_voices = ["data"]
        dialog.voice_language.setCurrentIndex(dialog.voice_language.findData("de"))

        dialog._refresh_voice_options()

        self.assertEqual(dialog.voice.currentText(), "data")

    def test_overlay_dispatches_to_local_backends(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(stt_provider="whisper.cpp", tts_provider="openai",
                           stt_url="http://127.0.0.1:8081", tts_url="http://127.0.0.1:8097",
                           tts_voice="data", tts_language="German", sample_rate=16000)
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
                           ollama_api_key="secret", tts_provider="openai", tts_voice="data",
                           response_language="Deutsch")
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
        config = AppConfig(llm_thinking="off")
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
        dialog = SettingsDialog(AppConfig(llm_model="gemma4", llm_thinking="off"))

        self.assertEqual(dialog.thinking.currentData(), "off")
        self.assertEqual(dialog.thinking.currentText(), "Aus")
        dialog._apply_thinking_options(dialog._thinking_token, ["off", "on"])
        self.assertEqual([dialog.thinking.itemText(i) for i in range(dialog.thinking.count())],
                         ["Modell-Standard", "Aus", "An"])
        self.assertEqual(dialog.thinking.currentData(), "off")

        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        self.assertEqual([dialog.thinking.itemData(i) for i in range(dialog.thinking.count())],
                         ["", "low", "medium", "high"])
        self.assertEqual(dialog.thinking.currentData(), "")
        self.assertEqual(dialog.apply(AppConfig()).llm_thinking, "off")

        dialog.thinking.setCurrentIndex(dialog.thinking.findData("high"))
        dialog.thinking.activated.emit(dialog.thinking.currentIndex())
        self.assertEqual(dialog.apply(AppConfig()).llm_thinking, "high")
        dialog.deleteLater()

    def test_model_without_thinking_controls_disables_the_choice_but_keeps_the_setting(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(llm_model="deepseek-v2", llm_thinking="high"))

        dialog._apply_thinking_options(dialog._thinking_token, [])

        self.assertFalse(dialog.thinking.isEnabled())
        self.assertEqual(dialog.thinking.currentText(), "Vom Modell nicht steuerbar")
        self.assertEqual(dialog.apply(AppConfig()).llm_thinking, "high")
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "medium", "high"])
        self.assertTrue(dialog.thinking.isEnabled())
        self.assertEqual(dialog.thinking.currentData(), "high")
        dialog.deleteLater()

    def test_stale_thinking_answer_for_a_previous_model_is_ignored(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(llm_model="a"))
        with patch("voxcomm.app.threading.Thread"):
            dialog._lookup_thinking()
            stale = dialog._thinking_token
            dialog._lookup_thinking()

        dialog._apply_thinking_options(stale, ["low", "high"])

        self.assertEqual(dialog.thinking.count(), 1)
        dialog._apply_thinking_options(dialog._thinking_token, ["low", "high"])
        self.assertEqual(dialog.thinking.count(), 3)
        dialog.deleteLater()

    def test_thinking_lookup_runs_off_the_ui_thread_with_current_connection_values(self):
        app = QApplication.instance() or QApplication([])
        dialog = SettingsDialog(AppConfig(llm_provider="ollama", llm_url="http://h:11434", llm_model="gpt-oss"))
        received = []
        dialog.discovery.thinking.connect(lambda token, options: received.append((token, options)))

        with patch("voxcomm.app.thinking_options", return_value=["low", "medium", "high"]) as lookup:
            dialog._lookup_thinking()
            for _ in range(100):
                app.processEvents()
                if received:
                    break
                QTest.qWait(10)

        lookup.assert_called_once_with("ollama", "http://h:11434", "gpt-oss", "")
        self.assertEqual(received, [(dialog._thinking_token, ["low", "medium", "high"])])
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()