import logging
import re
from flask import current_app

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Settings helpers
# ------------------------------------------------------------------ #

def _s(key):
    """Load a setting from DB with fallback to DEFAULTS."""
    try:
        from models import AppSettings
        return AppSettings.get(key, AppSettings.DEFAULTS.get(key, ""))
    except Exception:
        from models import AppSettings
        return AppSettings.DEFAULTS.get(key, "")


def _fill(template: str, **kwargs) -> str:
    """Replace {variable} placeholders in a template string."""
    for k, v in kwargs.items():
        template = template.replace("{" + k + "}", str(v or ""))
    return template


# ------------------------------------------------------------------ #
# OpenAI - full context-aware reply (primary engine)
# ------------------------------------------------------------------ #

def _openai_reply(lead, conversation_history: list, inbound_text: str) -> str | None:
    """
    Generate a fully AI-driven SMS reply using the configured system prompt
    and full conversation history. Returns None if OpenAI unavailable.
    """
    try:
        api_key = current_app.config.get("OPENAI_API_KEY", "")
    except Exception:
        return None

    if not api_key or not str(api_key).strip().startswith("sk-"):
        return None

    try:
        import openai
        client = openai.OpenAI(api_key=api_key.strip())

        raw_prompt = _s("ai_system_prompt")
        system_prompt = _fill(
            raw_prompt,
            agent_name=_s("sms_agent_name"),
            business_name=_s("business_name"),
            lead_name=lead.business_name or "the business",
            rating=lead.rating or "4+",
            reviews=lead.reviews_count or "many",
            website=_s("business_website"),
        )

        # Append all message templates as reference for the AI
        system_prompt += "\n\n--- APPROVED MESSAGE TEMPLATES (use as style guide) ---\n"
        template_keys = [
            ("OPENING",      "msg_opening"),
            ("STEP 1",       "msg_compliment"),
            ("STEP 2 OFFER", "msg_offer"),
            ("INTERESTED",   "msg_interested"),
            ("NOT INTERESTED","msg_not_interested"),
            ("OPT OUT",      "msg_opt_out"),
            ("UNCLEAR",      "msg_unclear"),
            ("WHO ARE YOU",  "msg_who_are_you"),
        ]
        for label, key in template_keys:
            tmpl = _s(key)
            if tmpl:
                system_prompt += f"\n[{label}]\n{tmpl}\n"

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (last 12 messages)
        for msg in (conversation_history or [])[-12:]:
            role = "assistant" if msg.get("direction") == "outbound" else "user"
            messages.append({"role": role, "content": msg.get("message", "")})

        messages.append({"role": "user", "content": inbound_text})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0.75,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"OpenAI reply for lead {lead.id}: '{reply[:80]}'")
        return reply

    except Exception as e:
        logger.warning(f"OpenAI reply failed: {e}")
        return None


# ------------------------------------------------------------------ #
# Intent Classification (for determining status/step only)
# ------------------------------------------------------------------ #

POSITIVE_PATTERNS = [
    r"\byes\b", r"\byep\b", r"\byeah\b", r"\bsure\b", r"\bok\b", r"\bokay\b",
    r"\bsounds good\b", r"\bplease\b", r"\bsend it\b", r"\bsend me\b",
    r"\binterested\b", r"\bgo ahead\b", r"\bdefinitely\b", r"\bwhy not\b",
    r"\bof course\b", r"\babsolutely\b",
]
NEGATIVE_PATTERNS = [
    r"\bnot interested\b", r"\bno thanks\b", r"\bno thank you\b",
    r"\bdont need\b", r"\bdon'?t need\b", r"\balready have\b",
    r"\bhave a website\b", r"\bnot for me\b", r"\bpass\b", r"\bnope\b",
]
OPT_OUT_PATTERNS = [
    r"\bstop\b", r"\bunsubscribe\b", r"\bremove me\b", r"\bopt out\b",
    r"\bopt-out\b", r"\bdo not contact\b", r"\bleave me alone\b",
    r"\bremove my number\b", r"\bdon'?t text\b", r"\bblock\b",
]


def _classify_status(text: str, step: int) -> str:
    """Classify intent to determine pipeline status only (not the reply)."""
    t = text.lower().strip()

    for p in OPT_OUT_PATTERNS:
        if re.search(p, t):
            return "opt_out"

    if step >= 2:
        for p in POSITIVE_PATTERNS:
            if re.search(p, t):
                return "positive"
        for p in NEGATIVE_PATTERNS:
            if re.search(p, t):
                return "negative"

    return "neutral"


# ------------------------------------------------------------------ #
# Template fallbacks (used when OpenAI unavailable)
# ------------------------------------------------------------------ #

