import logging
import re
from flask import current_app

logger = logging.getLogger(__name__)


def _setting(key):
    try:
        from models import AppSettings
        return AppSettings.get(key, AppSettings.DEFAULTS.get(key, ""))
    except Exception:
        defaults = {
            "business_website": "https://amzusdigital.com/",
            "business_name":    "AMZUS Digital",
            "sms_agent_name":   "Sarah",
            "opening_message":  "Hi! Is this {business_name}? 👋 I'm {agent_name} from {company_name}.",
        }
        return defaults.get(key, "")


# ------------------------------------------------------------------ #
# Message Templates
# ------------------------------------------------------------------ #

def _greeting(business_name):
    template = _setting("opening_message")
    return (template
            .replace("{business_name}", business_name)
            .replace("{agent_name}", _setting("sms_agent_name"))
            .replace("{company_name}", _setting("business_name")))

def _compliment(rating, reviews):
    return (
        f"That's great! We noticed you have an impressive {rating}-star Google rating "
        f"with {reviews} reviews — that really stands out! ⭐\n\n"
        f"However, we also noticed there's no website linked to your Google profile. "
        f"That means potential customers searching online may not find you easily, "
        f"which could be costing you sales every day."
    )

def _offer(business_name):
    return (
        f"We've actually already put together a free preview website for {business_name}. "
        f"Would you like me to send you the link so you can take a look? "
        f"No obligation at all — just want to show you what's possible! 🌐"
    )

def _interested_ack():
    return (
        "Wonderful! I'll have that link ready for you shortly. "
        "One of our team will be in touch very soon with your personalised website. "
        "Looking forward to showing you what we've built! 😊"
    )

def _not_interested():
    return (
        "No problem at all — I completely understand! "
        "If you ever change your mind in the future, feel free to reach out. "
        "Have a great day! 👋"
    )

def _opt_out():
    return (
        "Of course! I've removed you from our list and you won't hear from us again. "
        "Sorry for any inconvenience, and have a wonderful day! 🙏"
    )

def _unclear(business_name):
    return (
        f"Thanks for getting back to us! Just to confirm — would you like to see the "
        f"free website preview we've put together for {business_name}? "
        f"A simple yes or no works perfectly 😊"
    )

def _followup(business_name, count):
    messages = [
        (
            f"Hi again! 👋 Just following up — we still have that free website preview "
            f"ready for {business_name}. Would you like to take a look? Only takes 30 seconds!"
        ),
        (
            f"Last check-in from us, {business_name}! We don't want you to miss out — "
            f"businesses with websites get significantly more enquiries. "
            f"Want me to send you the free preview link? 🌐"
        ),
    ]
    return messages[min(count - 1, len(messages) - 1)]

def _website_info(lead_name: str = "", step: int = 0):
    website  = _setting("business_website")
    name     = _setting("business_name")
    agent    = _setting("sms_agent_name")
    if step == 0:
        biz = f" for {lead_name}" if lead_name else ""
        return (
            f"Hi! I'm {agent} from {name} — we specialise in building professional websites "
            f"for local businesses{biz}. 😊\n\n"
            f"You can check us out at {website}\n\n"
            f"I was reaching out because we noticed your Google profile doesn't have a website "
            f"linked — is this the right number to discuss that?"
        )
    return (
        f"We're {name} — specialists in building websites for local businesses. "
        f"You can see our work at {website} 😊"
    )

def _number_question():
    return (
        "I found your business listed on Google Maps — your details are publicly visible there. "
        "I noticed you have great reviews but no website linked, so I reached out. 😊\n\n"
        "If you'd prefer not to be contacted, just say STOP and I'll remove you immediately. "
        "Otherwise, would you like to hear more?"
    )

def _objection_response(business_name):
    return (
        f"Totally understand! Just to be clear — there's no cost to take a look. "
        f"We've already built a free preview for {business_name} and wanted to show you "
        f"what's possible. Would you at least like to see it? 😊"
    )


# ------------------------------------------------------------------ #
# Intent Classification
# ------------------------------------------------------------------ #

