import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voxcomm.audio import play_pcm, save_wav


class AudioTests(unittest.TestCase):
    def test_play_pcm_aborts_its_owned_stream_when_cancelled(self):
        cancel_event = threading.Event()
        streams = []

        class FakeStream:
            def __init__(self, **_kwargs):
                self.writes = 0
                self.aborted = False
                self.stopped = False
                self.closed = False
                streams.append(self)

            def start(self):
                pass

            def write(self, _samples):
                self.writes += 1
                cancel_event.set()

            def abort(self):
                self.aborted = True

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        with patch("voxcomm.audio.sd.OutputStream", FakeStream):
            play_pcm(b"\x01\x00" * 1600, 16000, cancel_event)

        stream = streams[0]
        self.assertEqual(stream.writes, 1)
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)
        self.assertTrue(stream.closed)

    def test_save_wav_preserves_mono_pcm(self):
        audio = b"\x01\x00\x02\x00"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.wav"
            save_wav(audio, str(path), 16000)

            with wave.open(str(path), "rb") as recording:
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(recording.getsampwidth(), 2)
                self.assertEqual(recording.getframerate(), 16000)
                self.assertEqual(recording.readframes(recording.getnframes()), audio)


if __name__ == "__main__":
    unittest.main()