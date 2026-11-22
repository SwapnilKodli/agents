LiveKit docs › Build Agents › Models › TTS › Additional models › Plivo

---

# Plivo TTS plugin guide

> How to use the Plivo Koyal TTS plugin for LiveKit Agents.

Available in:
- [ ] Node.js
- [x] Python

## Overview

Use the Plivo TTS plugin to synthesize speech with [Koyal](https://www.plivo.com/blog/introducing-koyal-text-to-speech/), Plivo's text-to-speech model, in LiveKit Agents. Koyal has 33 voices across Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada and English with American, British, Australian and Indian accents. Its Hindi voices read English words written in Latin script, so code-mixed Hinglish such as `आपका order आज deliver होगा` sounds natural.

The plugin streams over Koyal's realtime WebSocket. It sends each sentence as soon as the LLM finishes writing it, returns word timestamps for synced transcripts, and cancels the reply on Koyal's side when the user interrupts.

### Authentication

The Plivo plugin uses your Plivo Auth ID and Auth Token, shown on the [Plivo console](https://console.plivo.com/dashboard/) overview page. Koyal is included in the Plivo free trial.

Set `PLIVO_AUTH_ID` and `PLIVO_AUTH_TOKEN` in your `.env` file:

```shell
PLIVO_AUTH_ID=<your-plivo-auth-id>
PLIVO_AUTH_TOKEN=<your-plivo-auth-token>

```

### Installation

Install the plugin:

**Python**:

```shell
uv add "livekit-agents[plivo]~=1.8"

```

### Usage

Use Plivo TTS within an `AgentSession` or as a standalone speech generator. For example, you can use this TTS in the [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai.md).

For most voice agents, begin with the following settings:

- `voice`: Pick a voice that speaks your agent's language. Each Koyal voice speaks one language. See [Voices](#voices).
- `language`: Set it to the voice's language, for example `hi` or `en-IN`. If you leave it out, Koyal uses the voice's own language.
- `sample_rate`: Keep the default `24000` for web and mobile. Koyal accepts any rate from `8000` to `48000`, and LiveKit resamples for telephony.

**Python**:

```python
from livekit.agents import AgentSession
from livekit.plugins import plivo

session = AgentSession(
    tts=plivo.TTS(
        voice="6uWzc4xFj31t4jNJUaED",  # Meera, Hindi
        language="hi",
        sample_rate=24000,
        pace=1.0,
    ),
    # ... llm, stt, etc.
)
```

To generate a clip outside a session, call `synthesize()`, which uses Koyal's batch API:

```python
tts = plivo.TTS(voice="H25SvwivznBqsIx1R7hX")  # Nisha, English (India)
frame = await tts.synthesize("Your order is out for delivery.").collect()
```

### Parameters

This section describes commonly used parameters. See the plugin reference links in the [Additional resources](#additional-resources) section for a complete list of all available parameters.

- **`voice`** _(string)_ (optional) - Default: `6uWzc4xFj31t4jNJUaED`: The Koyal voice ID. The default is Meera, a female Hindi voice. See [Voices](#voices).

- **`language`** _(string)_ (optional) - Default: the voice's language: The language to speak. Valid values are `en-US`, `en-GB`, `en-IN`, `en-AU`, `hi`, `bn`, `ta`, `te`, `mr`, `gu` and `kn`. Each voice speaks exactly one language; a language the voice doesn't speak returns `language_not_supported`. Region tags for Indian languages such as `hi-IN` are converted to `hi`.

- **`sample_rate`** _(int)_ (optional) - Default: `24000`: Output sample rate in Hz. Koyal accepts values from `8000` to `48000`.

- **`pace`** _(float)_ (optional) - Default: `1.0`: Speaking speed. Valid range: `0.7` to `1.2`.

- **`volume`** _(float)_ (optional) - Default: `1.0`: Loudness. Valid range: `0.5` to `2.0`.

- **`pronunciations`** _(list[dict])_ (optional): Up to 100 inline pronunciation rules, each `{"word": ..., "value": ..., "kind": ...}` where `kind` is `ipa`, `arpa` or `respell`. Which kinds a voice accepts depends on the voice.

- **`lexicon_id`** _(string)_ (optional): ID of a saved Koyal lexicon, for pronunciations you reuse across agents.

- **`word_timestamps`** _(bool)_ (optional) - Default: `true`: Request per-word timings from Koyal so the agent's transcript follows its speech.

- **`tokenizer`** _(tokenize.SentenceTokenizer)_ (optional) - Default: blingfire: Splits streamed LLM text into sentences before it is sent. The default splits on the Devanagari danda (`।`) as well as Latin punctuation.

- **`text_pacing`** _(tts.SentenceStreamPacer | bool)_ (optional) - Default: `false`: Pace text sent to Koyal to the audio already generated.

`update_options()` changes `voice`, `language`, `pace`, `volume`, `pronunciations` and `lexicon_id`. The change applies from the next reply.

### Voices

Koyal voices are referenced by ID. To list every voice on your account:

```python
voices = await plivo.TTS().list_voices()
for v in voices:
    print(v.id, v.name, v.gender, v.language)
```

| Language | Female | Male |
|---|---|---|
| Hindi (`hi`) | Meera `6uWzc4xFj31t4jNJUaED`, Ritika `Hiqcy02wuf8a4sCMcdNO` | Aman `OmziUEKO4d83QuGMo3bP`, Ishaan `nzWUWk5ShuIYGSosxESk` |
| English, India (`en-IN`) | Nisha `H25SvwivznBqsIx1R7hX`, Prerna `3gVUACYdS3ibtu8qPruB` | Aarav `vGOW5Fzss7kzS9IxCObo`, Karan `34AUgJTl9p1TLO99Mc7E` |
| English, US (`en-US`) | Hailey `lC7qzMoRE7SSjaNCVeff`, Allison `GhXTeeZNT1zacg9RUbXa` | Travis `Ak70ycoSdDYbeq5sAXsc`, Garrett `P2WNP5RVUP1t6dK9PY52` |
| Tamil (`ta`) | Nandhini `wDPugzmr5HSlFv9MaXk0` | Kathir `4YwYWODqig98puWmLYRz` |
| Bengali (`bn`) | Ishani `GKrPm1RIxbJRv4yYqAxX` | Subhankar `vxMrWeHTjRflUziEla6E` |

Telugu, Marathi, Gujarati, Kannada, British English and Australian English voices are also available. See [Voices and languages](https://www.plivo.com/docs/models/text-to-speech/api/voices) for the full catalog.

### Troubleshooting

Common issues and solutions for the Plivo TTS plugin.

#### `401 unauthorized`

Check `PLIVO_AUTH_ID` and `PLIVO_AUTH_TOKEN`. If both are right, your Plivo account may restrict API access to allowed IP addresses; add your server's IP in the Plivo console.

#### `language_not_supported` or `voice_not_found`

Each voice speaks one language. Pick a voice for your agent's language, or leave `language` unset so Koyal uses the voice's own.

#### Writing Hinglish for Koyal

Koyal's Hindi voices take mixed script. Write Hindi words in Devanagari and English words in Latin script, the way the sentence is spoken: `आपका order confirm हो गया।`. LLMs often answer Hindi prompts in romanized Hindi (`aapka order`), so say which script to use in your agent's instructions; the [COD confirmation example](https://github.com/livekit/agents/tree/main/examples/cod_confirmation) shows one way.

#### Audio starts too slowly

The plugin sends text one sentence at a time, so the first audio waits for the LLM's first full sentence. Ask the LLM to open with a short sentence. Call `tts.prewarm()` when the session starts so the first reply doesn't pay for the WebSocket handshake.

## Additional resources

The following resources provide more information about using Plivo Koyal with LiveKit Agents.

- **[Koyal quickstart](https://www.plivo.com/docs/models/text-to-speech/quickstart)**: Plivo's getting-started guide for Koyal.

- **[Koyal realtime API reference](https://www.plivo.com/docs/models/text-to-speech/api/realtime)**: The WebSocket protocol the plugin uses.

- **[COD confirmation example](https://github.com/livekit/agents/tree/main/examples/cod_confirmation)**: A Hinglish voice agent that confirms cash-on-delivery orders with Koyal.

- **[Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai.md)**: Get started with LiveKit Agents.
