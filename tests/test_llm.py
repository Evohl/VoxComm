import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from voxcomm.providers.llm import _dated_search_query, chat, list_models


class ModelDiscoveryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()