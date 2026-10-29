from __future__ import annotations

import argparse
import os
import signal
import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QRect, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QAction, QFont, QGuiApplication, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from sc_ai_copilot.audio import play_pcm, record_until_released, save_wav
from sc_ai_copilot.branding import APP_ID, APP_NAME, install_launcher, make_app_icon
from sc_ai_copilot.config import AppConfig, load_config, save_config
from sc_ai_copilot.providers.llm import chat, list_models
from sc_ai_copilot.providers.wyoming import filter_voices, list_voices, speak_sync, transcribe_sync, voice_language
from sc_ai_copilot.prompts import VOICE_LANGUAGE_NAMES, build_system_prompt, response_language_for_voice
from sc_ai_copilot.ptt import PttListener, capture_key
from sc_ai_copilot.theme import dialog_stylesheet, make_icon, mix_colors, rgba, system_accent, system_surface, system_text


LANGUAGE_LABELS = VOICE_LANGUAGE_NAMES

TYPE_INTERVAL_MS = 24
TRANSCRIPT_WIDTH = 480
TRANSCRIPT_MAX_HEIGHT = 200
HIDE_DELAY_MS = 3000
THINKING_STATUS = "Denke nach …"
SETTINGS_LABEL_WIDTH = 140
SETTINGS_ICON_BUTTON = 33
USER_PREFIX = "Du: "
AI_LABEL = "A.I.: "
AI_MARKER = "\n" + AI_LABEL


def transcript_segments(text: str) -> list[tuple[str, str]]:
    """Teilt "Du: ...\nA.I.: ..." in (Text, Art)-Abschnitte; die Teile ergeben wieder den Originaltext."""
    user_part, marker, ai_part = text.partition(AI_MARKER)
    segments = []
    if user_part.startswith(USER_PREFIX):
        segments.append((USER_PREFIX, "user_label"))
        user_part = user_part[len(USER_PREFIX):]
    segments.append((user_part + ("\n" if marker else ""), "user"))
    if marker:
        segments.append((AI_LABEL, "ai_label"))
        segments.append((ai_part, "ai"))
    return [segment for segment in segments if segment[0]]
# 3 Buttons à 44 px + 2 Abstände à 10 px + Seitenränder
COMPACT_WIDTH = 3 * 44 + 2 * 10 + 24
FULL_WIDTH = COMPACT_WIDTH + 10 + TRANSCRIPT_WIDTH


class Events(QObject):
    ptt = Signal(str, str)
    message = Signal(str, str)
    discovery = Signal(str, object, str)
    speak = Signal(str)
    speech_finished = Signal()


class _CaptureButton(QPushButton):
    capturing = False

    def keyPressEvent(self, event):
        # Während der Aufnahme darf Enter/Esc weder den Dialog bestätigen noch schließen.
        if self.capturing:
            event.accept()
            return
        super().keyPressEvent(event)


class _CaptureSignals(QObject):
    done = Signal(str)
    failed = Signal(str)


