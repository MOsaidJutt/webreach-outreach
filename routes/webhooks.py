import hmac
import hashlib
import logging
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import Lead, Conversation, WebhookEvent, AppSettings
from services.conversation_ai import resolve_reply
from services.ghl_service import GHLService
from sqlalchemy import select

webhooks_bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Payload parsing — GHL sends several different shapes
# ------------------------------------------------------------------ #

def _verify_ghl_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify only when GHL actually sent a signature.

    A configured secret used to reject *every* webhook, because GHL workflow
    webhooks send no `X-GHL-Signature` header at all — the result was a silent
    401 on every inbound message.
    """
    if not secret or not signature:
        return True
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _extract_text(value) -> str:
    """The message arrives as a plain string or as an object like {"body": ...}."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("body", "message", "text", "content"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _first_text(*candidates) -> str:
    for c in candidates:
        text = _extract_text(c)
        if text:
            return text
    return ""


def _extract_contact_id(payload: dict, custom: dict) -> str:
    for source in (payload, custom):
        for key in ("contactId", "contact_id", "contactID"):
            val = source.get(key)
            if isinstance(val, str) and val:
                return val
    contact_obj = payload.get("contact")
    if isinstance(contact_obj, dict):
        val = contact_obj.get("id") or contact_obj.get("contactId")
        if isinstance(val, str) and val:
            return val
    val = payload.get("id")
    if isinstance(val, str) and val:
        return val
    return ""


def _extract_phone(payload: dict, custom: dict) -> str:
    for source in (payload, custom):
        for key in ("phone", "contactPhone", "contact_phone", "from",
                    "fromNumber", "from_number", "number", "phoneNumber"):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    contact_obj = payload.get("contact")
    if isinstance(contact_obj, dict):
        for key in ("phone", "phoneNumber"):
            val = contact_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _digits(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone or "")


def _is_outbound(payload: dict) -> bool:
    """Never let the AI answer our own messages."""
    direction = payload.get("direction") or payload.get("messageDirection") or ""
    return isinstance(direction, str) and direction.lower() == "outbound"


# ------------------------------------------------------------------ #
# Lead resolution
# ------------------------------------------------------------------ #

def _find_lead_by_phone(phone: str) -> Lead | None:
    """Match on the last 10 digits so +1/spacing/dashes never block a match."""
    clean = _digits(phone)[-10:]
    if len(clean) < 7:
        return None
    for lead in db.session.execute(select(Lead).where(Lead.phone.isnot(None))).scalars():
        if _digits(lead.phone).endswith(clean):
            return lead
    return None


def _resolve_lead(contact_id: str, phone: str) -> tuple[Lead | None, str]:
    """
    Find the lead this webhook is about, and self-heal the link when possible.

    Returns (lead, how_it_was_found).
    """
    if contact_id:
        lead = db.session.execute(
            select(Lead).where(Lead.ghl_contact_id == contact_id)
        ).scalar_one_or_none()
        if lead:
            return lead, "contact_id"

    if phone:
        lead = _find_lead_by_phone(phone)
        if lead:
            if contact_id and lead.ghl_contact_id != contact_id:
                lead.ghl_contact_id = contact_id
            return lead, "phone"

    # The payload carried a contact id we don't know and no phone number. Ask
    # GHL who that contact is, then match on their phone. This is what recovers
    # every lead after GHL contacts are deleted and recreated with new ids —
    # previously that combination dropped the message with no reply.
    if contact_id:
        try:
            contact = GHLService().get_contact(contact_id)
            api_phone = (contact.get("phone") or "").strip()
            if api_phone:
                lead = _find_lead_by_phone(api_phone)
                if lead:
                    lead.ghl_contact_id = contact_id
                    if not lead.phone:
                        lead.phone = api_phone
                    return lead, "ghl_api_lookup"
        except Exception as e:
            logger.warning(f"GHL contact lookup failed for {contact_id}: {e}")

    return None, "not_found"


