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
| `simulate_call.py` | A scripted caller talks to Riya; records `call.wav` and per-turn latency | Plivo, Deepgram, Groq |

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

A call recorded with `simulate_call.py` (the caller is Koyal's Aman voice):

> **Riya:** नमस्ते रोहन जी, मैं Riya बोल रही हूँ, आपके 486 रुपये के COD order के बारे में कॉल किया था। क्या मैं आपका नाम सही समझ रही हूँ?
> **Caller:** हाँ, मैं रोहन बोल रहा हूँ।
> **Riya:** धन्यवाद रोहन जी। आपका order जिसमें Paneer Butter Masala, 2 Butter Naan और Gulab Jamun हैं, आज शाम 7 से 8 बजे के slot में डिलीवर होगा, क्या यह सही है?
> **Caller:** हाँ, order सही है।
> **Riya:** आपका delivery address है: Flat 302, Sai Residency, HSR Layout Sector 2, Bengaluru। क्या यह सही है?
> **Caller:** Address सही है, बस landmark add कर दो, Total petrol pump के पास।
> *`update_address(correction="Total petrol pump के पास")`*
> **Riya:** धन्यवाद, अब आपका address "Flat 302, Sai Residency, HSR Layout Sector 2, Bengaluru, Total petrol pump के पास" हो गया है। अभी UPI से payment करने पर 20 रुपये की छूट मिल सकती है, क्या आप UPI से पे करना चाहेंगे?
> **Caller:** नहीं, cash ही दूँगा, UPI नहीं चाहिए।
> *`confirm_order()`*
> **Riya:** आपका COD order confirm हो गया है, डिलीवरी आज 7 से 8 बजे के बीच होगी। धन्यवाद, फिर मिलते हैं!
> *`end_call()`*

## Test without a phone

```bash
python simulate_call.py
```

A caller voiced by Koyal answers Riya through the same speech-to-text, LLM and Koyal pipeline as
a real call, in real time. The script writes `call.wav` and `call.json` (transcript, tool calls,
and how long the caller waited for each reply). Across 16 simulated calls, Koyal's time to first
audio was 48 ms at the median (p90 70 ms, 54 replies), measured from India. With this agent's
settings, the caller waited 1.44 s at the median (p90 1.96 s, 25 turns) from the end of their
sentence to Riya's first word; nearly all of it is end-of-turn detection, speech-to-text and the
LLM.

To run Riya in a LiveKit room instead, use `python agent.py dev` with `LIVEKIT_URL`,
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` set, and join from the
[Agents Playground](https://agents-playground.livekit.io/).

## How it's built

- **Speech:** `plivo.TTS(voice="Hiqcy02wuf8a4sCMcdNO", language="hi")`, Ritika, a female Hindi
  voice. The instructions tell the LLM to write Hindi in Devanagari and English words in Latin
  script, which is how Koyal's Hindi voices expect Hinglish.
- **Hearing:** Deepgram `nova-3` with `language="multi"`, which transcribes Hindi and English in
  the same sentence.
- **Thinking:** `openai/gpt-oss-120b` on Groq with `reasoning_effort="medium"`. At the default
  `low`, it sometimes answered its own questions or called `confirm_order` before the customer
  replied.
- **Guarding speech:** gpt-oss occasionally keeps writing after a question, e.g.
  `(Waiting for customer response)`. `drop_stage_directions` is a TTS text transform that stops
  speaking at the first `(` of a reply, so that text reaches the transcript but is never spoken.
- **Outcomes:** each decision is a `function_tool` on the agent. `EndCallTool` hangs up after the
  goodbye. In production, write `Riya.outcome` back to your order system and pass the real order
  in through job metadata instead of `DEMO_ORDER`.
