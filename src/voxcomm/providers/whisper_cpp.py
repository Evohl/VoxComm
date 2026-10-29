from __future__ import annotations

import io
import wave

import httpx


STT_TIMEOUT_SECONDS = 180


def inference_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/inference") or base.endswith("/audio/transcriptions"):
        return base
    return f"{base}/inference"


def transcribe(audio: bytes, url: str, sample_rate: int = 16000, language: str = "de") -> str:
    if not audio:
        return ""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)
    data = {"response_format": "json", "temperature": "0.0"}
    if language.strip():
        data["language"] = language
    response = httpx.post(
        inference_url(url),
        files={"file": ("audio.wav", buffer.getvalue(), "audio/wav")},
        data=data,
        timeout=STT_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"whisper.cpp HTTP {response.status_code}: {response.text[:300]}")
    if response.headers.get("content-type", "").startswith("application/json"):
        return str(response.json().get("text", "")).strip()
    return response.text.strip()
