from __future__ import annotations

import argparse
import os
import signal
import threading
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QRect, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QAction, QFont, QGuiApplication, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from voxcomm.audio import play_stream, prefetch, record_until_released, save_wav
from voxcomm.branding import APP_ID, APP_NAME, install_launcher, make_app_icon
from voxcomm.config import AppConfig, load_config, save_config
from voxcomm.i18n import SUPPORTED_LANGUAGES, set_language, tr
from voxcomm.mangohud import publish_status
from voxcomm.providers import cloud, openai_tts, whisper_cpp
from voxcomm.providers.llm import chat, list_models, thinking_options
from voxcomm.providers.wyoming import filter_voices, list_voices, speak_stream_sync, transcribe_sync, voice_language
from voxcomm.prompts import VOICE_LANGUAGE_NAMES, build_system_prompt, response_language_for_voice
from voxcomm.ptt import PttListener, capture_input
from voxcomm.streaming import SentenceStream
from voxcomm.theme import dialog_stylesheet, make_icon, mix_colors, rgba, system_accent, system_surface, system_text


LANGUAGE_LABELS = VOICE_LANGUAGE_NAMES

TYPE_INTERVAL_MS = 24
MIN_PTT_DURATION_MS = 200
TRANSCRIPT_WIDTH = 480
TRANSCRIPT_MAX_HEIGHT = 200
THINKING_STATUS = "Thinking …"
SPEAKING_STATUS = "Speaking …"
WHISPER_CPP_DEFAULT_URL = "http://127.0.0.1:8081"
OPENAI_TTS_DEFAULT_URL = "http://127.0.0.1:8097"
SETTINGS_LABEL_WIDTH = 140
SETTINGS_ICON_BUTTON = 33
THINKING_LABELS = {"off": "Off", "on": "On", "low": "Low", "medium": "Medium", "high": "High"}
USER_PREFIX = "You: "
AI_LABEL = "AI: "
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
OVERLAY_HORIZONTAL_MARGINS = 24


