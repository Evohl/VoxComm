import json
import unittest
import threading
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import httpx

from voxcomm.providers.llm import (
    _THINKING_CACHE, _dated_search_query, _request_stream, _thinking_values, chat, list_models,
    thinking_options, thinking_request_value,
)


class FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield from self._lines


def _ndjson(*chunks):
    return [json.dumps({"message": chunk, "done": False}) for chunk in chunks]


class StreamingTests(unittest.TestCase):
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_ollama_stream_reports_each_delta_and_returns_joined_message(self, stream):
        stream.return_value = FakeStreamResponse(_ndjson({"content": "Hal"}, {"content": "lo"}, {"content": ""}))
        deltas = []

        message = _request_stream("ollama", "http://h:11434", "m", [], on_text=deltas.append)

        self.assertEqual(deltas, ["Hal", "lo"])
        self.assertEqual(message, {"role": "assistant", "content": "Hallo"})
        payload = stream.call_args.kwargs["json"]
        self.assertTrue(payload["stream"])
        self.assertEqual(stream.call_args.args[:2], ("POST", "http://h:11434/api/chat"))

    @patch("voxcomm.providers.llm.httpx.stream")
    def test_ollama_stream_collects_tool_calls(self, stream):
        call = {"function": {"name": "web_search", "arguments": {"query": "x"}}}
        stream.return_value = FakeStreamResponse(_ndjson({"content": ""}, {"tool_calls": [call]}))

        message = _request_stream("ollama", "http://h", "m", [], [{"name": "web_search"}])

        self.assertEqual(message["tool_calls"], [call])

    @patch("voxcomm.providers.llm.httpx.stream")
    def test_llama_cpp_sse_text_and_fragmented_tool_calls(self, stream):
        events = [
            {"choices": [{"delta": {"content": "A"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "input_control", "arguments": "{\"act"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ion\": \"On\"}"}}]}}]},
        ]
        lines = [f"data: {json.dumps(event)}" for event in events] + ["", "data: [DONE]"]
        stream.return_value = FakeStreamResponse(lines)
        deltas = []

        message = _request_stream("llama.cpp", "http://h:8080", "m", [], on_text=deltas.append)

        self.assertEqual(deltas, ["A"])
        self.assertEqual(message["tool_calls"], [{
            "id": "c1", "type": "function",
            "function": {"name": "input_control", "arguments": "{\"action\": \"On\"}"}}])
        self.assertEqual(stream.call_args.args[1], "http://h:8080/v1/chat/completions")

    @patch("voxcomm.providers.llm.httpx.stream")
    def test_cancelled_stream_stops_reading(self, stream):
        cancel_event = threading.Event()
        deltas = []

        def on_text(delta):
            deltas.append(delta)
            cancel_event.set()

        stream.return_value = FakeStreamResponse(_ndjson({"content": "a"}, {"content": "b"}, {"content": "c"}))

        _request_stream("ollama", "http://h", "m", [], on_text=on_text, cancel_event=cancel_event)

        self.assertEqual(deltas, ["a"])

    @patch("voxcomm.providers.llm.httpx.post")
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_chat_streams_when_listener_given_and_web_search_is_off(self, stream, post):
        stream.return_value = FakeStreamResponse(_ndjson({"content": "Guten "}, {"content": "Tag"}))
        deltas = []

        answer = chat("ollama", "http://h:11434", "m", "Hi", on_text=deltas.append)

        self.assertEqual(answer, "Guten Tag")
        self.assertEqual(deltas, ["Guten ", "Tag"])
        post.assert_not_called()

    @patch("voxcomm.providers.llm.httpx.post")
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_chat_does_not_stream_when_web_search_could_rewrite_the_answer(self, stream, post):
        post.return_value = Mock(json=Mock(return_value={"message": {"content": "Antwort"}}))
        deltas = []

        answer = chat("ollama", "http://h:11434", "m", "Hi", enabled_tools=["web_search"], on_text=deltas.append)

        self.assertEqual(answer, "Antwort")
        self.assertEqual(deltas, [])
        stream.assert_not_called()

    @patch("voxcomm.providers.llm.press_gamepad_button", return_value="Button3")
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_streamed_tool_call_is_executed_and_followed_by_a_streamed_reply(self, stream, press):
        call = {"function": {"name": "input_control", "arguments": {"action": "Gear On"}}}
        stream.side_effect = [
            FakeStreamResponse(_ndjson({"tool_calls": [call]})),
            FakeStreamResponse(_ndjson({"content": "Fahrwerk ausgefahren."})),
        ]
        deltas = []

        answer = chat(
            "ollama", "http://h:11434", "m", "Bitte das Fahrwerk ausfahren, wenn m\u00f6glich",
            enabled_tools=["input_control"],
            input_actions="Gear On => gear down => Button3 => Gear extended\nGear Off => gear up => Button4 => Gear retracted",
            on_text=deltas.append,
        )

        self.assertEqual(answer, "Fahrwerk ausgefahren.")
        self.assertEqual(deltas, ["Fahrwerk ausgefahren."])
        press.assert_called_once_with("Button3")


