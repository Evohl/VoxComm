from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QRect, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QAction, QFont, QGuiApplication, QIcon, QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGraphicsOpacityEffect, QGridLayout,
    QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSystemTrayIcon, QTabWidget, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from voxcomm.action_catalog import format_replies, merge_catalog_reply_overrides, parse_replies, reply_language_tag
from voxcomm.audio import play_stream, prefetch, record_until_released, save_wav
from voxcomm.branding import APP_ID, APP_NAME, install_launcher, make_app_icon
from voxcomm.config import (
    AppConfig, load_config, reply_override_key, reply_overrides_for_profile, save_config, save_overlay_position,
    split_reply_override_key,
)
from voxcomm.i18n import SUPPORTED_LANGUAGES, set_language, tr
from voxcomm.mangohud import publish_status
from voxcomm.providers import cloud, openai_tts, whisper_cpp
from voxcomm.providers.llm import chat, list_models, thinking_options
from voxcomm.providers.wyoming import filter_voices, list_voices, speak_stream_sync, transcribe_sync
from voxcomm.prompts import VOICE_LANGUAGE_NAMES, build_system_prompt
from voxcomm.ptt import PttListener, capture_input
from voxcomm.profiles import output_language, resolve_profile
from voxcomm.skills.star_citizen.events import (
    GAME_ANNOUNCE_FIELDS, GAME_EVENT_TEXT, game_event_reply, game_reply_action, game_reply_defaults,
)
from voxcomm.skills.star_citizen.gamelog import GameFacts, GameLogTracker
from voxcomm.skills.star_citizen.gamelog.reader import RecentLogReader
from voxcomm.skills.star_citizen.tool import (
    STAR_CITIZEN_LEGACY, STAR_CITIZEN_LOG, STAR_CITIZEN_UEX, allows_game_log, allows_uex,
)
from voxcomm.streaming import SentenceStream
from voxcomm.theme import make_icon, mix_colors, rgba, system_accent, system_surface, system_text


LANGUAGE_LABELS = VOICE_LANGUAGE_NAMES

TYPE_INTERVAL_MS = 24
GAME_LOG_POLL_MS = 2000
GAME_LOG_VIEW_MS = 1000
GAME_LOG_HISTORY = 50
GAME_LOG_RAW_LINES = 10000
MIN_PTT_DURATION_MS = 200
TRANSCRIPT_WIDTH = 480
TRANSCRIPT_MAX_HEIGHT = 200
THINKING_STATUS = "Thinking …"
SPEAKING_STATUS = "Speaking …"
WHISPER_CPP_DEFAULT_URL = "http://127.0.0.1:8081"
OPENAI_TTS_DEFAULT_URL = "http://127.0.0.1:8097"
SETTINGS_LABEL_WIDTH = 140
MANUAL_CONTROLLER_BUTTON_COUNT = 100
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
    speak = Signal(object, object, object, object)
    speech_finished = Signal()
    controller_pressed = Signal(str, str)
    raw_game_log = Signal(int, bool, object)


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


def _game_status_lines(facts: GameFacts) -> list[str]:
    unknown = tr("Unknown")

    def flag(value: bool | None) -> str:
        if value is None:
            return unknown
        return tr("Yes") if value else tr("No")

    return [
        f"{tr('Game version')}: {facts.game_version or unknown}",
        f"{tr('Game build')}: {facts.game_build or unknown}",
        f"{tr('Approximate region (Streaming SOC)')}: {facts.streaming_region or unknown}",
        f"{tr('Last known stay (inventory location)')}: {facts.location or unknown}",
        f"{tr('Ship')}: {facts.ship or unknown}",
        f"{tr('In ship')}: {flag(facts.in_ship)}",
        f"{tr('Pilot seat')}: {flag(facts.pilot_seat)}",
    ]