class Events(QObject):
    ptt = Signal(str, str)
    message = Signal(str, str)
    request_message = Signal(str, str, object)
    activity = Signal(str, object)
    discovery = Signal(str, object, str)
    thinking = Signal(int, object)
    speak = Signal(object, object, object)
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
        self.button.setToolTip(tr("Click, then press a key or controller button. Press Esc to cancel."))
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
            self.clear_button.setToolTip(tr("Remove hotkey"))
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
            self.button.setText(tr("Press a key / button …"))
        else:
            self.button.setText(self._value.upper() if self._value else tr("Not assigned"))
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
                result = capture_input(stop) or ""
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
        set_language(config.ui_language)
        self._response_language_fallback = config.response_language
        self.ui_language = QComboBox()
        for code, label in SUPPORTED_LANGUAGES.items():
            self.ui_language.addItem(label, code)
        self.ui_language.setCurrentIndex(max(self.ui_language.findData(config.ui_language), 0))
        self.setWindowTitle(tr("Settings · {name}", name=APP_NAME))
        self.setMinimumWidth(880)
        self.setStyleSheet(dialog_stylesheet(system_accent(), system_surface(), system_text()))
        self.discovery = Events()
        self.discovery.discovery.connect(self._discovery_result)
        self.discovery.thinking.connect(self._apply_thinking_options)
        self.stt = QLineEdit(config.stt_url)
        self.stt_provider = QComboBox()
        self.stt_provider.addItem(tr("Wyoming"), "wyoming")
        self.stt_provider.addItem(tr("whisper.cpp server"), "whisper.cpp")
        self.stt_provider.setCurrentIndex(max(self.stt_provider.findData(config.stt_provider), 0))
        self.stt_language = QComboBox()
        self.stt_language.addItem(tr("Automatic"), "auto")
        for code, label in LANGUAGE_LABELS.items():
            self.stt_language.addItem(f"{label} ({code})", code)
        stt_language_index = self.stt_language.findData(config.stt_language)
        self.stt_language.setCurrentIndex(max(stt_language_index, 0))
        self.tts = QLineEdit(config.tts_url)
        self.tts_provider = QComboBox()
        self.tts_provider.addItem(tr("Wyoming Piper"), "wyoming")
        self.tts_provider.addItem(tr("OpenAI-compatible (qwentts and others)"), "openai")
        self.tts_provider.setCurrentIndex(max(self.tts_provider.findData(config.tts_provider), 0))
        self.tts_language = QComboBox()
        self.tts_language.addItem(tr("Service default"), "")
        for label in VOICE_LANGUAGE_NAMES.values():
            self.tts_language.addItem(label, label)
        self.tts_language.setCurrentIndex(max(self.tts_language.findData(config.tts_language), 0))
        self._available_voices: list[str] = []
        self.voice_language = QComboBox()
        self.voice_language.addItem(tr("All languages"), "")
        for code, label in LANGUAGE_LABELS.items():
            self.voice_language.addItem(f"{label} ({code})", code)
        language_index = self.voice_language.findData(config.voice_language)
        self.voice_language.setCurrentIndex(max(language_index, 0))
        self.voice_language.currentIndexChanged.connect(self._refresh_voice_options)
        self._global_voice = config.tts_voice
        self.response_language = QLabel()
        self.response_language.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.special_prompts = dict(config.special_prompts)
        self.special_hotkeys = {name: key for name, key in config.special_hotkeys.items() if name in self.special_prompts}
        self.input_action_catalogs = dict(config.input_action_catalogs)
        self.enabled_tools = {name: [tool for tool in tools if tool in {"web_search", "input_control"}]
                      for name, tools in config.enabled_tools.items()}
        self.profile_hotkey = HotkeyField("", clearable=True, validator=self._profile_hotkey_conflict)
        self.profile_hotkey.setToolTip(tr("This hotkey uses this profile in addition to Global"))
        self.profile_hotkey.changed.connect(self._store_profile_hotkey)
        self._global_prompt = config.global_prompt
        self.special_profile = QComboBox()
        self.special_profile.addItem(tr("Global"), "__global__")
        for name in sorted(self.special_prompts, key=str.casefold):
            self.special_profile.addItem(name, name)
        active_profile = config.active_special_prompt if config.active_special_prompt in self.special_prompts else "__global__"
        self.special_profile.setCurrentIndex(max(self.special_profile.findData(active_profile), 0))
        self._active_profile_name = active_profile
        self.special_profile.currentIndexChanged.connect(self._select_special_profile)
        self.web_search = QCheckBox(tr("Allow web search"))
        self.web_search.setToolTip(tr("Search queries are sent to external search providers via DDGS; results are not opened automatically."))
        self.web_search.setChecked("web_search" in self.enabled_tools.get(active_profile, []))
        self.input_control = QCheckBox(tr("Allow VoxComm controller actions"))
        self.input_control.setToolTip(tr("This profile can only trigger configured actions through the virtual VoxComm controller."))
        self.input_control.setChecked("input_control" in self.enabled_tools.get(active_profile, []))
        self.input_action_catalog = QPlainTextEdit(
            self.input_action_catalogs.get(active_profile, ""))
        self.input_action_catalog.setPlaceholderText(
            tr("Action name => keywords|alias => ButtonN => short reply"))
        self.input_action_catalog.setMinimumHeight(280)
        self.input_action_catalog.setMaximumHeight(360)
        self.action_catalog_section = self._section(tr("VoxComm Controller"), [(tr("Action catalog"), self.input_action_catalog)])
        self.action_catalog_section.setVisible(self.input_control.isChecked())
        self.input_control.toggled.connect(self.action_catalog_section.setVisible)
        initial_prompt = config.global_prompt if active_profile == "__global__" else self.special_prompts.get(active_profile, "")
        self.special_prompt = QPlainTextEdit(initial_prompt)
        self.special_prompt.setPlaceholderText(tr("Global base prompt") if active_profile == "__global__"
                              else tr("Task knowledge and behavior rules for this profile"))
        self.special_prompt.setMaximumBlockCount(100)
        self.special_prompt.setMinimumHeight(150)
        self.add_profile = QPushButton(tr("Add"))
        self.add_profile.clicked.connect(self._add_special_profile)
        self.rename_profile = QPushButton(tr("Rename"))
        self.rename_profile.clicked.connect(self._rename_special_profile)
        self.remove_profile = QPushButton(tr("Remove"))
        self.remove_profile.clicked.connect(self._remove_special_profile)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        profile_row.addWidget(self.add_profile, 1)
        profile_row.addWidget(self.rename_profile, 1)
        profile_row.addWidget(self.remove_profile, 1)
        self._update_profile_controls()
        self.provider = QComboBox()
        self.provider.addItem(tr("Ollama (local)"), "ollama")
        self.provider.addItem("Ollama Cloud", "ollama-cloud")
        self.provider.addItem("llama.cpp", "llama.cpp")
        self.provider.setCurrentIndex(max(self.provider.findData(config.llm_provider), 0))
        self.llm_url = QLineEdit(config.llm_url)
        self.api_key = QLineEdit(config.ollama_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(tr("API key from ollama.com/settings/keys"))
        self.key_test = QPushButton(tr("Verify key"))
        self.key_row = QWidget()
        key_layout = QHBoxLayout(self.key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(self.api_key, 1)
        key_layout.addWidget(self.key_test)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setCurrentText(config.llm_model)
        self._model_overrides = dict(config.llm_model_overrides)
        self._voice_overrides = dict(config.tts_voice_overrides)
        self.profile_model = QComboBox()
        self.profile_model.setEditable(True)
        self.profile_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.profile_model.lineEdit().setPlaceholderText(tr("Use global model"))
        self.profile_model.setCurrentText(self._model_overrides.get(active_profile, ""))
        self.profile_model_refresh = self._refresh_button(tr("Load models from service"))
        self.profile_model_refresh.clicked.connect(lambda: self._discover("models", self.profile_model_refresh))
        profile_model_row = QHBoxLayout()
        profile_model_row.setContentsMargins(0, 0, 0, 0)
        profile_model_row.setSpacing(8)
        profile_model_row.addWidget(self.profile_model, 1)
        profile_model_row.addWidget(self.profile_model_refresh)
        self.profile_voice = QComboBox()
        self.profile_voice.setEditable(True)
        self.profile_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.profile_voice.lineEdit().setPlaceholderText(tr("Use global voice"))
        self.profile_voice.setCurrentText(
            config.tts_voice if active_profile == "__global__" else self._voice_overrides.get(active_profile, ""))
        self.profile_voice.currentTextChanged.connect(self._update_response_language)
        self.profile_voice_refresh = self._refresh_button(tr("Load voices from service"))
        self.profile_voice_refresh.clicked.connect(lambda: self._discover("voices", self.profile_voice_refresh))
        profile_voice_row = QHBoxLayout()
        profile_voice_row.setContentsMargins(0, 0, 0, 0)
        profile_voice_row.setSpacing(8)
        profile_voice_row.addWidget(self.profile_voice, 1)
        profile_voice_row.addWidget(self.profile_voice_refresh)
        self._load_profile_overrides()
        self._thinking_setting = config.llm_thinking
        self._thinking_overrides = dict(config.llm_thinking_overrides)
        self._thinking_options: list[str] = []
        self.profile_thinking = QComboBox()
        self.profile_thinking.addItem(tr("Global default"), "")
        profile_thinking = self._thinking_overrides.get(active_profile, "")
        if profile_thinking:
            self.profile_thinking.addItem(tr(THINKING_LABELS.get(profile_thinking, profile_thinking.capitalize())), profile_thinking)
            self.profile_thinking.setCurrentIndex(1)
        self._thinking_token = 0
        self._thinking_shown = False
        self.thinking = QComboBox()
        self.thinking.setToolTip(tr(
            "The model reports which levels it supports. For reasoning models, setting this to Off can "
            "significantly reduce response time."))
        self._fill_thinking([])
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setSingleShot(True)
        self._thinking_timer.setInterval(400)
        self._thinking_timer.timeout.connect(self._lookup_thinking)
        self.profile_model.lineEdit().editingFinished.connect(self._thinking_timer.start)
        self.profile_model.activated.connect(lambda _: self._thinking_timer.start())
        self.thinking.activated.connect(lambda _: self._store_thinking())
        self.key = HotkeyField(config.ptt_key, clearable=True, validator=self._general_hotkey_conflict)
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(8000)
        self.sample_rate.setValue(config.sample_rate)
        self.sample_rate.setFixedHeight(30)
        self.overlay_hide_seconds = QSpinBox()
        self.overlay_hide_seconds.setRange(1, 120)
        self.overlay_hide_seconds.setSuffix(" s")
        self.overlay_hide_seconds.setValue(config.overlay_hide_seconds)
        self.overlay_hide_seconds.setFixedHeight(30)
        self.auto_speak = QCheckBox(tr("Automatically read responses aloud after generation"))
        self.auto_speak.setChecked(config.auto_speak)
        self.tray_mode = QCheckBox(tr("Tray mode: click to start/stop recording; hide overlay buttons"))
        self.tray_mode.setChecked(config.tray_mode)
        self.reset_position = QPushButton(tr("Reset overlay position"))
        if parent is not None and hasattr(parent, "reset_overlay_position"):
            self.reset_position.clicked.connect(parent.reset_overlay_position)
        else:
            self.reset_position.setEnabled(False)
        self.model_refresh = self._refresh_button(tr("Load models from service"))
        self.model_refresh.clicked.connect(lambda: self._discover("models"))
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(10)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.model_refresh)
        self._model_row = model_row
        self.response_language.setObjectName("hint")
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._section(tr("Input"), [
            (tr("Interface language"), self.ui_language),
            (tr("PTT key / button"), self.key), (tr("Microphone sample rate"), self.sample_rate),
            (tr("Overlay display duration"), self.overlay_hide_seconds),
            (None, self.tray_mode), (None, self.reset_position),
        ]))
        self.llm_section = self._section(tr("Language model"), [
            (tr("Provider"), self.provider), (tr("URL"), self.llm_url), (tr("Model"), model_row),
            (tr("Reasoning mode"), self.thinking), (tr("API key"), self.key_row),
        ])
        left.addWidget(self.llm_section)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(14)
        self.stt_section = self._section(tr("Speech recognition"), [
            (tr("Provider"), self.stt_provider), (tr("URL"), self.stt), (tr("Recognition language"), self.stt_language),
        ])
        right.addWidget(self.stt_section)
        self.tts_section = self._section(tr("Speech output"), [
            (tr("Provider"), self.tts_provider), (tr("URL"), self.tts), (tr("TTS language"), self.tts_language),
            (tr("Voice language"), self.voice_language), (tr("Response language"), self.response_language),
            (None, self.auto_speak),
        ])
        right.addWidget(self.tts_section)
        right.addStretch(1)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        global_tab = QWidget()
        global_layout = QVBoxLayout(global_tab)
        global_layout.setContentsMargins(0, 0, 0, 0)
        global_layout.addLayout(columns, 1)
        prompt_tab = QWidget()
        prompt_layout = QVBoxLayout(prompt_tab)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.addWidget(self._section(tr("Prompt"), [
            (tr("Profile"), self.special_profile), ("", profile_row), (tr("Profile hotkey"), self.profile_hotkey),
            (tr("Profile model"), profile_model_row), (tr("Profile voice"), profile_voice_row),
            (tr("Profile reasoning"), self.profile_thinking),
            (None, self.web_search), (None, self.input_control),
            (None, self.special_prompt),
        ]), 1)
        prompt_layout.addWidget(self.action_catalog_section)
        settings_tabs = QTabWidget()
        settings_tabs.addTab(global_tab, tr("Global Settings"))
        settings_tabs.addTab(prompt_tab, tr("Prompt Settings"))
        title = QLabel(tr("Settings"))
        title.setObjectName("dialogTitle")
        self.discovery_status = QLabel(tr("Load voices and models from the service using the refresh button"))
        self.discovery_status.setObjectName("hint")
        self.discovery_status.setWordWrap(True)
        self.key.error.connect(self.discovery_status.setText)
        self.profile_hotkey.error.connect(self.discovery_status.setText)
        self.provider.currentIndexChanged.connect(self._update_llm_fields)
        for restart in (self.model.currentTextChanged, self.provider.currentIndexChanged,
                        self.llm_url.editingFinished, self.api_key.editingFinished):
            restart.connect(lambda *_: self._thinking_timer.start())
        self.tts_provider.currentIndexChanged.connect(self._update_tts_fields)
        self.stt_provider.currentIndexChanged.connect(self._update_stt_fields)
        self.tts_language.currentIndexChanged.connect(lambda: self._update_response_language(self.profile_voice.currentText()))
        self.key_test.clicked.connect(self._test_api_key)
        self._update_stt_fields()
        self._update_tts_fields()
        self._update_llm_fields()
        save = QPushButton(tr("Save"))
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
        root.addWidget(settings_tabs, 1)
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
        frame.form = form
        return frame

    def showEvent(self, event):
        super().showEvent(event)
        if not self._thinking_shown:
            self._thinking_shown = True
            self._lookup_thinking()

    def _fill_thinking(self, options: list[str], known: bool = False):
        self._thinking_options = list(options)
        selected = self._thinking_setting
        self.thinking.clear()
        self.thinking.addItem(tr("Model default"), "")
        for option in options:
            self.thinking.addItem(tr(THINKING_LABELS.get(option, option.capitalize())), option)
        if known and not options:
            self.thinking.clear()
            self.thinking.addItem(tr("Not controlled by model"), "")
        elif not known and selected:
            self.thinking.addItem(tr(THINKING_LABELS.get(selected, selected.capitalize())), selected)
        self.thinking.setEnabled(not known or bool(options))
        self.thinking.setCurrentIndex(max(self.thinking.findData(selected), 0))
        self._fill_profile_thinking()

    def _fill_profile_thinking(self):
        selected = self._thinking_overrides.get(self._active_profile_name, "")
        self.profile_thinking.blockSignals(True)
        self.profile_thinking.clear()
        self.profile_thinking.addItem(tr("Global default"), "")
        for option in self._thinking_options:
            self.profile_thinking.addItem(tr(THINKING_LABELS.get(option, option.capitalize())), option)
        if selected and selected not in self._thinking_options:
            self.profile_thinking.addItem(tr(THINKING_LABELS.get(selected, selected.capitalize())), selected)
        self.profile_thinking.setCurrentIndex(max(self.profile_thinking.findData(selected), 0))
        self.profile_thinking.blockSignals(False)

    def _store_profile_thinking(self):
        selected = self.profile_thinking.currentData() or ""
        if selected:
            self._thinking_overrides[self._active_profile_name] = selected
        else:
            self._thinking_overrides.pop(self._active_profile_name, None)

    def _lookup_thinking(self):
        self._thinking_token += 1
        token = self._thinking_token
        provider, url = self.provider.currentData() or "ollama", self.llm_url.text().strip()
        model = (self.profile_model.currentText().strip()
             or self._model_overrides.get(self._active_profile_name, "").strip()
             or self.model.currentText().strip())
        api_key = self.api_key.text()
        signals = self.discovery

        def run():
            options = thinking_options(provider, url, model, api_key)
            try:
                signals.thinking.emit(token, options)
            except RuntimeError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _apply_thinking_options(self, token: int, options: list[str]):
        if token == self._thinking_token:
            self._fill_thinking(options, known=True)

    def _store_thinking(self):
        self._thinking_setting = self.thinking.currentData() or ""

    def _update_response_language(self, voice_name: str):
        if not voice_name and self._active_profile_name != "__global__":
            voice_name = self._global_voice
        if self.tts_provider.currentData() == "openai":
            text = self.tts_language.currentData() or self._response_language_fallback
        else:
            text = response_language_for_voice(voice_name, self._response_language_fallback)
        self.response_language.setText(text)

    def _update_llm_fields(self):
        cloud_mode = self.provider.currentData() == "ollama-cloud"
        self.llm_section.form.setRowVisible(self.key_row, cloud_mode)
        current = self.llm_url.text().strip()
        if cloud_mode and current in ("", "http://127.0.0.1:11434"):
            self.llm_url.setText(cloud.CLOUD_BASE_URL)
        elif not cloud_mode and current == cloud.CLOUD_BASE_URL:
            self.llm_url.setText("http://127.0.0.1:11434")
        if cloud_mode:
            self._load_cloud_models()

    def _update_tts_fields(self):
        openai_mode = self.tts_provider.currentData() == "openai"
        self.tts_section.form.setRowVisible(self.voice_language, not openai_mode)
        current = self.tts.text().strip()
        if openai_mode and current in ("", "tcp://127.0.0.1:10200"):
            self.tts.setText(OPENAI_TTS_DEFAULT_URL)
        elif not openai_mode and current == OPENAI_TTS_DEFAULT_URL:
            self.tts.setText("tcp://127.0.0.1:10200")
        self._refresh_voice_options()
        self._update_response_language(self.profile_voice.currentText())

    def _update_stt_fields(self):
        whisper_mode = self.stt_provider.currentData() == "whisper.cpp"
        current = self.stt.text().strip()
        if whisper_mode and current in ("", "tcp://127.0.0.1:10300"):
            self.stt.setText(WHISPER_CPP_DEFAULT_URL)
        elif not whisper_mode and current == WHISPER_CPP_DEFAULT_URL:
            self.stt.setText("tcp://127.0.0.1:10300")

    def _fill_models(self, values: list[str]):
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(values)
        self.model.setCurrentText(current)

    def _load_cloud_models(self):
        models, fetched_at = cloud.cached_models()
        if models:
            self._fill_models(models)
            age = max(0, int(time.time() - fetched_at))
            self.discovery_status.setText(tr("{count} models from cache ({hours} h old)",
                                             count=len(models), hours=age // 3600))
        if not models or time.time() - fetched_at > cloud.MODEL_CACHE_TTL_SECONDS:
            self._discover("models")

    def _test_api_key(self):
        self.key_test.setEnabled(False)
        self.discovery_status.setText(tr("Verifying API key …"))
        endpoint = self.llm_url.text().strip() or cloud.CLOUD_BASE_URL
        api_key = self.api_key.text()

        def run():
            try:
                cloud.verify_api_key(endpoint, api_key)
                self.discovery.discovery.emit("key", [], "")
            except Exception as error:
                self.discovery.discovery.emit("key", [], str(error))

        threading.Thread(target=run, daemon=True).start()

    def _general_hotkey_conflict(self, key: str) -> str | None:
        for name, other in self.special_hotkeys.items():
            if other == key:
                return tr("Hotkey {key} is already assigned to profile '{name}'", key=key.upper(), name=name)
        return None

    def _profile_hotkey_conflict(self, key: str) -> str | None:
        if key == self.key.value().lower():
                return tr("Hotkey {key} is already assigned to the global push-to-talk key", key=key.upper())
        for name, other in self.special_hotkeys.items():
            if other == key and name != self._active_profile_name:
                return tr("Hotkey {key} is already assigned to profile '{name}'", key=key.upper(), name=name)
        return None

    def _store_profile_hotkey(self, key: str):
        if self._active_profile_name in self.special_prompts:
            if key:
                self.special_hotkeys[self._active_profile_name] = key
            else:
                self.special_hotkeys.pop(self._active_profile_name, None)

    def _store_profile_overrides(self):
        model = self.profile_model.currentText().strip()
        voice = self.profile_voice.currentText().strip()
        if self._active_profile_name == "__global__":
            self._global_voice = voice
            return
        if self._active_profile_name not in self.special_prompts:
            return
        if model:
            self._model_overrides[self._active_profile_name] = model
        else:
            self._model_overrides.pop(self._active_profile_name, None)
        if voice:
            self._voice_overrides[self._active_profile_name] = voice
        else:
            self._voice_overrides.pop(self._active_profile_name, None)

    def _load_profile_overrides(self):
        has_profile = self._active_profile_name in self.special_prompts
        model = self._model_overrides.get(self._active_profile_name, "")
        voice = self._global_voice if self._active_profile_name == "__global__" else self._voice_overrides.get(self._active_profile_name, "")
        self.profile_model.setCurrentText(model)
        self.profile_voice.setCurrentText(voice)
        self.profile_model.setEnabled(has_profile)
        self.profile_voice.setEnabled(True)
        self._update_response_language(voice)

    def done(self, result):
        self.key.stop_capture()
        self.profile_hotkey.stop_capture()
        super().done(result)

    def _store_special_profile(self):
        self._store_enabled_tools()
        self._store_input_action_catalog()
        self._store_profile_overrides()
        self._store_profile_thinking()
        if self._active_profile_name == "__global__":
            self._global_prompt = self.special_prompt.toPlainText().strip()
            return
        if self._active_profile_name:
            self.special_prompts[self._active_profile_name] = self.special_prompt.toPlainText().strip()

    def _store_input_action_catalog(self):
        if self._active_profile_name not in self.special_prompts and self._active_profile_name != "__global__":
            return
        catalog = self.input_action_catalog.toPlainText().strip()
        if catalog:
            self.input_action_catalogs[self._active_profile_name] = catalog
        else:
            self.input_action_catalogs.pop(self._active_profile_name, None)

    def _load_input_action_catalog(self, catalog: str):
        self.input_action_catalog.setPlainText(catalog)

    def _store_enabled_tools(self):
        if self._active_profile_name in self.special_prompts or self._active_profile_name == "__global__":
            tools = []
            if self.web_search.isChecked():
                tools.append("web_search")
            if self.input_control.isChecked():
                tools.append("input_control")
            if tools:
                self.enabled_tools[self._active_profile_name] = tools
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
        self.input_control.setChecked("input_control" in self.enabled_tools.get(self._active_profile_name, []))
        self._load_input_action_catalog(self.input_action_catalogs.get(self._active_profile_name, ""))
        self.special_prompt.setPlainText(prompt)
        self._fill_profile_thinking()
        self.special_prompt.setPlaceholderText(tr("Global base prompt") if self._active_profile_name == "__global__"
                               else tr("Task knowledge and behavior rules for this profile"))
        self._update_profile_controls()

    def _update_profile_controls(self):
        has_profile = self.special_profile.currentData() not in (None, "", "__global__")
        self.special_prompt.setEnabled(True)
        self.rename_profile.setEnabled(has_profile)
        self.remove_profile.setEnabled(has_profile)
        self.profile_hotkey.setEnabled(has_profile)
        self.profile_hotkey.setValue(self.special_hotkeys.get(self._active_profile_name, "") if has_profile else "")
        if hasattr(self, "profile_model"):
            self._load_profile_overrides()

    def _refresh_special_profiles(self, selected: str):
        self.special_profile.blockSignals(True)
        self.special_profile.clear()
        self.special_profile.addItem(tr("Global"), "__global__")
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
        self.input_control.setChecked("input_control" in self.enabled_tools.get(self._active_profile_name, []))
        self._load_input_action_catalog(self.input_action_catalogs.get(self._active_profile_name, ""))
        self.special_prompt.setPlainText(prompt)
        self._fill_profile_thinking()
        self.special_prompt.setPlaceholderText(tr("Global base prompt") if self._active_profile_name == "__global__"
                               else tr("Task knowledge and behavior rules for this profile"))
        self._update_profile_controls()

    def _add_special_profile(self):
        self._store_special_profile()
        name, accepted = QInputDialog.getText(self, tr("Add special profile"), tr("Profile name"))
        name = name.strip()
        if not accepted or not name:
            return
        if name in self.special_prompts:
            QMessageBox.information(self, tr("Profile exists"), tr("This profile name is already in use."))
            return
        self.special_prompts[name] = ""
        self._refresh_special_profiles(name)

    def _rename_special_profile(self):
        current = self.special_profile.currentData()
        if not current:
            return
        name, accepted = QInputDialog.getText(self, tr("Rename special profile"), tr("Profile name"), text=current)
        name = name.strip()
        if not accepted or not name or name == current:
            return
        if name in self.special_prompts:
            QMessageBox.information(self, tr("Profile exists"), tr("This profile name is already in use."))
            return
        self._store_special_profile()
        self.special_prompts[name] = self.special_prompts.pop(current)
        if current in self.enabled_tools:
            self.enabled_tools[name] = self.enabled_tools.pop(current)
        if current in self.special_hotkeys:
            self.special_hotkeys[name] = self.special_hotkeys.pop(current)
        if current in self.input_action_catalogs:
            self.input_action_catalogs[name] = self.input_action_catalogs.pop(current)
        if current in self._thinking_overrides:
            self._thinking_overrides[name] = self._thinking_overrides.pop(current)
        if current in self._model_overrides:
            self._model_overrides[name] = self._model_overrides.pop(current)
        if current in self._voice_overrides:
            self._voice_overrides[name] = self._voice_overrides.pop(current)
        self._refresh_special_profiles(name)

    def _remove_special_profile(self):
        current = self.special_profile.currentData()
        if not current:
            return
        answer = QMessageBox.question(self, tr("Remove special profile"),
                          tr("Really remove profile '{name}'?", name=current))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store_special_profile()
        self.special_prompts.pop(current, None)
        self.enabled_tools.pop(current, None)
        self.special_hotkeys.pop(current, None)
        self.input_action_catalogs.pop(current, None)
        self._thinking_overrides.pop(current, None)
        self._model_overrides.pop(current, None)
        self._voice_overrides.pop(current, None)
        self._refresh_special_profiles("")

    def _discover(self, kind: str, button=None):
        if kind == "voices":
            button = button or self.profile_voice_refresh
            endpoint = self.tts.text().strip()
            if self.tts_provider.currentData() == "openai":
                lookup = lambda: openai_tts.list_voices(endpoint)
            else:
                lookup = lambda: list_voices(endpoint)
        else:
            button = button or self.model_refresh
            provider = self.provider.currentData()
            endpoint = self.llm_url.text().strip() or (
                cloud.CLOUD_BASE_URL if provider == "ollama-cloud" else "")
            lookup = lambda: list_models(provider, endpoint)
        button.setEnabled(False)
        self.discovery_status.setText(tr("Connecting to service …"))

        def run():
            try:
                self.discovery.discovery.emit(kind, lookup(), "")
            except Exception as error:
                self.discovery.discovery.emit(kind, [], str(error))

        threading.Thread(target=run, daemon=True).start()

    def _discovery_result(self, kind: str, values: list[str], error: str):
        if kind == "key":
            self.key_test.setEnabled(True)
            self.discovery_status.setText(tr("API key is valid") if not error
                                          else tr("Invalid API key: {error}", error=error))
            return
        buttons = (self.profile_voice_refresh,) if kind == "voices" else (self.model_refresh, self.profile_model_refresh)
        for button in buttons:
            button.setEnabled(True)
        label = tr("voices") if kind == "voices" else tr("models")
        if error:
            self.discovery_status.setText(tr("Could not load {label}: {error}", label=label, error=error))
            return
        if kind == "voices":
            self._available_voices = values
            self._refresh_voice_language_options()
            self._refresh_voice_options()
            self.discovery_status.setText(tr("Found {count} voices", count=len(values)) if values
                                          else tr("The service reported no voices"))
            return
        if self.provider.currentData() == "ollama-cloud":
            cloud.store_models(values)
        self._fill_models(values)
        current = self.profile_model.currentText()
        self.profile_model.clear()
        self.profile_model.addItems(values)
        self.profile_model.setCurrentText(current)
        self.discovery_status.setText(tr("Found {count} {label}", count=len(values), label=label) if values
                          else tr("The service reported no {label}", label=label))

    def _refresh_voice_language_options(self):
        current = self.voice_language.currentData()
        discovered = {voice_language(voice) for voice in self._available_voices}
        available_codes = set(LANGUAGE_LABELS) | discovered
        self.voice_language.blockSignals(True)
        self.voice_language.clear()
        self.voice_language.addItem(tr("All languages"), "")
        for code in sorted(available_codes, key=lambda value: (LANGUAGE_LABELS.get(value, value.upper()).casefold(), value)):
            label = LANGUAGE_LABELS.get(code, code.upper())
            self.voice_language.addItem(f"{label} ({code})", code)
        index = self.voice_language.findData(current)
        self.voice_language.setCurrentIndex(max(index, 0))
        self.voice_language.blockSignals(False)

    def _refresh_voice_options(self):
        current = self.profile_voice.currentText()
        if self.tts_provider.currentData() == "openai":
            voices = list(self._available_voices)
        else:
            voices = filter_voices(self._available_voices, self.voice_language.currentData() or "")
        self.profile_voice.blockSignals(True)
        self.profile_voice.clear()
        self.profile_voice.addItems(voices)
        if current in voices or not voices:
            self.profile_voice.setCurrentText(current)
        else:
            self.profile_voice.setCurrentIndex(0)
        self.profile_voice.blockSignals(False)
        self._update_response_language(current)

    def apply(self, config: AppConfig) -> AppConfig:
        self._store_special_profile()
        config.ui_language = self.ui_language.currentData() or "en"
        config.stt_url, config.tts_url, config.tts_voice = self.stt.text(), self.tts.text(), self._global_voice
        config.stt_provider = self.stt_provider.currentData() or "wyoming"
        config.tts_provider = self.tts_provider.currentData() or "wyoming"
        config.tts_language = self.tts_language.currentData() or ""
        config.stt_language = self.stt_language.currentData() or "auto"
        config.voice_language = self.voice_language.currentData() or ""
        selected_voice = self._voice_overrides.get(self._active_profile_name, self._global_voice)
        if config.tts_provider == "wyoming":
            config.response_language = response_language_for_voice(selected_voice, config.response_language)
        elif config.tts_language:
            config.response_language = config.tts_language
        self._store_global_prompt()
        config.special_prompts = dict(self.special_prompts)
        config.special_hotkeys = {name: key for name, key in self.special_hotkeys.items() if key and name in self.special_prompts}
        allowed_tool_profiles = {"__global__", *self.special_prompts}
        config.input_action_catalogs = {name: catalog for name, catalog in self.input_action_catalogs.items()
                          if name in allowed_tool_profiles and catalog.strip()}
        config.enabled_tools = {name: [tool for tool in ("web_search", "input_control") if tool in tools]
                for name, tools in self.enabled_tools.items()
                if name in allowed_tool_profiles and tools}
        config.llm_thinking_overrides = {name: value for name, value in self._thinking_overrides.items()
                        if name in allowed_tool_profiles and value}
        config.llm_model_overrides = {name: value for name, value in self._model_overrides.items()
                if name in self.special_prompts and value}
        config.tts_voice_overrides = {name: value for name, value in self._voice_overrides.items()
                if name in self.special_prompts and value}
        config.active_special_prompt = self.special_profile.currentData() if self.special_profile.currentData() != "__global__" else ""
        config.global_prompt = self._global_prompt
        config.auto_speak = self.auto_speak.isChecked()
        config.tray_mode = self.tray_mode.isChecked()
        config.llm_provider, config.llm_url, config.llm_model = self.provider.currentData() or "ollama", self.llm_url.text(), self.model.currentText()
        config.llm_thinking = self._thinking_setting
        config.ollama_api_key = self.api_key.text().strip()
        config.ptt_key, config.sample_rate = self.key.value(), self.sample_rate.value()
        config.overlay_hide_seconds = self.overlay_hide_seconds.value()
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
        set_language(self.config.ui_language)
        self.events = Events()
        self.events.ptt.connect(self._ptt_event)
        self.events.message.connect(self._show_message)
        self.events.request_message.connect(self._show_request_message)
        self.events.activity.connect(self._show_activity)
        self.events.speak.connect(self._start_speak)
        self.events.speech_finished.connect(self._speech_finished)
        self.setWindowTitle(tr("{name}", name=APP_NAME))
        self.setWindowIcon(make_app_icon())
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
        self.status.setToolTip(tr("Drag to move"))
        self.transcript = _OverlayTranscript()
        self.transcript.setReadOnly(True)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.document().setDocumentMargin(0)
        transcript_font = QFont()
        transcript_font.setFamilies([
            "AtkynsonMono Nerd Font", "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans CJK SC",
        ])
        transcript_font.setPointSize(11)
        self.transcript.setFont(transcript_font)
        self.transcript.setFixedSize(TRANSCRIPT_WIDTH, 0)
        self.transcript.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        text_column.addWidget(self.status)
        text_column.addWidget(self.transcript)
        text_container = _OverlayDragSurface()
        text_container.setCursor(Qt.CursorShape.SizeAllCursor)
        text_container.setToolTip(tr("Drag to move"))
        text_container.setLayout(text_column)
        self._text_container = text_container
        self._text_opacity = QGraphicsOpacityEffect(text_container)
        self._text_opacity.setOpacity(1)
        text_container.setGraphicsEffect(self._text_opacity)
        self.mic = QToolButton()
        self.mic.setFixedSize(44, 44)
        self.mic.pressed.connect(lambda: self._ptt_event("pressed", ""))
        self.mic.released.connect(lambda: self._ptt_event("released", ""))
        self.mic.setToolTip(tr("Hold to speak"))
        root.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignTop)
        self.say = QToolButton()
        self.say.setFixedSize(44, 44)
        self.say.setToolTip(tr("Read last response aloud"))
        self.say.clicked.connect(self._speak_last)
        root.addWidget(self.say, 0, Qt.AlignmentFlag.AlignTop)
        self.settings_button = QToolButton()
        self.settings_button.setFixedSize(44, 44)
        self.settings_button.setToolTip(tr("Settings"))
        self.settings_button.clicked.connect(self._settings)
        root.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(text_container, 1)
        container = _OverlayDragSurface()
        container.setToolTip(tr("Drag to move"))
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
        self._hide_timer.setInterval(max(1, self.config.overlay_hide_seconds) * 1000)
        self._hide_timer.timeout.connect(self._fade_out)
        self._type_timer = QTimer(self)
        self._type_timer.setInterval(TYPE_INTERVAL_MS)
        self._type_timer.timeout.connect(self._type_next_character)
        self._typing_text = ""
        self._typing_index = 0
        self._hide_after_typing = False
        self._buttons_visible = True
        self._speech_jobs = 0
        self._speech_cancel = None
        self._recording = False
        self._capture_stop = None
        self._ptt_pressed_at = None
        self._request_cancellations = set()
        self._request_cancellations_lock = threading.Lock()
        self._last_response = ""
        self._last_response_voice = ""
        self._last_text = ""
        self._gamepad_owned = False
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
        self.tray_toggle_action = QAction(tr("Start recording"), self)
        self.tray_toggle_action.triggered.connect(self._toggle_tray_recording)
        self.tray_menu.addAction(self.tray_toggle_action)
        self.tray_menu.addSeparator()
        self.tray_settings_action = QAction(tr("Settings …"), self)
        self.tray_settings_action.triggered.connect(self._settings)
        self.tray_menu.addAction(self.tray_settings_action)
        self.tray_play_action = QAction(tr("Read last response aloud again"), self)
        self.tray_play_action.triggered.connect(self._speak_last)
        self.tray_menu.addAction(self.tray_play_action)
        self.tray_menu.addSeparator()
        self.tray_exit_action = QAction(tr("Quit"), self)
        self.tray_exit_action.triggered.connect(self.close)
        self.tray_menu.addAction(self.tray_exit_action)
        self.tray_icon = QSystemTrayIcon(make_app_icon(), self)
        self.tray_icon.setToolTip(APP_NAME)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)

    def _apply_display_mode(self):
        buttons_visible = not self.config.tray_mode
        self._buttons_visible = buttons_visible
        for button in (self.mic, self.say, self.settings_button):
            button.setVisible(buttons_visible)
        if self.config.tray_mode:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.config.tray_mode = False
                self._buttons_visible = True
                for button in (self.mic, self.say, self.settings_button):
                    button.setVisible(True)
                self.tray_icon.hide()
                self._fit_to_content()
                self.show()
                self._set_status(tr("System tray unavailable · Overlay mode enabled"))
                return
            self._fit_to_content()
            self.tray_icon.show()
            self.hide()
            return
        self.tray_icon.hide()
        self._fit_to_content()
        self.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_tray_recording()

    def _toggle_tray_recording(self):
        if self._recording:
            self._tray_recording = False
            self.tray_toggle_action.setText(tr("Start recording"))
            self._ptt_event("released", "")
        else:
            self._tray_recording = True
            self.tray_toggle_action.setText(tr("Send recording"))
            self.tray_icon.setToolTip(tr("{name} · Recording. Click again to send.", name=APP_NAME))
            self._ptt_event("pressed", "")

    def _make_listener(self):
        extra_keys = {key: name for name, key in self.config.special_hotkeys.items()
                      if key and name in self.config.special_prompts}
        return PttListener(self.config.ptt_key, lambda kind, text: self.events.ptt.emit(kind, text), extra_keys)

    def _start_gamepad(self):
        try:
            from voxcomm.gamepad import start, status
        except ImportError as error:
            self._set_status(tr("Controller unavailable · {error}", error=str(error)[:120]))
            return

        try:
            status()
            return
        except (OSError, RuntimeError):
            pass
        try:
            self._gamepad_owned = start().startswith("VoxComm controller started")
        except (OSError, RuntimeError) as error:
            self._set_status(tr("Controller failed to start · {error}", error=str(error)[:120]))

    def _stop_gamepad(self):
        if not self._gamepad_owned:
            return
        self._gamepad_owned = False
        try:
            from voxcomm.gamepad import stop
        except ImportError:
            return

        try:
            stop()
        except (OSError, RuntimeError):
            pass

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
            profile = text if text in self.config.special_prompts else self.config.active_special_prompt
            if profile not in self.config.special_prompts:
                profile = ""
            self._stop_speaking()
            with self._request_cancellations_lock:
                for cancel_event in self._request_cancellations:
                    cancel_event.set()
            self._clear_transcript()
            self._ptt_pressed_at = time.monotonic()
            self._hide_timer.stop()
            self._hide_after_typing = False
            self._tray_recording = True
            self.tray_toggle_action.setText(tr("Send recording"))
            self._fade_animation.stop()
            self._text_opacity.setOpacity(1)
            profile_note = f" · {profile}" if profile else ""
            self._set_status(tr("Recording{profile} · click again to send", profile=profile_note) if self.config.tray_mode
                             else tr("Recording{profile} · release to send", profile=profile_note))
            self.show()
            self.raise_()
            if not self._recording:
                self._recording = True
                self._capture_stop = threading.Event()
                request_cancel = threading.Event()
                with self._request_cancellations_lock:
                    self._request_cancellations.add(request_cancel)
                threading.Thread(
                    target=self._capture,
                    args=(self._capture_stop, profile, request_cancel),
                    daemon=True,
                ).start()
        elif kind == "released":
            press_duration = time.monotonic() - self._ptt_pressed_at if self._ptt_pressed_at is not None else None
            self._ptt_pressed_at = None
            self._recording = False
            self._tray_recording = False
            self.tray_toggle_action.setText(tr("Start recording"))
            self.tray_icon.setToolTip(APP_NAME)
            is_short_press = press_duration is not None and press_duration < MIN_PTT_DURATION_MS / 1000
            if is_short_press:
                self._cancel_active_work()
            if self._capture_stop is not None:
                self._capture_stop.set()
                self._capture_stop = None
            if not is_short_press:
                self._set_status(tr("Processing speech …"))

    def _clear_transcript(self):
        self._type_timer.stop()
        self._typing_text = ""
        self._typing_index = 0
        self.transcript.clear()

    def _cancel_active_work(self):
        with self._request_cancellations_lock:
            for cancel_event in self._request_cancellations:
                cancel_event.set()
        self._stop_speaking()
        self._clear_transcript()
        self._hide_timer.stop()
        self._hide_after_typing = True
        self._set_status(tr("Ready · request cancelled"))
        self._schedule_hide()

    def _capture(self, stop_event: threading.Event, profile: str = "", cancel_event=None):
        cancel_event = cancel_event or threading.Event()
        with self._request_cancellations_lock:
            self._request_cancellations.add(cancel_event)
        try:
            audio = record_until_released(lambda: not stop_event.is_set(), self.config.sample_rate)
            if cancel_event.is_set():
                return
            if not audio:
                return
            minimum_bytes = self.config.sample_rate * 2 * MIN_PTT_DURATION_MS // 1000
            if len(audio) < minimum_bytes:
                self.events.request_message.emit(tr("Ready · recording too short"), "", cancel_event)
                return
            debug_wav = os.environ.get("SC_AI_COPILOT_DEBUG_WAV")
            if debug_wav:
                save_wav(audio, debug_wav, self.config.sample_rate)
            if self.config.stt_provider == "whisper.cpp":
                text = whisper_cpp.transcribe(audio, self.config.stt_url, self.config.sample_rate, self.config.stt_language)
            else:
                text = transcribe_sync(audio, self.config.stt_url, self.config.sample_rate, self.config.stt_language)
            if cancel_event.is_set():
                return
            if not text:
                self.events.request_message.emit(tr("Ready · no speech detected"), "", cancel_event)
                return
            self.events.request_message.emit(THINKING_STATUS, f"{USER_PREFIX}{text}\n", cancel_event)
            profile_name = profile if profile in self.config.special_prompts else ""
            profile_model = self.config.llm_model_overrides.get(profile_name, self.config.llm_model)
            profile_voice = self.config.tts_voice_overrides.get(profile_name, self.config.tts_voice)
            enabled_tools = self.config.enabled_tools.get(profile_name or "__global__", [])
            # Mit Websuche kann die Antwort nachträglich ersetzt werden, daher erst nach der fertigen Antwort sprechen.
            speech = None
            if self.config.auto_speak and "web_search" not in enabled_tools:
                speech = SentenceStream(
                    cancel_event, on_first=lambda: self.events.speak.emit(speech, cancel_event, profile_voice))
            streamed = []
            try:
                answer = chat(
                    self.config.llm_provider,
                    self.config.llm_url,
                    profile_model,
                    text,
                    system_prompt=build_system_prompt(
                        piper_voice=profile_voice if self.config.tts_provider == "wyoming" else "",
                        special_prompt_name=profile_name,
                        special_prompt=self.config.special_prompts.get(profile_name, ""),
                        fallback_language=(response_language_for_voice(profile_voice, self.config.response_language)
                                           if self.config.tts_provider == "wyoming" else self.config.response_language),
                        global_prompt=self.config.global_prompt,
                    ),
                    enabled_tools=enabled_tools,
                    input_actions=self.config.input_action_catalogs.get(profile_name or "__global__", ""),
                    api_key=self.config.ollama_api_key,
                    cancel_event=cancel_event,
                    on_text=(lambda delta: (streamed.append(delta), speech.feed(delta))) if speech else None,
                    thinking=self.config.llm_thinking_overrides.get(
                        profile_name or "__global__", self.config.llm_thinking),
                    on_status=lambda status: self.events.activity.emit(status, cancel_event),
                )
                if speech is not None and not streamed and not cancel_event.is_set():
                    speech.feed(answer)
            finally:
                if speech is not None:
                    speech.finish()
            if cancel_event.is_set():
                return
            self._last_response_voice = profile_voice
            self.events.request_message.emit(
                tr("Ready · response generated locally"), f"{USER_PREFIX}{text}{AI_MARKER}{answer}", cancel_event
            )
            if self.config.auto_speak and speech is None and not cancel_event.is_set():
                self.events.speak.emit(answer, cancel_event, profile_voice)
        except Exception as error:
            if not cancel_event.is_set():
                self.events.request_message.emit(tr("Error · {error}", error=error), "", cancel_event)
        finally:
            with self._request_cancellations_lock:
                self._request_cancellations.discard(cancel_event)

    def _show_request_message(self, status: str, text: str, cancel_event: threading.Event):
        if not cancel_event.is_set():
            self._show_message(status, text)

    def _show_activity(self, status: str, cancel_event: threading.Event):
        if not cancel_event.is_set():
            self._set_status(status)

    def _show_message(self, status: str, text: str):
        self._set_status(status)
        if AI_MARKER in text:
            user_text, _marker, answer = text.partition(AI_MARKER)
            publish_status(f"{user_text}\n{AI_LABEL}{answer}")
        elif text.startswith(USER_PREFIX):
            publish_status(text)
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
        message_id = text
        text = tr(message_id)
        self.status.setText(text)
        showing_last_answer = AI_MARKER in self._last_text and self._typing_text == self._last_text
        if message_id in (SPEAKING_STATUS, "Ready · press PTT to speak") and showing_last_answer:
            user_text, _marker, answer = self._last_text.partition(AI_MARKER)
            publish_status(f"{user_text}\n{AI_LABEL}{answer}")
        else:
            publish_status(text)
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
        transcript_width = TRANSCRIPT_WIDTH if self._buttons_visible else FULL_WIDTH - OVERLAY_HORIZONTAL_MARGINS
        self.transcript.setFixedWidth(transcript_width)
        scroll_bar = self.transcript.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        self.setFixedWidth(FULL_WIDTH if has_text else COMPACT_WIDTH)
        # Das Layout cached seine Größe bis zum nächsten Event, daher direkt rechnen.
        text_height = self.status.sizeHint().height() + 2 + transcript_height if has_text else 0
        self.setFixedHeight(max(
            58,
            16 + max(44, text_height),
        ))

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
        if (
            self.isVisible()
            and self._hide_after_typing
            and not self._recording
            and self._speech_jobs == 0
            and not self._type_timer.isActive()
            and (self.status.text() or self.transcript.toPlainText())
        ):
            self._fade_animation.start()

    def _clear_faded_text(self):
        self.status.clear()
        self.transcript.clear()
        publish_status("")
        self._typing_text = ""
        self._text_opacity.setOpacity(1)
        self._fit_to_content()
        if self.config.tray_mode:
            self.hide()

    def _speak_last(self):
        if not self._last_response:
            self._show_message(tr("No response to read yet"), "")
            return
        self._show_message(SPEAKING_STATUS, self._last_text)
        self._start_speak(self._last_response, voice_override=self._last_response_voice)

    def _start_speak(self, source, cancel_event=None, voice_override=""):
        if cancel_event is not None and cancel_event.is_set():
            return
        self._stop_speaking()
        cancel_event = cancel_event or threading.Event()
        self._speech_cancel = cancel_event
        self._set_status(SPEAKING_STATUS)
        self._speech_jobs += 1
        self._hide_timer.stop()
        self._fade_animation.stop()
        self._text_opacity.setOpacity(1)
        threading.Thread(target=self._speak, args=(source, cancel_event, voice_override), daemon=True).start()

    def _stop_speaking(self):
        if self._speech_cancel is not None:
            self._speech_cancel.set()

    def _speech_chunks(self, sentences, voice_override=""):
        voice = voice_override or self.config.tts_voice
        for sentence in sentences:
            if self.config.tts_provider == "openai":
                yield from openai_tts.speak_stream(
                    sentence, self.config.tts_url, voice, self.config.tts_language)
            else:
                yield from speak_stream_sync(sentence, self.config.tts_url, voice)

    def _speak(self, source, cancel_event: threading.Event, voice_override=""):
        try:
            sentences = [source] if isinstance(source, str) else source
            play_stream(prefetch(self._speech_chunks(sentences, voice_override), cancel_event), cancel_event)
            if not cancel_event.is_set():
                self.events.request_message.emit(tr("Ready · press PTT to speak"), "", cancel_event)
        except Exception as error:
            if not cancel_event.is_set():
                self.events.request_message.emit(tr("Speech output error · {error}", error=error), "", cancel_event)
        finally:
            self.events.speech_finished.emit()

    def _settings(self):
        dialog = SettingsDialog(self.config)
        if dialog.exec():
            previous_language = self.config.ui_language
            self.listener.stop()
            self.config = dialog.apply(self.config)
            self._hide_timer.setInterval(self.config.overlay_hide_seconds * 1000)
            if self.config.tray_mode and not QSystemTrayIcon.isSystemTrayAvailable():
                QMessageBox.warning(dialog, tr("System tray unavailable"),
                                    tr("No system tray is available in this session. Overlay mode will remain active."))
                self.config.tray_mode = False
            save_config(self.config)
            if self.config.ui_language != previous_language:
                QMessageBox.information(dialog, tr("Restart required"),
                                        tr("Restart VoxComm to apply the interface language change."))
            self.listener = self._make_listener()
            self.listener.start()
            self._apply_display_mode()

    def closeEvent(self, event):
        if self._position_save_timer.isActive():
            self._position_save_timer.stop()
            self._save_overlay_position()
        self.listener.stop()
        self._stop_gamepad()
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
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    # Unter Wayland ordnet der Desktop Fenster über diese ID dem Starter samt Icon zu.
    QGuiApplication.setDesktopFileName(APP_ID)
    app.setWindowIcon(make_app_icon())


def prefer_x11_on_wayland():
    # Wayland erlaubt Clients weder Fensterpositionen noch Always-on-top; XWayland schon.
    if os.environ.get("DISPLAY") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def main():
    parser = argparse.ArgumentParser(prog=APP_ID, description=APP_NAME)
    parser.add_argument("--install-launcher", action="store_true", help="Install the launcher and icon in the application menu")
    arguments = parser.parse_args()
    if arguments.install_launcher:
        desktop_path, icon_path = install_launcher()
        print(f"Launcher: {desktop_path}\nIcon:     {icon_path}")
        raise SystemExit(0)
    prefer_x11_on_wayland()
    app = QApplication([])
    apply_app_identity(app)
    restore_sigint_handler = configure_application(app)
    overlay = Overlay()
    overlay._start_gamepad()
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
