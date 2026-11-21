# Copyright 2026 LiveKit, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import weakref
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from typing import Any

import aiohttp

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIError,
    APIStatusError,
    APITimeoutError,
    tokenize,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given
from livekit.agents.voice.io import TimedString

from .log import logger
from .models import TTSDefaultVoiceId, TTSLanguages

DEFAULT_BASE_URL = "https://api.plivo.com"

# Koyal error codes that will fail the same way on retry.
_NON_RETRYABLE_ERRORS = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "forbidden",
        "voice_not_found",
        "language_not_supported",
        "lexicon_not_found",
        "payment_required",
        "insufficient_credits",
    }
)

# Koyal takes the bare code for Indian languages ("hi"), but region tags for English.
_INDIC_LANGUAGES = frozenset({"hi", "bn", "ta", "te", "mr", "gu", "kn"})


@dataclass
class Voice:
    """A Koyal voice, as returned by :meth:`TTS.list_voices`."""

    id: str
    name: str
    gender: str
    language: str
    accent: str
    description: str


@dataclass
class _TTSOptions:
    auth_id: str
    auth_token: str
    voice: str
    language: str | None
    sample_rate: int
    pace: float | None
    volume: float | None
    pronunciations: list[dict[str, str]] | None
    lexicon_id: str | None
    word_timestamps: bool
    base_url: str

    @property
    def auth_header(self) -> str:
        token = base64.b64encode(f"{self.auth_id}:{self.auth_token}".encode()).decode()
        return f"Basic {token}"

    def http_url(self, path: str) -> str:
        return f"{self.base_url}/v1/Account/{self.auth_id}/TTS/{path}"

    def ws_url(self) -> str:
        return self.http_url("Realtime/").replace("http", "ws", 1)

    def synthesis_options(self) -> dict[str, Any]:
        """Options Koyal accepts on a batch request or the first message of a reply.

        Koyal rejects explicit nulls, so unset options are left out.
        """
        opts: dict[str, Any] = {
            "voice_id": self.voice,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate,
            },
        }
        if self.language:
            opts["language"] = self.language
        if self.pace is not None:
            opts["pace"] = self.pace
        if self.volume is not None:
            opts["volume"] = self.volume
        if self.pronunciations:
            opts["pronunciations"] = self.pronunciations
        if self.lexicon_id:
            opts["lexicon_id"] = self.lexicon_id
        return opts


