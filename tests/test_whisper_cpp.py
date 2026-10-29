import io
import unittest
import wave
from unittest.mock import Mock, patch

from voxcomm.providers import whisper_cpp


class InferenceUrlTests(unittest.TestCase):
    def test_base_url_appends_inference_route(self):
        self.assertEqual(whisper_cpp.inference_url("http://127.0.0.1:8081/"), "http://127.0.0.1:8081/inference")

    def test_full_transcription_url_is_used_verbatim(self):
        url = "http://127.0.0.1:8081/v1/audio/transcriptions"
        self.assertEqual(whisper_cpp.inference_url(url), url)

    def test_inference_url_is_not_doubled(self):
        url = "http://127.0.0.1:8081/inference"
        self.assertEqual(whisper_cpp.inference_url(url), url)


class TranscribeTests(unittest.TestCase):
    def test_empty_audio_is_not_sent(self):
        with patch("voxcomm.providers.whisper_cpp.httpx.post") as post:
            self.assertEqual(whisper_cpp.transcribe(b"", "http://127.0.0.1:8081"), "")
        post.assert_not_called()

    @patch("voxcomm.providers.whisper_cpp.httpx.post")
    def test_audio_is_wrapped_as_wav_and_language_forwarded(self, post):
        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.json.return_value = {"text": "hallo"}
        post.return_value = response
        pcm = b"\x01\x02" * 800

        text = whisper_cpp.transcribe(pcm, "http://127.0.0.1:8081", 16000, "de")

        self.assertEqual(text, "hallo")
        upload = post.call_args.kwargs["files"]["file"]
        with wave.open(io.BytesIO(upload[1]), "rb") as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)
        self.assertEqual(post.call_args.kwargs["data"]["language"], "de")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8081/inference")

    @patch("voxcomm.providers.whisper_cpp.httpx.post")
    def test_auto_language_is_forwarded(self, post):
        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.json.return_value = {"text": "x"}
        post.return_value = response

        whisper_cpp.transcribe(b"\x00\x00", "http://127.0.0.1:8081", 16000, "auto")

        self.assertEqual(post.call_args.kwargs["data"]["language"], "auto")

    @patch("voxcomm.providers.whisper_cpp.httpx.post")
    def test_empty_language_is_omitted(self, post):
        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.json.return_value = {"text": "x"}
        post.return_value = response

        whisper_cpp.transcribe(b"\x00\x00", "http://127.0.0.1:8081", 16000, "  ")

        self.assertNotIn("language", post.call_args.kwargs["data"])

    @patch("voxcomm.providers.whisper_cpp.httpx.post")
    def test_plain_text_body_is_returned_stripped(self, post):
        post.return_value = Mock(status_code=200, headers={"content-type": "text/html"}, text="  erkannt  ")

        self.assertEqual(whisper_cpp.transcribe(b"\x00\x00", "http://127.0.0.1:8081"), "erkannt")

    @patch("voxcomm.providers.whisper_cpp.httpx.post")
    def test_server_error_raises_runtime_error(self, post):
        post.return_value = Mock(status_code=500, text="boom")

        with self.assertRaises(RuntimeError) as raised:
            whisper_cpp.transcribe(b"\x00\x00", "http://127.0.0.1:8081")

        self.assertIn("whisper.cpp HTTP 500", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
