from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

# Ein einzelner großer Chunk führt bei whisper.cpp zu wiederholten/abgebrochenen Satzanfängen.
STT_CHUNK_BYTES = 2048


def _address(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Der Wyoming-Port muss eine Zahl zwischen 1 und 65535 sein, z. B. tcp://127.0.0.1:10300") from error
    if parsed.scheme != "tcp" or not parsed.hostname or port is None or not 1 <= port <= 65535:
        raise ValueError("Wyoming-Adresse erwartet tcp://host:port mit einem Port zwischen 1 und 65535, z. B. tcp://127.0.0.1:10300")
    return parsed.hostname, port


async def _send(writer: asyncio.StreamWriter, event: dict, payload: bytes = b"") -> None:
    data = event.pop("data", None)
    data_bytes = json.dumps(data, separators=(",", ":")).encode() if data else b""
    if data_bytes:
        event["data_length"] = len(data_bytes)
    if payload:
        event["payload_length"] = len(payload)
    writer.write(json.dumps(event, separators=(",", ":")).encode() + b"\n")
    if data_bytes:
        writer.write(data_bytes)
    if payload:
        writer.write(payload)
    await writer.drain()


async def _receive(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    header = await reader.readline()
    if not header:
        raise ConnectionError("Wyoming service closed the connection")
    event = json.loads(header)
    data_length = int(event.get("data_length", 0))
    if data_length:
        data_bytes = await reader.readexactly(data_length)
        event.setdefault("data", {}).update(json.loads(data_bytes))
    payload_length = int(event.get("payload_length", 0))
    payload = await reader.readexactly(payload_length) if payload_length else b""
    return event, payload


def _voice_names(info: dict) -> list[str]:
    names: list[str] = []

    def collect(value):
        if isinstance(value, dict):
            for tts_service in value.get("tts", []):
                if isinstance(tts_service, dict):
                    for voice in tts_service.get("voices", []):
                        name = voice.get("name")
                        if name:
                            names.append(str(name))
            if value.get("type") == "tts":
                for voice in value.get("voices", []):
                    name = voice.get("name")
                    if name:
                        names.append(str(name))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(info.get("data", {}))
    return sorted(set(names), key=str.casefold)


def voice_language(voice_name: str) -> str:
    return voice_name.split("-", 1)[0].split("_", 1)[0].lower()


def filter_voices(voices: list[str], language: str) -> list[str]:
    if not language:
        return voices
    return [voice for voice in voices if voice_language(voice) == language.lower()]


async def describe(url: str) -> dict:
    host, port = _address(url)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    try:
        await _send(writer, {"type": "describe"})
        while True:
            event, _ = await asyncio.wait_for(_receive(reader), timeout=10)
            if event.get("type") == "info":
                return event
            if event.get("type") == "error":
                raise RuntimeError(event.get("data", {}).get("text", "Wyoming describe failed"))
    finally:
        writer.close()
        await writer.wait_closed()


def list_voices(url: str) -> list[str]:
    return _voice_names(asyncio.run(describe(url)))


async def transcribe(audio: bytes, url: str, sample_rate: int = 16000, language: str = "de") -> str:
    host, port = _address(url)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    try:
        await _send(writer, {"type": "transcribe", "data": {"language": language}})
        await _send(writer, {"type": "audio-start", "data": {"rate": sample_rate, "width": 2, "channels": 1}})
        for start in range(0, len(audio), STT_CHUNK_BYTES):
            chunk = audio[start:start + STT_CHUNK_BYTES]
            await _send(writer, {"type": "audio-chunk", "data": {"rate": sample_rate, "width": 2, "channels": 1}}, chunk)
        await _send(writer, {"type": "audio-stop"})
        while True:
            event, _ = await asyncio.wait_for(_receive(reader), timeout=60)
            if event.get("type") == "transcript":
                text = event.get("data", {}).get("text", "")
                if isinstance(text, list):
                    text = " ".join(item.get("text", "") for item in text)
                return str(text).strip()
            if event.get("type") == "error":
                raise RuntimeError(event.get("data", {}).get("text", "Wyoming transcription failed"))
    finally:
        writer.close()
        await writer.wait_closed()


async def speak_stream(text: str, url: str, voice: str = ""):
    """Liefert (PCM, Samplerate)-Chunks, sobald der Dienst sie sendet."""
    host, port = _address(url)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    sample_rate = 22050
    try:
        data = {"text": text}
        if voice:
            data["voice"] = {"name": voice}
        await _send(writer, {"type": "synthesize", "data": data})
        while True:
            event, payload = await asyncio.wait_for(_receive(reader), timeout=60)
            if event.get("type") == "audio-chunk":
                yield payload, sample_rate
            elif event.get("type") == "audio-start":
                sample_rate = int(event.get("data", {}).get("rate", sample_rate))
            elif event.get("type") == "audio-stop":
                return
            elif event.get("type") == "error":
                raise RuntimeError(event.get("data", {}).get("text", "Wyoming synthesis failed"))
    finally:
        writer.close()
        await writer.wait_closed()


def transcribe_sync(audio: bytes, url: str, sample_rate: int = 16000, language: str = "de") -> str:
    return asyncio.run(transcribe(audio, url, sample_rate, language))


def speak_stream_sync(text: str, url: str, voice: str = ""):
    loop = asyncio.new_event_loop()
    stream = speak_stream(text, url, voice)
    try:
        while True:
            try:
                yield loop.run_until_complete(stream.__anext__())
            except StopAsyncIteration:
                return
    finally:
        loop.run_until_complete(stream.aclose())
        loop.close()
