from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd
import wave


def record_until_released(is_pressed, sample_rate: int = 16000) -> bytes:
    chunks: list[bytes] = []

    def collect(indata, _frames, _time, _status):
        chunks.append(indata.copy().tobytes())

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", callback=collect):
        while is_pressed():
            sd.sleep(40)
    if not chunks:
        return b""
    return np.frombuffer(b"".join(chunks), dtype=np.int16).tobytes()


def save_wav(audio: bytes, path: str, sample_rate: int = 16000) -> None:
    with wave.open(path, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(sample_rate)
        recording.writeframes(audio)


def prefetch(chunks, cancel_event=None, depth: int = 64):
    """Erzeugt die Chunks in einem Hintergrund-Thread, damit Synthese und Wiedergabe sich überlappen."""
    buffer: queue.Queue = queue.Queue(maxsize=depth)
    stop = threading.Event()
    done = object()

    def stopped() -> bool:
        return stop.is_set() or (cancel_event is not None and cancel_event.is_set())

    def put(item) -> bool:
        while not stopped():
            try:
                buffer.put(item, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def produce() -> None:
        try:
            for chunk in chunks:
                if not put(chunk):
                    return
            put(done)
        except Exception as error:
            put(error)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()

    threading.Thread(target=produce, daemon=True).start()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                item = buffer.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()


def play_stream(chunks, cancel_event=None) -> None:
    """Spielt (PCM, Samplerate)-Chunks ab dem ersten ab; der Ausgabestream wird erst dann geöffnet."""

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    stream = None
    pending = b""
    try:
        for audio, sample_rate in chunks:
            if cancelled():
                break
            pending += audio
            usable = len(pending) // 2 * 2
            samples = np.frombuffer(pending[:usable], dtype=np.int16)
            pending = pending[usable:]
            if not samples.size:
                continue
            if stream is None:
                stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16")
                stream.start()
            slice_size = max(1, sample_rate // 50)
            for offset in range(0, samples.size, slice_size):
                if cancelled():
                    break
                stream.write(samples[offset:offset + slice_size])
    finally:
        if stream is not None:
            if cancelled():
                stream.abort()
            else:
                stream.stop()
            stream.close()
