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
    event_type = payload.get("type", "")

    if event_type != "InboundMessage":
        return jsonify({"message": "Event ignored"}), 200

    if payload.get("messageType", "") != "SMS":
        return jsonify({"message": "Non-SMS ignored"}), 200

    contact_id = payload.get("contactId", "")
    conversation_id = payload.get("conversationId", "")
    inbound_text = payload.get("message", "").strip()
    ghl_message_id = payload.get("messageId", "")

    if not contact_id or not inbound_text:
        return jsonify({"error": "Missing contactId or message"}), 400

    lead = db.session.execute(
        select(Lead).where(Lead.ghl_contact_id == contact_id)
    ).scalar_one_or_none()

    if not lead:
        logger.warning(f"SMS from unknown GHL contact: {contact_id}")
        return jsonify({"message": "Contact not in system"}), 200

    db.session.add(Conversation(
        lead_id=lead.id, direction="inbound", message=inbound_text,
        step=lead.conversation_step, status="received", ghl_message_id=ghl_message_id,
    ))

    reply_text, new_status, new_step = get_next_message(lead, inbound_text)

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
