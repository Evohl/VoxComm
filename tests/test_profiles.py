from dataclasses import FrozenInstanceError
import threading
from unittest.mock import call, patch

import pytest
from PySide6.QtWidgets import QApplication

from voxcomm.config import AppConfig
from voxcomm.profiles import ResolvedProfile, output_language, resolve_profile


def test_global_profile_ignores_active_named_profile():
    config = AppConfig(
        global_prompt="Base instructions",
        special_prompts={"Work": "Work instructions"},
        active_special_prompt="Work",
        profile_models={"__global__": "global-model"},
        profile_voices={"__global__": "global-voice"},
        profile_thinking={"__global__": "low"},
        profile_response_languages={"__global__": "English"},
        enabled_tools={"__global__": ["web_search"]},
        input_action_catalogs={"__global__": "global actions"},
    )

    assert resolve_profile(config) == ResolvedProfile(
        key="__global__", name="", global_prompt="Base instructions", special_prompt="",
        model="global-model", voice="global-voice",
        thinking="low", response_language="English", enabled_tools=("web_search",),
        action_catalog="global actions",
    )


def test_profile_languages_override_legacy_values_including_empty_values():
    config = AppConfig(
        special_prompts={"Work": "", "Old": ""},
        stt_language="fr", tts_language="French", voice_language="fr",
        profile_stt_languages={"Work": "auto"},
        profile_tts_languages={"Work": ""},
        profile_voice_languages={"Work": ""},
    )
    work = resolve_profile(config, "Work")
    assert (work.stt_language, work.tts_language, work.voice_language) == ("auto", "", "")
    old = resolve_profile(config, "Old")
    assert (old.stt_language, old.tts_language, old.voice_language) == ("fr", "French", "fr")


def test_auto_speak_uses_profile_setting_with_legacy_fallback():
    config = AppConfig(auto_speak=True, special_prompts={"Work": "", "Old": ""},
                       profile_auto_speak={"Work": False})
    assert resolve_profile(config, "Work").auto_speak is False
    assert resolve_profile(config, "Old").auto_speak is True
    assert resolve_profile(config).auto_speak is True


def test_output_language_migrates_without_overwriting_legacy_settings():
    config = AppConfig(tts_language="French", profile_response_languages={"Work": "German"},
                       profile_output_languages={"Other": "Spanish"})
    assert output_language(config, "Work", "en_US-amy") == "German"
    assert output_language(config, "Other", "de_DE-voice") == "Spanish"
    assert output_language(config, "__global__", "de_DE-voice") == "French"
    assert config.tts_language == "French"

def test_named_profile_resolves_its_own_settings():
    config = AppConfig(
        special_prompts={"Work": "Work instructions"},
        profile_models={"Work": "work-model"},
        profile_voices={"Work": "work-voice"},
        profile_thinking={"Work": "high"},
        profile_response_languages={"Work": "German"},
        enabled_tools={"Work": ["input_control", "star_citizen"]},
        input_action_catalogs={"Work": "work actions"},
    )

    assert resolve_profile(config, "Work") == ResolvedProfile(
        key="Work", name="Work", global_prompt=config.global_prompt, special_prompt="Work instructions",
        model="work-model", voice="work-voice",
        thinking="high", response_language="German", enabled_tools=("input_control", "star_citizen"),
        action_catalog="work actions",
    )


def test_invalid_profile_resolves_to_global_even_with_stale_entries():
    config = AppConfig(
        special_prompts={"Work": "Work instructions"},
        active_special_prompt="Work",
        profile_models={"__global__": "global-model", "Missing": "stale-model"},
        enabled_tools={"__global__": ["web_search"], "Missing": ["input_control"]},
        input_action_catalogs={"__global__": "global actions", "Missing": "stale actions"},
    )

    assert resolve_profile(config, "Missing") == resolve_profile(config)


def test_profiles_do_not_inherit_settings_or_tools_from_each_other():
    config = AppConfig(
        special_prompts={"Work": "Work instructions", "Empty": ""},
        profile_models={"__global__": "global-model"},
        profile_voices={"__global__": "global-voice"},
        profile_thinking={"__global__": "high"},
        profile_response_languages={"__global__": "German"},
        enabled_tools={"__global__": ["web_search"], "Work": ["input_control"]},
        input_action_catalogs={"__global__": "global actions"},
    )

    work = resolve_profile(config, "Work")
    empty = resolve_profile(config, "Empty")
    assert work.enabled_tools == ("input_control",)
    assert empty.enabled_tools == ()
    assert (work.model, work.voice, work.thinking, work.response_language, work.action_catalog) == (
        "", "", "", "", "")
    assert resolve_profile(config).enabled_tools == ("web_search",)


def test_resolved_profile_is_an_immutable_snapshot():
    config = AppConfig(enabled_tools={"__global__": ["web_search"]})
    profile = resolve_profile(config)

    config.enabled_tools["__global__"].append("input_control")
    assert profile.enabled_tools == ("web_search",)
    with pytest.raises(FrozenInstanceError):
        profile.name = "Work"


