"""Run a scripted call against Riya and record it, without a phone or a LiveKit server.

A caller voiced by Koyal (Aman, a male Hindi voice) answers Riya's questions. The caller's
audio goes through the same speech-to-text, LLM and Koyal pipeline as a real call, in real
time. The script writes the call as a WAV file and prints how long the caller waited for each
reply, with Koyal's time to first audio for each turn.

    python simulate_call.py            # writes call.wav and call.json
"""

from __future__ import annotations

import asyncio
import json
import time
import wave
from pathlib import Path

from agent import DEMO_ORDER, Riya, create_session
from dotenv import load_dotenv

from livekit import rtc
from livekit.agents import AgentStateChangedEvent, MetricsCollectedEvent, utils
from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities
from livekit.plugins import plivo

load_dotenv()

SAMPLE_RATE = 24000
FRAME_MS = 20
CALLER_VOICE = "OmziUEKO4d83QuGMo3bP"  # Aman, Hindi (male)
CALLER_LINES = [
    "हाँ, मैं रोहन बोल रहा हूँ।",
    "हाँ, order सही है।",
    "Address सही है, बस landmark add कर दो, Total petrol pump के पास।",
    "नहीं, cash ही दूँगा, UPI नहीं चाहिए।",
    "हाँ, confirm कर दो। Thank you!",
]


class Timeline:
    """Both sides of the call on one clock, mixed into a mono recording."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.tracks: dict[str, bytearray] = {"caller": bytearray(), "agent": bytearray()}

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def write(self, track: str, at: float, pcm: bytes) -> None:
        buf = self.tracks[track]
        offset = int(at * SAMPLE_RATE) * 2
        if len(buf) < offset:
            buf.extend(b"\x00" * (offset - len(buf)))
        buf[offset : offset + len(pcm)] = pcm

    def save(self, path: Path) -> None:
        import array

        a = array.array("h", bytes(self.tracks["caller"]))
        b = array.array("h", bytes(self.tracks["agent"]))
        n = max(len(a), len(b))
        a.extend([0] * (n - len(a)))
        b.extend([0] * (n - len(b)))
        mixed = array.array(
            "h", (max(-32768, min(32767, x + y)) for x, y in zip(a, b, strict=True))
        )
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(mixed.tobytes())


class CallerInput(AudioInput):
    """The caller's microphone: silence, or a scripted line, in real time."""

    def __init__(self, timeline: Timeline) -> None:
        super().__init__(label="SimulatedCaller")
        self._timeline = timeline
        self._pending = bytearray()
        self._samples = SAMPLE_RATE * FRAME_MS // 1000
        self._next = time.perf_counter()
        self.done_speaking = asyncio.Event()

    def say(self, pcm: bytes) -> None:
        self.done_speaking.clear()
        self._pending.extend(pcm)

    async def __anext__(self) -> rtc.AudioFrame:
        self._next += FRAME_MS / 1000
        await asyncio.sleep(max(0.0, self._next - time.perf_counter()))
        size = self._samples * 2
        if self._pending:
            chunk = bytes(self._pending[:size]).ljust(size, b"\x00")
            del self._pending[:size]
            self._timeline.write("caller", self._timeline.now(), chunk)
            if not self._pending:
                self.done_speaking.set()
        else:
            chunk = b"\x00" * size
        return rtc.AudioFrame(chunk, SAMPLE_RATE, 1, self._samples)