class SettingsDialog(QDialog):
    save_requested = Signal()

    def __init__(self, config: AppConfig, parent=None, reset_position_callback=None):
        super().__init__(parent)
        set_language(config.ui_language)
        self.ui_language = QComboBox()
        for code, label in SUPPORTED_LANGUAGES.items():
            self.ui_language.addItem(label, code)
        self.ui_language.setCurrentIndex(max(self.ui_language.findData(config.ui_language), 0))
        self.setWindowTitle(tr("Settings · {name}", name=APP_NAME))
        self.setMinimumSize(1040, 720)
        self.discovery = Events()
        self.discovery.discovery.connect(self._discovery_result)
        self.discovery.thinking.connect(self._apply_thinking_options)
        self.discovery.controller_pressed.connect(self._manual_controller_result)
        self.discovery.raw_game_log.connect(self._raw_game_log_result)
        self.stt = QLineEdit(config.stt_url)
        self.stt_api_key = QLineEdit(config.stt_api_key)
        self.stt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
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
        self.tts_api_key = QLineEdit(config.tts_api_key)
        self.tts_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tts_provider = QComboBox()
        self.tts_provider.addItem(tr("Wyoming Piper"), "wyoming")
        self.tts_provider.addItem(tr("OpenAI-compatible (qwentts and others)"), "openai")
        self.tts_provider.setCurrentIndex(max(self.tts_provider.findData(config.tts_provider), 0))
        self._available_voices: list[str] = []
        self.special_prompts = dict(config.special_prompts)
        self.special_hotkeys = {name: key for name, key in config.special_hotkeys.items() if name in self.special_prompts}
        self.input_action_catalogs = dict(config.input_action_catalogs)
        self.input_reply_overrides = dict(config.input_reply_overrides)
        self.enabled_tools = {name: list(tools) for name, tools in config.enabled_tools.items()}
        self._default_hotkey = config.ptt_key
        self.profile_hotkey = HotkeyField("", clearable=True, validator=self._profile_hotkey_conflict)
        self.profile_hotkey.setToolTip(tr("This hotkey activates the selected profile"))
        self.profile_hotkey.changed.connect(self._store_profile_hotkey)
        self._global_prompt = config.global_prompt
        self.special_profile = QComboBox()
        self.special_profile.addItem(tr("Default"), "__global__")
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
        self.star_citizen = QCheckBox(tr("Star Citizen Log"))
        self.star_citizen.setToolTip(tr("Adds Game.log context and ship status announcements for this profile."))
        self.star_citizen.setChecked(allows_game_log(self.enabled_tools.get(active_profile, [])))
        self.star_citizen_uex = QCheckBox(tr("Star Citizen UEX"))
        self.star_citizen_uex.setToolTip(tr("Allows this profile to query UEX community data for commodities, terminals, prices and trade routes."))
        self.star_citizen_uex.setChecked(allows_uex(self.enabled_tools.get(active_profile, [])))
        self.controller_mode = QComboBox()
        self.controller_mode.addItem(tr("Hybrid: script, LLM on keyword"), "hybrid")
        self.controller_mode.addItem(tr("Script only"), "script")
        self.controller_mode.addItem(tr("LLM only"), "llm")
        self.controller_mode.setCurrentIndex(max(self.controller_mode.findData(config.controller_mode), 0))
        self.controller_mode.setToolTip(tr("Applies to all profiles. Script only never asks the model about controller actions."))
        self.controller_mode.currentIndexChanged.connect(self._update_controller_fields)
        self.controller_llm_keyword = QLineEdit(config.controller_llm_keyword)
        self.controller_llm_keyword.setPlaceholderText(tr("Keyword that sends the request to the LLM"))
        self.input_action_catalog = QPlainTextEdit(self._migrate_controller_replies(
            self.input_action_catalogs.get(active_profile, ""), active_profile))
        self.input_action_catalog.setPlaceholderText(
            tr("Action name => keywords + {set} => ButtonN [hold] => {EN} reply | {DE} reply | {ES} reply"))
        self.input_action_catalog.setMinimumHeight(280)
        self.catalog_warning = QLabel(tr("Some existing custom replies could not be merged into this catalog."))
        self.catalog_warning.setObjectName("hint")
        self.catalog_warning.setWordWrap(True)
        self._update_catalog_warning()
        self.manual_controller_toggle = QToolButton()
        self.manual_controller_toggle.setText(tr("Manual buttons"))
        self.manual_controller_toggle.setToolTip(tr("Manual presses send raw buttons without catalog preconditions."))
        self.manual_controller_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.manual_controller_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.manual_controller_toggle.setCheckable(True)
        self.manual_controller_panel = QWidget()
        manual_layout = QVBoxLayout(self.manual_controller_panel)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_controls = QHBoxLayout()
        manual_controls.setSpacing(8)
        self.manual_hold = QCheckBox(tr("Hold for 1 second"))
        self.manual_delay = QSpinBox()
        self.manual_delay.setRange(0, 10)
        self.manual_delay.setSuffix(tr(" s"))
        self.manual_delay.setFixedWidth(88)
        self.manual_delay.setToolTip(tr("Delay before pressing, to switch to the game"))
        self.manual_cancel = QPushButton(tr("Cancel pending press"))
        self.manual_cancel.setAutoDefault(False)
        self.manual_cancel.setVisible(False)
        manual_controls.addWidget(self.manual_hold)
        manual_controls.addSpacing(20)
        manual_controls.addWidget(QLabel(tr("Delay")))
        manual_controls.addWidget(self.manual_delay)
        manual_controls.addStretch(1)
        manual_controls.addWidget(self.manual_cancel)
        manual_layout.addLayout(manual_controls)
        self.manual_grid = QWidget()
        grid = QGridLayout(self.manual_grid)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.manual_buttons = []
        for number in range(1, MANUAL_CONTROLLER_BUTTON_COUNT + 1):
            button = QToolButton()
            button.setText(f"Button{number}")
            button.setToolTip(tr("Press {button}", button=f"Button{number}"))
            button.clicked.connect(lambda _checked=False, number=number: self._queue_manual_button(number))
            grid.addWidget(button, (number - 1) // 10, (number - 1) % 10)
            self.manual_buttons.append(button)
        manual_layout.addWidget(self.manual_grid)
        self.manual_status = QLabel()
        self.manual_status.setWordWrap(True)
        manual_layout.addWidget(self.manual_status)
        self.manual_controller_panel.setVisible(False)
        self.manual_controller_toggle.toggled.connect(self._toggle_manual_controller)
        self.manual_cancel.clicked.connect(self._cancel_manual_button)
        self._manual_timer = QTimer(self)
        self._manual_timer.setSingleShot(True)
        self._manual_timer.timeout.connect(self._send_manual_button)
        self._manual_pending = None
        self._manual_busy = False
        self.input_control.toggled.connect(lambda enabled: None if enabled else self._cancel_manual_button())
        self.action_catalog_section = self._section(None, [
            (tr("Mode (all profiles)"), self.controller_mode),
            (tr("LLM keyword (all profiles)"), self.controller_llm_keyword),
            (None, self.manual_controller_toggle), (None, self.manual_controller_panel),
            (tr("Action catalog"), self.input_action_catalog), (None, self.catalog_warning),
        ])
        self.action_catalog_section.setVisible(self.input_control.isChecked())
        self.input_control.toggled.connect(self.action_catalog_section.setVisible)
        self._update_controller_fields()
        self._game_log = GameLogTracker()
        self._game_log_events: deque[str] = deque(maxlen=GAME_LOG_HISTORY)
        self._game_log_text = ""
        self._raw_log_reader = None
        self._raw_log_generation = 0
        self._raw_log_busy = False
        self.game_log_view = QPlainTextEdit()
        self.game_log_view.setReadOnly(True)
        self.game_log_view.setMinimumHeight(140)
        self.game_log_view.setMaximumBlockCount(GAME_LOG_RAW_LINES)
        self.game_log_unfiltered = QCheckBox(tr("Unfiltered Game.log (up to 10,000 lines)"))
        self.game_log_unfiltered.toggled.connect(self._game_log_mode_changed)
        self.game_status_view = QLabel("\n".join(_game_status_lines(GameFacts())))
        self.game_status_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.game_status_view.setWordWrap(True)
        self._announce_checks = {}
        self._game_reply_fields = {}
        self._game_reply_drafts = {}
        announcement_rows = []
        announcement_scope = tr("The checkboxes apply to all profiles. Reply texts are saved per profile. Star Citizen Log must be enabled for the active profile.")
        for event, field_name in GAME_ANNOUNCE_FIELDS.items():
            check = QCheckBox(tr(GAME_EVENT_TEXT[event]))
            check.setToolTip(announcement_scope)
            check.setChecked(getattr(config, field_name))
            self._announce_checks[event] = check
            check.setMinimumWidth(250)
            field = QLineEdit()
            field.setAccessibleName(tr("Reply: {event}", event=tr(GAME_EVENT_TEXT[event])))
            field.textEdited.connect(lambda text, event=event: self._store_game_reply_text(event, text))
            self._game_reply_fields[event] = field
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(check)
            row.addWidget(field, 1)
            announcement_rows.append((None, row))
        self._load_game_reply_text()
        self.audio_confirmation_heading = QLabel(tr("Audio confirmation (all profiles)"))
        self.audio_confirmation_heading.setToolTip(announcement_scope)
        self.game_log_section = self._section(
            None,
            [(None, self.audio_confirmation_heading)]
            + announcement_rows + [(tr("Game status"), self.game_status_view),
                                   (None, self.game_log_unfiltered),
                                   (tr("Star Citizen Log") + ":", self.game_log_view)])
        self.game_log_section.setVisible(self.star_citizen.isChecked())
        self.star_citizen.toggled.connect(self.game_log_section.setVisible)
        self.star_citizen.toggled.connect(self._game_log_permission_changed)
        self._game_log_timer = QTimer(self)
        self._game_log_timer.setInterval(GAME_LOG_VIEW_MS)
        self._game_log_timer.timeout.connect(self._refresh_game_log)
        self._game_log_timer.start()
        initial_prompt = config.global_prompt if active_profile == "__global__" else self.special_prompts.get(active_profile, "")
        self.special_prompt = QPlainTextEdit(initial_prompt)
        self.special_prompt.setPlaceholderText(tr("Default profile instructions") if active_profile == "__global__"
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
        self.api_key.setPlaceholderText(tr("Optional API key"))
        self.key_test = QPushButton(tr("Verify key"))
        self.key_row = QWidget()
        key_layout = QHBoxLayout(self.key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        key_layout.addWidget(self.api_key, 1)
        key_layout.addWidget(self.key_test)
        self._profile_models = dict(config.profile_models)
        self._profile_voices = dict(config.profile_voices)
        self._profile_thinkings = dict(config.profile_thinking)
        self._profile_response_languages = dict(config.profile_response_languages)
        profiles = {"__global__", *self.special_prompts}
        self._profile_stt_languages = {name: config.profile_stt_languages.get(name, config.stt_language) for name in profiles}
        self._profile_tts_languages = {name: config.profile_tts_languages.get(name, config.tts_language) for name in profiles}
        self._profile_voice_languages = {name: config.profile_voice_languages.get(name, config.voice_language) for name in profiles}
        self._profile_auto_speak = {name: config.profile_auto_speak.get(name, config.auto_speak) for name in profiles}
        self._profile_output_languages = {name: output_language(config, name, self._profile_voices.get(name, ""))
                          for name in profiles}
        self.auto_speak = QCheckBox(tr("Automatically read responses aloud after generation"))
        self.profile_model = QComboBox()
        self.profile_model.setEditable(True)
        self.profile_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.profile_model.setCurrentText(self._profile_models.get(active_profile, ""))
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
        self.profile_voice.setCurrentText(self._profile_voices.get(active_profile, ""))
        self.profile_voice_refresh = self._refresh_button(tr("Load voices from service"))
        self.profile_voice_refresh.clicked.connect(lambda: self._discover("voices", self.profile_voice_refresh))
        profile_voice_row = QHBoxLayout()
        profile_voice_row.setContentsMargins(0, 0, 0, 0)
        profile_voice_row.setSpacing(8)
        profile_voice_row.addWidget(self.profile_voice, 1)
        profile_voice_row.addWidget(self.profile_voice_refresh)
        self.profile_response_language = QComboBox()
        for label in VOICE_LANGUAGE_NAMES.values():
            self.profile_response_language.addItem(label, label)
        self.profile_response_language.currentIndexChanged.connect(self._output_language_changed)
        self._thinking_options: list[str] = []
        self._thinking_user_changed = False
        self.profile_thinking = QComboBox()
        self._thinking_token = 0
        self._thinking_shown = False
        self.profile_thinking.setToolTip(tr(
            "The model reports which levels it supports. For reasoning models, setting this to Off can "
            "significantly reduce response time."))
        self._fill_thinking([])
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setSingleShot(True)
        self._thinking_timer.setInterval(400)
        self._thinking_timer.timeout.connect(self._lookup_thinking)
        self.profile_model.lineEdit().editingFinished.connect(self._thinking_timer.start)
        self.profile_model.activated.connect(lambda _: self._thinking_timer.start())
        self.profile_thinking.activated.connect(self._profile_thinking_activated)
        self._load_profile_settings()
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(8000)
        self.sample_rate.setValue(config.sample_rate)
        self.overlay_hide_seconds = QSpinBox()
        self.overlay_hide_seconds.setRange(1, 120)
        self.overlay_hide_seconds.setSuffix(" s")
        self.overlay_hide_seconds.setValue(config.overlay_hide_seconds)
        self.tray_mode = QCheckBox(tr("Tray mode"))
        self.tray_mode.setToolTip(tr("Tray mode: click to start/stop recording; hide overlay buttons"))
        self.tray_mode.setChecked(config.tray_mode)
        self.reset_position = QPushButton(tr("Reset overlay position"))
        reset_position_callback = reset_position_callback or getattr(parent, "reset_overlay_position", None)
        if reset_position_callback is not None:
            self.reset_position.clicked.connect(reset_position_callback)
        else:
            self.reset_position.setEnabled(False)
        global_tab = QWidget()
        global_layout = QVBoxLayout(global_tab)
        global_layout.setContentsMargins(0, 0, 0, 0)
        global_layout.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._section(tr("Application"), [
            (tr("Interface language"), self.ui_language),
            (tr("Microphone sample rate"), self.sample_rate),
            (tr("Overlay display duration"), self.overlay_hide_seconds),
            (None, self.tray_mode), (None, self.reset_position),
        ]))
        self.llm_section = self._section(tr("Language model"), [
            (tr("Provider"), self.provider), (tr("URL"), self.llm_url), (tr("API key (optional)"), self.key_row),
        ])
        left.addWidget(self.llm_section)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(14)
        self.stt_section = self._section(tr("Speech recognition"), [
            (tr("Provider"), self.stt_provider), (tr("URL"), self.stt),
            (tr("API key (optional)"), self.stt_api_key),
        ])
        right.addWidget(self.stt_section)
        self.tts_section = self._section(tr("Speech output"), [
            (tr("Provider"), self.tts_provider), (tr("URL"), self.tts),
            (tr("API key (optional)"), self.tts_api_key),
        ])
        right.addWidget(self.tts_section)
        right.addStretch(1)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        global_layout.addLayout(columns, 1)
        profile_tab = QWidget()
        profile_layout = QVBoxLayout(profile_tab)
        profile_layout.setContentsMargins(12, 12, 12, 0)
        profile_layout.setSpacing(12)
        profile_header = QHBoxLayout()
        profile_header.setContentsMargins(0, 0, 0, 0)
        profile_header.setSpacing(8)
        profile_header.addWidget(QLabel(tr("Profile")))
        profile_header.addWidget(self.special_profile, 1)
        profile_header.addLayout(profile_row)
        profile_layout.addLayout(profile_header)

        profile_details = QWidget()
        details_layout = QVBoxLayout(profile_details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.profile_settings_section = self._section(None, [
            (tr("Profile hotkey"), self.profile_hotkey),
            (tr("Model"), profile_model_row),
            (tr("Input language"), self.stt_language),
            (tr("Output language"), self.profile_response_language),
            (tr("Voice"), profile_voice_row),
            (tr("Reasoning mode"), self.profile_thinking),
            (None, self.auto_speak),
            (tr("Instructions"), self.special_prompt),
        ])
        details_layout.addWidget(self.profile_settings_section, 1)

        tools_page = QWidget()
        tools_layout = QVBoxLayout(tools_page)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.addWidget(self._section(None, [
            (None, self.web_search), (None, self.input_control), (None, self.star_citizen),
            (None, self.star_citizen_uex),
        ]))
        tools_layout.addStretch(1)

        controller_page = QWidget()
        controller_layout = QVBoxLayout(controller_page)
        controller_layout.setContentsMargins(0, 0, 0, 0)
        controller_layout.addWidget(self.action_catalog_section, 1)
        game_page = QWidget()
        game_layout = QVBoxLayout(game_page)
        game_layout.setContentsMargins(0, 0, 0, 0)
        game_layout.addWidget(self.game_log_section, 1)

        self.profile_tabs = QTabWidget()
        self.profile_tabs.addTab(self._scroll_page(profile_details), tr("Profile settings"))
        self.profile_tabs.addTab(self._scroll_page(tools_page), tr("Skills and tools"))
        controller_index = self.profile_tabs.addTab(self._scroll_page(controller_page), tr("VoxComm Controller"))
        game_index = self.profile_tabs.addTab(self._scroll_page(game_page), tr("Star Citizen log"))
        self.profile_tabs.setTabVisible(controller_index, self.input_control.isChecked())
        self.profile_tabs.setTabVisible(game_index, self.star_citizen.isChecked())
        self.input_control.toggled.connect(lambda enabled: self.profile_tabs.setTabVisible(controller_index, enabled))
        self.star_citizen.toggled.connect(lambda enabled: self.profile_tabs.setTabVisible(game_index, enabled))
        profile_layout.addWidget(self.profile_tabs, 1)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.addTab(self._scroll_page(global_tab), tr("General settings"))
        profile_index = self.settings_tabs.addTab(profile_tab, tr("Profiles"))
        self.settings_tabs.currentChanged.connect(
            lambda index: self.settings_tabs.setDocumentMode(index == profile_index)
        )
        self.discovery_status = QLabel(tr("Load voices and models from the service using the refresh button"))
        self.discovery_status.setObjectName("hint")
        self.discovery_status.setWordWrap(True)
        self.profile_hotkey.error.connect(self.discovery_status.setText)
        self.provider.currentIndexChanged.connect(self._update_llm_fields)
        for restart in (self.provider.currentIndexChanged, self.llm_url.editingFinished,
                self.api_key.editingFinished):
            restart.connect(lambda *_: self._thinking_timer.start())
        self.tts_provider.currentIndexChanged.connect(self._update_tts_fields)
        self.stt_provider.currentIndexChanged.connect(self._update_stt_fields)
        self.key_test.clicked.connect(self._test_api_key)
        self._update_stt_fields()
        self._update_tts_fields()
        self._update_llm_fields()
        save = QPushButton(tr("Save"))
        save.setObjectName("primary")
        save.setDefault(True)
        save.setMinimumWidth(130)
        save.clicked.connect(self._request_save)
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 12, 12, 10)
        footer.addWidget(self.discovery_status, 1)
        footer.addWidget(save)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.settings_tabs, 1)
        root.addLayout(footer)

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        scroll.viewport().setAutoFillBackground(False)
        page.setAutoFillBackground(False)
        return scroll

    def _refresh_button(self, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("iconButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        icon = QIcon.fromTheme("view-refresh")
        button.setIcon(icon if not icon.isNull() else make_icon("refresh", button.palette().buttonText().color()))
        button.setToolTip(tooltip)
        return button

    def _section(self, title: str | None, rows: list) -> QFrame:
        frame = QFrame()
        frame.setObjectName("section")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        if title:
            heading = QLabel(title.upper())
            heading.setObjectName("sectionTitle")
            self._set_heading_accent(heading)
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
                caption.setWordWrap(True)
                form.addRow(caption, field)
        layout.addLayout(form)
        frame.form = form
        return frame

    @staticmethod
    def _set_heading_accent(heading: QLabel) -> None:
        palette = heading.palette()
        accent = system_accent()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, QPalette.ColorRole.WindowText, accent)
        heading.setPalette(palette)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            for heading in self.findChildren(QLabel, "sectionTitle"):
                self._set_heading_accent(heading)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._thinking_shown:
            self._thinking_shown = True
            self._lookup_thinking()

    def _fill_thinking(self, options: list[str], known: bool = False):
        self._thinking_options = list(options)
        selected = self._profile_thinkings.get(self._active_profile_name, "")
        self.profile_thinking.blockSignals(True)
        self.profile_thinking.clear()
        self.profile_thinking.addItem(tr("Model default"), "")
        for option in options:
            self.profile_thinking.addItem(tr(THINKING_LABELS.get(option, option.capitalize())), option)
        if selected and selected not in options and not known:
            self.profile_thinking.addItem(tr(THINKING_LABELS.get(selected, selected.capitalize())), selected)
        if known and not options:
            self.profile_thinking.clear()
            self.profile_thinking.addItem(tr("Not controlled by model"), "")
        self.profile_thinking.setEnabled(not known or bool(options))
        self.profile_thinking.setCurrentIndex(max(self.profile_thinking.findData(selected), 0))
        self.profile_thinking.blockSignals(False)

    def _profile_thinking_activated(self, _index):
        self._thinking_user_changed = True
        self._store_profile_settings()

    def _lookup_thinking(self):
        self._thinking_token += 1
        token = self._thinking_token
        provider, url = self.provider.currentData() or "ollama", self.llm_url.text().strip()
        model = self.profile_model.currentText().strip()
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

    def _output_language_changed(self, *_):
        self._refresh_voice_options()

    def _update_llm_fields(self):
        cloud_mode = self.provider.currentData() == "ollama-cloud"
        self.key_test.setVisible(cloud_mode)
        current = self.llm_url.text().strip()
        if cloud_mode and current in ("", "http://127.0.0.1:11434"):
            self.llm_url.setText(cloud.CLOUD_BASE_URL)
        elif not cloud_mode and current == cloud.CLOUD_BASE_URL:
            self.llm_url.setText("http://127.0.0.1:11434")
        if cloud_mode:
            self._load_cloud_models()

    def _update_tts_fields(self):
        openai_mode = self.tts_provider.currentData() == "openai"
        self.tts_api_key.setEnabled(openai_mode)
        self.tts_api_key.setToolTip(tr("Wyoming TCP does not support API keys") if not openai_mode else "")
        current = self.tts.text().strip()
        if openai_mode and current in ("", "tcp://127.0.0.1:10200"):
            self.tts.setText(OPENAI_TTS_DEFAULT_URL)
        elif not openai_mode and current == OPENAI_TTS_DEFAULT_URL:
            self.tts.setText("tcp://127.0.0.1:10200")
        self._refresh_voice_options()

    def _update_stt_fields(self):
        whisper_mode = self.stt_provider.currentData() == "whisper.cpp"
        self.stt_api_key.setEnabled(whisper_mode)
        self.stt_api_key.setToolTip(tr("Wyoming TCP does not support API keys") if not whisper_mode else "")
        current = self.stt.text().strip()
        if whisper_mode and current in ("", "tcp://127.0.0.1:10300"):
            self.stt.setText(WHISPER_CPP_DEFAULT_URL)
        elif not whisper_mode and current == WHISPER_CPP_DEFAULT_URL:
            self.stt.setText("tcp://127.0.0.1:10300")

    def _fill_models(self, values: list[str]):
        current = self.profile_model.currentText()
        self.profile_model.clear()
        self.profile_model.addItems(values)
        self.profile_model.setCurrentText(current)

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

    def _profile_hotkey_conflict(self, key: str) -> str | None:
        if self._active_profile_name != "__global__" and key.casefold() == self._default_hotkey.casefold():
            return tr("Hotkey {key} is already assigned to profile '{name}'", key=key.upper(), name=tr("Default"))
        for name, other in self.special_hotkeys.items():
            if other.casefold() == key.casefold() and name != self._active_profile_name:
                return tr("Hotkey {key} is already assigned to profile '{name}'", key=key.upper(), name=name)
        return None

    def _store_profile_hotkey(self, key: str):
        if self._active_profile_name == "__global__":
            self._default_hotkey = key
        elif self._active_profile_name in self.special_prompts:
            if key:
                self.special_hotkeys[self._active_profile_name] = key
            else:
                self.special_hotkeys.pop(self._active_profile_name, None)

    def _store_profile_settings(self):
        profile = self._active_profile_name
        thinking = self.profile_thinking.currentData() or ""
        stored_thinking = self._profile_thinkings.get(profile, "")
        if (not thinking and stored_thinking and not self._thinking_user_changed
                and stored_thinking not in self._thinking_options):
            thinking = stored_thinking
        values = (
            (self._profile_models, self.profile_model.currentText().strip()),
            (self._profile_voices, self.profile_voice.currentText().strip()),
            (self._profile_thinkings, thinking),
        )
        for settings, value in values:
            if value:
                settings[profile] = value
            else:
                settings.pop(profile, None)
        self._profile_stt_languages[profile] = self.stt_language.currentData() or "auto"
        self._profile_auto_speak[profile] = self.auto_speak.isChecked()
        self._profile_output_languages[profile] = self.profile_response_language.currentData() or "English"

    def _load_profile_settings(self):
        profile = self._active_profile_name
        self._thinking_user_changed = False
        self.auto_speak.setChecked(self._profile_auto_speak.get(profile, False))
        for field, value in (
            (self.stt_language, self._profile_stt_languages.get(profile, "de")),
        ):
            field.blockSignals(True)
            index = field.findData(value)
            if index < 0:
                field.addItem(value, value)
                index = field.count() - 1
            field.setCurrentIndex(index)
            field.blockSignals(False)
        selected = self._profile_output_languages.get(profile, "English")
        self.profile_response_language.blockSignals(True)
        index = self.profile_response_language.findData(selected)
        if index < 0:
            self.profile_response_language.addItem(selected, selected)
            index = self.profile_response_language.count() - 1
        self.profile_response_language.setCurrentIndex(index)
        self.profile_response_language.blockSignals(False)
        self.profile_model.setCurrentText(self._profile_models.get(profile, ""))
        self.profile_voice.setCurrentText(self._profile_voices.get(profile, ""))
        self._refresh_voice_options()
        self.profile_voice.setCurrentText(self._profile_voices.get(profile, ""))
        self.profile_model.setEnabled(True)
        self.profile_voice.setEnabled(True)
        self._fill_thinking(self._thinking_options)

    def done(self, result):
        self._cancel_manual_button()
        self.profile_hotkey.stop_capture()
        super().done(result)

    def closeEvent(self, event):
        self._cancel_manual_button()
        super().closeEvent(event)

    def _toggle_manual_controller(self, expanded: bool):
        self.manual_controller_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.manual_controller_panel.setVisible(expanded)
        if not expanded:
            self._cancel_manual_button()

    def _queue_manual_button(self, number: int):
        if self._manual_busy or self._manual_pending is not None or not self.input_control.isChecked():
            return
        self._manual_pending = (f"Button{number}", self.manual_hold.isChecked())
        self.manual_grid.setEnabled(False)
        self.manual_hold.setEnabled(False)
        self.manual_delay.setEnabled(False)
        delay = self.manual_delay.value()
        if delay:
            self.manual_status.setText(tr("Waiting to press {button}…", button=self._manual_pending[0]))
            self.manual_cancel.setVisible(True)
            self._manual_timer.start(delay * 1000)
        else:
            self._send_manual_button()

    def _cancel_manual_button(self):
        if self._manual_pending is None:
            return
        self._manual_timer.stop()
        self._manual_pending = None
        self.manual_cancel.setVisible(False)
        self.manual_grid.setEnabled(True)
        self.manual_hold.setEnabled(True)
        self.manual_delay.setEnabled(True)
        self.manual_status.setText(tr("Press cancelled"))

    def _send_manual_button(self):
        if self._manual_pending is None:
            return
        button, hold = self._manual_pending
        self._manual_pending = None
        self._manual_busy = True
        self.manual_cancel.setVisible(False)
        self.manual_status.setText(tr("Pressing {button}…", button=button))
        signals = self.discovery

        def run():
            try:
                from voxcomm.gamepad import press_gamepad_button
                result = press_gamepad_button(button, hold=hold)
                message = tr("Pressed {button}", button=result)
            except Exception as error:
                message = tr("Controller error: {error}", error=str(error))
            try:
                signals.controller_pressed.emit(button, message)
            except RuntimeError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _manual_controller_result(self, _button: str, message: str):
        self._manual_busy = False
        self.manual_grid.setEnabled(True)
        self.manual_hold.setEnabled(True)
        self.manual_delay.setEnabled(True)
        self.manual_status.setText(message)

    def _store_special_profile(self):
        self._store_enabled_tools()
        self._store_input_action_catalog()
        self._store_profile_settings()
        if self._active_profile_name == "__global__":
            self._global_prompt = self.special_prompt.toPlainText().strip()
            return
        if self._active_profile_name:
            self.special_prompts[self._active_profile_name] = self.special_prompt.toPlainText().strip()

    def _store_input_action_catalog(self):
        if self._active_profile_name not in self.special_prompts and self._active_profile_name != "__global__":
            return
        catalog = self._migrate_controller_replies(self.input_action_catalog.toPlainText().strip(),
                                                   self._active_profile_name)
        if catalog != self.input_action_catalog.toPlainText().strip():
            self.input_action_catalog.setPlainText(catalog)
        self._update_catalog_warning()
        if catalog:
            self.input_action_catalogs[self._active_profile_name] = catalog
        else:
            self.input_action_catalogs.pop(self._active_profile_name, None)

    def _load_input_action_catalog(self, catalog: str):
        self.input_action_catalog.setPlainText(self._migrate_controller_replies(catalog, self._active_profile_name))
        self._update_catalog_warning()
        self._load_game_reply_text()

    def _migrate_controller_replies(self, catalog: str, profile: str) -> str:
        overrides = {key: value for key, value in reply_overrides_for_profile(self.input_reply_overrides, profile).items()
                     if not key[0].startswith("game_log:")}
        catalog, merged = merge_catalog_reply_overrides(catalog, overrides)
        for action, tag in merged:
            self.input_reply_overrides.pop(reply_override_key(profile, action, tag), None)
        return catalog

    def _update_catalog_warning(self):
        overrides = reply_overrides_for_profile(self.input_reply_overrides, self._active_profile_name)
        self.catalog_warning.setVisible(any(not action.startswith("game_log:") for action, _tag in overrides))

    def _load_game_reply_text(self):
        for event, field in self._game_reply_fields.items():
            defaults = game_reply_defaults(event)
            replies = {tag: self.input_reply_overrides.get(
                reply_override_key(self._active_profile_name, game_reply_action(event), tag), text)
                for tag, text in defaults.items()}
            for (action, tag), text in reply_overrides_for_profile(
                    self.input_reply_overrides, self._active_profile_name).items():
                if action == game_reply_action(event):
                    replies[tag] = text
            text = self._game_reply_drafts.get((self._active_profile_name, event),
                                               format_replies(replies))
            field.setText(text)
            field.setCursorPosition(0)

    def _store_game_reply_text(self, event: str, text: str):
        profile = self._active_profile_name
        self._game_reply_drafts[profile, event] = text
        try:
            replies = parse_replies(text)
        except ValueError:
            return
        existing_tags = {tag for (action, tag) in reply_overrides_for_profile(
            self.input_reply_overrides, profile) if action == game_reply_action(event)}
        defaults = game_reply_defaults(event)
        for tag in existing_tags | replies.keys():
            key = reply_override_key(profile, game_reply_action(event), tag)
            if tag in replies and replies[tag] != defaults.get(tag):
                self.input_reply_overrides[key] = replies[tag]
            else:
                self.input_reply_overrides.pop(key, None)

    def _request_save(self):
        for (profile, event), text in self._game_reply_drafts.items():
            try:
                parse_replies(text)
            except ValueError:
                self.settings_tabs.setCurrentIndex(1)
                self.special_profile.setCurrentIndex(self.special_profile.findData(profile))
                if self.profile_tabs.isTabVisible(3):
                    self.profile_tabs.setCurrentIndex(3)
                    self.discovery_status.setText(tr("Invalid announcement reply: {event}", event=tr(GAME_EVENT_TEXT[event])))
                    self._game_reply_fields[event].setFocus()
                else:
                    self.profile_tabs.setCurrentIndex(1)
                    self.discovery_status.setText(tr("Enable Star Citizen Log to fix the invalid announcement reply."))
                return
        self.save_requested.emit()

    def _update_controller_fields(self):
        self.action_catalog_section.form.setRowVisible(
            self.controller_llm_keyword, self.controller_mode.currentData() == "hybrid")

    def _store_enabled_tools(self):
        if self._active_profile_name in self.special_prompts or self._active_profile_name == "__global__":
            existing = self.enabled_tools.get(self._active_profile_name, [])
            tools = []
            if self.web_search.isChecked():
                tools.append("web_search")
            if self.input_control.isChecked():
                tools.append("input_control")
            if self.star_citizen.isChecked() and self.star_citizen_uex.isChecked() and STAR_CITIZEN_LEGACY in existing:
                tools.append(STAR_CITIZEN_LEGACY)
            else:
                if self.star_citizen.isChecked():
                    tools.append(STAR_CITIZEN_LOG)
                if self.star_citizen_uex.isChecked():
                    tools.append(STAR_CITIZEN_UEX)
            known = {"web_search", "input_control", STAR_CITIZEN_LEGACY, STAR_CITIZEN_LOG, STAR_CITIZEN_UEX}
            tools.extend(tool for tool in existing if tool not in known)
            if tools:
                self.enabled_tools[self._active_profile_name] = tools
            else:
                self.enabled_tools.pop(self._active_profile_name, None)

    def _select_special_profile(self):
        self._cancel_manual_button()
        self._store_special_profile()
        self._active_profile_name = self.special_profile.currentData() or ""
        if self._active_profile_name == "__global__":
            prompt = self._global_prompt
        else:
            prompt = self.special_prompts.get(self._active_profile_name, "")
        self.star_citizen.setChecked(allows_game_log(self.enabled_tools.get(self._active_profile_name, [])))
        self.star_citizen_uex.setChecked(allows_uex(self.enabled_tools.get(self._active_profile_name, [])))
        self.web_search.setChecked("web_search" in self.enabled_tools.get(self._active_profile_name, []))
        self.input_control.setChecked("input_control" in self.enabled_tools.get(self._active_profile_name, []))
        self._load_input_action_catalog(self.input_action_catalogs.get(self._active_profile_name, ""))
        self.special_prompt.setPlainText(prompt)
        self._load_profile_settings()
        self.special_prompt.setPlaceholderText(tr("Default profile instructions") if self._active_profile_name == "__global__"
                               else tr("Task knowledge and behavior rules for this profile"))
        self._update_profile_controls()

    def _update_profile_controls(self):
        has_profile = self.special_profile.currentData() not in (None, "", "__global__")
        self.special_prompt.setEnabled(True)
        self.rename_profile.setEnabled(has_profile)
        self.remove_profile.setEnabled(has_profile)
        self.profile_hotkey.setEnabled(True)
        self.profile_hotkey.setValue(self._default_hotkey if self._active_profile_name == "__global__"
                         else self.special_hotkeys.get(self._active_profile_name, ""))
        if hasattr(self, "profile_model"):
            self._load_profile_settings()

    def _refresh_special_profiles(self, selected: str):
        self.special_profile.blockSignals(True)
        self.special_profile.clear()
        self.special_profile.addItem(tr("Default"), "__global__")
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
        self.star_citizen.setChecked(allows_game_log(self.enabled_tools.get(self._active_profile_name, [])))
        self.star_citizen_uex.setChecked(allows_uex(self.enabled_tools.get(self._active_profile_name, [])))
        self.input_control.setChecked("input_control" in self.enabled_tools.get(self._active_profile_name, []))
        self._load_input_action_catalog(self.input_action_catalogs.get(self._active_profile_name, ""))
        self.special_prompt.setPlainText(prompt)
        self._load_profile_settings()
        self.special_prompt.setPlaceholderText(tr("Default profile instructions") if self._active_profile_name == "__global__"
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
        defaults = AppConfig()
        self._profile_stt_languages[name] = defaults.stt_language
        self._profile_tts_languages[name] = defaults.tts_language
        self._profile_voice_languages[name] = defaults.voice_language
        self._profile_auto_speak[name] = defaults.auto_speak
        self._profile_output_languages[name] = "English"
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
        self._game_reply_drafts = {(name if profile == current else profile, event): text
                                   for (profile, event), text in self._game_reply_drafts.items()}
        for key in [key for key in self.input_reply_overrides if split_reply_override_key(key)[0] == current]:
            _profile, action, tag = split_reply_override_key(key)
            self.input_reply_overrides[reply_override_key(name, action, tag)] = self.input_reply_overrides.pop(key)
        for settings in (self._profile_thinkings, self._profile_models, self._profile_voices,
                         self._profile_response_languages, self._profile_stt_languages,
                         self._profile_tts_languages, self._profile_voice_languages, self._profile_auto_speak,
                         self._profile_output_languages):
            if current in settings:
                settings[name] = settings.pop(current)
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
        self._game_reply_drafts = {(profile, event): text for (profile, event), text in self._game_reply_drafts.items()
                       if profile != current}
        self.input_reply_overrides = {key: text for key, text in self.input_reply_overrides.items()
                                      if split_reply_override_key(key)[0] != current}
        self._profile_thinkings.pop(current, None)
        self._profile_models.pop(current, None)
        self._profile_voices.pop(current, None)
        self._profile_response_languages.pop(current, None)
        self._profile_stt_languages.pop(current, None)
        self._profile_tts_languages.pop(current, None)
        self._profile_voice_languages.pop(current, None)
        self._profile_auto_speak.pop(current, None)
        self._profile_output_languages.pop(current, None)
        self._refresh_special_profiles("")

    def _discover(self, kind: str, button=None):
        if kind == "voices":
            button = button or self.profile_voice_refresh
            endpoint = self.tts.text().strip()
            if self.tts_provider.currentData() == "openai":
                api_key = self.tts_api_key.text().strip()
                lookup = lambda: openai_tts.list_voices(endpoint, api_key) if api_key else openai_tts.list_voices(endpoint)
            else:
                lookup = lambda: list_voices(endpoint)
        else:
            button = button or self.profile_model_refresh
            provider = self.provider.currentData()
            endpoint = self.llm_url.text().strip() or (
                cloud.CLOUD_BASE_URL if provider == "ollama-cloud" else "")
            api_key = self.api_key.text().strip()
            lookup = lambda: list_models(provider, endpoint, api_key) if api_key else list_models(provider, endpoint)
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
        buttons = (self.profile_voice_refresh,) if kind == "voices" else (self.profile_model_refresh,)
        for button in buttons:
            button.setEnabled(True)
        label = tr("voices") if kind == "voices" else tr("models")
        if error:
            self.discovery_status.setText(tr("Could not load {label}: {error}", label=label, error=error))
            return
        if kind == "voices":
            self._available_voices = values
            self._refresh_voice_options()
            self.discovery_status.setText(tr("Found {count} voices", count=len(values)) if values
                                          else tr("The service reported no voices"))
            return
        if self.provider.currentData() == "ollama-cloud":
            cloud.store_models(values)
        self._fill_models(values)
        self.discovery_status.setText(tr("Found {count} {label}", count=len(values), label=label) if values
                          else tr("The service reported no {label}", label=label))

    def _refresh_voice_options(self):
        current = self.profile_voice.currentText()
        if self.tts_provider.currentData() == "openai":
            voices = list(self._available_voices)
        else:
            selected = self.profile_response_language.currentData() or ""
            code = reply_language_tag(selected).lower()
            voices = filter_voices(self._available_voices, code)
        self.profile_voice.blockSignals(True)
        self.profile_voice.clear()
        self.profile_voice.addItems(voices)
        if current in voices or not voices:
            self.profile_voice.setCurrentText(current)
        else:
            self.profile_voice.setCurrentIndex(0)
        self.profile_voice.blockSignals(False)

    def _refresh_game_log(self):
        if not self.star_citizen.isChecked():
            return
        self._game_log.poll()
        stamp = datetime.now().strftime("%H:%M:%S")
        for event in self._game_log.take_events():
            self._game_log_events.append(f"{stamp}  {tr(GAME_EVENT_TEXT[event])}")
        self.game_status_view.setText("\n".join(_game_status_lines(self._game_log.facts)))
        if self.game_log_unfiltered.isChecked():
            path = self._game_log.path
            if self._raw_log_reader is None or self._raw_log_reader.path != path:
                self._raw_log_generation += 1
                self._raw_log_reader = RecentLogReader(path, GAME_LOG_RAW_LINES) if path else None
                self.game_log_view.clear()
            if self._raw_log_reader is not None and not self._raw_log_busy:
                self._raw_log_busy = True
                reader, generation, signals = self._raw_log_reader, self._raw_log_generation, self.discovery

                def run():
                    reset, lines = reader.read_new_lines()
                    try:
                        signals.raw_game_log.emit(generation, reset, lines)
                    except RuntimeError:
                        pass

                threading.Thread(target=run, daemon=True).start()
            return
        text = "\n".join(self._game_log_events)
        if text != self._game_log_text:
            self._game_log_text = text
            self.game_log_view.setPlainText(text)

    def _game_log_mode_changed(self, unfiltered: bool):
        self._raw_log_generation += 1
        self._raw_log_reader = None
        self._game_log_text = ""
        self.game_log_view.clear()
        self.game_log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if unfiltered
                                           else QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._refresh_game_log()

    def _game_log_permission_changed(self, enabled: bool):
        if not enabled:
            self._raw_log_generation += 1
            self._raw_log_reader = None
            self._game_log_text = ""
            self.game_log_view.clear()

    def _raw_game_log_result(self, generation: int, reset: bool, lines: list[str]):
        self._raw_log_busy = False
        if (generation != self._raw_log_generation or not self.star_citizen.isChecked()
                or not self.game_log_unfiltered.isChecked()):
            return
        if reset:
            self.game_log_view.setPlainText("\n".join(lines))
            self.game_log_view.verticalScrollBar().setValue(self.game_log_view.verticalScrollBar().maximum())
        elif lines:
            self.game_log_view.appendPlainText("\n".join(lines))

    def apply(self, config: AppConfig) -> AppConfig:
        self._store_special_profile()
        for event, field_name in GAME_ANNOUNCE_FIELDS.items():
            setattr(config, field_name, self._announce_checks[event].isChecked())
        config.ui_language = self.ui_language.currentData() or "en"
        config.stt_url, config.tts_url = self.stt.text(), self.tts.text()
        config.stt_provider = self.stt_provider.currentData() or "wyoming"
        config.tts_provider = self.tts_provider.currentData() or "wyoming"
        config.stt_api_key = self.stt_api_key.text().strip()
        config.tts_api_key = self.tts_api_key.text().strip()
        config.tts_language = self._profile_tts_languages["__global__"]
        config.stt_language = self._profile_stt_languages["__global__"]
        config.voice_language = self._profile_voice_languages["__global__"]
        self._store_global_prompt()
        config.special_prompts = dict(self.special_prompts)
        config.special_hotkeys = {name: key for name, key in self.special_hotkeys.items() if key and name in self.special_prompts}
        allowed_tool_profiles = {"__global__", *self.special_prompts}
        config.input_action_catalogs = {name: catalog for name, catalog in self.input_action_catalogs.items()
                          if name in allowed_tool_profiles and catalog.strip()}
        config.input_reply_overrides = {key: text for key, text in self.input_reply_overrides.items()
                                        if split_reply_override_key(key)[0] in allowed_tool_profiles and text.strip()}
        config.enabled_tools = {name: list(tools)
                for name, tools in self.enabled_tools.items()
            if tools}
        config.profile_models = {name: value for name, value in self._profile_models.items()
                     if name in allowed_tool_profiles and value}
        config.profile_voices = {name: value for name, value in self._profile_voices.items()
                     if name in allowed_tool_profiles and value}
        config.profile_thinking = {name: value for name, value in self._profile_thinkings.items()
                       if name in allowed_tool_profiles and value}
        config.profile_response_languages = {
            name: value for name, value in self._profile_response_languages.items()
            if name in allowed_tool_profiles and value}
        config.profile_stt_languages = {name: value for name, value in self._profile_stt_languages.items()
                                        if name in allowed_tool_profiles}
        config.profile_tts_languages = {name: value for name, value in self._profile_tts_languages.items()
                                        if name in allowed_tool_profiles}
        config.profile_voice_languages = {name: value for name, value in self._profile_voice_languages.items()
                                          if name in allowed_tool_profiles}
        config.profile_auto_speak = {name: value for name, value in self._profile_auto_speak.items()
                         if name in allowed_tool_profiles}
        config.profile_output_languages = {name: value for name, value in self._profile_output_languages.items()
                           if name in allowed_tool_profiles}
        config.active_special_prompt = self.special_profile.currentData() if self.special_profile.currentData() != "__global__" else ""
        config.global_prompt = self._global_prompt
        config.auto_speak = self._profile_auto_speak["__global__"]
        config.tray_mode = self.tray_mode.isChecked()
        config.llm_provider, config.llm_url = self.provider.currentData() or "ollama", self.llm_url.text()
        config.controller_mode = self.controller_mode.currentData() or "hybrid"
        config.controller_llm_keyword = self.controller_llm_keyword.text().strip()
        config.ollama_api_key = self.api_key.text().strip()
        config.ptt_key, config.sample_rate = self._default_hotkey, self.sample_rate.value()
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
        self._game_log = GameLogTracker()
        self.config = load_config()
        self._settings_dialog = None
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
        self.transcript.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.transcript.setAutoFillBackground(False)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.document().setDocumentMargin(0)
        transcript_font = QFont()
        transcript_font.setFamilies([
            "AtkynsonMono Nerd Font", "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans CJK SC",
        ])
        transcript_font.setPointSize(11)
        self.transcript.setFont(transcript_font)
        self.transcript.setFixedSize(TRANSCRIPT_WIDTH, 0)
        self.transcript.viewport().setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.transcript.viewport().setAutoFillBackground(False)
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
        self._last_response_tts_language = ""
        self._last_text = ""
        self._gamepad_owned = False
        self._position_tracking_enabled = False
        self._position_save_timer = QTimer(self)
        self._position_save_timer.setSingleShot(True)
        self._position_save_timer.setInterval(500)
        self._position_save_timer.timeout.connect(self._save_overlay_position)
        self._game_log_timer = QTimer(self)
        self._game_log_timer.setInterval(GAME_LOG_POLL_MS)
        self._game_log_timer.timeout.connect(self._poll_game_log)
        self._game_log_timer.start()
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
            QAbstractScrollArea::viewport {{ background: transparent; border: none; }}
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

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange, QEvent.Type.ThemeChange)
                and hasattr(self, "_panel")):
            self._apply_theme()

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
            profile = text if text in self.config.special_prompts else ""
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
        elif kind in ("released", "cancelled") and self._recording:
            press_duration = time.monotonic() - self._ptt_pressed_at if self._ptt_pressed_at is not None else None
            self._ptt_pressed_at = None
            self._recording = False
            self._tray_recording = False
            self.tray_toggle_action.setText(tr("Start recording"))
            self.tray_icon.setToolTip(APP_NAME)
            is_short_press = press_duration is not None and press_duration < MIN_PTT_DURATION_MS / 1000
            is_cancelled = kind == "cancelled" or is_short_press
            if is_cancelled:
                self._cancel_active_work()
            if self._capture_stop is not None:
                self._capture_stop.set()
                self._capture_stop = None
            if not is_cancelled:
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
            resolved_profile = resolve_profile(self.config, profile)
            if self.config.stt_provider == "whisper.cpp":
                args = (audio, self.config.stt_url, self.config.sample_rate, resolved_profile.stt_language)
                text = whisper_cpp.transcribe(*args, api_key=self.config.stt_api_key) if self.config.stt_api_key else whisper_cpp.transcribe(*args)
            else:
                text = transcribe_sync(audio, self.config.stt_url, self.config.sample_rate, resolved_profile.stt_language)
            if cancel_event.is_set():
                return
            if not text:
                self.events.request_message.emit(tr("Ready · no speech detected"), "", cancel_event)
                return
            self.events.request_message.emit(THINKING_STATUS, f"{USER_PREFIX}{text}\n", cancel_event)
            profile_name = resolved_profile.name
            profile_key = resolved_profile.key
            profile_model = resolved_profile.model
            profile_voice = resolved_profile.voice
            reply_language = self._response_language_for_profile(profile_key, profile_voice)
            enabled_tools = list(resolved_profile.enabled_tools)
            game_context = ""
            game_state = None
            if allows_game_log(enabled_tools):
                game_context = self._game_log.prompt_context()
                game_facts = self._game_log.facts
                game_state = {"in_ship": game_facts.in_ship, "pilot_seat": game_facts.pilot_seat}
            # Mit Websuche kann die Antwort nachträglich ersetzt werden, daher erst nach der fertigen Antwort sprechen.
            speech = None
            if resolved_profile.auto_speak and "web_search" not in enabled_tools:
                speech = SentenceStream(
                    cancel_event, on_first=lambda: self.events.speak.emit(
                        speech, cancel_event, profile_voice, reply_language))
            streamed = []
            try:
                answer = chat(
                    self.config.llm_provider,
                    self.config.llm_url,
                    profile_model,
                    text,
                    system_prompt=build_system_prompt(
                        special_prompt_name=profile_name,
                        special_prompt=resolved_profile.special_prompt,
                        response_language=reply_language,
                        global_prompt=resolved_profile.global_prompt,
                        extra_context=game_context,
                    ),
                    enabled_tools=enabled_tools,
                    input_actions=resolved_profile.action_catalog,
                    api_key=self.config.ollama_api_key,
                    cancel_event=cancel_event,
                    on_text=(lambda delta: (streamed.append(delta), speech.feed(delta))) if speech else None,
                    thinking=resolved_profile.thinking,
                    on_status=lambda status: self.events.activity.emit(status, cancel_event),
                    reply_language=reply_language,
                    controller_mode=self.config.controller_mode,
                    llm_keyword=self.config.controller_llm_keyword,
                    game_state=game_state,
                    reply_overrides=reply_overrides_for_profile(
                        self.config.input_reply_overrides, profile_key),
                    profile_key=profile_key,
                )
                if speech is not None and not streamed and not cancel_event.is_set():
                    speech.feed(answer)
            finally:
                if speech is not None:
                    speech.finish()
            if cancel_event.is_set():
                return
            self._last_response_voice = profile_voice
            self._last_response_tts_language = reply_language
            self.events.request_message.emit(
                tr("Ready · response generated locally"), f"{USER_PREFIX}{text}{AI_MARKER}{answer}", cancel_event
            )
            if resolved_profile.auto_speak and speech is None and not cancel_event.is_set():
                self.events.speak.emit(answer, cancel_event, profile_voice, reply_language)
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
        save_overlay_position(position.x(), position.y())

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

    def _poll_game_log(self):
        profile = next((name for name in (*sorted(self.config.special_prompts, key=str.casefold), "__global__")
                        if allows_game_log(self.config.enabled_tools.get(name, []))), None)
        if profile is None:
            return
        self._game_log.poll()
        events = [event for event in self._game_log.take_events()
                  if getattr(self.config, GAME_ANNOUNCE_FIELDS[event])]
        # Laufende Antworten nicht unterbrechen; Meldungen während der Ausgabe entfallen.
        if not events or self._speech_jobs:
            return
        voice = self.config.profile_voices.get(profile, "")
        tag = reply_language_tag(self._response_language_for_profile(profile, voice)) or "EN"
        ship = self._game_log.facts.ship
        text = " ".join(
            game_event_reply(self.config.input_reply_overrides, event, profile, tag, ship)
            for event in events
        )
        tts_language = self._response_language_for_profile(profile, voice)
        self._start_speak(text, voice_override=voice, language_override=tts_language)

    def _active_profile(self) -> str:
        profile = self.config.active_special_prompt
        return profile if profile in self.config.special_prompts else "__global__"

    def _active_profile_voice(self) -> str:
        return self.config.profile_voices.get(self._active_profile(), "")

    def _response_language_for_profile(self, profile: str, voice: str) -> str:
        return output_language(self.config, profile, voice)

    def _speak_last(self):
        if not self._last_response:
            self._show_message(tr("No response to read yet"), "")
            return
        self._show_message(SPEAKING_STATUS, self._last_text)
        self._start_speak(self._last_response, voice_override=self._last_response_voice,
                          language_override=self._last_response_tts_language)

    def _start_speak(self, source, cancel_event=None, voice_override: str | None = None,
                     language_override: str | None = None):
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
        if language_override is None:
            language_override = self._response_language_for_profile(self._active_profile(), self._active_profile_voice())
        threading.Thread(target=self._speak, args=(source, cancel_event, voice_override, language_override), daemon=True).start()

    def _stop_speaking(self):
        if self._speech_cancel is not None:
            self._speech_cancel.set()

    def _speech_chunks(self, sentences, voice_override: str | None = None, language_override: str | None = None):
        voice = self._active_profile_voice() if voice_override is None else voice_override
        language = self._response_language_for_profile(self._active_profile(), voice) if language_override is None else language_override
        language = VOICE_LANGUAGE_NAMES.get(reply_language_tag(language).lower(), language)
        for sentence in sentences:
            if self.config.tts_provider == "openai":
                args = (sentence, self.config.tts_url, voice, language)
                yield from openai_tts.speak_stream(*args, api_key=self.config.tts_api_key) if self.config.tts_api_key else openai_tts.speak_stream(*args)
            else:
                yield from speak_stream_sync(sentence, self.config.tts_url, voice)

    def _speak(self, source, cancel_event: threading.Event, voice_override: str | None = None,
               language_override: str | None = None):
        try:
            sentences = [source] if isinstance(source, str) else source
            play_stream(prefetch(self._speech_chunks(sentences, voice_override, language_override), cancel_event), cancel_event)
            if not cancel_event.is_set():
                self.events.request_message.emit(tr("Ready · press PTT to speak"), "", cancel_event)
        except Exception as error:
            if not cancel_event.is_set():
                self.events.request_message.emit(tr("Speech output error · {error}", error=error), "", cancel_event)
        finally:
            self.events.speech_finished.emit()

    def _settings(self):
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.config, reset_position_callback=self.reset_overlay_position)
            self._settings_dialog.save_requested.connect(
                lambda: self._save_settings(self._settings_dialog))
            self._settings_dialog.finished.connect(self._settings_dialog_closed)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _settings_dialog_closed(self, _result: int):
        self._settings_dialog = None

    def _save_settings(self, dialog: SettingsDialog):
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
