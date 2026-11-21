"""Riya: a Hinglish voice agent that confirms cash-on-delivery orders before dispatch.

Unconfirmed COD orders that get refused at the door (RTO) are a large cost for Indian
e-commerce and food delivery. A short confirmation call before dispatch catches them.

Riya speaks with Plivo Koyal, hears with Deepgram nova-3 (multilingual, so Hinglish works)
and thinks with an LLM on Groq.

    python examples/cod_confirmation/agent.py console
"""

import json
import logging
from dataclasses import asdict, dataclass

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    cli,
)
from livekit.agents.beta import EndCallTool
from livekit.agents.llm import function_tool
from livekit.plugins import deepgram, groq, plivo

logger = logging.getLogger("cod-confirmation")

load_dotenv()


@dataclass
class Order:
    order_id: str
    customer: str
    items: list[str]
    amount_inr: int
    address: str
    slot: str
    prepaid_discount_inr: int


# In production this comes from the dispatch queue, passed in through job metadata.
DEMO_ORDER = Order(
    order_id="ORD-4821",
    customer="Rohan",
    items=["Paneer Butter Masala", "2 Butter Naan", "Gulab Jamun"],
    amount_inr=486,
    address="Flat 302, Sai Residency, HSR Layout Sector 2, Bengaluru",
    slot="today, 7 to 8 pm",
    prepaid_discount_inr=20,
)

INSTRUCTIONS = """Aap "Riya" hain. Aap ek delivery company ki taraf se customer ko call kar rahi hain,
taaki unka Cash-on-Delivery (COD) order dispatch se pehle confirm ho jaye.

Order (sirf yahi facts use karein, kuch bhi invent mat karein):
{order}

Kaise bolna hai:
- Natural Hinglish, jaise Indian support agents bolte hain. Hindi words Devanagari me, English words
  (order, delivery, address, cash, UPI) Latin script me. Example:
  "नमस्ते रोहन जी, मैं Riya बोल रही हूँ, आपके order के बारे में call किया था।"
- Har reply 1-2 chhote sentences. Ye phone call hai.
- Koi markdown, emoji ya brackets nahi. Jo bhi likhengi, woh bola jayega.
- Customer English me bole to English me jawab dein.

Flow:
1. Naam confirm karein aur batayein ki {amount} rupaye ke COD order ke liye call hai.
2. Items aur delivery slot confirm karein.
3. Address padh kar confirm karein. Correction ya landmark mile to update_address.
4. Ek baar offer karein: abhi UPI se pay karein to {discount} rupaye off. Mana karein to push mat karein.
5. Faisla hone par confirm_order, cancel_order ya reschedule_delivery.
6. Thank you bolkar end_call.

Guardrails:
- "Call mat karo" ya "don't call me" sunte hi mark_do_not_call, maafi maangein, end_call.
- Kabhi bhi OTP, card number ya UPI PIN mat maangiye.
"""


class Riya(Agent):
    def __init__(self, order: Order) -> None:
        super().__init__(
            instructions=INSTRUCTIONS.format(
                order=json.dumps(asdict(order), ensure_ascii=False, indent=2),
                amount=order.amount_inr,
                discount=order.prepaid_discount_inr,
            ),
            tools=[EndCallTool(end_instructions="thank the customer and say goodbye in Hinglish")],
        )
        self.order = order
        self.outcome: dict[str, str] = {}

    async def on_enter(self) -> None:
        self.session.generate_reply(instructions="Greet the customer and confirm their name.")

    def _record(self, status: str, **details: str) -> str:
        self.outcome = {"status": status, **details}
        logger.info("order outcome", extra={"order_id": self.order.order_id, "status": status})
        return status

    @function_tool
    async def confirm_order(self, context: RunContext) -> str:
        """Customer ne COD order confirm kar diya."""
        return self._record("confirmed")

    @function_tool
    async def cancel_order(self, context: RunContext, reason: str) -> str:
        """Customer order cancel karna chahta hai.

        Args:
            reason: Cancel karne ki wajah, customer ke shabdon me.
        """
        return self._record("cancelled", reason=reason)

    @function_tool
    async def reschedule_delivery(self, context: RunContext, new_slot: str) -> str:
        """Customer delivery ka din ya time badalna chahta hai.

        Args:
            new_slot: Naya slot, jaise "kal subah 10 se 12".
        """
        return self._record("rescheduled", new_slot=new_slot)

    @function_tool
    async def update_address(self, context: RunContext, correction: str) -> str:
        """Sirf tab call karein jab customer ne address me correction ya landmark bataya ho.

        Args:
            correction: Customer ka bataya hua correction ya landmark.
        """
        if not correction.strip():
            return "rejected: no correction given, address unchanged"
        self.outcome["address_correction"] = correction
        return "address updated"

    @function_tool
    async def mark_do_not_call(self, context: RunContext) -> str:
        """Customer ne aage se call na karne ko kaha."""
        return self._record("do_not_call")


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=groq.LLM(model="openai/gpt-oss-120b"),
        tts=plivo.TTS(voice="Hiqcy02wuf8a4sCMcdNO", language="hi"),  # Ritika
        tts_text_transforms=["filter_emoji", "filter_markdown"],
    )
    await session.start(agent=Riya(DEMO_ORDER), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
