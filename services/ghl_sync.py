"""
Pull message history from GHL into the local conversation log.

The local log only ever contained what this application itself sent or
received. Anything typed into GHL by hand, and every inbound reply that
arrived while the webhook was failing, was invisible here. Syncing makes the
Conversations page show the real thread rather than our partial view of it.
"""

import logging
from datetime import datetime

from extensions import db
from models import Lead, Conversation
from services.ghl_service import GHLService

logger = logging.getLogger(__name__)


def _parse_ts(value):
    """GHL sends ISO-8601, sometimes with a Z, sometimes with millis."""
    if not value:
        return None
    if isinstance(value, (int, float)):          # epoch millis
        try:
            return datetime.utcfromtimestamp(value / 1000 if value > 1e11 else value)
        except Exception:
            return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _direction_of(msg: dict) -> str:
    raw = (msg.get("direction") or "").lower()
    if raw in ("inbound", "outbound"):
        return raw
    # Older payloads use a numeric type: 1 = inbound, 2 = outbound.
    if msg.get("type") in (1, "1"):
        return "inbound"
    return "outbound"


def _body_of(msg: dict) -> str:
    for key in ("body", "message", "text"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sync_lead_messages(lead: Lead, ghl: GHLService = None) -> dict:
    """
    Bring one lead's thread up to date from GHL.

    Returns {"added": n, "fetched": n, "conversation_id": str, "error": str|None}.
    Never raises — a lead that cannot be synced must not break the page.
    """
    result = {"added": 0, "fetched": 0, "conversation_id": None, "error": None}

    if not lead.ghl_contact_id:
        result["error"] = "Lead is not linked to a GHL contact"
        return result

    try:
        ghl = ghl or GHLService()

        conversation_id = lead.ghl_conversation_id
        if not conversation_id:
            convo = ghl.find_conversation(lead.ghl_contact_id)
            if not convo:
                result["error"] = "No conversation in GHL yet"
                return result
            conversation_id = convo.get("id") or convo.get("conversationId")
            if conversation_id:
                lead.ghl_conversation_id = conversation_id

        result["conversation_id"] = conversation_id
        if not conversation_id:
            result["error"] = "GHL returned no conversation id"
            return result

        messages = ghl.get_conversation_messages(conversation_id)
        result["fetched"] = len(messages)

        existing = list(lead.conversations)
        known_ids = {c.ghl_message_id for c in existing if c.ghl_message_id}
        # Messages this app sent were logged without a GHL id in older builds,
        # so also match on direction + text to avoid duplicating them.
        by_body = {}
        for c in existing:
            by_body.setdefault((c.direction, (c.message or "").strip()), c)

        added = 0
        for msg in messages:
            body = _body_of(msg)
            if not body:
                continue

            msg_id = msg.get("id") or msg.get("messageId") or ""
            if msg_id and msg_id in known_ids:
                continue

            direction = _direction_of(msg)
            timestamp = _parse_ts(msg.get("dateAdded") or msg.get("createdAt"))

            match = by_body.get((direction, body))
            if match is not None:
                # Same message, recorded locally without GHL's id. Adopt the id
                # and the real send time so the thread reads in the right order
                # and future syncs can match on id alone.
                if msg_id and not match.ghl_message_id:
                    match.ghl_message_id = msg_id
                    known_ids.add(msg_id)
                if timestamp:
                    match.created_at = timestamp
                continue

            record = Conversation(
                lead_id=lead.id,
                direction=direction,
                message=body,
                ghl_message_id=msg_id,
                step=lead.conversation_step,
                status="synced",
            )
            if timestamp:
                record.created_at = timestamp

            db.session.add(record)
            if msg_id:
                known_ids.add(msg_id)
            by_body[(direction, body)] = record
            added += 1

        if added:
            lead.updated_at = datetime.utcnow()
        db.session.commit()
        result["added"] = added
        logger.info(f"Synced lead {lead.id} '{lead.business_name}': "
                    f"{added} new of {len(messages)} message(s)")

    except Exception as e:
        db.session.rollback()
        result["error"] = str(e)
        logger.warning(f"GHL sync failed for lead {getattr(lead, 'id', '?')}: {e}")

    return result


def sync_many(app, lead_ids, ):
    """Sync a batch in the background. Progress is readable via get_sync_state()."""
    with app.app_context():
        _sync_state.update({"running": True, "done": 0, "added": 0,
                            "total": len(lead_ids), "errors": []})
        try:
            ghl = GHLService()
            for lead_id in lead_ids:
                lead = db.session.get(Lead, lead_id)
                if not lead:
                    continue
                outcome = sync_lead_messages(lead, ghl)
                _sync_state["done"] += 1
                _sync_state["added"] += outcome["added"]
                if outcome["error"] and "No conversation" not in outcome["error"]:
                    _sync_state["errors"].append(f"{lead.business_name}: {outcome['error']}")
        except Exception as e:
            _sync_state["errors"].append(str(e))
            logger.exception(f"Bulk GHL sync failed: {e}")
        finally:
            _sync_state["running"] = False
            logger.info(f"Bulk GHL sync finished: {_sync_state['added']} message(s) added")


_sync_state = {"running": False, "done": 0, "added": 0, "total": 0, "errors": []}


def get_sync_state() -> dict:
    state = dict(_sync_state)
    state["errors"] = state["errors"][-10:]
    return state
