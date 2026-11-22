from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import struct
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from livekit.agents import APIConnectOptions, APIError, APIStatusError
from livekit.agents.types import USERDATA_TIMED_TRANSCRIPT
from livekit.plugins import plivo

from .stt_reconnect import wait_until

pytestmark = pytest.mark.unit

AUTH_ID = "MAFAKEAUTHID0000000"
AUTH_TOKEN = "fake-auth-token-do-not-log"
NO_RETRY = APIConnectOptions(max_retry=0, timeout=2.0)
PCM = b"\x01\x00" * 480  # 20 ms at 24 kHz


def _frame(context_id: str, audio: bytes) -> bytes:
    ctx = context_id.encode()
    return struct.pack("<H", len(ctx)) + ctx + audio


class FakeKoyal:
    """Koyal's realtime and batch APIs, as documented: replies are keyed by context_id, the
    first message of a reply carries its options, flush and finish speak buffered text, and
    cancel ends a reply with a `done` whose reason is `cancelled`."""

    def __init__(self, *, chunks: int = 3, error: dict | None = None) -> None:
        self.chunks = chunks
        self.error = error  # sent once instead of audio
        self.connections = 0
        self.received: list[dict] = []
        self.spoken: dict[str, str] = {}  # context_id -> text spoken so far
        self.stale_frames = False  # start each reply with audio from another context

    def _authorized(self, request: web.Request) -> bool:
        expected = base64.b64encode(f"{AUTH_ID}:{AUTH_TOKEN}".encode()).decode()
        return request.headers.get("Authorization") == f"Basic {expected}"

    async def realtime(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        used: set[str] = set()
        buffers: dict[str, str] = {}
        options: dict[str, dict] = {}
        generations: dict[str, asyncio.Task[None]] = {}

        async def speak(
            ctx: str, text: str, *, finish: bool, after: asyncio.Task[None] | None
        ) -> None:
            # a reply's audio is generated in order: `done` comes after everything flushed
            if after is not None:
                with contextlib.suppress(Exception):
                    await after
            if self.error:
                error, self.error = self.error, None
                await ws.send_json({"type": "error", "context_id": ctx, **error})
                return
            if self.stale_frames:
                await ws.send_bytes(_frame("old-reply", b"\xff\x7f" * 480))
                await ws.send_json(
                    {"type": "done", "context_id": "old-reply", "reason": "complete"}
                )
            if text:
                if options[ctx].get("timestamps"):
                    chars = list(text)
                    await ws.send_json(
                        {
                            "type": "timestamps",
                            "context_id": ctx,
                            "chars": chars,
                            "start_ms": [i * 50 for i in range(len(chars))],
                            "duration_ms": [50] * len(chars),
                        }
                    )
                for _ in range(self.chunks):
                    await ws.send_bytes(_frame(ctx, PCM))
                    await asyncio.sleep(0.005)
                self.spoken[ctx] = self.spoken.get(ctx, "") + text
            if finish:
                await ws.send_json({"type": "done", "context_id": ctx, "reason": "complete"})

        async for msg in ws:
            event = json.loads(msg.data)
            self.received.append(event)
            ctx = event.get("context_id", "")
            if event["type"] == "text":
                if ctx not in options:
                    if ctx in used:
                        await ws.send_json(
                            {
                                "type": "error",
                                "context_id": ctx,
                                "code": "invalid_request",
                                "param": "context_id",
                            }
                        )
                        continue
                    used.add(ctx)
                    options[ctx] = event
                    buffers[ctx] = ""
                elif set(event) - {"type", "context_id", "text", "finish"}:
                    await ws.send_json(
                        {"type": "error", "context_id": ctx, "code": "invalid_request"}
                    )
                    continue
                buffers[ctx] += event.get("text", "")
                if event.get("finish"):
                    text, buffers[ctx] = buffers[ctx], ""
                    generations[ctx] = asyncio.create_task(
                        speak(ctx, text, finish=True, after=generations.get(ctx))
                    )
            elif event["type"] == "flush":
                text, buffers[ctx] = buffers.get(ctx, ""), ""
                generations[ctx] = asyncio.create_task(
                    speak(ctx, text, finish=False, after=generations.get(ctx))
                )
            elif event["type"] == "cancel":
                if ctx in options:
                    if (task := generations.get(ctx)) and not task.done():
                        task.cancel()
                    del options[ctx]
                    await ws.send_json({"type": "done", "context_id": ctx, "reason": "cancelled"})
        for task in generations.values():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await asyncio.gather(*generations.values(), return_exceptions=True)
        return ws

    async def batch(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"error": "invalid auth_id or auth_token"}, status=401)
        self.received.append(await request.json())
        resp = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
        await resp.prepare(request)
        for _ in range(self.chunks):
            await resp.write(PCM)
        return resp

    async def voices(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "objects": [
                    {
                        "voice_id": "6uWzc4xFj31t4jNJUaED",
                        "name": "Meera",
                        "gender": "female",
                        "languages": ["hi"],
                        "accent_type": "Indian",
                        "description": "Female Indian voice. Speaks Hindi.",
                    }
                ]
            }
        )