POSITIVE_PATTERNS = [
    r"\byes\b", r"\byep\b", r"\byeah\b", r"\bsure\b", r"\bok\b", r"\bokay\b",
    r"\bsounds good\b", r"\bplease\b", r"\bsend it\b", r"\bsend me\b",
    r"\bwould love\b", r"\binterested\b", r"\bgo ahead\b", r"\bdefinitely\b",
    r"\babsolutely\b", r"\bof course\b", r"\bwhy not\b",
]
NEGATIVE_PATTERNS = [
    r"\bnot interested\b", r"\bno thanks\b", r"\bno thank you\b",
    r"\bdont need\b", r"\bdon't need\b", r"\balready have\b",
    r"\bhave a website\b", r"\bnot for me\b", r"\bpass\b", r"\bnope\b",
]
OPT_OUT_PATTERNS = [
    r"\bstop\b", r"\bunsubscribe\b", r"\bremove me\b", r"\bopt out\b",
    r"\bopt-out\b", r"\bdo not contact\b", r"\bleave me alone\b",
    r"\bblock\b", r"\bremove my number\b", r"\bdon'?t text\b",
]
WEBSITE_ASK_PATTERNS = [
    r"\bwho are you\b", r"\bwho is this\b", r"\bwho'?s this\b",
    r"\byour website\b", r"\bmore info\b", r"\bwhere can i\b",
    r"\blearn more\b", r"\babout you\b", r"\byour company\b",
    r"\bwhat company\b", r"\bwhat business\b",
]
NUMBER_QUESTION_PATTERNS = [
    r"\bwhere did you get\b", r"\bhow did you get\b", r"\bwho gave you\b",
    r"\bwhere'd you get\b", r"\bhow'd you get\b", r"\bwhere you get\b",
    r"\bmy number\b", r"\bthis number\b", r"\bget my\b",
]
OBJECTION_PATTERNS = [
    r"\btoo busy\b", r"\bnot now\b", r"\bmaybe later\b", r"\bnot the right time\b",
    r"\bcan'?t afford\b", r"\btoo expensive\b", r"\bdon'?t need\b",
    r"\balready sorted\b", r"\bhave someone\b",
]
CONFIRMATION_PATTERNS = [
    r"\byes\b", r"\byep\b", r"\byeah\b", r"\bthat'?s us\b", r"\bcorrect\b",
    r"\bright\b", r"\bconfirm\b", r"\bhi\b", r"\bhello\b", r"\bspeaking\b",
]


def classify_intent(message: str, expected_step: int) -> str:
    text = message.lower().strip()

    for p in OPT_OUT_PATTERNS:
        if re.search(p, text):
            return "opt_out"

    for p in NUMBER_QUESTION_PATTERNS:
        if re.search(p, text):
            return "number_question"

    for p in WEBSITE_ASK_PATTERNS:
        if re.search(p, text):
            return "website_ask"

    for p in OBJECTION_PATTERNS:
        if re.search(p, text):
            return "objection"

    if expected_step == 0:
        for p in NEGATIVE_PATTERNS:
            if re.search(p, text):
                return "negative"
        return "confirmation"

    for p in POSITIVE_PATTERNS:
        if re.search(p, text):
            return "positive"
    for p in NEGATIVE_PATTERNS:
        if re.search(p, text):
            return "negative"

    return _openai_classify(text, expected_step) or "unclear"


def _openai_classify(text: str, step: int):
    try:
        api_key = current_app.config.get("OPENAI_API_KEY", "")
    except Exception:
        return None

    if not api_key or not str(api_key).strip().startswith("sk-"):
        return None

    try:
        import openai
        client = openai.OpenAI(api_key=api_key.strip())
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Classify this SMS reply from a small business owner contacted about a free website. "
                    f"Conversation step: {step}. "
                    "Reply with ONLY one word: positive, negative, opt_out, objection, or unclear."
                )},
                {"role": "user", "content": text},
            ],
            max_tokens=5, temperature=0,
        )
        result = response.choices[0].message.content.strip().lower()
        return result if result in ("positive", "negative", "opt_out", "objection", "unclear") else None
    except Exception as e:
        logger.warning(f"OpenAI classification failed: {e}")
        return None