def _autocreate_lead(contact_id: str, phone: str) -> Lead | None:
    """
    Create a lead for an inbound message from an unknown number so a fresh test
    (or a genuine reply from a number that was never imported) still gets an
    answer instead of being silently dropped.
    """
    if AppSettings.get("webhook_autocreate_leads", "true").lower() != "true":
        return None

    resolved_phone, name = phone, ""
    if contact_id:
        try:
            contact = GHLService().get_contact(contact_id)
            resolved_phone = (contact.get("phone") or phone or "").strip()
            name = (contact.get("firstName") or contact.get("name")
                    or contact.get("companyName") or "").strip()
        except Exception as e:
            logger.warning(f"Could not fetch GHL contact {contact_id} for auto-create: {e}")

    if not resolved_phone:
        return None

    lead = Lead(
        business_name=name or f"Unknown ({resolved_phone})",
        phone=resolved_phone,
        ghl_contact_id=contact_id or None,
        imported_to_ghl=bool(contact_id),
        imported_to_ghl_at=datetime.utcnow() if contact_id else None,
        status="message_sent",
        conversation_step=0,
        notes="Auto-created from an inbound SMS — this number was not in the lead list.",
    )
    db.session.add(lead)
    db.session.flush()
    logger.info(f"Auto-created lead {lead.id} for inbound from {resolved_phone}")
    return lead


# ------------------------------------------------------------------ #
# Event log
# ------------------------------------------------------------------ #

def _record(outcome, detail, *, contact_id="", phone="", message="",
            lead=None, reply=None, raw_body=b""):
    """Record the webhook hit. Never let logging break the request."""
    try:
        body = raw_body[:4000].decode("utf-8", errors="replace") if raw_body else ""
        db.session.add(WebhookEvent(
            outcome=outcome, detail=detail, contact_id=contact_id or "",
            phone=phone or "", message=(message or "")[:2000],
            lead_id=getattr(lead, "id", None), reply=reply, raw_body=body,
        ))
        db.session.commit()
    except Exception as e:
        logger.warning(f"Could not record webhook event: {e}")
        db.session.rollback()


# ------------------------------------------------------------------ #
# The webhook
# ------------------------------------------------------------------ #

@webhooks_bp.errorhandler(Exception)
def _webhook_crashed(exc):
    """
    Never let a webhook fail silently again.

    An unhandled AttributeError here returned Flask's bare 500 page to GHL for
    weeks. GHL retried, gave up, and marked the action failed — while the
    dashboard showed nothing at all, because the crash happened before anything
    was recorded. Now the traceback is logged and the failure is written to the
    event log, so it shows up in Admin -> Inbound Health like any other outcome.
    """
    import traceback

    tb = traceback.format_exc()
    logger.error(f"Webhook handler crashed: {exc}\n{tb}")
    try:
        db.session.rollback()
    except Exception:
        pass
    _record("error", f"Unhandled {type(exc).__name__}: {exc}",
            raw_body=request.get_data() or b"")
    # 500 so GHL retries — the message is not lost if this was transient.
    return jsonify({"error": "Webhook handler failed",
                    "detail": f"{type(exc).__name__}: {exc}"}), 500


