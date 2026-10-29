import tempfile
import unittest
import wave
from pathlib import Path

from voxcomm.audio import save_wav


class AudioTests(unittest.TestCase):
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