class ThinkingTests(unittest.TestCase):
    def setUp(self):
        _THINKING_CACHE.clear()

    def test_boolean_models_offer_off_and_on(self):
        self.assertEqual(_thinking_values({"thinking": {"values": [False, True], "default": True}}), ["off", "on"])

    def test_leveled_models_offer_their_own_levels(self):
        info = {"thinking": {"values": ["low", "medium", "high"], "default": "medium"}}

        self.assertEqual(_thinking_values(info), ["low", "medium", "high"])

    def test_models_without_thinking_controls_offer_nothing(self):
        self.assertEqual(_thinking_values({"thinking": None, "capabilities": ["completion"]}), [])
        self.assertEqual(_thinking_values({"thinking": {"values": [False]}}), [])
        self.assertEqual(_thinking_values({}), [])

    def test_older_servers_with_only_a_thinking_capability_offer_off_and_on(self):
        self.assertEqual(_thinking_values({"capabilities": ["completion", "thinking"]}), ["off", "on"])

    def test_request_value_maps_setting_and_ignores_unknown_ones(self):
        self.assertIs(thinking_request_value("off", ["off", "on"]), False)
        self.assertIs(thinking_request_value("on", ["off", "on"]), True)
        self.assertEqual(thinking_request_value("high", ["low", "medium", "high"]), "high")
        self.assertIsNone(thinking_request_value("high", ["off", "on"]))
        self.assertIsNone(thinking_request_value("off", []))

    @patch("voxcomm.providers.llm.httpx.post")
    def test_options_come_from_show_and_are_cached_per_model(self, post):
        post.return_value = Mock(json=Mock(return_value={"thinking": {"values": ["low", "high"]}}))

        first = thinking_options("ollama", "http://h:11434/", " gpt-oss ")
        second = thinking_options("ollama", "http://h:11434", "gpt-oss")

        self.assertEqual((first, second), (["low", "high"], ["low", "high"]))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "http://h:11434/api/show")
        self.assertEqual(post.call_args.kwargs["json"], {"model": "gpt-oss"})

    @patch("voxcomm.providers.llm.httpx.post", side_effect=httpx.ConnectError("down"))
    def test_unreachable_server_yields_no_options_and_is_not_cached(self, post):
        self.assertEqual(thinking_options("ollama", "http://h", "m"), [])
        self.assertEqual(thinking_options("ollama", "http://h", "m"), [])
        self.assertEqual(post.call_count, 2)

    @patch("voxcomm.providers.llm.httpx.post")
    def test_other_providers_and_blank_models_are_not_queried(self, post):
        self.assertEqual(thinking_options("llama.cpp", "http://h", "m"), [])
        self.assertEqual(thinking_options("ollama", "http://h", "  "), [])
        post.assert_not_called()

    @patch("voxcomm.providers.llm.thinking_options", return_value=["off", "on"])
    @patch("voxcomm.providers.llm.httpx.post")
    def test_chat_sends_think_false_when_off_is_selected(self, post, _options):
        post.return_value = Mock(json=Mock(return_value={"message": {"content": "Hi"}}))

        chat("ollama", "http://h:11434", "m", "Hallo", thinking="off")

        self.assertIs(post.call_args.kwargs["json"]["think"], False)

    @patch("voxcomm.providers.llm.thinking_options", return_value=["low", "medium", "high"])
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_streaming_chat_sends_the_selected_level(self, stream, _options):
        stream.return_value = FakeStreamResponse(_ndjson({"content": "Hi"}))

        chat("ollama", "http://h:11434", "m", "Hallo", thinking="low", on_text=lambda _: None)

        self.assertEqual(stream.call_args.kwargs["json"]["think"], "low")

    @patch("voxcomm.providers.llm.thinking_options", return_value=["off", "on"])
    @patch("voxcomm.providers.llm.httpx.post")
    def test_setting_unknown_to_the_model_is_not_sent(self, post, _options):
        post.return_value = Mock(json=Mock(return_value={"message": {"content": "Hi"}}))

        chat("ollama", "http://h:11434", "m", "Hallo", thinking="high")

        self.assertNotIn("think", post.call_args.kwargs["json"])

    @patch("voxcomm.providers.llm.thinking_options")
    @patch("voxcomm.providers.llm.httpx.post")
    def test_default_setting_does_not_query_or_send_anything(self, post, options):
        post.return_value = Mock(json=Mock(return_value={"message": {"content": "Hi"}}))

        chat("ollama", "http://h:11434", "m", "Hallo")

        options.assert_not_called()
        self.assertNotIn("think", post.call_args.kwargs["json"])


