import os
import signal
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLabel, QMessageBox, QPushButton, QSystemTrayIcon, QToolButton, QWidget,
)

from sc_ai_copilot.app import (
    COMPACT_WIDTH, FULL_WIDTH, HIDE_DELAY_MS, TRANSCRIPT_MAX_HEIGHT, TYPE_INTERVAL_MS, HotkeyField, Overlay,
    SettingsDialog,
    configure_application, prefer_x11_on_wayland, transcript_segments,
)
from sc_ai_copilot.config import AppConfig
from sc_ai_copilot.theme import rgba, system_accent, system_surface


class ApplicationLifecycleTests(unittest.TestCase):
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
        with patch("sc_ai_copilot.app.PttListener") as listener_class:
            with patch("sc_ai_copilot.app.threading.Thread") as thread_class:
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

    def test_tray_click_toggles_recording_and_menu_contains_requested_actions(self):
        app = QApplication.instance() or QApplication([])
        with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.threading.Thread") as thread_class:
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
        with patch("sc_ai_copilot.app.load_config", return_value=AppConfig(tray_mode=True)):
            with patch("sc_ai_copilot.app.PttListener"):
                with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
                    overlay = Overlay()
                    self.assertFalse(overlay.config.tray_mode)
                    self.assertTrue(overlay.isVisible())
                    overlay.listener.stop()
                    overlay.deleteLater()

    def test_tray_mode_shows_text_overlay_without_buttons_then_hides_it(self):
        app = QApplication.instance() or QApplication([])
        with patch("sc_ai_copilot.app.load_config", return_value=AppConfig(tray_mode=True)):
            with patch("sc_ai_copilot.app.PttListener"):
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
        field = HotkeyField("f9")
        changes = []
        field.changed.connect(changes.append)

        with patch("sc_ai_copilot.app.capture_key", return_value="PAGEUP"):
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

        with patch("sc_ai_copilot.app.capture_key", return_value=None):
            field.button.click()
            for _ in range(10):
                app.processEvents()
                QTest.qWait(10)
        self.assertEqual(field.value(), "pageup")
        field.deleteLater()

    def test_hotkey_field_reports_missing_keyboard_access(self):
        app = QApplication.instance() or QApplication([])
        field = HotkeyField("f9")
        errors = []
        field.error.connect(errors.append)

        with patch("sc_ai_copilot.app.capture_key", side_effect=PermissionError("kein Zugriff")):
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

        with patch("sc_ai_copilot.app.QInputDialog.getText", return_value=("SC", True)):
            dialog._rename_special_profile()
        self.assertEqual(dialog.special_hotkeys["SC"], "f10")
        self.assertNotIn("Star Citizen", dialog.special_hotkeys)
        self.assertEqual(dialog.profile_hotkey.value(), "f10")

        with patch("sc_ai_copilot.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog._remove_special_profile()
        self.assertNotIn("SC", dialog.special_hotkeys)

        dialog.special_profile.setCurrentIndex(dialog.special_profile.findData("Elite"))
        dialog.profile_hotkey.clear_button.click()
        updated = dialog.apply(config)
        self.assertEqual(updated.special_hotkeys, {})
        self.assertEqual(updated.ptt_key, "F9")
        dialog.deleteLater()

    def test_profile_hotkey_selects_prompt_profile_for_recording(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(special_prompts={"Star Citizen": "Ship context"}, special_hotkeys={"Star Citizen": "f10"})
        with patch("sc_ai_copilot.app.load_config", return_value=config):
            with patch("sc_ai_copilot.app.PttListener") as listener_class:
                with patch("sc_ai_copilot.app.threading.Thread") as thread_class:
                    overlay = Overlay()
                    self.assertEqual(listener_class.call_args.args[-1], {"f10": "Star Citizen"})

                    overlay._ptt_event("pressed", "Star Citizen")

                    self.assertEqual(thread_class.call_args.kwargs["args"][1], "Star Citizen")
                    self.assertIn("Star Citizen", overlay.status.text())
                    overlay.listener.stop()
                    overlay.tray_icon.hide()
                    overlay.deleteLater()

        with patch("sc_ai_copilot.app.load_config", return_value=config), patch("sc_ai_copilot.app.PttListener"):
            overlay = Overlay()
            prompts = []
            with patch("sc_ai_copilot.app.record_until_released", return_value=b"x"), \
                 patch("sc_ai_copilot.app.transcribe_sync", return_value="Hallo"), \
                 patch("sc_ai_copilot.app.chat", side_effect=lambda *a, **k: prompts.append(k["system_prompt"]) or "Antwort"):
                overlay._capture(threading.Event(), "Star Citizen")
                overlay._capture(threading.Event(), "")
            self.assertIn("Ship context", prompts[0])
            self.assertNotIn("Ship context", prompts[1])
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()

    def test_overlay_restores_saved_position(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(overlay_x=140, overlay_y=160)
        with patch("sc_ai_copilot.app.load_config", return_value=config):
            with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.save_config") as save_config:
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
        with patch("sc_ai_copilot.app.load_config", return_value=config):
            with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.save_config") as save_config:
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
        with patch("sc_ai_copilot.app.PttListener"):
            overlay = Overlay()
            self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(overlay.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(overlay._hide_timer.interval(), 3000)
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
        with patch("sc_ai_copilot.app.load_config", return_value=AppConfig()):
            with patch("sc_ai_copilot.app.PttListener"):
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

    def test_status_only_message_hides_again_and_shrinks(self):
        app = QApplication.instance() or QApplication([])
        with patch("sc_ai_copilot.app.PttListener"):
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
        with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.threading.Thread"):
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
            self.assertEqual(overlay.status.text(), "Piper spricht …")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertEqual(overlay.transcript.toPlainText(), full_text)
            self.assertFalse(overlay._hide_timer.isActive())

            overlay.events.speech_finished.emit()

            self.assertTrue(overlay._hide_timer.isActive())
            overlay.listener.stop()
            overlay.deleteLater()

    def test_answer_types_slowly_and_scrolls_when_long(self):
        app = QApplication.instance() or QApplication([])
        with patch("sc_ai_copilot.app.PttListener"):
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
        with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.threading.Thread"):
            overlay = Overlay()
            overlay.show()
            overlay._show_message("Bereit · Antwort lokal erzeugt", "Du: Test?\nA.I.: Antwort")
            overlay._start_speak("Antwort")
            while overlay._type_timer.isActive():
                overlay._type_next_character()
            self.assertFalse(overlay._hide_timer.isActive())

            overlay.events.speech_finished.emit()

            self.assertTrue(overlay._hide_timer.isActive())
            self.assertEqual(overlay._hide_timer.interval(), HIDE_DELAY_MS)
            overlay.listener.stop()
            overlay.deleteLater()

    def test_overlay_waits_for_typing_when_audio_finishes_first(self):
        app = QApplication.instance() or QApplication([])
        with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.threading.Thread"):
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
        with patch("sc_ai_copilot.app.PttListener"):
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
        with patch("sc_ai_copilot.app.PttListener"), patch("sc_ai_copilot.app.save_config"):
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
            sorted(["EINGABE", "SPRACHERKENNUNG", "SPRACHMODELL", "SPRACHAUSGABE", "PROMPT"]),
        )
        sections = dialog.findChildren(QFrame, "section")
        self.assertEqual(len(sections), 5)
        self.assertIn(system_accent().name(), dialog.styleSheet())
        self.assertEqual(dialog.findChild(QPushButton, "primary").text(), "Speichern")
        for field in (dialog.stt, dialog.tts, dialog.llm_url, dialog.key, dialog.special_prompt):
            self.assertTrue(any(section.isAncestorOf(field) for section in sections))

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


if __name__ == "__main__":
    unittest.main()