def get_ai_reply_with_context(lead, conversation_history: list, inbound_text: str):
    """
    Use OpenAI with full conversation history for intelligent context-aware replies.
    Falls back to rule-based if OpenAI unavailable.
    """
    try:
        api_key = current_app.config.get("OPENAI_API_KEY", "")
    except Exception:
        api_key = ""

    if not api_key or not str(api_key).strip().startswith("sk-"):
        return None

    try:
        import openai
        client = openai.OpenAI(api_key=api_key.strip())

        website  = _setting("business_website")
        name     = _setting("business_name")
        agent    = _setting("sms_agent_name")

        system_prompt = f"""You are {agent}, a friendly sales assistant from {name}.
You are having an SMS conversation with the owner of '{lead.business_name}'.
Their business has a {lead.rating}-star Google rating with {lead.reviews_count} reviews.
They currently have NO website linked to their Google profile.

Your goal:
1. Confirm you're speaking to the right person
2. Compliment their Google rating
3. Point out they have no website and are losing potential customers
4. Offer a free website preview you've already built for them
5. If they say yes → confirm someone will be in touch shortly

Key facts:
- Company website: {website}
- You've already built them a FREE preview website
- No obligation, no pressure
- If they ask how you got their number: from Google Maps public listing
- If they say STOP/unsubscribe: apologise and confirm removal

Rules:
- Keep replies SHORT (1-3 sentences max for SMS)
- Be warm, professional, and human
- Never be pushy
- Remember the full conversation context
- If they've already said yes, don't re-offer
"""

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        for msg in conversation_history[-10:]:
            role = "assistant" if msg.get("direction") == "outbound" else "user"
            messages.append({"role": role, "content": msg.get("message", "")})

        # Add current message
        messages.append({"role": "user", "content": inbound_text})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=150,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"OpenAI context reply failed: {e}")
        return None


# ------------------------------------------------------------------ #
# Main conversation engine
# ------------------------------------------------------------------ #

def get_next_message(lead, inbound_text: str):
    step   = lead.conversation_step or 0
    intent = classify_intent(inbound_text, expected_step=step)
    name   = lead.business_name or "there"
    rating = lead.rating or "4+"
    reviews = lead.reviews_count or "many"

    logger.info(f"Lead {lead.id} '{name}' | step={step} | intent={intent} | msg='{inbound_text[:50]}'")

    if intent == "opt_out":
        return _opt_out(), "opted_out", step

    if intent == "number_question":
        return _number_question(), lead.status, step

    if intent == "website_ask":
        return _website_info(name, step), lead.status, step

    if intent == "objection":
        return _objection_response(name), lead.status, step

    if step == 0:
        if intent == "negative":
            return _not_interested(), "not_interested", step
        return _compliment(rating, reviews), "replied", 1

    if step == 1:
        return _offer(name), "replied", 2

    if step == 2:
        if intent == "positive":
            return _interested_ack(), "interested", 3
        if intent == "negative":
            return _not_interested(), "not_interested", step
        return _unclear(name), "replied", 2

    if step >= 3:
        return None, "interested", step

    return None, lead.status, step


def get_initial_message(lead) -> str:
    return _greeting(lead.business_name or "there")


def get_followup_message(lead) -> str:
    count = (lead.followup_count or 0) + 1
    return _followup(lead.business_name or "there", count)


# ------------------------------------------------------------------ #
# Simulation
# ------------------------------------------------------------------ #

def simulate_conversation(business_name: str, rating: float, reviews: int, messages: list) -> str:
    bot_count    = sum(1 for m in messages if m.get("role") == "bot")
    current_step = max(0, bot_count - 1) if bot_count > 0 else 0

    class FakeLead:
        pass

    fake = FakeLead()
    fake.id = 0
    fake.business_name    = business_name
    fake.rating           = rating
    fake.reviews_count    = reviews
    fake.conversation_step = current_step
    fake.followup_count   = 0
    fake.status           = "message_sent"

    if not messages:
        return get_initial_message(fake)

    last_user = next((m["text"] for m in reversed(messages) if m.get("role") == "user"), "")
    if not last_user:
        return get_initial_message(fake)

    # Build history for context-aware AI
    history = [{"direction": "outbound" if m.get("role") == "bot" else "inbound",
                "message": m.get("text", "")} for m in messages]

    ai_reply = get_ai_reply_with_context(fake, history, last_user)
    if ai_reply:
        return ai_reply

    reply, _, _ = get_next_message(fake, last_user)
    return reply or "Thanks for your message! Our team will be in touch shortly. 😊"
