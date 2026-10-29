import unittest
from unittest.mock import Mock, patch
from ddgs.exceptions import DDGSException

from voxcomm.providers.web import MAX_QUERY_LENGTH, search_web


class WebSearchTests(unittest.TestCase):
    @patch("voxcomm.providers.web.DDGS")
    def test_search_uses_bounded_safe_search_and_normalizes_results(self, ddgs):
        ddgs.return_value.text.return_value = [
            {"title": "Result", "href": "https://example.com", "body": "Snippet"},
            {"title": "Unsafe", "href": "file:///etc/passwd", "body": "Do not include"},
        ]

        results = search_web("  latest news  ")

        self.assertEqual(results, [{"title": "Result", "url": "https://example.com", "snippet": "Snippet"}])
        self.assertEqual(ddgs.call_count, 2)
        self.assertEqual(ddgs.call_args_list[0].args, ())
        self.assertEqual([call.kwargs for call in ddgs.call_args_list], [{"timeout": 8}, {"timeout": 8}])
        self.assertEqual(
            [call.kwargs["backend"] for call in ddgs.return_value.text.call_args_list],
            ["bing", "google"],
        )
        self.assertTrue(all(call.args == ("latest news",) for call in ddgs.return_value.text.call_args_list))

    @patch("voxcomm.providers.web.DDGS")
    def test_search_uses_results_when_another_backend_returns_no_results(self, ddgs):
        failing_backend = Mock()
        failing_backend.text.side_effect = DDGSException("No results found.")
        working_backend = Mock()
        working_backend.text.return_value = [
            {"title": "Headline", "href": "https://example.com/news", "body": "News snippet"},
        ]
        ddgs.side_effect = [failing_backend, working_backend]

        results = search_web("today's headlines")

        self.assertEqual(results[0]["title"], "Headline")
        self.assertEqual(ddgs.call_count, 2)

    @patch("voxcomm.providers.web.DDGS")
    def test_search_rejects_empty_and_overlong_queries(self, ddgs):
        with self.assertRaises(ValueError):
            search_web("  ")
        with self.assertRaises(ValueError):
            search_web("q" * (MAX_QUERY_LENGTH + 1))
        ddgs.assert_not_called()


if __name__ == "__main__":
    unittest.main()