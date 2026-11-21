# COD confirmation agent (Plivo Koyal)

Riya calls a customer before a cash-on-delivery order is dispatched and confirms it in Hinglish:
name, items, delivery slot and address, plus one offer to prepay by UPI. Then she records the
outcome with a tool call: confirmed, cancelled, rescheduled or do-not-call.

COD orders refused at the door (return to origin, RTO) are a large cost for Indian e-commerce and
food delivery, and a short call before dispatch is one of the most common voice-agent jobs in
India. The agent speaks with [Plivo Koyal](https://www.plivo.com/docs/models/text-to-speech/quickstart)
through the `livekit-plugins-plivo` plugin.

| File | What it does | Keys needed |
|---|---|---|
| `say.py` | Koyal reads three lines, in Hindi, Indian English and Tamil | Plivo |
| `agent.py` | Riya, the full voice agent | Plivo, Deepgram, Groq |

## Setup

From the root of this repo:

```bash
uv venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e livekit-agents -e livekit-plugins/livekit-plugins-plivo \
  -e livekit-plugins/livekit-plugins-deepgram -e livekit-plugins/livekit-plugins-groq python-dotenv
cp examples/cod_confirmation/.env.example examples/cod_confirmation/.env   # then fill it in
cd examples/cod_confirmation
```

Your Plivo Auth ID and Auth Token are on the [Plivo console](https://console.plivo.com/dashboard/)
overview page; the free trial includes Koyal. Deepgram and Groq both have free tiers.

## Hear Koyal

```bash
python say.py console
```

Console mode runs the agent in your terminal and plays audio on your speakers. It doesn't need a
LiveKit server.

## Talk to Riya

```bash
python agent.py console
```

Riya speaks first. Answer in Hindi, English or a mix, the way you would on a real call. Try
correcting the address ("Total petrol pump ke paas hai"), refusing the UPI offer, or saying
"mujhe call mat karo".

A typical call:

> **Riya:** नमस्ते रोहन जी, मैं Riya बोल रही हूँ, आपके चार सौ छियासी रुपये के COD order के बारे में call किया था।
> **You:** हाँ, बोलिए।
> **Riya:** आपके order में Paneer Butter Masala, 2 Butter Naan और Gulab Jamun हैं, delivery आज शाम 7 से 8 बजे है। सही है?
> **You:** Haan sahi hai, bas landmark add kar do, Total petrol pump ke paas.
> **Riya:** *(calls `update_address`)* ठीक है, landmark जोड़ दिया।

To run Riya in a LiveKit room instead, use `python agent.py dev` with `LIVEKIT_URL`,
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` set, and join from the
[Agents Playground](https://agents-playground.livekit.io/).

## How it's built

- **Speech:** `plivo.TTS(voice="Hiqcy02wuf8a4sCMcdNO", language="hi")`, Ritika, a female Hindi
  voice. The instructions tell the LLM to write Hindi in Devanagari and English words in Latin
  script, which is how Koyal's Hindi voices expect Hinglish.
- **Hearing:** Deepgram `nova-3` with `language="multi"`, which transcribes Hindi and English in
  the same sentence.
- **Thinking:** `openai/gpt-oss-120b` on Groq.
- **Outcomes:** each decision is a `function_tool` on the agent. `EndCallTool` hangs up after the
  goodbye. In production, write `Riya.outcome` back to your order system and pass the real order
  in through job metadata instead of `DEMO_ORDER`.