class CallRecorder(AudioOutput):
    """The caller's speaker: plays the agent's audio in real time and records it."""

    def __init__(self, timeline: Timeline) -> None:
        super().__init__(
            label="CallRecorder",
            next_in_chain=None,
            sample_rate=SAMPLE_RATE,
            capabilities=AudioOutputCapabilities(pause=False),
        )
        self._timeline = timeline
        self._cursor = 0.0  # where the next agent frame plays
        self._segment_start: float | None = None
        self._pushed = 0.0
        self._finish: asyncio.TimerHandle | None = None
        self.first_audio_at: list[float] = []
        self.segments: list[tuple[float, float]] = []  # when each reply played, in call time

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        now = self._timeline.now()
        if self._segment_start is None:
            self._segment_start = max(now, self._cursor)
            self._cursor = self._segment_start
            self.first_audio_at.append(now)
            self.on_playback_started(created_at=time.time())
        self._timeline.write("agent", self._cursor, bytes(frame.data))
        self._cursor += frame.duration
        self._pushed += frame.duration

    def flush(self) -> None:
        super().flush()
        if self._segment_start is None:
            return
        self.segments.append((self._segment_start, self._cursor))
        delay = max(0.0, self._cursor - self._timeline.now())
        pushed = self._pushed

        def finished() -> None:
            self._reset()
            self.on_playback_finished(
                playback_position=pushed, interrupted=False, synchronized_transcript=None
            )

        self._finish = asyncio.get_event_loop().call_later(delay, finished)

    def clear_buffer(self) -> None:
        if self._segment_start is None:
            return
        played = min(self._pushed, self._timeline.now() - self._segment_start)
        self.segments.append((self._segment_start, self._segment_start + played))
        # cut what the caller didn't hear out of the recording
        cut = int((self._segment_start + played) * SAMPLE_RATE) * 2
        del self._timeline.tracks["agent"][cut:]
        self._cursor = self._timeline.now()
        self._reset()
        self.on_playback_finished(
            playback_position=played, interrupted=True, synchronized_transcript=None
        )

    def _reset(self) -> None:
        if self._finish:
            self._finish.cancel()
            self._finish = None
        self._segment_start = None
        self._pushed = 0.0


async def main() -> None:
    caller_tts = plivo.TTS(voice=CALLER_VOICE, language="hi", sample_rate=SAMPLE_RATE)
    lines = [(await caller_tts.synthesize(t).collect()).data.tobytes() for t in CALLER_LINES]
    await caller_tts.aclose()

    timeline = Timeline()
    caller = CallerInput(timeline)
    recorder = CallRecorder(timeline)
    session = create_session()
    session.input.audio = caller
    session.output.audio = recorder

    listening = asyncio.Event()
    closed = asyncio.Event()
    koyal_ttfb: list[float] = []

    @session.on("agent_state_changed")
    def _on_state(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "listening" and ev.old_state == "speaking":
            listening.set()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        if ev.metrics.type == "tts_metrics" and ev.metrics.ttfb > 0:
            koyal_ttfb.append(round(ev.metrics.ttfb * 1000))

    session.on("close", lambda _: closed.set())

    await session.start(agent=Riya(DEMO_ORDER))

    turns = []
    for text, pcm in zip(CALLER_LINES, lines, strict=True):
        listening.clear()
        await asyncio.wait(
            [asyncio.ensure_future(listening.wait()), asyncio.ensure_future(closed.wait())],
            timeout=40,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if closed.is_set():
            break
        await asyncio.sleep(0.6)  # a human pause before answering
        replies_before = len(recorder.first_audio_at)
        said_at = timeline.now()
        caller.say(pcm)
        line_secs = len(pcm) / 2 / SAMPLE_RATE
        try:
            await asyncio.wait_for(caller.done_speaking.wait(), line_secs + 2)
        except asyncio.TimeoutError:
            break  # nobody is reading the caller's audio: the agent hung up
        ended = timeline.now()
        deadline = time.perf_counter() + 20
        while len(recorder.first_audio_at) == replies_before and time.perf_counter() < deadline:
            await asyncio.sleep(0.01)
        waited = (
            round((recorder.first_audio_at[-1] - ended) * 1000)
            if len(recorder.first_audio_at) > replies_before
            else None
        )
        turns.append(
            {"caller": text, "start": round(said_at, 2), "end": round(ended, 2), "reply_ms": waited}
        )
        print(f"caller: {text}\n  reply after {waited} ms")

    await asyncio.wait([asyncio.ensure_future(closed.wait())], timeout=15)
    if not closed.is_set():
        await session.aclose()

    out = Path(__file__).with_name("call.wav")
    timeline.save(out)
    transcript = []
    for item in session.history.items:
        if item.type == "message":
            transcript.append({"role": item.role, "text": item.text_content})
        elif item.type == "function_call":
            transcript.append({"role": "tool", "text": f"{item.name}({item.arguments})"})
    replies = [{"start": round(a, 2), "end": round(b, 2)} for a, b in recorder.segments]
    report = {
        "turns": turns,
        "replies": replies,
        "koyal_ttfb_ms": koyal_ttfb,
        "transcript": transcript,
    }
    out.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Koyal time to first audio per reply (ms): {koyal_ttfb}")
    print(f"wrote {out} and {out.with_suffix('.json')}")


async def run() -> None:
    # outside a LiveKit job, plugins need an http session bound to the context
    async with utils.http_context.open():
        await main()


if __name__ == "__main__":
    asyncio.run(run())
