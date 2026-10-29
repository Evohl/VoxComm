from __future__ import annotations

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


def play_pcm(audio: bytes, sample_rate: int, cancel_event=None) -> None:
    samples = np.frombuffer(audio, dtype=np.int16)
    if not samples.size or (cancel_event is not None and cancel_event.is_set()):
        return

    stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16")
    try:
        stream.start()
        chunk_size = max(1, sample_rate // 50)
        for offset in range(0, samples.size, chunk_size):
            if cancel_event is not None and cancel_event.is_set():
                break
            stream.write(samples[offset:offset + chunk_size])
    finally:
        if cancel_event is not None and cancel_event.is_set():
            stream.abort()
        else:
            stream.stop()
        stream.close()
