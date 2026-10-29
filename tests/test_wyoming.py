import asyncio
import json
import unittest

from voxcomm.providers.wyoming import (
    STT_CHUNK_BYTES,
    _address,
    _voice_names,
    describe,
    filter_voices,
    speak,
    transcribe,
)


async def receive_event(reader):
    header = json.loads(await reader.readline())
    data_size = int(header.get("data_length", 0))
    if data_size:
        header["data"] = json.loads(await reader.readexactly(data_size))
    size = int(header.get("payload_length", 0))
    payload = await reader.readexactly(size) if size else b""
    return header, payload


async def send_event(writer, event, payload=b""):
    if payload:
        event["payload_length"] = len(payload)
    writer.write(json.dumps(event).encode() + b"\n")
    if payload:
        writer.write(payload)
    await writer.drain()


class WyomingTests(unittest.IsolatedAsyncioTestCase):
    def test_filter_voices_by_language(self):
        voices = ["de_DE-thorsten-medium", "en_US-amy-medium", "de_DE-eva_k-x_low"]

        self.assertEqual(filter_voices(voices, "de"), ["de_DE-thorsten-medium", "de_DE-eva_k-x_low"])
        self.assertEqual(filter_voices(voices, ""), voices)

    def test_address_rejects_non_numeric_port_with_example(self):
        with self.assertRaisesRegex(ValueError, "tcp://127.0.0.1:10300"):
            _address("tcp://local-AI:not-a-port")

    def test_address_rejects_missing_or_out_of_range_port(self):
        for address in ("tcp://local-AI", "tcp://local-AI:70000"):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, "1 und 65535"):
                _address(address)

    async def test_list_voices_reads_wyoming_info(self):
        async def handler(reader, writer):
            event, _ = await receive_event(reader)
            self.assertEqual(event["type"], "describe")
            data = {"tts": [{"name": "piper", "voices": [{"name": "en_US-amy"}, {"name": "de_DE-thorsten"}]}]}
            data_bytes = json.dumps(data).encode()
            writer.write(json.dumps({"type": "info", "data_length": len(data_bytes)}).encode() + b"\n")
            writer.write(data_bytes)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertEqual(_voice_names(await describe(f"tcp://127.0.0.1:{port}")), ["de_DE-thorsten", "en_US-amy"])
        finally:
            server.close()
            await server.wait_closed()

    async def test_transcribe_reads_string_transcript(self):
        async def handler(reader, writer):
            request, _ = await receive_event(reader)
            self.assertEqual((request["type"], request["data"]), ("transcribe", {"language": "de"}))
            audio_start, _ = await receive_event(reader)
            self.assertEqual(audio_start["type"], "audio-start")
            self.assertEqual(audio_start["data"], {"rate": 16000, "width": 2, "channels": 1})
            audio_chunk, audio = await receive_event(reader)
            self.assertEqual(audio_chunk["type"], "audio-chunk")
            self.assertEqual(audio, b"pcm")
            stop, _ = await receive_event(reader)
            self.assertEqual(stop["type"], "audio-stop")
            await send_event(writer, {"type": "transcript", "data": {"text": "Shield status"}})
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertEqual(await transcribe(b"pcm", f"tcp://127.0.0.1:{port}"), "Shield status")
        finally:
            server.close()
            await server.wait_closed()

    async def test_transcribe_splits_audio_into_small_chunks(self):
        pcm = bytes(range(256)) * 40
        received = []

        async def handler(reader, writer):
            await receive_event(reader)
            await receive_event(reader)
            while True:
                event, payload = await receive_event(reader)
                if event["type"] == "audio-stop":
                    break
                received.append(payload)
            await send_event(writer, {"type": "transcript", "data": {"text": "ok"}})
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertEqual(await transcribe(pcm, f"tcp://127.0.0.1:{port}"), "ok")
        finally:
            server.close()
            await server.wait_closed()
        self.assertGreater(len(received), 1)
        self.assertTrue(all(len(chunk) <= STT_CHUNK_BYTES for chunk in received))
        self.assertEqual(b"".join(received), pcm)

    async def test_speak_uses_service_sample_rate(self):
        async def handler(reader, writer):
            event, _ = await receive_event(reader)
            self.assertEqual(event["type"], "synthesize")
            await send_event(writer, {"type": "audio-start", "data": {"rate": 22050}})
            await send_event(writer, {"type": "audio-chunk"}, b"pcm")
            await send_event(writer, {"type": "audio-stop"})
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertEqual(await speak("Hello", f"tcp://127.0.0.1:{port}"), (b"pcm", 22050))
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