@contextlib.asynccontextmanager
async def serve(fake: FakeKoyal, **tts_kwargs: object) -> AsyncIterator[plivo.TTS]:
    app = web.Application()
    app.router.add_get("/v1/Account/{auth_id}/TTS/Realtime/", fake.realtime)
    app.router.add_post("/v1/Account/{auth_id}/TTS/", fake.batch)
    app.router.add_get("/v1/Account/{auth_id}/TTS/Voices/", fake.voices)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    async with aiohttp.ClientSession() as session:
        tts = plivo.TTS(
            auth_id=AUTH_ID,
            auth_token=tts_kwargs.pop("auth_token", AUTH_TOKEN),  # type: ignore[arg-type]
            base_url=f"http://127.0.0.1:{port}",
            http_session=session,
            **tts_kwargs,  # type: ignore[arg-type]
        )
        try:
            yield tts
        finally:
            await tts.aclose()
            await runner.cleanup()


async def _speak(tts: plivo.TTS, *chunks: str) -> list:
    stream = tts.stream(conn_options=NO_RETRY)
    for chunk in chunks:
        stream.push_text(chunk)
    stream.end_input()
    async with stream:
        return [ev async for ev in stream]


def _texts(fake: FakeKoyal) -> list[dict]:
    return [e for e in fake.received if e["type"] == "text"]


async def test_reply_sends_options_once_and_finishes() -> None:
    fake = FakeKoyal()
    async with serve(fake, voice="voice-1", language="hi-IN", pace=1.1) as tts:
        events = await _speak(tts, "नमस्ते रोहन जी। ", "आपका order confirm है।")
    assert events, "no audio was synthesized"
    first, *rest = _texts(fake)
    assert first["voice_id"] == "voice-1"
    assert first["language"] == "hi"  # region tag dropped for Indic languages
    assert first["pace"] == 1.1
    assert first["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 24000,
    }
    assert "volume" not in first  # unset options are omitted, Koyal rejects nulls
    # later messages only carry text, otherwise Koyal refuses them
    assert all(set(m) <= {"type", "context_id", "text", "finish"} for m in rest)
    assert rest[-1]["finish"] is True
    assert "".join(fake.spoken.values()).split() == "नमस्ते रोहन जी। आपका order confirm है।".split()


async def test_each_sentence_is_flushed_as_it_arrives() -> None:
    fake = FakeKoyal()
    async with serve(fake) as tts:
        stream = tts.stream(conn_options=NO_RETRY)
        stream.push_text("This is the first sentence of the reply. And here is a second ")
        await wait_until(lambda: any(e["type"] == "flush" for e in fake.received))
        stream.push_text("one.")
        stream.end_input()
        async with stream:
            frames = [ev async for ev in stream]
    assert frames
    assert fake.spoken  # the first sentence was spoken before the input ended


async def test_replies_share_one_socket_with_fresh_context_ids() -> None:
    fake = FakeKoyal()
    async with serve(fake) as tts:
        assert await _speak(tts, "Hello there.")
        assert await _speak(tts, "") == []  # an empty reply is silent and sends nothing
        assert await _speak(tts, "Hello again.")
    assert fake.connections == 1
    assert len(fake.spoken) == 2  # two replies, two distinct context ids


async def test_barge_in_cancels_the_reply_and_keeps_the_socket() -> None:
    fake = FakeKoyal(chunks=200)
    async with serve(fake) as tts:
        stream = tts.stream(conn_options=NO_RETRY)
        stream.push_text("A long reply that the user interrupts.")
        stream.end_input()
        await stream.__anext__()
        await stream.aclose()
        fake.chunks = 3
        assert await _speak(tts, "The next reply.")
    cancels = [e for e in fake.received if e["type"] == "cancel"]
    assert len(cancels) == 1
    assert cancels[0]["context_id"] == _texts(fake)[0]["context_id"]
    assert fake.connections == 1


