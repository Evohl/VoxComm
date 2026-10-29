import io
import unittest
import wave
from unittest.mock import Mock, patch

from voxcomm.providers import openai_tts


def _wav_bytes(frames: bytes, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return buffer.getvalue()


class UrlTests(unittest.TestCase):
    def test_routes_are_appended_to_base_url(self):
        self.assertEqual(openai_tts.speech_url("http://127.0.0.1:8097"), "http://127.0.0.1:8097/v1/audio/speech")
        self.assertEqual(openai_tts.voices_url("http://127.0.0.1:8097/"), "http://127.0.0.1:8097/v1/audio/voices")

    def test_full_routes_are_used_verbatim(self):
        speech = "http://h/v1/audio/speech"
        self.assertEqual(openai_tts.speech_url(speech), speech)


class VoiceListTests(unittest.TestCase):
    @patch("voxcomm.providers.openai_tts.httpx.get")
    def test_qwentts_voice_shape_is_parsed(self, get):
        response = Mock()
        response.json.return_value = {"voices": [{"name": "data", "kind": "speaker"}]}
        get.return_value = response

        self.assertEqual(openai_tts.list_voices("http://127.0.0.1:8097"), ["data"])
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8097/v1/audio/voices")

    @patch("voxcomm.providers.openai_tts.httpx.get")
    def test_plain_string_list_is_parsed(self, get):
        response = Mock()
        response.json.return_value = {"voices": ["Zeta", "alpha"]}
        get.return_value = response

        self.assertEqual(openai_tts.list_voices("http://127.0.0.1:8097"), ["alpha", "Zeta"])

    @patch("voxcomm.providers.openai_tts.httpx.get")
    def test_openai_data_shape_is_parsed(self, get):
        response = Mock()
        response.json.return_value = {"data": [{"id": "alloy"}]}
        get.return_value = response

        self.assertEqual(openai_tts.list_voices("http://127.0.0.1:8097"), ["alloy"])

    @patch("voxcomm.providers.openai_tts.httpx.get")
    def test_unknown_shape_yields_no_voices(self, get):
        response = Mock()
        response.json.return_value = {"unexpected": True}
        get.return_value = response

        self.assertEqual(openai_tts.list_voices("http://127.0.0.1:8097"), [])


class SpeechTests(unittest.TestCase):
    def test_blank_text_is_not_sent(self):
        with patch("voxcomm.providers.openai_tts.httpx.post") as post:
            self.assertEqual(openai_tts.speak_sync("   ", "http://127.0.0.1:8097"), (b"", 0))
        post.assert_not_called()

    @patch("voxcomm.providers.openai_tts.httpx.post")
    def test_wav_response_is_decoded_and_payload_complete(self, post):
        frames = b"\x10\x20" * 800
        post.return_value = Mock(status_code=200, content=_wav_bytes(frames, 16000))

        audio, sample_rate = openai_tts.speak_sync("Hallo", "http://127.0.0.1:8097", "data", "German")

        self.assertEqual(audio, frames)
        self.assertEqual(sample_rate, 16000)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["voice"], "data")
        self.assertEqual(payload["language"], "German")
        self.assertEqual(payload["response_format"], "wav")

    @patch("voxcomm.providers.openai_tts.httpx.post")
    def test_non_wav_body_raises(self, post):
        post.return_value = Mock(status_code=200, content=b"not a riff file")

        with self.assertRaises(RuntimeError) as raised:
            openai_tts.speak_sync("Hallo", "http://127.0.0.1:8097")

        self.assertIn("WAV", str(raised.exception))

    @patch("voxcomm.providers.openai_tts.httpx.post")
    def test_server_error_raises_with_status(self, post):
        post.return_value = Mock(status_code=503, text="down")

        with self.assertRaises(RuntimeError) as raised:
            openai_tts.speak_sync("Hallo", "http://127.0.0.1:8097")

        self.assertIn("TTS HTTP 503", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