class HotkeyField(QWidget):
    """Zeigt einen Hotkey und fängt per Klick die nächste gedrückte Taste ein (Esc bricht ab)."""

    changed = Signal(str)
    error = Signal(str)

    def __init__(self, value: str = "", clearable: bool = False, validator=None, parent=None):
        super().__init__(parent)
        self._value = value
        self._validator = validator
        self._stop: threading.Event | None = None
        self._signals = _CaptureSignals(self)
        self._signals.done.connect(self._finish)
        self._signals.failed.connect(self._fail)
        self.button = _CaptureButton()
        self.button.setAutoDefault(False)
        self.button.setToolTip("Klicken und dann die gewünschte Taste drücken. Esc bricht ab.")
        self.button.clicked.connect(self._toggle_capture)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.button, 1)
        self.clear_button = None
        if clearable:
            self.clear_button = QToolButton()
            self.clear_button.setObjectName("iconButton")
            self.clear_button.setText("✕")
            self.clear_button.setFixedSize(SETTINGS_ICON_BUTTON, SETTINGS_ICON_BUTTON)
            self.clear_button.setToolTip("Hotkey entfernen")
            self.clear_button.clicked.connect(lambda: self._apply(""))
            layout.addWidget(self.clear_button)
        self._render()

    def value(self) -> str:
        return self._value

    def setValue(self, value: str) -> None:
        self.stop_capture()
        self._value = value
        self._render()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        if not enabled:
            self.stop_capture()

    def stop_capture(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        self.button.capturing = False
        self._render()

    def _render(self) -> None:
        if self._stop is not None:
            self.button.setText("Taste drücken …")
        else:
            self.button.setText(self._value.upper() if self._value else "Nicht belegt")
        if self.clear_button is not None:
            self.clear_button.setEnabled(bool(self._value))

    def _toggle_capture(self) -> None:
        if self._stop is not None:
            self.stop_capture()
            return
        stop = self._stop = threading.Event()
        self.button.capturing = True
        self._render()
        signals = self._signals

        def run():
            try:
                result = capture_key(stop) or ""
                if not stop.is_set():
                    signals.done.emit(result)
            except RuntimeError:
                pass
            except Exception as error:
                try:
                    signals.failed.emit(str(error))
                except RuntimeError:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def _release_capture(self) -> None:
        self._stop = None
        # Das Taste-Loslassen/Enter erreicht Qt kurz nach evdev, daher erst verzögert freigeben.
        QTimer.singleShot(200, self._end_capture)

    def _end_capture(self) -> None:
        self.button.capturing = False
        self._render()

    def _finish(self, name: str) -> None:
        if self._stop is None:
            return
        self._release_capture()
        if name:
            self._apply(name.lower())

    def _fail(self, message: str) -> None:
        self.stop_capture()
        self.error.emit(message)

    def _apply(self, value: str) -> None:
        if value == self._value:
            self._render()
            return
        conflict = self._validator(value) if self._validator and value else None
        if conflict:
            self.error.emit(conflict)
            self._render()
            return
        self._value = value
        self._render()
        self.changed.emit(value)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Einstellungen · {APP_NAME}")
        self.setMinimumWidth(880)
        self.setStyleSheet(dialog_stylesheet(system_accent(), system_surface(), system_text()))
        self.discovery = Events()
        self.discovery.discovery.connect(self._discovery_result)
        self.stt = QLineEdit(config.stt_url)
        self.stt_language = QComboBox()
        self.stt_language.addItem("Automatisch", "auto")
        for code, label in LANGUAGE_LABELS.items():
            self.stt_language.addItem(f"{label} ({code})", code)
        stt_language_index = self.stt_language.findData(config.stt_language)
        self.stt_language.setCurrentIndex(max(stt_language_index, 0))
        self.tts = QLineEdit(config.tts_url)
        self._available_voices: list[str] = []
        self.voice_language = QComboBox()
        self.voice_language.addItem("Alle Sprachen", "")
        for code, label in LANGUAGE_LABELS.items():
            self.voice_language.addItem(f"{label} ({code})", code)
        language_index = self.voice_language.findData(config.voice_language)
        self.voice_language.setCurrentIndex(max(language_index, 0))
        self.voice_language.currentIndexChanged.connect(self._refresh_voice_options)
        self.voice = QComboBox()
        self.voice.setEditable(True)
        self.voice.setCurrentText(config.tts_voice)
        self.voice.currentTextChanged.connect(self._update_response_language)
        self.response_language = QLabel()
        self.response_language.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._update_response_language(self.voice.currentText())
        self.special_prompts = dict(config.special_prompts)
        self.special_hotkeys = {name: key for name, key in config.special_hotkeys.items() if name in self.special_prompts}
        self.enabled_tools = {name: [tool for tool in tools if tool == "web_search"]
                      for name, tools in config.enabled_tools.items()}
        self.profile_hotkey = HotkeyField("", clearable=True, validator=self._profile_hotkey_conflict)
        self.profile_hotkey.setToolTip("Mit diesem Hotkey wird zusätzlich zu Global dieses Profil verwendet")
        self.profile_hotkey.changed.connect(self._store_profile_hotkey)
        self._global_prompt = config.global_prompt
        self.special_profile = QComboBox()
        self.special_profile.addItem("Global", "__global__")
        for name in sorted(self.special_prompts, key=str.casefold):
            self.special_profile.addItem(name, name)
        active_profile = config.active_special_prompt if config.active_special_prompt in self.special_prompts else "__global__"
        self.special_profile.setCurrentIndex(max(self.special_profile.findData(active_profile), 0))
        self._active_profile_name = active_profile
        self.special_profile.currentIndexChanged.connect(self._select_special_profile)
        self.web_search = QCheckBox("Websuche erlauben")
        self.web_search.setToolTip("Suchanfragen werden über DDGS an externe Suchanbieter gesendet; Treffer werden nicht automatisch geöffnet.")
        self.web_search.setChecked("web_search" in self.enabled_tools.get(active_profile, []))
        initial_prompt = config.global_prompt if active_profile == "__global__" else self.special_prompts.get(active_profile, "")
        self.special_prompt = QPlainTextEdit(initial_prompt)
        self.special_prompt.setPlaceholderText("Globaler Basis-Prompt" if active_profile == "__global__" else "Aufgabenwissen und Verhaltensregeln für dieses Profil")
        self.special_prompt.setMaximumBlockCount(100)
        self.special_prompt.setMinimumHeight(150)
        self.add_profile = QPushButton("Hinzufügen")
        self.add_profile.clicked.connect(self._add_special_profile)
        self.rename_profile = QPushButton("Umbenennen")
        self.rename_profile.clicked.connect(self._rename_special_profile)
        self.remove_profile = QPushButton("Entfernen")
        self.remove_profile.clicked.connect(self._remove_special_profile)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        profile_row.addWidget(self.add_profile, 1)
        profile_row.addWidget(self.rename_profile, 1)
        profile_row.addWidget(self.remove_profile, 1)
        self._update_profile_controls()
        self.provider = QComboBox()
        self.provider.addItems(["ollama", "llama.cpp"])
        self.provider.setCurrentText(config.llm_provider)
        self.llm_url = QLineEdit(config.llm_url)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setCurrentText(config.llm_model)
        self.key = HotkeyField(config.ptt_key, validator=self._general_hotkey_conflict)
        self.device = QSpinBox()
        self.device.setRange(-1, 32)
        self.device.setValue(config.joystick_device)
        self.button = QSpinBox()
        self.button.setRange(0, 128)
        self.button.setValue(config.joystick_button)
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(8000)
        self.sample_rate.setValue(config.sample_rate)
        self.auto_speak = QCheckBox("Antworten nach der Generierung automatisch vorlesen")
        self.auto_speak.setChecked(config.auto_speak)
        self.tray_mode = QCheckBox("Tray-Modus: Klick startet/stoppt die Aufnahme; Overlay-Buttons ausblenden")
        self.tray_mode.setChecked(config.tray_mode)
        self.reset_position = QPushButton("Overlay-Position zurücksetzen")
        if parent is not None and hasattr(parent, "reset_overlay_position"):
            self.reset_position.clicked.connect(parent.reset_overlay_position)
        else:
            self.reset_position.setEnabled(False)
        self.voice_refresh = self._refresh_button("Stimmen vom Dienst laden")
        self.voice_refresh.clicked.connect(lambda: self._discover("voices"))
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice, 1)
        voice_row.addWidget(self.voice_refresh)
        self.model_refresh = self._refresh_button("Modelle vom Dienst laden")
        self.model_refresh.clicked.connect(lambda: self._discover("models"))
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.model_refresh)
        self.response_language.setObjectName("hint")
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._section("Eingabe", [
            ("Push-to-talk-Hotkey", self.key), ("Joystick (-1 = aus)", self.device),
            ("Joystick-Taste", self.button), ("Mikrofon-Samplerate", self.sample_rate),
            (None, self.tray_mode), (None, self.reset_position),
        ]))
        left.addWidget(self._section("Spracherkennung", [
            ("Wyoming Whisper", self.stt), ("Erkennungssprache", self.stt_language),
        ]))
        left.addWidget(self._section("Sprachmodell", [
            ("Anbieter", self.provider), ("URL", self.llm_url), ("Modell", model_row),
        ]))
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._section("Sprachausgabe", [
            ("Wyoming Piper", self.tts), ("Stimmen-Sprache", self.voice_language), ("Stimme", voice_row),
            ("Antwortsprache", self.response_language), (None, self.auto_speak),
        ]))
        right.addWidget(self._section("Prompt", [
            ("Profil", self.special_profile), (None, profile_row), ("Profil-Hotkey", self.profile_hotkey),
            (None, self.web_search), (None, self.special_prompt),
        ]), 1)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        title = QLabel("Einstellungen")
        title.setObjectName("dialogTitle")
        self.discovery_status = QLabel("Stimmen und Modelle lassen sich über das Aktualisieren-Symbol vom Dienst laden")
        self.discovery_status.setObjectName("hint")
        self.discovery_status.setWordWrap(True)
        self.key.error.connect(self.discovery_status.setText)
        self.profile_hotkey.error.connect(self.discovery_status.setText)
        save = QPushButton("Speichern")
        save.setObjectName("primary")
        save.setDefault(True)
        save.setMinimumWidth(130)
        save.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addWidget(self.discovery_status, 1)
        footer.addWidget(save)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        root.addWidget(title)
        root.addLayout(columns, 1)
        root.addLayout(footer)

    def _refresh_button(self, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("iconButton")
        button.setIcon(make_icon("refresh", system_accent()))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(SETTINGS_ICON_BUTTON, SETTINGS_ICON_BUTTON)
        button.setToolTip(tooltip)
        return button

    def _section(self, title: str, rows: list) -> QFrame:
        frame = QFrame()
        frame.setObjectName("section")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        heading = QLabel(title.upper())
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for label, field in rows:
            if label is None:
                form.addRow(field)
            else:
                caption = QLabel(label)
                caption.setFixedWidth(SETTINGS_LABEL_WIDTH)
                form.addRow(caption, field)
        layout.addLayout(form)
        return frame

    def _update_response_language(self, voice_name: str):
        self.response_language.setText(response_language_for_voice(voice_name))

    def _general_hotkey_conflict(self, key: str) -> str | None:
        for name, other in self.special_hotkeys.items():
            if other == key:
                return f"Hotkey {key.upper()} ist schon für das Profil '{name}' vergeben"
        return None

    def _profile_hotkey_conflict(self, key: str) -> str | None:
        if key == self.key.value().lower():
            return f"Hotkey {key.upper()} ist schon der allgemeine Push-to-talk-Hotkey"
        for name, other in self.special_hotkeys.items():
            if other == key and name != self._active_profile_name:
                return f"Hotkey {key.upper()} ist schon für das Profil '{name}' vergeben"
        return None

    def _store_profile_hotkey(self, key: str):
        if self._active_profile_name in self.special_prompts:
            if key:
                self.special_hotkeys[self._active_profile_name] = key
            else:
                self.special_hotkeys.pop(self._active_profile_name, None)

    def done(self, result):
        self.key.stop_capture()
        self.profile_hotkey.stop_capture()
        super().done(result)

    def _store_special_profile(self):
        self._store_enabled_tools()
        if self._active_profile_name == "__global__":
            self._global_prompt = self.special_prompt.toPlainText().strip()
            return
        if self._active_profile_name:
            self.special_prompts[self._active_profile_name] = self.special_prompt.toPlainText().strip()

    def _store_enabled_tools(self):
        if self._active_profile_name in self.special_prompts or self._active_profile_name == "__global__":
            if self.web_search.isChecked():
                self.enabled_tools[self._active_profile_name] = ["web_search"]
            else:
                self.enabled_tools.pop(self._active_profile_name, None)

    def _select_special_profile(self):
        self._store_special_profile()
        self._active_profile_name = self.special_profile.currentData() or ""
        if self._active_profile_name == "__global__":
            prompt = self._global_prompt
        else:
            prompt = self.special_prompts.get(self._active_profile_name, "")
        self.web_search.setChecked("web_search" in self.enabled_tools.get(self._active_profile_name, []))
        self.special_prompt.setPlainText(prompt)
        self.special_prompt.setPlaceholderText("Globaler Basis-Prompt" if self._active_profile_name == "__global__" else "Aufgabenwissen und Verhaltensregeln für dieses Profil")
        self._update_profile_controls()

    def _update_profile_controls(self):
        has_profile = self.special_profile.currentData() not in (None, "", "__global__")
        self.special_prompt.setEnabled(True)
        self.rename_profile.setEnabled(has_profile)
        self.remove_profile.setEnabled(has_profile)
        self.profile_hotkey.setEnabled(has_profile)
        self.profile_hotkey.setValue(self.special_hotkeys.get(self._active_profile_name, "") if has_profile else "")

    def _refresh_special_profiles(self, selected: str):
        self.special_profile.blockSignals(True)
        self.special_profile.clear()
        self.special_profile.addItem("Global", "__global__")
        for name in sorted(self.special_prompts, key=str.casefold):
            self.special_profile.addItem(name, name)
        self.special_profile.setCurrentIndex(max(self.special_profile.findData(selected), 0))
        self.special_profile.blockSignals(False)
        self._active_profile_name = self.special_profile.currentData() or ""
        if self._active_profile_name == "__global__":
            prompt = self._global_prompt
        else:
            prompt = self.special_prompts.get(self._active_profile_name, "")
        self.web_search.setChecked("web_search" in self.enabled_tools.get(self._active_profile_name, []))
        self.special_prompt.setPlainText(prompt)
        self.special_prompt.setPlaceholderText("Globaler Basis-Prompt" if self._active_profile_name == "__global__" else "Aufgabenwissen und Verhaltensregeln für dieses Profil")
        self._update_profile_controls()

    def _add_special_profile(self):
        self._store_special_profile()
        name, accepted = QInputDialog.getText(self, "Spezialprofil hinzufügen", "Profilname")
        name = name.strip()
        if not accepted or not name:
            return
        if name in self.special_prompts:
            QMessageBox.information(self, "Profil vorhanden", "Dieser Profilname ist bereits vorhanden.")
            return
        self.special_prompts[name] = ""
        self._refresh_special_profiles(name)

    def _rename_special_profile(self):
        current = self.special_profile.currentData()
        if not current:
            return
        name, accepted = QInputDialog.getText(self, "Spezialprofil umbenennen", "Profilname", text=current)
        name = name.strip()
        if not accepted or not name or name == current:
            return
        if name in self.special_prompts:
            QMessageBox.information(self, "Profil vorhanden", "Dieser Profilname ist bereits vorhanden.")
            return
        self._store_special_profile()
        self.special_prompts[name] = self.special_prompts.pop(current)
        if current in self.enabled_tools:
            self.enabled_tools[name] = self.enabled_tools.pop(current)
        if current in self.special_hotkeys:
            self.special_hotkeys[name] = self.special_hotkeys.pop(current)
        self._refresh_special_profiles(name)

    def _remove_special_profile(self):
        current = self.special_profile.currentData()
        if not current:
            return
        answer = QMessageBox.question(self, "Spezialprofil entfernen", f"Profil '{current}' wirklich entfernen?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store_special_profile()
        self.special_prompts.pop(current, None)
        self.enabled_tools.pop(current, None)
        self.special_hotkeys.pop(current, None)
        self._refresh_special_profiles("")

    def _discover(self, kind: str):
        if kind == "voices":
            button = self.voice_refresh
            endpoint = self.tts.text().strip()
            lookup = lambda: list_voices(endpoint)
        else:
            button = self.model_refresh
            endpoint = self.llm_url.text().strip()
            provider = self.provider.currentText()
            lookup = lambda: list_models(provider, endpoint)
        button.setEnabled(False)
        self.discovery_status.setText("Verbinde mit Dienst …")

        def run():
            try:
                self.discovery.discovery.emit(kind, lookup(), "")
            except Exception as error:
                self.discovery.discovery.emit(kind, [], str(error))

        threading.Thread(target=run, daemon=True).start()

    def _discovery_result(self, kind: str, values: list[str], error: str):
        combo, button, label = (self.voice, self.voice_refresh, "Stimmen") if kind == "voices" else (self.model, self.model_refresh, "Modelle")
        button.setEnabled(True)
        if error:
            self.discovery_status.setText(f"{label} konnten nicht geladen werden: {error}")
            return
        current = combo.currentText()
        if kind == "voices":
            self._available_voices = values
            self._refresh_voice_language_options()
            self._refresh_voice_options()
            self.discovery_status.setText(f"{len(values)} Stimmen gefunden" if values else "Keine Stimmen vom Dienst gemeldet")
            return
        combo.clear()
        combo.addItems(values)
        combo.setCurrentText(current)
        self.discovery_status.setText(f"{len(values)} {label.lower()} gefunden" if values else f"Keine {label.lower()} vom Dienst gemeldet")

    def _refresh_voice_language_options(self):
        current = self.voice_language.currentData()
        discovered = {voice_language(voice) for voice in self._available_voices}
        available_codes = set(LANGUAGE_LABELS) | discovered
        self.voice_language.blockSignals(True)
        self.voice_language.clear()
        self.voice_language.addItem("Alle Sprachen", "")
        for code in sorted(available_codes, key=lambda value: (LANGUAGE_LABELS.get(value, value.upper()).casefold(), value)):
            label = LANGUAGE_LABELS.get(code, code.upper())
            self.voice_language.addItem(f"{label} ({code})", code)
        index = self.voice_language.findData(current)
        self.voice_language.setCurrentIndex(max(index, 0))
        self.voice_language.blockSignals(False)

    def _refresh_voice_options(self):
        current = self.voice.currentText()
        voices = filter_voices(self._available_voices, self.voice_language.currentData() or "")
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItems(voices)
        if current in voices or not voices:
            self.voice.setCurrentText(current)
        else:
            self.voice.setCurrentIndex(0)
        self.voice.blockSignals(False)
        self._update_response_language(self.voice.currentText())

    def apply(self, config: AppConfig) -> AppConfig:
        config.stt_url, config.tts_url, config.tts_voice = self.stt.text(), self.tts.text(), self.voice.currentText()
        config.stt_language = self.stt_language.currentData() or "auto"
        config.voice_language = self.voice_language.currentData() or ""
        config.response_language = response_language_for_voice(config.tts_voice, config.response_language)
        self._store_global_prompt()
        self._store_special_profile()
        config.special_prompts = dict(self.special_prompts)
        config.special_hotkeys = {name: key for name, key in self.special_hotkeys.items() if key and name in self.special_prompts}
        allowed_tool_profiles = {"__global__", *self.special_prompts}
        config.enabled_tools = {name: ["web_search"] for name, tools in self.enabled_tools.items()
                    if name in allowed_tool_profiles and "web_search" in tools}
        config.active_special_prompt = self.special_profile.currentData() if self.special_profile.currentData() != "__global__" else ""
        config.global_prompt = self._global_prompt
        config.auto_speak = self.auto_speak.isChecked()
        config.tray_mode = self.tray_mode.isChecked()
        config.llm_provider, config.llm_url, config.llm_model = self.provider.currentText(), self.llm_url.text(), self.model.currentText()
        config.ptt_key, config.joystick_device = self.key.value(), self.device.value()
        config.joystick_button, config.sample_rate = self.button.value(), self.sample_rate.value()
        return config

    @property
    def _global_prompt(self) -> str:
        return self._stored_global_prompt

    @_global_prompt.setter
    def _global_prompt(self, value: str) -> None:
        self._stored_global_prompt = value

    def _store_global_prompt(self):
        if self._active_profile_name == "__global__":
            self._global_prompt = self.special_prompt.toPlainText().strip()

class _OverlayDragMixin:
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            window_handle = self.window().windowHandle()
            self._system_move_started = bool(window_handle and window_handle.startSystemMove())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and hasattr(self, "_drag_offset")
            and not self._system_move_started
        ):
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            timer = getattr(self.window(), "_position_save_timer", None)
            if timer is not None and self.window()._position_tracking_enabled:
                timer.start()
            self.__dict__.pop("_drag_offset", None)
            self.__dict__.pop("_system_move_started", None)
        super().mouseReleaseEvent(event)


class _OverlayDragHandle(_OverlayDragMixin, QLabel):
    pass


class _OverlayDragSurface(_OverlayDragMixin, QWidget):
    pass


class _OverlayTranscript(_OverlayDragMixin, QTextEdit):
    pass


class Overlay(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.events = Events()
        self.events.ptt.connect(self._ptt_event)
        self.events.message.connect(self._show_message)
        self.events.speak.connect(self._start_speak)
        self.events.speech_finished.connect(self._speech_finished)
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        root = QHBoxLayout()
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(10)
        self.status = _OverlayDragHandle("")
        self.status.setFont(QApplication.font())
        self.status.setCursor(Qt.CursorShape.SizeAllCursor)
        self.status.setToolTip("Zum Verschieben ziehen")
        self.transcript = _OverlayTranscript()
        self.transcript.setReadOnly(True)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.document().setDocumentMargin(0)
        self.transcript.setFont(QFont("AtkynsonMono Nerd Font", 11))
        self.transcript.setFixedSize(TRANSCRIPT_WIDTH, 0)
        self.transcript.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        text_column.addWidget(self.status)
        text_column.addWidget(self.transcript)
        text_container = _OverlayDragSurface()
        text_container.setCursor(Qt.CursorShape.SizeAllCursor)
        text_container.setToolTip("Zum Verschieben ziehen")
        text_container.setLayout(text_column)
        self._text_container = text_container
        self._text_opacity = QGraphicsOpacityEffect(text_container)
        self._text_opacity.setOpacity(1)
        text_container.setGraphicsEffect(self._text_opacity)
        self.mic = QToolButton()
        self.mic.setFixedSize(44, 44)
        self.mic.pressed.connect(lambda: self._ptt_event("pressed", ""))
        self.mic.released.connect(lambda: self._ptt_event("released", ""))
        self.mic.setToolTip("Zum Sprechen gedrückt halten")
        root.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignTop)
        self.say = QToolButton()
        self.say.setFixedSize(44, 44)
        self.say.setToolTip("Letzte Antwort vorlesen")
        self.say.clicked.connect(self._speak_last)
        root.addWidget(self.say, 0, Qt.AlignmentFlag.AlignTop)
        self.settings_button = QToolButton()
        self.settings_button.setFixedSize(44, 44)
        self.settings_button.setToolTip("Einstellungen")
        self.settings_button.clicked.connect(self._settings)
        root.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(text_container, 1)
        container = _OverlayDragSurface()
        container.setToolTip("Zum Verschieben ziehen")
        container.setObjectName("overlayPanel")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        container.setLayout(root)
        self.status.setObjectName("status")
        self._panel = container
        self._drag_surface = container
        self.setCentralWidget(container)
        self._apply_theme()
        self._fade_animation = QPropertyAnimation(self._text_opacity, b"opacity", self)
        self._fade_animation.setDuration(450)
        self._fade_animation.setStartValue(1.0)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.finished.connect(self._clear_faded_text)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._fade_out)
        self._type_timer = QTimer(self)
        self._type_timer.setInterval(TYPE_INTERVAL_MS)
        self._type_timer.timeout.connect(self._type_next_character)
        self._typing_text = ""
        self._typing_index = 0
        self._hide_after_typing = False
        self._speech_jobs = 0
        self._recording = False
        self._capture_stop = None
        self._last_response = ""
        self._last_text = ""
        self._position_tracking_enabled = False
        self._position_save_timer = QTimer(self)
        self._position_save_timer.setSingleShot(True)
        self._position_save_timer.setInterval(500)
        self._position_save_timer.timeout.connect(self._save_overlay_position)
        self._fit_to_content()
        self._restore_overlay_position()
        self._tray_recording = False
        self._init_tray()
        self.listener = self._make_listener()
        self.listener.start()
        self._apply_display_mode()
        self._position_tracking_enabled = True

    def _init_tray(self):
        self.tray_menu = QMenu(self)
        self.tray_menu.setStyleSheet(dialog_stylesheet(system_accent(), system_surface(), system_text()))
        self.tray_toggle_action = QAction("Aufnahme starten", self)
        self.tray_toggle_action.triggered.connect(self._toggle_tray_recording)
        self.tray_menu.addAction(self.tray_toggle_action)
        self.tray_menu.addSeparator()
        self.tray_settings_action = QAction("Einstellungen …", self)
        self.tray_settings_action.triggered.connect(self._settings)
        self.tray_menu.addAction(self.tray_settings_action)
        self.tray_play_action = QAction("Letzte Antwort erneut vorlesen", self)
        self.tray_play_action.triggered.connect(self._speak_last)
        self.tray_menu.addAction(self.tray_play_action)
        self.tray_menu.addSeparator()
        self.tray_exit_action = QAction("Beenden", self)
        self.tray_exit_action.triggered.connect(self.close)
        self.tray_menu.addAction(self.tray_exit_action)
        self.tray_icon = QSystemTrayIcon(make_app_icon(), self)
        self.tray_icon.setToolTip(APP_NAME)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)

    def _apply_display_mode(self):
        buttons_visible = not self.config.tray_mode
        for button in (self.mic, self.say, self.settings_button):
            button.setVisible(buttons_visible)
        if self.config.tray_mode:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.config.tray_mode = False
                for button in (self.mic, self.say, self.settings_button):
                    button.setVisible(True)
                self.tray_icon.hide()
                self.show()
                self._set_status("System-Tray nicht verfügbar · Overlay-Modus aktiviert")
                return
            self.tray_icon.show()
            self.hide()
            return
        self.tray_icon.hide()
        self.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_tray_recording()

    def _toggle_tray_recording(self):
        if self._recording:
            self._tray_recording = False
            self.tray_toggle_action.setText("Aufnahme starten")
            self._ptt_event("released", "")
        else:
            self._tray_recording = True
            self.tray_toggle_action.setText("Aufnahme senden")
            self.tray_icon.setToolTip(f"{APP_NAME} · Aufnahme läuft. Zum Senden erneut klicken.")
            self._ptt_event("pressed", "")

    def _make_listener(self):
        extra_keys = {key: name for name, key in self.config.special_hotkeys.items()
                      if key and name in self.config.special_prompts}
        return PttListener(self.config.ptt_key, self.config.joystick_device, self.config.joystick_button,
                           lambda kind, text: self.events.ptt.emit(kind, text), extra_keys)

    def _apply_theme(self):
        accent, surface, text = system_accent(), system_surface(), system_text()
        self._panel.setStyleSheet(f"""
            QWidget#overlayPanel {{
                background-color: {rgba(surface, 170)};
                border: 1px solid {rgba(accent, 170)};
                border-radius: 16px;
            }}
            QLabel {{ color: {text.name()}; background: transparent; border: none; }}
            QLabel#status {{ color: {accent.name()}; font-weight: 600; }}
            QTextEdit {{ color: {text.name()}; background: transparent; border: none; padding: 0;
                         selection-background-color: {rgba(accent, 110)}; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {rgba(accent, 150)}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QToolButton {{ background: transparent; border: none; border-radius: 22px; }}
            QToolButton:hover {{ background-color: {rgba(accent, 46)}; }}
            QToolButton:pressed {{ background-color: {rgba(accent, 96)}; }}
        """)
        self.mic.setIcon(make_icon("mic", accent))
        self.say.setIcon(make_icon("play", accent))
        self.settings_button.setIcon(make_icon("gear", accent))
        for button in (self.mic, self.say, self.settings_button):
            button.setIconSize(QSize(22, 22))
        self._transcript_formats = {
            "user_label": self._text_format(mix_colors(text, surface, 0.5), bold=True),
            "user": self._text_format(mix_colors(text, surface, 0.78)),
            "ai_label": self._text_format(accent, bold=True),
            "ai": self._text_format(mix_colors(accent, text, 0.82)),
        }

    @staticmethod
    def _text_format(color, bold: bool = False) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(color)
        text_format.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return text_format

    def _render_transcript(self, text: str, count: int):
        self.transcript.clear()
        cursor = self.transcript.textCursor()
        remaining = count
        for chunk, kind in transcript_segments(text):
            if remaining <= 0:
                break
            piece = chunk[:remaining]
            remaining -= len(piece)
            cursor.insertText(piece, self._transcript_formats[kind])

    def _ptt_event(self, kind: str, text: str):
        if kind == "error":
            self._set_status(text)
            self._hide_after_typing = True
            self._schedule_hide()
        elif kind == "pressed":
            self._hide_timer.stop()
            self._hide_after_typing = False
            self._tray_recording = True
            self.tray_toggle_action.setText("Aufnahme senden")
            self._fade_animation.stop()
            self._text_opacity.setOpacity(1)
            profile_note = f" · {text}" if text else ""
            self._set_status(f"Aufnahme läuft{profile_note} · erneut klicken zum Senden" if self.config.tray_mode
                             else f"Aufnahme läuft{profile_note} · loslassen zum Senden")
            self.show()
            self.raise_()
            if not self._recording:
                self._recording = True
                self._capture_stop = threading.Event()
                threading.Thread(target=self._capture, args=(self._capture_stop, text), daemon=True).start()
        elif kind == "released":
            self._recording = False
            self._tray_recording = False
            self.tray_toggle_action.setText("Aufnahme starten")
            self.tray_icon.setToolTip(APP_NAME)
            if self._capture_stop is not None:
                self._capture_stop.set()
                self._capture_stop = None
            self._set_status("Verarbeite Sprache …")

    def _capture(self, stop_event: threading.Event, profile: str = ""):
        try:
            audio = record_until_released(lambda: not stop_event.is_set(), self.config.sample_rate)
            if not audio:
                return
            debug_wav = os.environ.get("SC_AI_COPILOT_DEBUG_WAV")
            if debug_wav:
                save_wav(audio, debug_wav, self.config.sample_rate)
            text = transcribe_sync(audio, self.config.stt_url, self.config.sample_rate, self.config.stt_language)
            if not text:
                self.events.message.emit("Bereit · keine Sprache erkannt", "")
                return
            self.events.message.emit(THINKING_STATUS, f"{USER_PREFIX}{text}\n")
            profile_name = profile if profile in self.config.special_prompts else self.config.active_special_prompt
            answer = chat(
                self.config.llm_provider,
                self.config.llm_url,
                self.config.llm_model,
                text,
                system_prompt=build_system_prompt(
                    piper_voice=self.config.tts_voice,
                    special_prompt_name=profile_name,
                    special_prompt=self.config.special_prompts.get(profile_name, ""),
                    fallback_language=self.config.response_language,
                    global_prompt=self.config.global_prompt,
                ),
                enabled_tools=self.config.enabled_tools.get(profile_name or "__global__", []),
            )
            self.events.message.emit("Bereit · Antwort lokal erzeugt", f"{USER_PREFIX}{text}{AI_MARKER}{answer}")
            if self.config.auto_speak:
                self.events.speak.emit(answer)
        except Exception as error:
            self.events.message.emit(f"Fehler · {error}", "")

    def _show_message(self, status: str, text: str):
        self._set_status(status)
        self._hide_after_typing = status != THINKING_STATUS
        self.show()
        self.raise_()
        if not text:
            self._schedule_hide()
            return
        self._fade_animation.stop()
        self._text_opacity.setOpacity(1)
        self._typing_text = text
        self._typing_index = 0
        self.transcript.clear()
        self._fit_to_content()
        self._hide_timer.stop()
        self._type_timer.start()
        if AI_MARKER in text:
            self._last_text = text
            self._last_response = text.split(AI_MARKER, 1)[1]

    def _set_status(self, text: str):
        self.status.setText(text)
        self._fit_to_content()

    def _type_next_character(self):
        self._typing_index = min(self._typing_index + 1, len(self._typing_text))
        self._render_transcript(self._typing_text, self._typing_index)
        self._fit_to_content()
        if self._typing_index >= len(self._typing_text):
            self._type_timer.stop()
            self._schedule_hide()

    def _fit_to_content(self):
        has_text = bool(self.status.text() or self.transcript.toPlainText())
        self._text_container.setVisible(has_text)
        height = int(self.transcript.document().size().height() + 0.999) if self.transcript.toPlainText() else 0
        transcript_height = min(height, TRANSCRIPT_MAX_HEIGHT)
        self.transcript.setFixedHeight(transcript_height)
        scroll_bar = self.transcript.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        self.setFixedWidth(FULL_WIDTH if has_text else COMPACT_WIDTH)
        # Das Layout cached seine Größe bis zum nächsten Event, daher direkt rechnen.
        text_height = self.status.sizeHint().height() + 2 + transcript_height if has_text else 0
        self.setFixedHeight(max(58, 16 + max(44, text_height)))

    def _restore_overlay_position(self):
        screens = QGuiApplication.screens()
        if not screens:
            return
        if self.config.overlay_x is not None and self.config.overlay_y is not None:
            saved = QPoint(self.config.overlay_x, self.config.overlay_y)
            window_rect = QRect(saved, self.size())
            for screen in screens:
                visible = screen.availableGeometry().intersected(window_rect)
                if visible.width() >= min(80, window_rect.width()) and visible.height() >= min(40, window_rect.height()):
                    self.move(saved)
                    return
        primary = QGuiApplication.primaryScreen() or screens[0]
        area = primary.availableGeometry()
        x = area.left() + (area.width() - self.width()) // 2
        y = area.top() + 12
        self.move(x, y)

    def _save_overlay_position(self):
        position = self.pos()
        self.config.overlay_x = position.x()
        self.config.overlay_y = position.y()
        save_config(self.config)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._position_tracking_enabled:
            self._position_save_timer.start()

    def reset_overlay_position(self):
        self._position_save_timer.stop()
        self.config.overlay_x = None
        self.config.overlay_y = None
        self._restore_overlay_position()
        self._save_overlay_position()

    def _schedule_hide(self):
        # Erst ausblenden, wenn Text fertig getippt und Audio fertig abgespielt ist.
        if (
            self._hide_after_typing
            and not self._recording
            and self._speech_jobs == 0
            and not self._type_timer.isActive()
        ):
            self._hide_timer.start()

    def _speech_finished(self):
        self._speech_jobs = max(0, self._speech_jobs - 1)
        self._schedule_hide()

    def _fade_out(self):
        if self.isVisible() and (self.status.text() or self.transcript.toPlainText()):
            self._fade_animation.start()

    def _clear_faded_text(self):
        self.status.clear()
        self.transcript.clear()
        self._typing_text = ""
        self._text_opacity.setOpacity(1)
        self._fit_to_content()
        if self.config.tray_mode:
            self.hide()

    def _speak_last(self):
        if not self._last_response:
            self._show_message("Noch keine Antwort zum Vorlesen", "")
            return
        self._show_message("Piper spricht …", self._last_text)
        self._start_speak(self._last_response)

    def _start_speak(self, text: str):
        self._set_status("Piper spricht …")
        self._speech_jobs += 1
        self._hide_timer.stop()
        self._fade_animation.stop()
        self._text_opacity.setOpacity(1)
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str):
        try:
            audio, sample_rate = speak_sync(text, self.config.tts_url, self.config.tts_voice)
            play_pcm(audio, sample_rate)
            self.events.message.emit("Bereit · PTT zum Sprechen", "")
        except Exception as error:
            self.events.message.emit(f"Piper-Fehler · {error}", "")
        finally:
            self.events.speech_finished.emit()

    def _settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            self.listener.stop()
            self.config = dialog.apply(self.config)
            if self.config.tray_mode and not QSystemTrayIcon.isSystemTrayAvailable():
                QMessageBox.warning(dialog, "Tray nicht verfügbar", "In dieser Sitzung ist kein System-Tray verfügbar. Der Overlay-Modus bleibt aktiv.")
                self.config.tray_mode = False
            save_config(self.config)
            self.listener = self._make_listener()
            self.listener.start()
            self._apply_display_mode()

    def closeEvent(self, event):
        if self._position_save_timer.isActive():
            self._position_save_timer.stop()
            self._save_overlay_position()
        self.listener.stop()
        self.tray_icon.hide()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()