def _template_reply(lead, inbound_text: str, step: int) -> tuple[str, str, int]:
    """
    Rule-based fallback when OpenAI is unavailable.
    Returns (reply, new_status, new_step).
    """
    name    = lead.business_name or "there"
    rating  = lead.rating or "4+"
    reviews = lead.reviews_count or "many"

    def fill(key, **kw):
        return _fill(_s(key),
                     business_name=name, rating=rating, reviews=reviews,
                     agent_name=_s("sms_agent_name"),
                     business_name_company=_s("business_name"),
                     website=_s("business_website"), **kw)

    intent = _classify_status(inbound_text, step)

    if intent == "opt_out":
        return fill("msg_opt_out"), "opted_out", step

    if step == 0:
        if intent == "negative":
            return fill("msg_not_interested"), "not_interested", step
        return fill("msg_compliment"), "replied", 1

    if step == 1:
        return fill("msg_offer"), "replied", 2

    if step == 2:
        if intent == "positive":
            return fill("msg_interested"), "interested", 3
        if intent == "negative":
            return fill("msg_not_interested"), "not_interested", step
        return fill("msg_unclear"), "replied", 2

    return None, "interested", step


# ------------------------------------------------------------------ #
# Status/step resolver
# ------------------------------------------------------------------ #

def _resolve_status(text: str, step: int, current_status: str) -> tuple[str, int]:
    """Determine the new pipeline status and step from the inbound message."""
    intent = _classify_status(text, step)

    if intent == "opt_out":
        return "opted_out", step

    if step == 0:
        if intent == "negative":
            return "not_interested", step
        return "replied", 1

    if step == 1:
        return "replied", 2

    if step == 2:
        if intent == "positive":
            return "interested", 3
        if intent == "negative":
            return "not_interested", step
        return "replied", 2

    if step >= 3:
        return "interested", step

    return current_status, step


# ------------------------------------------------------------------ #
# Main entry point — called by webhook handler
# ------------------------------------------------------------------ #

def get_next_message(lead, inbound_text: str, conversation_history: list = None):
    """
    Returns (reply_message, new_status, new_step).
    Always tries OpenAI first, falls back to templates.
    """
    step = lead.conversation_step or 0

    logger.info(f"Lead {lead.id} '{lead.business_name}' | step={step} | msg='{inbound_text[:60]}'")

    # Determine new status/step from rule-based classification
    new_status, new_step = _resolve_status(inbound_text, step, lead.status)

    # If already past interested/opted_out/not_interested — stop
    if new_status in ("opted_out", "not_interested") and step >= 2:
        # Still send the appropriate closing message
        pass
    if new_step >= 3 and new_status == "interested":
        # Send ack then stop
        pass

    # Try OpenAI first
    history = conversation_history or []
    ai_reply = _openai_reply(lead, history, inbound_text)

    if ai_reply:
        return ai_reply, new_status, new_step

    # Fallback to templates
    reply, fb_status, fb_step = _template_reply(lead, inbound_text, step)
    return reply, fb_status, fb_step


# ------------------------------------------------------------------ #
# Initial & follow-up messages
# ------------------------------------------------------------------ #

def get_initial_message(lead) -> str:
    return _fill(
        _s("msg_opening"),
        business_name=lead.business_name or "there",
        agent_name=_s("sms_agent_name"),
        company_name=_s("business_name"),
    )


def get_followup_message(lead) -> str:
    count = (lead.followup_count or 0) + 1
    key   = "msg_followup_2" if count >= 2 else "msg_followup_1"
    return _fill(
        _s(key),
        business_name=lead.business_name or "there",
        agent_name=_s("sms_agent_name"),
        company_name=_s("business_name"),
    )


# ------------------------------------------------------------------ #
# AI Chat simulation (for the test UI)
# ------------------------------------------------------------------ #

def simulate_conversation(business_name: str, rating: float, reviews: int, messages: list) -> str:
    bot_count    = sum(1 for m in messages if m.get("role") == "bot")
    current_step = max(0, bot_count - 1) if bot_count > 0 else 0

    class FakeLead:
        pass

    fake = FakeLead()
    fake.id            = 0
    fake.business_name = business_name
    fake.rating        = rating
    fake.reviews_count = reviews
    fake.conversation_step = current_step
    fake.followup_count    = 0
    fake.status            = "message_sent"

    if not messages:
        return get_initial_message(fake)

    last_user = next((m["text"] for m in reversed(messages) if m.get("role") == "user"), "")
    if not last_user:
        return get_initial_message(fake)

    history = [{"direction": "outbound" if m.get("role") == "bot" else "inbound",
                "message": m.get("text", "")} for m in messages]

    reply, _, _ = get_next_message(fake, last_user, history)
    return reply or "Thanks for your message! Our team will be in touch shortly. 😊"