@pytest.mark.parametrize("stt_provider", ["wyoming", "whisper.cpp"])
@pytest.mark.parametrize("streamed", [True, False])
def test_capture_and_replay_use_requested_profile_languages(stt_provider, streamed):
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    config = AppConfig(
        special_prompts={"Work": ""}, active_special_prompt="Work",
        stt_provider=stt_provider, tts_provider="openai", auto_speak=True,
        profile_stt_languages={"__global__": "de", "Work": "en"},
        profile_tts_languages={"__global__": "German", "Work": "English"},
        profile_voices={"__global__": "default-voice", "Work": "work-voice"},
        enabled_tools={} if streamed else {"Work": ["web_search"]},
    )
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        try:
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Hello") as wyoming,
                patch("voxcomm.app.whisper_cpp.transcribe", return_value="Hello") as whisper,
                patch("voxcomm.app.chat", return_value="A complete answer.") as chat,
            ):
                overlay._capture(threading.Event(), "Work")
                transcribe = whisper if stt_provider == "whisper.cpp" else wyoming
                assert transcribe.call_args.args[-1] == "en"
                assert spoken[-1][2:] == ("work-voice", "English")
                assert overlay._last_response_tts_language == "English"
                with patch.object(overlay, "_start_speak") as replay:
                    overlay._speak_last()
                assert replay.call_args.kwargs == {"voice_override": "work-voice", "language_override": "English"}
                overlay._capture(threading.Event(), "")
                assert transcribe.call_args.args[-1] == "de"
                assert spoken[-1][2:] == ("default-voice", "German")
                assert chat.call_count == 2
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


@pytest.mark.parametrize(("tools", "tool", "source"), [
    (["star_citizen_uex"], "star_citizen_uex", "Requesting Local UEX Database."),
    (["star_citizen_log", "web_search"], "web_search", "Requesting CommArray."),
])
def test_capture_speaks_actual_search_source_before_answer(tools, tool, source):
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    config = AppConfig(profile_models={"__global__": "test-model"},
                       profile_auto_speak={"__global__": True}, enabled_tools={"__global__": tools})
    function = ({"name": "star_citizen", "arguments": {"operation": "commodities", "query": "Gold"}}
                if tool == "star_citizen_uex" else {"name": "web_search", "arguments": {"query": "topic"}})
    with (patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"),
          patch("voxcomm.app.database.start_refresh_if_due"),
          patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
          patch("voxcomm.app.transcribe_sync", return_value="Find something"),
          patch("voxcomm.providers.llm.query_uex") as uex,
          patch("voxcomm.providers.llm.search_web") as search,
          patch("voxcomm.providers.llm._request_stream", side_effect=[
              {"content": "", "tool_calls": [{"function": function}]}, {"content": "Here is the answer."},
          ])):
        overlay = Overlay()
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        displayed = []
        overlay.events.request_message.connect(lambda status, text, cancel: displayed.append(text))
        observations = []

        def verify_early(*args):
            observations.append((displayed[-1], len(spoken), bool(spoken and spoken[0][0].started)))
            return {"status": "ok", "matches": []} if tool == "star_citizen_uex" else []

        uex.side_effect = verify_early
        search.side_effect = verify_early
        try:
            overlay._capture(threading.Event())
            assert len(observations) == 1
            assert observations[0][0].endswith(source)
            assert observations[0][1:] == (1, True)
            assert len(spoken) == 1
            assert list(spoken[0][0]) == [source, "Here is the answer."]
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


def test_other_profile_web_search_has_no_source_announcement():
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    config = AppConfig(profile_models={"__global__": "test-model"},
                       profile_auto_speak={"__global__": True}, enabled_tools={"__global__": ["web_search"]})
    call = {"function": {"name": "web_search", "arguments": {"query": "topic"}}}
    with (patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"),
          patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
          patch("voxcomm.app.transcribe_sync", return_value="Find something"),
          patch("voxcomm.providers.llm.search_web", return_value=[]),
          patch("voxcomm.providers.llm._request_stream", side_effect=[
              {"content": "", "tool_calls": [call]}, {"content": "Here is the answer."},
          ])):
        overlay = Overlay()
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        try:
            overlay._capture(threading.Event())
            assert [entry[0] for entry in spoken] == ["Here is the answer."]
            assert overlay._last_response == "Here is the answer."
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


def test_synthesis_and_game_announcement_use_their_profile_language():
    from unittest.mock import Mock
    from voxcomm.app import Overlay
    from voxcomm.gamelog.facts import GameFacts, NAV_TARGET_SET

    app = QApplication.instance() or QApplication([])
    config = AppConfig(special_prompts={"Work": ""}, active_special_prompt="Work",
                       tts_provider="openai", tts_language="French",
                       profile_tts_languages={"__global__": "German", "Work": ""},
                       profile_voices={"Work": "work-voice"},
                       enabled_tools={"Work": ["star_citizen_log"]})
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        try:
            with patch("voxcomm.app.openai_tts.speak_stream", return_value=iter([])) as speak:
                list(overlay._speech_chunks(["Hello"], "work-voice", ""))
                speak.assert_called_once_with("Hello", config.tts_url, "work-voice", "")
            overlay._game_log = Mock(facts=GameFacts(), path=None)
            overlay._game_log.take_events.return_value = [NAV_TARGET_SET]
            with patch.object(overlay, "_start_speak") as announce:
                overlay._poll_game_log()
            assert announce.call_args.kwargs == {"voice_override": "work-voice", "language_override": "English"}
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


