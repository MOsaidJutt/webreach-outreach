import hmac
import hashlib
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import Lead, Conversation
from services.conversation_ai import get_next_message
from services.ghl_service import GHLService
from sqlalchemy import select

webhooks_bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)


def _verify_ghl_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return True
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or ""))


@webhooks_bp.route("/ghl", methods=["POST"])
def ghl_webhook():
    raw_body = request.get_data()

    secret = current_app.config.get("WEBHOOK_SECRET", "")
    sig = request.headers.get("X-GHL-Signature", "")
    if not _verify_ghl_signature(raw_body, sig, secret):
        return jsonify({"error": "Invalid signature"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    event_type   = payload.get("type", "")
    message_type = payload.get("messageType", "SMS")

    # Accept InboundMessage events, workflow webhooks (no type), or any SMS event
    if event_type and event_type not in ("InboundMessage", "SMS"):
        return jsonify({"message": f"Event type '{event_type}' ignored"}), 200

    if message_type and message_type not in ("SMS", ""):
        return jsonify({"message": "Non-SMS ignored"}), 200

    # GHL Workflow sends custom data merged at top level or nested under customData
    custom = payload.get("customData") or {}

    contact_id      = (payload.get("contactId") or payload.get("contact_id") or
                       custom.get("contactId") or payload.get("id", ""))
    conversation_id = (payload.get("conversationId") or payload.get("conversation_id") or
                       custom.get("conversationId", ""))
    inbound_text    = (payload.get("message") or custom.get("message") or
                       payload.get("body") or payload.get("messageBody") or
                       payload.get("last_message_body", "")).strip()
    ghl_message_id  = payload.get("messageId") or payload.get("message_id", "")

    if not contact_id or not inbound_text:
        return jsonify({"error": "Missing contactId or message"}), 400

    lead = None
    if contact_id:
        lead = db.session.execute(
            select(Lead).where(Lead.ghl_contact_id == contact_id)
        ).scalar_one_or_none()

    # Fallback: match by phone number
    if not lead:
        import re as _re
        phone_raw = (payload.get("phone") or custom.get("phone") or
                     payload.get("contactPhone", "")).strip()
        if phone_raw:
            clean = _re.sub(r"[^\d]", "", phone_raw)[-10:]
            for l in db.session.execute(select(Lead)).scalars().all():
                if l.phone and _re.sub(r"[^\d]", "", l.phone).endswith(clean):
                    lead = l
                    if contact_id and not lead.ghl_contact_id:
                        lead.ghl_contact_id = contact_id
                        db.session.commit()
                    break

    if not lead:
        logger.warning(f"Webhook: no lead found for contact_id={contact_id} keys={list(payload.keys())}")
        return jsonify({"message": "Contact not found", "received": True}), 200

    db.session.add(Conversation(
        lead_id=lead.id, direction="inbound", message=inbound_text,
        step=lead.conversation_step, status="received", ghl_message_id=ghl_message_id,
    ))

    conversation_history = [c.to_dict() for c in lead.conversations]
    reply_text, new_status, new_step = get_next_message(lead, inbound_text, conversation_history)

    lead.status = new_status
    lead.conversation_step = new_step
    lead.updated_at = datetime.utcnow()
    if conversation_id:
        lead.ghl_conversation_id = conversation_id

    db.session.commit()

    if reply_text:
        _send_reply(lead, reply_text, conversation_id)
        if new_status == "interested":
            _tag_interested_lead(lead)

    return jsonify({"message": "Processed", "new_status": new_status}), 200


def _send_reply(lead, message: str, conversation_id: str):
    try:
        ghl = GHLService()
        result = ghl.send_sms(lead.ghl_contact_id, message, conversation_id)
        db.session.add(Conversation(
            lead_id=lead.id, direction="outbound", message=message,
            step=lead.conversation_step, status="sent",
            ghl_message_id=result.get("messageId", ""),
        ))
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to send reply to lead {lead.id}: {e}")


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
