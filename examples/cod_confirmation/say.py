"""Hear Plivo Koyal speak, with only Plivo credentials.

No LiveKit server, speech-to-text or LLM is needed: console mode plays the audio on your
speakers.

    python examples/cod_confirmation/say.py console
"""

from dotenv import load_dotenv

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import plivo

load_dotenv()

# (voice ID, language, line). Each Koyal voice speaks one language.
LINES = [
    (
        "6uWzc4xFj31t4jNJUaED",  # Meera, Hindi
        "hi",
        "नमस्ते! मैं Koyal हूँ, Plivo का text-to-speech model. "
        "मैं Hindi और English को एक ही sentence में mix कर सकती हूँ, जैसे लोग असल में बोलते हैं।",
    ),
    (
        "H25SvwivznBqsIx1R7hX",  # Nisha, English (India)
        "en-IN",
        "And I speak English with an Indian accent, streamed sentence by sentence through LiveKit.",
    ),
    (
        "wDPugzmr5HSlFv9MaXk0",  # Nandhini, Tamil
        "ta",
        "வணக்கம்! நான் தமிழிலும் பேசுவேன்.",
    ),
]


class Narrator(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You read the given lines aloud.")

    async def on_enter(self) -> None:
        tts = self.session.tts
        assert isinstance(tts, plivo.TTS)
        for voice, language, text in LINES:
            tts.update_options(voice=voice, language=language)
            # nothing is listening, so don't let microphone noise cut a line short
            await self.session.say(text, allow_interruptions=False)
        self.session.shutdown()


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(tts=plivo.TTS())
    await session.start(agent=Narrator(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
