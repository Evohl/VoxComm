from __future__ import annotations

import httpx


TTS_TIMEOUT_SECONDS = 180


def speech_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1/audio/speech"):
        return base
    return f"{base}/v1/audio/speech"


def voices_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1/audio/voices"):
        return base
    return f"{base}/v1/audio/voices"


def _names(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    voices = payload.get("voices")
    if isinstance(voices, list):
        names = []
        for entry in voices:
            if isinstance(entry, dict):
                name = entry.get("name")
            elif isinstance(entry, str):
                name = entry
            else:
                name = None
            if isinstance(name, str) and name:
                names.append(name)
        return names
    data = payload.get("data")
    if isinstance(data, list):
        return [entry["id"] for entry in data
                if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]]
    return []


def list_voices(url: str) -> list[str]:
    response = httpx.get(voices_url(url), timeout=20)
    response.raise_for_status()
    return sorted({name for name in _names(response.json())}, key=str.casefold)


def _wav_header(buffer: bytes) -> tuple[int, int] | None:
    """(Samplerate, Offset der Audiodaten) oder None, solange der Header noch unvollständig ist."""
    if len(buffer) < 12:
        return None
    if buffer[:4] != b"RIFF" or buffer[8:12] != b"WAVE":
        raise RuntimeError("TTS service did not return WAV audio")
    position, sample_rate = 12, 0
    while len(buffer) >= position + 8:
        chunk_id = buffer[position:position + 4]
        size = int.from_bytes(buffer[position + 4:position + 8], "little")
        body = position + 8
        if chunk_id == b"data":
            if not sample_rate:
                raise RuntimeError("TTS service did not return WAV audio")
            return sample_rate, body
        if size > 1 << 20:
            raise RuntimeError("TTS service did not return WAV audio")
        if len(buffer) < body + size:
            return None
        if chunk_id == b"fmt ":
            sample_rate = int.from_bytes(buffer[body + 4:body + 8], "little")
        position = body + size + (size & 1)
    return None


def speak_stream(text: str, url: str, voice: str = "", language: str = ""):
    """Liefert (PCM, Samplerate)-Chunks, sobald der Server WAV-Daten sendet."""
    if not text.strip():
        return
    payload = {"input": text, "voice": voice, "response_format": "wav"}
    if language.strip():
        payload["language"] = language
    with httpx.stream("POST", speech_url(url), json=payload, timeout=TTS_TIMEOUT_SECONDS) as response:
        if response.status_code >= 400:
            response.read()
            raise RuntimeError(f"TTS HTTP {response.status_code}: {response.text[:300]}")
        buffer, sample_rate = b"", 0
        for chunk in response.iter_bytes():
            if sample_rate:
                yield chunk, sample_rate
                continue
            buffer += chunk
            header = _wav_header(buffer)
            if header is not None:
                sample_rate, offset = header
                if buffer[offset:]:
                    yield buffer[offset:], sample_rate
        if not sample_rate:
            raise RuntimeError("TTS service did not return WAV audio")