class ModelDiscoveryTests(unittest.TestCase):
    @patch("voxcomm.providers.llm._request_stream")
    @patch("voxcomm.providers.llm.search_web")
    def test_cancelled_model_request_does_not_run_tools_after_return(self, search, request):
        cancel_event = threading.Event()

        def finish_request(*_args, **_kwargs):
            cancel_event.set()
            return {"tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "topic"}}}]}

        request.side_effect = finish_request

        answer = chat(
            "ollama", "http://localhost:11434", "model", "Find this",
            enabled_tools=["web_search"], cancel_event=cancel_event,
        )

        self.assertEqual(answer, "")
        request.assert_called_once()
        search.assert_not_called()

    @patch("voxcomm.providers.llm.httpx.post")
    @patch("voxcomm.providers.llm.httpx.stream")
    def test_web_search_profile_uses_cancellable_stream_without_live_text(self, stream, post):
        stream.return_value = FakeStreamResponse(_ndjson({"content": "Antwort"}))
        deltas = []

        answer = chat(
            "ollama", "http://h:11434", "m", "Hi", enabled_tools=["web_search"],
            on_text=deltas.append, cancel_event=threading.Event(),
        )

        self.assertEqual(answer, "Antwort")
        self.assertEqual(deltas, [])
        post.assert_not_called()

    @patch("voxcomm.providers.llm.httpx.post")
    def test_chat_sends_system_prompt_before_user_message(self, post):
        response = Mock()
        response.json.return_value = {"message": {"content": "Hello"}}
        post.return_value = response

        answer = chat(
            "ollama", "http://localhost:11434", "local-model", "Hi",
            system_prompt="Linux copilot system prompt",
        )

        self.assertEqual(answer, "Hello")
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "Linux copilot system prompt"})
        self.assertEqual(messages[1], {"role": "user", "content": "Hi"})

    @patch("voxcomm.providers.llm.search_web", return_value=[
        {"title": "Result", "url": "https://example.com", "snippet": "Found it"},
    ])
    @patch("voxcomm.providers.llm.httpx.post")
    def test_ollama_web_tool_round_trip(self, post, search):
        tool_response = Mock()
        tool_response.json.return_value = {"message": {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "latest"}}}],
        }}
        final_response = Mock()
        final_response.json.return_value = {"message": {"content": "Here is the result"}}
        post.side_effect = [tool_response, final_response]

        answer = chat("ollama", "http://localhost:11434", "model", "Find this", enabled_tools=["web_search"])

        self.assertEqual(answer, "Here is the result")
        search.assert_called_once_with("latest")
        first_payload = post.call_args_list[0].kwargs["json"]
        self.assertEqual(first_payload["tools"][0]["function"]["name"], "web_search")
        second_messages = post.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("https://example.com", second_messages[-1]["content"])

    @patch("voxcomm.providers.llm.search_web")
    @patch("voxcomm.providers.llm.httpx.post")
    def test_llama_cpp_web_tool_round_trip(self, post, search):
        search.return_value = []
        tool_response = Mock()
        tool_response.json.return_value = {"choices": [{"message": {
            "tool_calls": [{"id": "call-1", "function": {"name": "web_search", "arguments": '{"query":"topic"}'}}],
        }}]}
        final_response = Mock()
        final_response.json.return_value = {"choices": [{"message": {"content": "Done"}}]}
        post.side_effect = [tool_response, final_response]

        answer = chat("llama.cpp", "http://localhost:8080/v1", "model", "Search", enabled_tools=("web_search",))

        self.assertEqual(answer, "Done")
        search.assert_called_once_with("topic")
        request = post.call_args_list[0].kwargs["json"]
        self.assertEqual(request["tools"][0]["function"]["name"], "web_search")
        self.assertEqual(request["tool_choice"], "auto")
        tool_message = post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call-1")

    @patch("voxcomm.providers.llm.httpx.post")
    def test_disabled_web_tool_is_not_offered_to_model(self, post):
        response = Mock()
        response.json.return_value = {"message": {"content": "No search"}}
        post.return_value = response

        chat("ollama", "http://localhost:11434", "model", "Hi")

        self.assertNotIn("tools", post.call_args.kwargs["json"])

    @patch("voxcomm.providers.llm.press_gamepad_button", return_value="Button12")
    @patch("voxcomm.providers.llm._request")
    def test_input_control_tool_executes_once_and_returns_result_for_confirmation(self, request, press):
        request.side_effect = [
            {"tool_calls": [{"function": {"name": "input_control", "arguments": {"action": "Landing Gear On"}}}]},
            {"content": "OK, Landing Gear toggled."},
        ]

        answer = chat("ollama", "http://localhost:11434", "model", "Toggle landing gear",
                      enabled_tools=["input_control"],
                      input_actions=(
                          "Landing Gear On => landing gear on => Button12 => Landing gear on\n"
                          "Landing Gear Off => landing gear off => Button13 => Landing gear off"
                      ))

        self.assertEqual(answer, "OK, Landing Gear toggled.")
        press.assert_called_once_with("Button12")
        first_tools = request.call_args_list[0].args[4]
        self.assertEqual([tool["name"] for tool in first_tools], ["input_control"])
        self.assertEqual(list(first_tools[0]["parameters"]["properties"]), ["action"])
        self.assertEqual(first_tools[0]["parameters"]["properties"]["action"]["enum"], ["Landing Gear On", "Landing Gear Off"])
        self.assertIsNone(request.call_args_list[1].args[4])
        self.assertIn('"ok": true', request.call_args_list[1].args[3][-1]["content"])
        system_message = request.call_args_list[0].args[3][0]["content"]
        self.assertIn("Never send keyboard input", system_message)

    @patch("voxcomm.providers.llm.press_gamepad_button", return_value="Button1")
    @patch("voxcomm.providers.llm._request")
    def test_catalog_action_executes_without_model_round_trip(self, request, press):
        answer = chat(
            "ollama", "http://localhost:11434", "unused-model", "Flight ready bitte",
            enabled_tools=["input_control"],
            input_actions=(
                "Flight Systems On => flight ready|systeme ein => Button1 => Flight Systems eingeschaltet\n"
                "Flight Systems Off => flight systems off|systeme aus => Button2 => Flight Systems ausgeschaltet"
            ),
        )

        self.assertEqual(answer, "Flight Systems eingeschaltet (Button1 gesendet).")
        press.assert_called_once_with("Button1")
        request.assert_not_called()

    @patch("voxcomm.providers.llm.press_gamepad_button")
    @patch("voxcomm.providers.llm._request", return_value={"content": "Erklaerung"})
    def test_catalog_does_not_execute_questions(self, request, press):
        chat(
            "ollama", "http://localhost:11434", "model", "Wie schalte ich Flight ready?",
            enabled_tools=["input_control"],
            input_actions=(
                "Flight Systems On => flight ready => Button1 => enabled\n"
                "Flight Systems Off => flight systems off => Button2 => disabled"
            ),
        )

        press.assert_not_called()
        request.assert_called_once()

    @patch("voxcomm.providers.llm.press_gamepad_button")
    @patch("voxcomm.providers.llm._request", return_value={"tool_calls": [{
        "function": {"name": "input_control", "arguments": {"keys": ["X"]}},
    }]})
    def test_disabled_input_control_tool_is_rejected_if_model_requests_it(self, request, press):
        answer = chat("ollama", "http://localhost:11434", "model", "Hello")

        self.assertEqual(answer, "The model returned an empty response.")
        press.assert_not_called()

    @patch("voxcomm.providers.llm.press_gamepad_button")
    @patch("voxcomm.providers.llm._request", return_value={"tool_calls": [{
        "function": {"name": "input_control", "arguments": {"keys": ["X"]}},
    }]})
    def test_legacy_keyboard_arguments_do_not_trigger_controller_input(self, request, press):
        chat(
            "ollama", "http://localhost:11434", "model", "Perform the requested action",
            enabled_tools=["input_control"],
            input_actions=(
                "Power On => power on => Button5 => powered on\n"
                "Power Off => power off => Button6 => powered off"
            ),
        )

        press.assert_not_called()
        self.assertEqual(request.call_count, 2)

    @patch("voxcomm.providers.llm.press_gamepad_button", return_value="Button5")
    @patch("voxcomm.providers.llm._request")
    def test_multiple_input_calls_in_one_response_execute_only_the_first(self, request, press):
        request.side_effect = [
            {"tool_calls": [
                {"function": {"name": "input_control", "arguments": {"action": "Power On"}}},
                {"function": {"name": "input_control", "arguments": {"action": "Power Off"}}},
            ]},
            {"content": "Done"},
        ]

        answer = chat(
            "ollama", "http://localhost:11434", "model", "Please do the system change",
            enabled_tools=["input_control"],
            input_actions=(
                "Power On => turn power on => Button5 => powered on\n"
                "Power Off => turn power off => Button6 => powered off"
            ),
        )

        self.assertEqual(answer, "Done")
        press.assert_called_once_with("Button5")
        self.assertEqual(request.call_count, 2)

    def test_yesterday_query_preserves_user_domain_and_resolves_relative_date(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        query = _dated_search_query("Hallo, wie hat der FCK gestern gespielt?", now)

        self.assertIn("FCK", query)
        self.assertIn("28.10.2026", query)
        self.assertNotIn("29.10.2026", query)

    @patch("voxcomm.providers.llm.search_web")
    @patch("voxcomm.providers.llm._request", return_value={"content": "No live access"})
    def test_time_sensitive_prompt_does_not_search_when_permission_is_disabled(self, request, search):
        answer = chat("ollama", "http://localhost:11434", "model", "Wie hat der FCK gestern gespielt?")

        search.assert_not_called()
        self.assertEqual(answer, "No live access")

    def test_weather_tomorrow_query_resolves_relative_date(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        query = _dated_search_query("What's the weather in Berlin tomorrow?", now)

        self.assertIn("weather in Berlin", query)
        self.assertIn("30.10.2026", query)
        self.assertNotIn("29.10.2026", query)

    def test_headlines_today_query_gets_local_date(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        query = _dated_search_query("Was sind die Schlagzeilen des Tages?", now)

        self.assertIn("Schlagzeilen des Tages", query)
        self.assertIn("29.10.2026", query)

    @patch("voxcomm.providers.llm.search_web", return_value=[
        {"title": "Useful result", "url": "https://example.com/info", "snippet": "Relevant information."},
    ])
    @patch("voxcomm.providers.llm._request", side_effect=[
        {"content": "I don't know the answer."},
        {"content": "Here is what I found."},
    ])
    def test_unknown_answer_searches_and_retries_with_results(self, request, search):
        answer = chat(
            "ollama", "http://localhost:11434", "model", "Who designed this obscure monument?",
            enabled_tools=["web_search"],
        )

        self.assertEqual(answer, "Here is what I found.")
        search.assert_called_once()
        self.assertIn("Who designed this obscure monument?", search.call_args.args[0])
        retry_messages = request.call_args_list[1].args[3]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertIn("https://example.com/info", retry_messages[-1]["content"])

    @patch("voxcomm.providers.llm.search_web")
    @patch("voxcomm.providers.llm._request", return_value={"content": "The answer is 42."})
    def test_known_answer_does_not_trigger_fallback_search(self, request, search):
        answer = chat(
            "ollama", "http://localhost:11434", "model", "What is six times seven?",
            enabled_tools=["web_search"],
        )

        self.assertEqual(answer, "The answer is 42.")
        search.assert_not_called()

    @patch("voxcomm.providers.llm.search_web", return_value=[])
    @patch("voxcomm.providers.llm._request")
    def test_tool_call_limit_bounds_repeated_model_requests(self, request, search):
        repeated_call = {"tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "topic"}}}]}
        request.side_effect = [repeated_call] * 4

        answer = chat("ollama", "http://localhost:11434", "model", "Search", enabled_tools=["web_search"])

        self.assertEqual(answer, "Web search stopped after the tool-call limit.")
        self.assertEqual(search.call_count, 3)
        self.assertEqual(request.call_count, 4)
        self.assertIsNone(request.call_args_list[-1].args[4])

    @patch("voxcomm.providers.llm.httpx.get")
    def test_ollama_models_are_sorted(self, get):
        response = Mock()
        response.json.return_value = {"models": [{"name": "z-model"}, {"name": "a-model"}]}
        get.return_value = response

        models = list_models("ollama", "http://localhost:11434/")

        self.assertEqual(models, ["a-model", "z-model"])
        get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)
        response.raise_for_status.assert_called_once_with()

    @patch("voxcomm.providers.llm.httpx.get")
    def test_llama_cpp_models_accept_v1_base_url(self, get):
        response = Mock()
        response.json.return_value = {"data": [{"id": "local-model"}]}
        get.return_value = response

        models = list_models("llama.cpp", "http://localhost:8080/v1/")

        self.assertEqual(models, ["local-model"])
        get.assert_called_once_with("http://localhost:8080/v1/models", timeout=5)


class OllamaCloudTests(unittest.TestCase):
    @patch("voxcomm.providers.llm.list_cloud_models", return_value=["b", "a"])
    def test_cloud_model_list_uses_cloud_client(self, listing):
        self.assertEqual(list_models("ollama-cloud", "https://ollama.com"), ["a", "b"])
        listing.assert_called_once_with("https://ollama.com")

    @patch("voxcomm.providers.llm.httpx.post")
    def test_cloud_chat_posts_bearer_key_without_streaming(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"message": {"content": "OK"}}

        post.return_value = response

        answer = chat("ollama-cloud", "https://ollama.com/", "gpt-oss:20b", "hi", api_key="k")

        self.assertEqual(answer, "OK")
        self.assertEqual(post.call_args.args[0], "https://ollama.com/api/chat")
        self.assertIs(post.call_args.kwargs["json"]["stream"], False)
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer k"})

    @patch("voxcomm.providers.llm.httpx.post")
    def test_rejected_cloud_key_becomes_plain_answer(self, post):
        post.return_value = Mock(status_code=401)

        answer = chat("ollama-cloud", "https://ollama.com", "gpt-oss:20b", "hi", api_key="bad")

        self.assertIn("API-Schlüssel", answer)
        self.assertIn("ungültig", answer)

    def test_empty_cloud_url_falls_back_to_public_base(self):
        with patch("voxcomm.providers.cloud.httpx.post") as post:
            response = Mock(status_code=200)
            response.json.return_value = {"message": {"content": "OK"}}
            post.return_value = response

            chat("ollama-cloud", "", "gpt-oss:20b", "hi", api_key="k")

        self.assertEqual(post.call_args.args[0], "https://ollama.com/api/chat")

    @patch("voxcomm.providers.llm.search_web")
    @patch("voxcomm.providers.llm.cloud_web_search", return_value=[])
    @patch("voxcomm.providers.llm.httpx.post")
    def test_cloud_tool_search_uses_cloud_endpoint(self, chat_post, cloud_search, ddgs):
        tool_response = Mock(status_code=200)
        tool_response.json.return_value = {"message": {
            "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "thema"}}}],
        }}
        final_response = Mock(status_code=200)
        final_response.json.return_value = {"message": {"content": "Antwort"}}
        chat_post.side_effect = [tool_response, final_response]

        chat("ollama-cloud", "https://ollama.com", "gpt-oss:20b", "Suche etwas",
             enabled_tools=["web_search"], api_key="k")

        ddgs.assert_not_called()
        cloud_search.assert_called_once_with("thema", "https://ollama.com", "k")

    @patch("voxcomm.providers.llm.httpx.post")
    def test_cloud_tool_message_omits_tool_call_id(self, post):
        tool_response = Mock(status_code=200)
        tool_response.json.return_value = {"message": {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "topic"}}}],
        }}
        final_response = Mock(status_code=200)
        final_response.json.return_value = {"message": {"content": "Fertig"}}
        post.side_effect = [tool_response, final_response]

        with patch("voxcomm.providers.llm.cloud_web_search", return_value=[
            {"title": "T", "url": "https://example.com", "snippet": "S"},
        ]):
            answer = chat("ollama-cloud", "https://ollama.com", "gpt-oss:20b", "Suche",
                          enabled_tools=["web_search"], api_key="k")

        self.assertEqual(answer, "Fertig")
        tool_message = post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertNotIn("tool_call_id", tool_message)


if __name__ == "__main__":
    unittest.main()