async def test_audio_from_other_contexts_is_dropped() -> None:
    fake = FakeKoyal()
    fake.stale_frames = True
    async with serve(fake) as tts:
        events = await _speak(tts, "Only this reply should play.")
    audio = b"".join(bytes(ev.frame.data) for ev in events)
    assert audio
    assert b"\xff\x7f" not in audio


async def test_word_timestamps_become_timed_transcripts() -> None:
    fake = FakeKoyal()
    async with serve(fake) as tts:
        events = await _speak(tts, "Hello there friend.")
    assert _texts(fake)[0]["timestamps"] is True
    words = [w for ev in events for w in ev.frame.userdata.get(USERDATA_TIMED_TRANSCRIPT, [])]
    assert [str(w).strip() for w in words] == ["Hello", "there", "friend."]
    assert words[1].start_time == pytest.approx(0.3)  # "there" starts at char 6, 50 ms each
    assert words[1].end_time == pytest.approx(0.55)


async def test_timestamps_can_be_turned_off() -> None:
    fake = FakeKoyal()
    async with serve(fake, word_timestamps=False) as tts:
        assert not tts.capabilities.aligned_transcript
        await _speak(tts, "Hello.")
    assert "timestamps" not in _texts(fake)[0]


@pytest.mark.parametrize(
    ("code", "retryable"), [("voice_not_found", False), ("internal_error", True)]
)
async def test_reply_errors_raise_api_error(code: str, retryable: bool) -> None:
    fake = FakeKoyal(error={"code": code, "message": "nope"})
    async with serve(fake) as tts:
        with pytest.raises(APIError) as exc_info:
            await _speak(tts, "Hello.")
    assert code in str(exc_info.value)
    assert exc_info.value.retryable is retryable


async def test_bad_credentials_are_not_leaked() -> None:
    fake = FakeKoyal()
    async with serve(fake, auth_token="wrong-token-do-not-log") as tts:
        with pytest.raises(APIStatusError) as exc_info:
            await _speak(tts, "Hello.")
        assert exc_info.value.status_code == 401
        assert "wrong-token-do-not-log" not in repr(exc_info.value)
        assert exc_info.value.__cause__ is None

        with pytest.raises(APIStatusError) as batch_info:
            await tts.synthesize("Hello.", conn_options=NO_RETRY).collect()
        assert batch_info.value.status_code == 401


async def test_synthesize_uses_the_batch_api() -> None:
    fake = FakeKoyal()
    async with serve(fake, voice="voice-1", sample_rate=16000) as tts:
        frame = await tts.synthesize("Hello there.", conn_options=NO_RETRY).collect()
    assert frame.sample_rate == 16000
    assert len(frame.data) > 0
    request = fake.received[0]
    assert request["text"] == "Hello there."
    assert request["voice_id"] == "voice-1"
    assert request["output_format"]["sample_rate"] == 16000


async def test_list_voices() -> None:
    async with serve(FakeKoyal()) as tts:
        voices = await tts.list_voices()
    assert voices == [
        plivo.Voice(
            id="6uWzc4xFj31t4jNJUaED",
            name="Meera",
            gender="female",
            language="hi",
            accent="Indian",
            description="Female Indian voice. Speaks Hindi.",
        )
    ]


async def test_update_options_applies_to_the_next_reply() -> None:
    fake = FakeKoyal()
    async with serve(fake, voice="voice-1") as tts:
        await _speak(tts, "First.")
        tts.update_options(voice="voice-2", language="en-in", volume=1.5)
        await _speak(tts, "Second.")
    first, second = (m for m in _texts(fake) if "voice_id" in m)
    assert first["voice_id"] == "voice-1"
    assert (second["voice_id"], second["language"], second["volume"]) == ("voice-2", "en-IN", 1.5)


def test_credentials_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLIVO_AUTH_ID", raising=False)
    monkeypatch.delenv("PLIVO_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PLIVO_AUTH_ID"):
        plivo.TTS()

    monkeypatch.setenv("PLIVO_AUTH_ID", AUTH_ID)
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", AUTH_TOKEN)
    tts = plivo.TTS()
    assert (tts.provider, tts.model, tts.sample_rate) == ("Plivo", "koyal", 24000)