def _koyal_language(language: str | None) -> str | None:
    if not language:
        return None
    base, _, region = language.partition("-")
    if base.lower() in _INDIC_LANGUAGES:
        return base.lower()
    if base.lower() == "en" and region:
        return f"en-{region.upper()}"
    return language


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        voice: str = TTSDefaultVoiceId,
        language: TTSLanguages | str | None = None,
        sample_rate: int = 24000,
        pace: float | None = None,
        volume: float | None = None,
        pronunciations: list[dict[str, str]] | None = None,
        lexicon_id: str | None = None,
        word_timestamps: bool = True,
        auth_id: str | None = None,
        auth_token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
        tokenizer: NotGivenOr[tokenize.SentenceTokenizer] = NOT_GIVEN,
        text_pacing: tts.SentenceStreamPacer | bool = False,
    ) -> None:
        """Create a new instance of Plivo Koyal TTS.

        Koyal is Plivo's text-to-speech model for Indian languages, English and code-mixed
        speech such as Hinglish. Streaming uses Koyal's realtime WebSocket API
        (https://www.plivo.com/docs/models/text-to-speech/api/realtime); ``synthesize()``
        uses the batch API.

        Args:
            voice (str, optional): Koyal voice ID. Defaults to Meera (female, Hindi). Use
                :meth:`list_voices` or the Plivo console to find others.
            language (str, optional): ``en-US``, ``en-GB``, ``en-IN``, ``en-AU``, ``hi``,
                ``bn``, ``ta``, ``te``, ``mr``, ``gu`` or ``kn``. Each voice speaks one
                language; leave unset to use the voice's own. ``hi-IN`` style tags are
                accepted and converted.
            sample_rate (int, optional): Output sample rate in Hz, 8000 to 48000.
                Defaults to 24000.
            pace (float, optional): Speaking speed, 0.7 to 1.2. Koyal's default is 1.0.
            volume (float, optional): Loudness, 0.5 to 2.0. Koyal's default is 1.0.
            pronunciations (list[dict], optional): Up to 100 inline rules such as
                ``{"word": "Plivo", "value": "plee-voh", "kind": "respell"}``.
            lexicon_id (str, optional): ID of a saved Koyal lexicon.
            word_timestamps (bool, optional): Request per-word timings so transcripts stay in
                sync with the audio. Defaults to True.
            auth_id (str, optional): Plivo Auth ID. Defaults to ``PLIVO_AUTH_ID``.
            auth_token (str, optional): Plivo Auth Token. Defaults to ``PLIVO_AUTH_TOKEN``.
            base_url (str, optional): Plivo API base URL. Defaults to ``https://api.plivo.com``.
            http_session (aiohttp.ClientSession, optional): Session to reuse for requests.
            tokenizer (tokenize.SentenceTokenizer, optional): Splits streamed text into
                sentences before it is sent. Defaults to the blingfire tokenizer, which
                handles the Devanagari danda (।).
            text_pacing (tts.SentenceStreamPacer | bool, optional): Pace text sent to Koyal
                to the audio already generated. Defaults to False.
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True, aligned_transcript=word_timestamps),
            sample_rate=sample_rate,
            num_channels=1,
        )

        plivo_auth_id = auth_id or os.environ.get("PLIVO_AUTH_ID")
        plivo_auth_token = auth_token or os.environ.get("PLIVO_AUTH_TOKEN")
        if not plivo_auth_id or not plivo_auth_token:
            raise ValueError(
                "Plivo credentials are required, either as arguments or set the"
                " PLIVO_AUTH_ID and PLIVO_AUTH_TOKEN environment variables"
            )

        self._opts = _TTSOptions(
            auth_id=plivo_auth_id,
            auth_token=plivo_auth_token,
            voice=voice,
            language=_koyal_language(language),
            sample_rate=sample_rate,
            pace=pace,
            volume=volume,
            pronunciations=pronunciations,
            lexicon_id=lexicon_id,
            word_timestamps=word_timestamps,
            base_url=base_url.rstrip("/"),
        )
        self._session = http_session
        self._pool = utils.ConnectionPool[aiohttp.ClientWebSocketResponse](
            connect_cb=self._connect_ws,
            close_cb=self._close_ws,
            max_session_duration=300,
            mark_refreshed_on_get=True,
        )
        self._streams = weakref.WeakSet[SynthesizeStream]()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sentence_tokenizer = (
            tokenizer if is_given(tokenizer) else tokenize.blingfire.SentenceTokenizer()
        )
        self._stream_pacer: tts.SentenceStreamPacer | None = None
        if text_pacing is True:
            self._stream_pacer = tts.SentenceStreamPacer()
        elif isinstance(text_pacing, tts.SentenceStreamPacer):
            self._stream_pacer = text_pacing

    @property
    def model(self) -> str:
        return "koyal"

    @property
    def provider(self) -> str:
        return "Plivo"

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        session = self._ensure_session()
        try:
            return await asyncio.wait_for(
                session.ws_connect(
                    self._opts.ws_url(), headers={"Authorization": self._opts.auth_header}
                ),
                timeout,
            )
        except asyncio.TimeoutError:
            raise APITimeoutError() from None
        except aiohttp.ClientResponseError as e:
            # the handshake error keeps the request headers, which carry the credentials.
            raise APIStatusError(
                message=e.message, status_code=e.status, request_id=None, body=None
            ) from None
        except Exception as e:
            # the URL contains the Auth ID, so don't chain the original error.
            raise _connection_error(e) from None

    async def _close_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await ws.close()

    def _recycle(self, ws: aiohttp.ClientWebSocketResponse, context_id: str) -> None:
        """Cancel an interrupted reply so Koyal stops generating it, then return the socket.

        Audio already in flight for the cancelled reply is dropped by the next reply, which only
        plays frames tagged with its own context_id.
        """

        async def _cancel() -> None:
            try:
                await ws.send_str(json.dumps({"type": "cancel", "context_id": context_id}))
            except Exception:
                self._drop(ws)
            else:
                self._pool.put(ws)

        self._spawn(_cancel())

    def _drop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Take a socket out of the pool and close it."""
        self._pool.remove(ws)
        self._spawn(self._close_ws(ws))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def prewarm(self) -> None:
        self._pool.prewarm()

    def update_options(
        self,
        *,
        voice: NotGivenOr[str] = NOT_GIVEN,
        language: NotGivenOr[TTSLanguages | str | None] = NOT_GIVEN,
        pace: NotGivenOr[float | None] = NOT_GIVEN,
        volume: NotGivenOr[float | None] = NOT_GIVEN,
        pronunciations: NotGivenOr[list[dict[str, str]] | None] = NOT_GIVEN,
        lexicon_id: NotGivenOr[str | None] = NOT_GIVEN,
    ) -> None:
        """Update synthesis options. Changes apply to the next reply, not one in progress.

        Args:
            voice (str, optional): Koyal voice ID.
            language (str, optional): Language code, or None for the voice's own.
            pace (float, optional): Speaking speed, 0.7 to 1.2.
            volume (float, optional): Loudness, 0.5 to 2.0.
            pronunciations (list[dict], optional): Inline pronunciation rules.
            lexicon_id (str, optional): ID of a saved Koyal lexicon.
        """
        if is_given(voice):
            self._opts.voice = voice
        if is_given(language):
            self._opts.language = _koyal_language(language)
        if is_given(pace):
            self._opts.pace = pace
        if is_given(volume):
            self._opts.volume = volume
        if is_given(pronunciations):
            self._opts.pronunciations = pronunciations
        if is_given(lexicon_id):
            self._opts.lexicon_id = lexicon_id

    async def list_voices(self) -> list[Voice]:
        """List the Koyal voices available to this Plivo account."""
        try:
            async with self._ensure_session().get(
                self._opts.http_url("Voices/"),
                headers={"Authorization": self._opts.auth_header},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise APIStatusError(
                        message=str(body.get("error", "failed to list voices")),
                        status_code=resp.status,
                        request_id=body.get("api_id"),
                        body=body,
                    )
        except APIError:
            raise
        except asyncio.TimeoutError:
            raise APITimeoutError() from None
        except Exception as e:
            raise _connection_error(e) from None

        return [
            Voice(
                id=v["voice_id"],
                name=v.get("name", ""),
                gender=v.get("gender", ""),
                language=(v.get("languages") or [""])[0],
                accent=v.get("accent_type", ""),
                description=v.get("description", ""),
            )
            for v in body.get("objects", [])
        ]

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        stream = SynthesizeStream(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        for stream in list(self._streams):
            await stream.aclose()
        self._streams.clear()
        await utils.aio.cancel_and_wait(*self._tasks)
        await self._pool.aclose()


class ChunkedStream(tts.ChunkedStream):
    """Synthesize a complete text with Koyal's batch API."""

    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        payload = self._opts.synthesis_options()
        payload["text"] = self._input_text

        try:
            async with self._tts._ensure_session().post(
                self._opts.http_url(""),
                headers={"Authorization": self._opts.auth_header},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30, sock_connect=self._conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.json(content_type=None)
                    raise _status_error(resp.status, body)

                output_emitter.initialize(
                    request_id=resp.headers.get("X-Request-Id") or utils.shortuuid(),
                    sample_rate=self._opts.sample_rate,
                    num_channels=1,
                    mime_type="audio/pcm",
                )
                async for data, _ in resp.content.iter_chunks():
                    output_emitter.push(data)
                output_emitter.flush()
        except APIError:
            raise
        except asyncio.TimeoutError:
            raise APITimeoutError() from None
        except Exception as e:
            raise _connection_error(e) from None


class SynthesizeStream(tts.SynthesizeStream):
    """Stream text to Koyal's realtime API. Each stream is one Koyal reply (context)."""

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        context_id = utils.shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )

        sent_tokenizer_stream = self._tts._sentence_tokenizer.stream()
        if self._tts._stream_pacer:
            sent_tokenizer_stream = self._tts._stream_pacer.wrap(
                sent_stream=sent_tokenizer_stream, audio_emitter=output_emitter
            )

        opened = False  # the first message of a reply carries its options
        started = asyncio.Event()  # first text sent, or the input ended empty
        input_done = asyncio.Event()  # all text sent, including finish
        reply_done = False

        def text_msg(text: str, *, finish: bool = False) -> str:
            nonlocal opened
            msg: dict[str, Any] = {"type": "text", "context_id": context_id, "text": text}
            if finish:
                msg["finish"] = True
            if not opened:
                msg.update(self._opts.synthesis_options())
                if self._opts.word_timestamps:
                    msg["timestamps"] = True
                opened = True
                started.set()
            return json.dumps(msg)

        async def input_task() -> None:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    sent_tokenizer_stream.flush()
                    continue
                sent_tokenizer_stream.push_text(data)
            sent_tokenizer_stream.end_input()

        async def send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            async for ev in sent_tokenizer_stream:
                self._mark_started()
                await ws.send_str(text_msg(ev.token + " "))
                # Koyal holds text until it sees the end of a sentence. The tokenizer already
                # emits whole sentences, so ask Koyal to speak each one as soon as it arrives;
                # this also covers a final fragment that has no closing punctuation.
                await ws.send_str(json.dumps({"type": "flush", "context_id": context_id}))
            if opened:
                await ws.send_str(text_msg("", finish=True))
            input_done.set()
            started.set()

        async def recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            nonlocal reply_done
            await started.wait()
            if not opened:
                return  # the stream had no text, so there is no reply to wait for

            segment_started = False
            while True:
                try:
                    msg = await ws.receive(timeout=self._conn_options.timeout)
                except asyncio.TimeoutError:
                    # Waiting on the LLM for more text is fine; once all text is sent,
                    # Koyal has to keep audio flowing.
                    if input_done.is_set():
                        raise APITimeoutError() from None
                    continue

                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    raise APIStatusError(
                        "Koyal connection closed unexpectedly",
                        status_code=ws.close_code or -1,
                        request_id=request_id,
                        body=None,
                    )

                if msg.type == aiohttp.WSMsgType.BINARY:
                    data: bytes = msg.data
                    if len(data) < 2:
                        continue
                    (id_len,) = struct.unpack_from("<H", data, 0)
                    # a pooled socket can still carry audio from a cancelled reply
                    if data[2 : 2 + id_len].decode("utf-8", "replace") != context_id:
                        continue
                    if not segment_started:
                        output_emitter.start_segment(segment_id=context_id)
                        segment_started = True
                    output_emitter.push(data[2 + id_len :])
                    continue

                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                event = json.loads(msg.data)
                if event.get("context_id") not in (None, context_id):
                    continue
                kind = event.get("type")
                if kind == "timestamps":
                    # timings for a sentence can arrive before its first audio frame
                    if not segment_started:
                        output_emitter.start_segment(segment_id=context_id)
                        segment_started = True
                    output_emitter.push_timed_transcript(_words(event))
                elif kind == "done":
                    reply_done = True
                    output_emitter.end_input()
                    return
                elif kind == "error":
                    if event.get("context_id") == context_id:
                        reply_done = True  # Koyal has already closed this reply
                    raise _koyal_error(event)
                elif kind == "warning":
                    logger.warning(
                        "Koyal warning",
                        extra={"code": event.get("code"), "message": event.get("message")},
                    )

        pool = self._tts._pool
        ws: aiohttp.ClientWebSocketResponse | None = None
        try:
            ws = await pool.get(timeout=self._conn_options.timeout)
            self._acquire_time = pool.last_acquire_time
            self._connection_reused = pool.last_connection_reused
            tasks = [
                asyncio.create_task(input_task()),
                asyncio.create_task(send_task(ws)),
                asyncio.create_task(recv_task(ws)),
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                await sent_tokenizer_stream.aclose()
                await utils.aio.gracefully_cancel(*tasks)
        except BaseException as e:
            if ws is not None:
                if isinstance(e, asyncio.CancelledError) and opened and not reply_done:
                    # interrupted: stop Koyal generating audio nobody will hear
                    self._tts._recycle(ws, context_id)
                elif isinstance(e, asyncio.CancelledError) or reply_done:
                    # nothing of this reply is left on the socket (or Koyal ended it with an
                    # error event, which doesn't close the socket)
                    pool.put(ws)
                else:
                    self._tts._drop(ws)
            if isinstance(e, (APIError, asyncio.CancelledError)):
                raise
            if isinstance(e, asyncio.TimeoutError):
                raise APITimeoutError() from None
            if isinstance(e, aiohttp.ClientResponseError):
                raise APIStatusError(
                    message=e.message, status_code=e.status, request_id=None, body=None
                ) from None
            if isinstance(e, Exception):
                raise _connection_error(e) from None
            raise

        pool.put(ws)


def _words(event: dict[str, Any]) -> list[TimedString]:
    """Group Koyal's per-character timings into words, keeping trailing spaces."""
    words: list[TimedString] = []
    text, start, end = "", 0.0, 0.0
    for ch, start_ms, dur_ms in zip(
        event.get("chars", []),
        event.get("start_ms", []),
        event.get("duration_ms", []),
        strict=False,
    ):
        if ch.isspace():
            text += ch
            continue
        if text and text[-1].isspace():
            words.append(TimedString(text=text, start_time=start, end_time=end))
            text = ""
        if not text:
            start = start_ms / 1000
        text += ch
        end = (start_ms + dur_ms) / 1000
    if text:
        words.append(TimedString(text=text, start_time=start, end_time=end))
    return words


def _connection_error(e: BaseException) -> APIConnectionError:
    # aiohttp errors can carry the request URL and headers, which include the credentials,
    # so only their type is kept. Other errors (no http session, a bad argument) keep their text.
    if isinstance(e, aiohttp.ClientError):
        return APIConnectionError(type(e).__name__)
    return APIConnectionError(f"{type(e).__name__}: {e}")


def _koyal_error(event: dict[str, Any]) -> APIError:
    code = event.get("code", "unknown")
    return APIError(
        f"Koyal error {code}: {event.get('message', '')}",
        body=event,
        retryable=code not in _NON_RETRYABLE_ERRORS,
    )


def _status_error(status: int, body: Any) -> APIStatusError:
    message = "Koyal request failed"
    request_id = None
    if isinstance(body, dict):
        message = str(body.get("error") or body.get("message") or message)
        request_id = body.get("api_id")
    return APIStatusError(message=message, status_code=status, request_id=request_id, body=body)
