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


class FakeResponse:
    def __init__(self, chunks, status_code=200, text=""):
        self._chunks = chunks
        self.status_code = status_code
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        pass

    def iter_bytes(self):
        yield from self._chunks


def _split(data: bytes, size: int) -> list[bytes]:
    return [data[index:index + size] for index in range(0, len(data), size)]


class SpeechTests(unittest.TestCase):
    def test_blank_text_is_not_sent(self):
        with patch("voxcomm.providers.openai_tts.httpx.stream") as stream:
            self.assertEqual(list(openai_tts.speak_stream("   ", "http://127.0.0.1:8097")), [])
        stream.assert_not_called()

    @patch("voxcomm.providers.openai_tts.httpx.stream")
    def test_wav_stream_yields_pcm_with_sample_rate_and_payload_complete(self, stream):
        frames = b"\x10\x20" * 800
        stream.return_value = FakeResponse([_wav_bytes(frames, 16000)])

        chunks = list(openai_tts.speak_stream("Hallo", "http://127.0.0.1:8097", "data", "German"))

        self.assertEqual(b"".join(audio for audio, _ in chunks), frames)
        self.assertEqual({rate for _, rate in chunks}, {16000})
        payload = stream.call_args.kwargs["json"]
        self.assertEqual(payload["voice"], "data")
        self.assertEqual(payload["language"], "German")
        self.assertEqual(payload["response_format"], "wav")

    @patch("voxcomm.providers.openai_tts.httpx.stream")
    def test_header_split_across_network_chunks_is_reassembled(self, stream):
        frames = bytes(range(200))
        stream.return_value = FakeResponse(_split(_wav_bytes(frames, 24000), 7))

        chunks = list(openai_tts.speak_stream("Hallo", "http://127.0.0.1:8097"))

        self.assertEqual(b"".join(audio for audio, _ in chunks), frames)
        self.assertEqual({rate for _, rate in chunks}, {24000})

    @patch("voxcomm.providers.openai_tts.httpx.stream")
    def test_first_audio_is_yielded_before_the_body_ends(self, stream):
        wav = _wav_bytes(b"\x01\x00" * 400, 16000)
        pulled = []

        class Lazy(FakeResponse):
            def iter_bytes(self):
                for chunk in (wav[:60], wav[60:]):
                    pulled.append(len(chunk))
                    yield chunk

        stream.return_value = Lazy([])
        iterator = openai_tts.speak_stream("Hallo", "http://127.0.0.1:8097")

        next(iterator)
        self.assertEqual(pulled, [60])

    @patch("voxcomm.providers.openai_tts.httpx.stream")
    def test_non_wav_body_raises(self, stream):
        stream.return_value = FakeResponse([b"not a riff file"])

        with self.assertRaises(RuntimeError) as raised:
            list(openai_tts.speak_stream("Hallo", "http://127.0.0.1:8097"))

        self.assertIn("WAV", str(raised.exception))

    @patch("voxcomm.providers.openai_tts.httpx.stream")
    def test_server_error_raises_with_status(self, stream):
        stream.return_value = FakeResponse([], status_code=503, text="down")

        with self.assertRaises(RuntimeError) as raised:
            list(openai_tts.speak_stream("Hallo", "http://127.0.0.1:8097"))

        self.assertIn("TTS HTTP 503", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
