from __future__ import annotations

import io
import wave

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


def speak_sync(text: str, url: str, voice: str = "", language: str = "") -> tuple[bytes, int]:
    if not text.strip():
        return b"", 0
    payload = {"input": text, "voice": voice, "response_format": "wav"}
    if language.strip():
        payload["language"] = language
    response = httpx.post(speech_url(url), json=payload, timeout=TTS_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise RuntimeError(f"TTS HTTP {response.status_code}: {response.text[:300]}")
    try:
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except (wave.Error, EOFError) as error:
        raise RuntimeError("TTS service did not return WAV audio") from error