def configure_application(app: QApplication):
    app.setQuitOnLastWindowClosed(False)
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    def handle_sigint(_signum, _frame):
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            dialog.reject()
        app.quit()

    signal.signal(signal.SIGINT, handle_sigint)
    signal_timer = QTimer(app)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)
    restored = False

    def restore_sigint_handler():
        nonlocal restored
        if restored:
            return
        restored = True
        signal_timer.stop()
        signal.signal(signal.SIGINT, previous_sigint_handler)

    return restore_sigint_handler


def apply_app_identity(app: QApplication):
    app.setApplicationName(APP_NAME)
    # Unter Wayland ordnet der Desktop Fenster über diese ID dem Starter samt Icon zu.
    QGuiApplication.setDesktopFileName(APP_ID)
    app.setWindowIcon(make_app_icon())


def prefer_x11_on_wayland():
    # Wayland erlaubt Clients weder Fensterpositionen noch Always-on-top; XWayland schon.
    if os.environ.get("DISPLAY") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def main():
    parser = argparse.ArgumentParser(prog=APP_ID, description=APP_NAME)
    parser.add_argument("--install-launcher", action="store_true", help="Starter und Icon im Anwendungsmenü installieren")
    arguments = parser.parse_args()
    if arguments.install_launcher:
        desktop_path, icon_path = install_launcher()
        print(f"Starter: {desktop_path}\nIcon:    {icon_path}")
        raise SystemExit(0)
    prefer_x11_on_wayland()
    app = QApplication([])
    apply_app_identity(app)
    restore_sigint_handler = configure_application(app)
    overlay = Overlay()
    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        exit_code = 130
    restore_sigint_handler()
    overlay.listener.stop()
    overlay.close()
    overlay.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
