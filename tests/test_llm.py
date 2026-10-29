import unittest
from unittest.mock import Mock, patch

from sc_ai_copilot.providers.llm import chat, list_models


class ModelDiscoveryTests(unittest.TestCase):
    @patch("sc_ai_copilot.providers.llm.httpx.post")
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

    @patch("sc_ai_copilot.providers.llm.httpx.get")
    def test_ollama_models_are_sorted(self, get):
        response = Mock()
        response.json.return_value = {"models": [{"name": "z-model"}, {"name": "a-model"}]}
        get.return_value = response

        models = list_models("ollama", "http://localhost:11434/")

        self.assertEqual(models, ["a-model", "z-model"])
        get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)
        response.raise_for_status.assert_called_once_with()

    @patch("sc_ai_copilot.providers.llm.httpx.get")
    def test_llama_cpp_models_accept_v1_base_url(self, get):
        response = Mock()
        response.json.return_value = {"data": [{"id": "local-model"}]}
        get.return_value = response

        models = list_models("llama.cpp", "http://localhost:8080/v1/")

        self.assertEqual(models, ["local-model"])
        get.assert_called_once_with("http://localhost:8080/v1/models", timeout=5)


if __name__ == "__main__":
    unittest.main()