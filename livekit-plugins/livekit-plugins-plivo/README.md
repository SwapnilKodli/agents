# Plivo plugin for LiveKit Agents

Text-to-speech with [Koyal](https://www.plivo.com/blog/introducing-koyal-text-to-speech/), Plivo's
model for Indian languages, English and code-mixed speech such as Hinglish.

- 33 voices across Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada and four English accents
- Streams over Koyal's realtime WebSocket, with word timestamps for synced transcripts
- Cancels the reply on Koyal's side when the user interrupts, so it stops generating audio nobody hears

The LiveKit docs page for this plugin is in [`plivo-tts-docs.md`](plivo-tts-docs.md) (to be published at docs.livekit.io/agents/models/tts/plivo).

## Installation

```bash
pip install "livekit-agents[plivo]"
# or
pip install livekit-plugins-plivo
```

## Authentication

Koyal uses your Plivo Auth ID and Auth Token, from the
[Plivo console](https://console.plivo.com/dashboard/) overview page. Koyal is included in the
free trial.

```bash
export PLIVO_AUTH_ID=<your auth id>
export PLIVO_AUTH_TOKEN=<your auth token>
```

You can also pass `auth_id=` and `auth_token=` to `plivo.TTS()`.

## Usage

```python
from livekit.agents import AgentSession
from livekit.plugins import plivo

session = AgentSession(
    tts=plivo.TTS(voice="6uWzc4xFj31t4jNJUaED"),  # Meera, Hindi
    # ... stt, llm, vad, etc.
)
```

Hear it with only Plivo credentials (no LiveKit server, STT or LLM needed). From a checkout of
this repo:

```bash
uv venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e livekit-agents -e livekit-plugins/livekit-plugins-plivo python-dotenv
export PLIVO_AUTH_ID=... PLIVO_AUTH_TOKEN=...
python examples/cod_confirmation/say.py console
```

Koyal greets you in Hindi, Indian English and Tamil through your speakers.
[`examples/cod_confirmation`](../../examples/cod_confirmation) also has a full Hinglish voice
agent built on Koyal.

## Options

| Option | Default | Description |
|---|---|---|
| `voice` | `6uWzc4xFj31t4jNJUaED` (Meera) | Koyal voice ID. See [Voices](#voices). |
| `language` | the voice's own | `en-US`, `en-GB`, `en-IN`, `en-AU`, `hi`, `bn`, `ta`, `te`, `mr`, `gu`, `kn`. Each voice speaks one language. Tags like `hi-IN` are converted to `hi`. |
| `sample_rate` | `24000` | Output rate in Hz, 8000 to 48000. |
| `pace` | `1.0` | Speaking speed, 0.7 to 1.2. |
| `volume` | `1.0` | Loudness, 0.5 to 2.0. |
| `pronunciations` | none | Up to 100 inline rules, e.g. `{"word": "Plivo", "value": "plee-voh", "kind": "respell"}`. |
| `lexicon_id` | none | A saved Koyal lexicon. |
| `word_timestamps` | `True` | Per-word timings, so transcripts follow the audio. |
| `tokenizer` | blingfire | Sentence tokenizer for streamed text. It splits on the Devanagari danda (।). |
| `text_pacing` | `False` | Pace text sent to Koyal to the audio already generated. |

`update_options()` changes `voice`, `language`, `pace`, `volume`, `pronunciations` and
`lexicon_id` for the next reply.

## Voices

Koyal voices are referenced by ID. A few to start with:

| Voice | ID | Language |
|---|---|---|
| Meera (F) | `6uWzc4xFj31t4jNJUaED` | Hindi |
| Ritika (F) | `Hiqcy02wuf8a4sCMcdNO` | Hindi |
| Aman (M) | `OmziUEKO4d83QuGMo3bP` | Hindi |
| Nisha (F) | `H25SvwivznBqsIx1R7hX` | English (India) |
| Aarav (M) | `vGOW5Fzss7kzS9IxCObo` | English (India) |
| Hailey (F) | `lC7qzMoRE7SSjaNCVeff` | English (US) |
| Nandhini (F) | `wDPugzmr5HSlFv9MaXk0` | Tamil |
| Ishani (F) | `GKrPm1RIxbJRv4yYqAxX` | Bengali |

List every voice on your account:

```python
voices = await plivo.TTS().list_voices()
for v in voices:
    print(v.id, v.name, v.gender, v.language)
```

Hindi voices read English words in Latin script naturally, so write Hinglish the way people
speak it: `"आपका order आज शाम 7 बजे deliver होगा।"`

## How it works

- `stream()` opens one Koyal reply (`context_id`) per agent turn on a pooled WebSocket. Text is
  split into sentences and each sentence is flushed as it arrives, so audio starts after the
  first sentence instead of the whole reply.
- If the stream is closed before the reply finishes (the user interrupted), the plugin sends
  Koyal a `cancel` for that reply and returns the socket to the pool. Audio still in flight for
  the cancelled reply is dropped by `context_id`.
- `synthesize()` uses the batch endpoint and returns the whole clip.
- Call `tts.prewarm()` when the session starts. A warm socket starts the first audio about
  50 ms after the first sentence is sent from India; a cold one adds the TLS and WebSocket
  handshake (about 100 to 250 ms).
