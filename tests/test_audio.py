import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voxcomm.audio import play_stream, prefetch, save_wav


class FakeStream:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.aborted = self.stopped = self.closed = False
        self.on_write = None
        FakeStream.instances.append(self)

    def start(self):
        pass

    def write(self, samples):
        self.writes.append(bytes(samples.tobytes()))
        if self.on_write:
            self.on_write()

    def abort(self):
        self.aborted = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class AudioTests(unittest.TestCase):
    def setUp(self):
        FakeStream.instances = []

    def test_play_stream_aborts_its_owned_stream_when_cancelled(self):
        cancel_event = threading.Event()

        class CancellingStream(FakeStream):
            def write(self, samples):
                super().write(samples)
                cancel_event.set()

        with patch("voxcomm.audio.sd.OutputStream", CancellingStream):
            play_stream(iter([(b"\x01\x00" * 1600, 16000)]), cancel_event)

        stream = FakeStream.instances[0]
        self.assertEqual(len(stream.writes), 1)
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)
        self.assertTrue(stream.closed)

    def test_play_stream_starts_playing_before_the_source_is_exhausted(self):
        opened_when_second_chunk_requested = []

        def chunks():
            yield b"\x01\x00" * 10, 16000
            opened_when_second_chunk_requested.append(bool(FakeStream.instances and FakeStream.instances[0].writes))
            yield b"\x02\x00" * 10, 16000

        with patch("voxcomm.audio.sd.OutputStream", FakeStream):
            play_stream(chunks())

        self.assertEqual(opened_when_second_chunk_requested, [True])
        stream = FakeStream.instances[0]
        self.assertTrue(stream.stopped and stream.closed and not stream.aborted)
        self.assertEqual(len(FakeStream.instances), 1)

    def test_play_stream_keeps_odd_bytes_for_the_next_chunk(self):
        with patch("voxcomm.audio.sd.OutputStream", FakeStream):
            play_stream(iter([(b"\x01", 16000), (b"\x00\x02\x00", 16000)]))

        self.assertEqual(b"".join(FakeStream.instances[0].writes), b"\x01\x00\x02\x00")

    def test_play_stream_without_audio_never_opens_a_device(self):
        with patch("voxcomm.audio.sd.OutputStream", FakeStream):
            play_stream(iter([(b"", 16000)]))

        self.assertEqual(FakeStream.instances, [])

    def test_prefetch_produces_ahead_and_propagates_errors(self):
        produced = []

        def source():
            for index in range(3):
                produced.append(index)
                yield index
            raise RuntimeError("kaputt")

        iterator = prefetch(source())
        self.assertEqual(next(iterator), 0)
        self.assertEqual(next(iterator), 1)
        self.assertEqual(next(iterator), 2)
        with self.assertRaisesRegex(RuntimeError, "kaputt"):
            next(iterator)

    def test_prefetch_stops_when_cancelled(self):
        cancel_event = threading.Event()
        closed = threading.Event()

        def endless():
            try:
                while True:
                    yield b"x", 16000
            finally:
                closed.set()

        iterator = prefetch(endless(), cancel_event, depth=2)
        next(iterator)
        cancel_event.set()

        self.assertEqual(list(iterator), [])
        self.assertTrue(closed.wait(2))

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