def test_output_language_drives_prompt_speech_and_profile_auto_read():
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    config = AppConfig(special_prompts={"Work": ""}, stt_provider="whisper.cpp",
                       stt_api_key="stt-key", tts_api_key="tts-key", tts_provider="openai",
                       profile_output_languages={"__global__": "German", "Work": "French"},
                       profile_stt_languages={"__global__": "de", "Work": "fr"},
                       profile_auto_speak={"__global__": False, "Work": True})
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        overlay.events.speak.disconnect(overlay._start_speak)
        spoken = []
        overlay.events.speak.connect(lambda *args: spoken.append(args))
        try:
            with (patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                  patch("voxcomm.app.whisper_cpp.transcribe", return_value="Bonjour") as transcribe,
                  patch("voxcomm.app.chat", return_value="Bonjour.") as chat):
                overlay._capture(threading.Event(), "Work")
                assert transcribe.call_args.kwargs["api_key"] == "stt-key"
                assert transcribe.call_args.args[-1] == "fr"
                assert "VERBINDLICHE ANTWORTSPRACHE: French" in chat.call_args.kwargs["system_prompt"]
                assert chat.call_args.kwargs["reply_language"] == "French"
                assert spoken[-1][3] == "French"
                overlay._capture(threading.Event(), "")
                assert "VERBINDLICHE ANTWORTSPRACHE: German" in chat.call_args.kwargs["system_prompt"]
                assert len(spoken) == 1
            with patch("voxcomm.app.openai_tts.speak_stream", return_value=iter([])) as speak:
                list(overlay._speech_chunks(["Bonjour"], "voice", "French"))
                speak.assert_called_once_with("Bonjour", config.tts_url, "voice", "French", api_key="tts-key")
            with patch("voxcomm.app.openai_tts.speak_stream", return_value=iter([])) as speak:
                list(overlay._speech_chunks(["Hallo"], "voice", "Deutsch"))
                speak.assert_called_once_with("Hallo", config.tts_url, "voice", "German", api_key="tts-key")
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()


def test_capture_resolves_invalid_profiles_and_keeps_language_helper():
    from voxcomm.app import Overlay

    app = QApplication.instance() or QApplication([])
    config = AppConfig(
        global_prompt="Base instructions",
        special_prompts={"Work": "Work instructions"},
        active_special_prompt="Work",
        profile_models={"__global__": "global-model", "Work": "work-model"},
        profile_voices={"__global__": "global-voice", "Work": "work-voice"},
        profile_thinking={"__global__": "low", "Work": "high"},
        enabled_tools={"__global__": ["web_search"], "Work": ["input_control"]},
        input_action_catalogs={"__global__": "global actions", "Work": "work actions"},
    )
    with patch("voxcomm.app.load_config", return_value=config), patch("voxcomm.app.PttListener"):
        overlay = Overlay()
        try:
            with (
                patch("voxcomm.app.record_until_released", return_value=b"\0" * 9600),
                patch("voxcomm.app.transcribe_sync", return_value="Hello"),
                patch("voxcomm.app.chat", return_value="Answer") as chat,
                patch.object(overlay, "_response_language_for_profile", return_value="French") as language,
            ):
                for requested_profile in ("", "Work", "Missing"):
                    overlay._capture(threading.Event(), requested_profile)

            assert language.call_args_list == [
                call("__global__", "global-voice"), call("Work", "work-voice"),
                call("__global__", "global-voice"),
            ]
            global_call, work_call, invalid_call = chat.call_args_list
            assert global_call.args == invalid_call.args
            for field in ("enabled_tools", "input_actions", "thinking", "reply_language"):
                assert global_call.kwargs[field] == invalid_call.kwargs[field]
            assert global_call.args[2] == "global-model"
            assert global_call.kwargs["enabled_tools"] == ["web_search"]
            assert global_call.kwargs["input_actions"] == "global actions"
            assert global_call.kwargs["thinking"] == "low"
            assert "Base instructions" in global_call.kwargs["system_prompt"]
            assert "Work instructions" not in global_call.kwargs["system_prompt"]
            assert "Base instructions" in invalid_call.kwargs["system_prompt"]
            assert "Work instructions" not in invalid_call.kwargs["system_prompt"]
            assert work_call.args[2] == "work-model"
            assert work_call.kwargs["enabled_tools"] == ["input_control"]
            assert work_call.kwargs["input_actions"] == "work actions"
            assert work_call.kwargs["thinking"] == "high"
            assert "Base instructions" not in work_call.kwargs["system_prompt"]
            assert "Work instructions" in work_call.kwargs["system_prompt"]
            assert work_call.kwargs["reply_language"] == "French"
        finally:
            overlay.listener.stop()
            overlay.tray_icon.hide()
            overlay.deleteLater()