@webhooks_bp.route("/ghl", methods=["GET"])
def ghl_webhook_probe():
    """
    Opening the webhook URL in a browser used to return 405, which looks
    identical to a broken deployment. Returning a status here means the URL can
    be checked from any phone or browser: if you see this JSON, the server is
    reachable and the endpoint is live, so any silence is GHL not calling it.
    """
    from version import BUILD
    from sqlalchemy import func

    total = db.session.scalar(select(func.count(WebhookEvent.id))) or 0
    last = db.session.execute(
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    return jsonify({
        "status": "ready",
        "build": BUILD,
        "message": "This endpoint is live. GHL should POST inbound SMS here.",
        "webhooks_received_ever": total,
        "last_webhook_at": last.created_at.isoformat() if last else None,
        "last_webhook_outcome": last.outcome if last else None,
        "hint": ("If webhooks_received_ever is 0, GHL has never called this URL — "
                 "check the workflow is published and its webhook action points here."),
    }), 200


@webhooks_bp.route("/ghl", methods=["POST"])
def ghl_webhook():
    raw_body = request.get_data()

    try:
        logger.info(
            "GHL webhook received | headers=%s | body=%s",
            dict(request.headers), raw_body[:2000].decode("utf-8", errors="replace"),
        )
    except Exception as e:
        logger.warning(f"GHL webhook: failed to log raw request: {e}")

    secret = current_app.config.get("WEBHOOK_SECRET", "")
    sig = request.headers.get("X-GHL-Signature", "")
    if not _verify_ghl_signature(raw_body, sig, secret):
        logger.warning("GHL webhook rejected: invalid signature")
        _record("ignored", "Invalid X-GHL-Signature", raw_body=raw_body)
        return jsonify({"error": "Invalid signature"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        _record("error", f"Payload was {type(payload).__name__}, expected object", raw_body=raw_body)
        return jsonify({"error": "Expected a JSON object"}), 400

    custom = payload.get("customData") or {}
    if not isinstance(custom, dict):
        custom = {}

    event_type = payload.get("type", "")
    message_type = payload.get("messageType", "SMS")

    if event_type and event_type not in ("InboundMessage", "SMS", "ConversationUnreadUpdate"):
        _record("ignored", f"Event type '{event_type}'", raw_body=raw_body)
        return jsonify({"message": f"Event type '{event_type}' ignored"}), 200

    if message_type and message_type not in ("SMS", ""):
        _record("ignored", f"Message type '{message_type}'", raw_body=raw_body)
        return jsonify({"message": "Non-SMS ignored"}), 200

    if _is_outbound(payload):
        _record("ignored", "Outbound message — not a lead reply", raw_body=raw_body)
        return jsonify({"message": "Outbound ignored"}), 200

    contact_id = _extract_contact_id(payload, custom)
    phone = _extract_phone(payload, custom)
    conversation_id = (payload.get("conversationId") or payload.get("conversation_id")
                       or custom.get("conversationId") or "")
    ghl_message_id = payload.get("messageId") or payload.get("message_id", "")

    inbound_text = _first_text(
        payload.get("message"), custom.get("message"), payload.get("body"),
        payload.get("messageBody"), payload.get("message_body"),
        payload.get("last_message_body"), payload.get("text"),
        payload.get("sms_body"), custom.get("body"), custom.get("text"),
    )

    if not inbound_text:
        logger.warning(
            f"GHL webhook: no message text | contact_id={contact_id!r} keys={list(payload.keys())}"
        )
        _record("no_text", f"No message text in payload. Keys: {list(payload.keys())}",
                contact_id=contact_id, phone=phone, raw_body=raw_body)
        return jsonify({"error": "Missing message text",
                        "hint": "Map the inbound message body to a 'message' or 'body' field"}), 400

    lead, found_by = _resolve_lead(contact_id, phone)
    if not lead:
        lead = _autocreate_lead(contact_id, phone)
        found_by = "auto_created" if lead else "not_found"

    if not lead:
        logger.warning(f"GHL webhook: no lead | contact_id={contact_id!r} phone={phone!r}")
        _record("no_lead",
                f"No lead matches contact_id={contact_id or '(none)'} phone={phone or '(none)'}",
                contact_id=contact_id, phone=phone, message=inbound_text, raw_body=raw_body)
        return jsonify({"message": "Contact not found", "received": True}), 200

    # Persist the inbound message first, and flush it so the history the engine
    # sees includes the message it is answering.
    db.session.add(Conversation(
        lead_id=lead.id, direction="inbound", message=inbound_text,
        step=lead.conversation_step, status="received", ghl_message_id=ghl_message_id,
    ))
    if conversation_id:
        lead.ghl_conversation_id = conversation_id
    lead.updated_at = datetime.utcnow()
    db.session.flush()

    if lead.ai_paused:
        db.session.commit()
        logger.info(f"Lead {lead.id} is in manual mode — inbound logged, AI skipped")
        _record("manual_mode", f"{lead.business_name} is in manual takeover",
                contact_id=contact_id, phone=phone, message=inbound_text,
                lead=lead, raw_body=raw_body)
        return jsonify({"message": "Manual mode — message logged, AI skipped"}), 200

    history = [c.to_dict() for c in lead.conversations]

    try:
        decision = resolve_reply(lead, inbound_text, history)
    except Exception as e:
        logger.exception(f"Reply engine failed for lead {lead.id}: {e}")
        db.session.commit()
        _record("error", f"Reply engine error: {e}", contact_id=contact_id, phone=phone,
                message=inbound_text, lead=lead, raw_body=raw_body)
        return jsonify({"error": "Reply engine failed", "detail": str(e)}), 500

    if not decision.reply:
        lead.status = decision.status
        lead.conversation_step = decision.step
        db.session.commit()
        _record("ignored", f"No reply is due at step {decision.step} (intent: {decision.intent})",
                contact_id=contact_id, phone=phone, message=inbound_text,
                lead=lead, raw_body=raw_body)
        return jsonify({"message": "No reply due", "intent": decision.intent,
                        "new_status": decision.status}), 200

    sent, send_error = _send_reply(lead, decision.reply, lead.ghl_conversation_id or conversation_id)

    if sent:
        # Only advance the lead once the reply is genuinely out of the door.
        # Advancing on a failed send used to desynchronise the conversation
        # permanently — the next inbound was answered from the wrong step.
        lead.status = decision.status
        lead.conversation_step = decision.step
        lead.last_contacted_at = datetime.utcnow()

    db.session.commit()

    if sent and decision.status == "interested":
        _tag_interested_lead(lead)

    _record("replied" if sent else "send_failed",
            f"{decision.intent} → {decision.template_key or decision.source}"
            + ("" if sent else f" | send failed: {send_error}"),
            contact_id=contact_id, phone=phone, message=inbound_text,
            lead=lead, reply=decision.reply, raw_body=raw_body)

    return jsonify({
        "message": "Processed" if sent else "Reply generated but sending failed",
        "sent": sent,
        "error": send_error,
        "intent": decision.intent,
        "template": decision.template_key,
        "source": decision.source,
        "reply": decision.reply,
        "new_status": lead.status,
        "new_step": lead.conversation_step,
        "lead": {"id": lead.id, "business_name": lead.business_name, "found_by": found_by},
    }), (200 if sent else 502)


def _send_reply(lead, message: str, conversation_id: str) -> tuple[bool, str]:
    """
    Send the reply. Returns (sent, error) so a failure is visible in the
    response and the event log instead of being swallowed.
    """
    try:
        ghl = GHLService()
        result = ghl.send_sms(lead.ghl_contact_id, message, conversation_id)
        db.session.add(Conversation(
            lead_id=lead.id, direction="outbound", message=message,
            # Not "sent": answering someone who messaged us is not outreach and
            # must not consume the daily outreach limit.
            step=lead.conversation_step, status="ai_reply",
            ghl_message_id=result.get("messageId", ""),
        ))
        return True, ""
    except Exception as e:
        logger.error(f"Failed to send reply to lead {lead.id}: {e}")
        db.session.add(Conversation(
            lead_id=lead.id, direction="outbound", message=message,
            step=lead.conversation_step, status="failed",
        ))
        return False, str(e)


def _tag_interested_lead(lead):
    try:
        ghl = GHLService()
        ghl.add_tag(lead.ghl_contact_id, "interested-in-website")
        ghl.add_note_to_contact(
            lead.ghl_contact_id,
            f"🔥 INTERESTED — {lead.business_name} wants to see the website. "
            "Create on Lovable and send the link manually."
        )
    except Exception as e:
        logger.warning(f"Could not tag lead {lead.id} in GHL: {e}")
