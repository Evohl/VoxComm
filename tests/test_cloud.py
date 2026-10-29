import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from voxcomm.providers import cloud


class CloudModelTests(unittest.TestCase):
    @patch("voxcomm.providers.cloud.httpx.get")
    def test_list_cloud_models_parses_names_and_uses_tags_route(self, get):
        response = Mock()
        response.json.return_value = {"models": [{"name": "a"}, {"name": ""}]}
        get.return_value = response

        models = cloud.list_cloud_models("https://ollama.com/")

        self.assertEqual(models, ["a"])
        self.assertEqual(get.call_args.args[0], "https://ollama.com/api/tags")

    def test_store_and_cache_round_trip_under_xdg_cache_home(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"XDG_CACHE_HOME": directory}):
                before = time.time()
                cloud.store_models(["a", "b"])
                models, fetched_at = cloud.cached_models()

                self.assertEqual(models, ["a", "b"])
                self.assertLess(abs(fetched_at - before), 1.0)
                self.assertEqual(cloud.model_cache_path(), Path(directory) / "voxcomm" / "ollama-cloud-models.json")

    def test_default_cache_path_uses_home_cache(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                cloud.model_cache_path(),
                Path.home() / ".cache" / "voxcomm" / "ollama-cloud-models.json",
            )

    def test_malformed_cache_files_yield_empty_result(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"XDG_CACHE_HOME": directory}):
                path = cloud.model_cache_path()
                path.parent.mkdir(parents=True)
                for payload in ("{", '{"models": "x"}', '{"models": ["a"], "fetched_at": "now"}'):
                    path.write_text(payload, encoding="utf-8")
                    self.assertEqual(cloud.cached_models(), ([], 0.0))


class CloudSearchTests(unittest.TestCase):
    def test_missing_key_raises_without_request(self):
        with patch("voxcomm.providers.cloud.httpx.post") as post:
            with self.assertRaises(cloud.CloudAuthError):
                cloud.web_search("query", "https://ollama.com", "   ")
        post.assert_not_called()

    def test_empty_query_is_rejected(self):
        with self.assertRaises(ValueError):
            cloud.web_search("   ", "https://ollama.com", "k")

    @patch("voxcomm.providers.cloud.httpx.post")
    def test_search_normalizes_and_filters_unsafe_urls(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"results": [
            {"title": "T", "url": "https://e.com", "content": "C"},
            {"title": "Bad", "url": "file:///etc/passwd", "content": "x"},
        ]}
        post.return_value = response

        results = cloud.web_search("ollama cloud models", "https://ollama.com", "k")

        self.assertEqual(results, [{"title": "T", "url": "https://e.com", "snippet": "C"}])
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer k"})
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 5)

    @patch("voxcomm.providers.cloud.httpx.post")
    def test_rejected_key_raises_auth_error(self, post):
        post.return_value = Mock(status_code=401)

        with self.assertRaises(cloud.CloudAuthError):
            cloud.web_search("query", "https://ollama.com", "bad")

    @patch("voxcomm.providers.cloud.httpx.post")
    def test_missing_results_key_yields_empty_list(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"unexpected": []}
        post.return_value = response

        self.assertEqual(cloud.web_search("query", "https://ollama.com", "k"), [])


if __name__ == "__main__":
    unittest.main()
