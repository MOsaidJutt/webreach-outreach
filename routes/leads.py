import io
import logging
from datetime import datetime
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from extensions import db
from models import Lead, Conversation
from services.ghl_service import GHLService
from services.conversation_ai import get_initial_message
from sqlalchemy import select, or_
from auth import require_admin_password

leads_bp = Blueprint("leads", __name__)
logger = logging.getLogger(__name__)

# Outscraper CSV column name variants → our internal field names
OUTSCRAPER_COLUMN_MAP = {
    "name":             "business_name",
    "business_name":    "business_name",
    "title":            "business_name",
    "rating":           "rating",
    "reviews":          "reviews_count",
    "reviews_count":    "reviews_count",
    "user_ratings_total": "reviews_count",
    "phone":            "phone",
    "phone_1":          "phone",
    "type":             "category",
    "category":         "category",
    "categories":       "category",
    "full_address":     "address",
    "address":          "address",
    "street":           "address",
    "city":             "city",
    "state":            "state",
    "country_code":     "country",
    "country":          "country",
    "place_id":         "place_id",
    "google_id":        "place_id",
    "site":             "website",
    "website":          "website",
    "url":              "google_maps_url",
    "google_maps_url":  "google_maps_url",
    "maps_url":         "google_maps_url",
    "link":             "google_maps_url",
}

VALID_STATUSES = [
    "not_contacted", "message_sent", "replied", "interested",
    "not_interested", "opted_out", "website_sent", "converted",
]


@leads_bp.route("/", methods=["GET"])
def list_leads():
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    status   = request.args.get("status", "")
    search   = request.args.get("search", "")
    sort_by  = request.args.get("sort_by", "created_at")
    sort_dir = request.args.get("sort_dir", "desc")

    has_phone    = request.args.get("has_phone", "")
    has_website  = request.args.get("has_website", "")
    min_rating   = request.args.get("min_rating", "")
    imported_ghl = request.args.get("imported_ghl", "")

    stmt = select(Lead)

    if status:
        stmt = stmt.where(Lead.status == status)

    if imported_ghl == "yes":
        stmt = stmt.where(Lead.imported_to_ghl == True)
    elif imported_ghl == "no":
        stmt = stmt.where(or_(Lead.imported_to_ghl == False, Lead.imported_to_ghl.is_(None)))

    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(
            Lead.business_name.ilike(like),
            Lead.phone.ilike(like),
            Lead.city.ilike(like),
            Lead.category.ilike(like),
        ))

    if has_phone == "yes":
        stmt = stmt.where(Lead.phone.isnot(None), Lead.phone != "")
    elif has_phone == "no":
        stmt = stmt.where(or_(Lead.phone.is_(None), Lead.phone == ""))

    if has_website == "no":
        stmt = stmt.where(or_(Lead.website.is_(None), Lead.website == ""))
    elif has_website == "yes":
        stmt = stmt.where(Lead.website.isnot(None), Lead.website != "")

    if min_rating:
        try:
            stmt = stmt.where(Lead.rating >= float(min_rating))
        except ValueError:
            pass

    sort_col = getattr(Lead, sort_by, Lead.created_at)
    stmt = stmt.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    return jsonify({
        "leads": [l.to_dict() for l in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
        "per_page": per_page,
    })


@leads_bp.route("/<int:lead_id>", methods=["GET"])
def get_lead(lead_id):
    """
    One lead plus its thread.

    Pass ?sync=1 to pull the latest messages from GHL first, so the thread
    shown is GHL's, not just the subset this application happens to have
    recorded.
    """
    lead = db.get_or_404(Lead, lead_id)

    sync_result = None
    if request.args.get("sync") in ("1", "true", "yes"):
        from services.ghl_sync import sync_lead_messages
        sync_result = sync_lead_messages(lead)

    data = lead.to_dict()
    data["conversations"] = [c.to_dict() for c in lead.conversations]
    return jsonify({"lead": data, "sync": sync_result})


@leads_bp.route("/conversation-list", methods=["GET"])
def conversation_list():
    """
    Compact lead list for the Conversations page.

    The page used to load /api/leads/ with per_page=500 and then hide anything
    still 'not_contacted' — so most of the list was unreachable. This returns
    only the fields that page needs, plus the last message, and filters and
    pages on the server so every lead can be found.
    """
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 100)), 500)
    status = request.args.get("status", "")
    search = request.args.get("search", "")
    only = request.args.get("only", "")          # "manual" | "with_messages" | ""

    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    if only == "manual":
        stmt = stmt.where(Lead.ai_paused == True)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Lead.business_name.ilike(like), Lead.phone.ilike(like)))
    if only == "with_messages":
        stmt = stmt.where(Lead.id.in_(select(Conversation.lead_id).distinct()))

    stmt = stmt.order_by(Lead.updated_at.desc().nullslast())
    pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    lead_ids = [l.id for l in pagination.items]
    last_message = {}
    unread = {}
    if lead_ids:
        rows = db.session.execute(
            select(Conversation)
            .where(Conversation.lead_id.in_(lead_ids))
            .order_by(Conversation.created_at.asc())
        ).scalars().all()
        for c in rows:
            last_message[c.lead_id] = c
            if c.direction == "inbound":
                unread[c.lead_id] = True
            else:
                unread[c.lead_id] = False

    def row(lead):
        last = last_message.get(lead.id)
        return {
            "id": lead.id,
            "business_name": lead.business_name,
            "phone": lead.phone,
            "status": lead.status,
            "status_label": lead.STATUS_LABELS.get(lead.status, lead.status),
            "ai_paused": bool(lead.ai_paused),
            "send_queued": lead.send_queued_at is not None,
            "imported_to_ghl": bool(lead.imported_to_ghl),
            "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
            "last_message": (last.message or "")[:120] if last else "",
            "last_direction": last.direction if last else "",
            "last_at": last.created_at.isoformat() if last and last.created_at else None,
            "awaiting_reply": bool(unread.get(lead.id)),
        }

    return jsonify({
        "leads": [row(l) for l in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
        "per_page": per_page,
    })


@leads_bp.route("/<int:lead_id>/sync-ghl", methods=["POST"])
@require_admin_password
def sync_lead_ghl(lead_id):
    """Pull this lead's messages from GHL into the local thread."""
    from services.ghl_sync import sync_lead_messages

    lead = db.get_or_404(Lead, lead_id)
    result = sync_lead_messages(lead)
    data = lead.to_dict()
    data["conversations"] = [c.to_dict() for c in lead.conversations]
    return jsonify({"sync": result, "lead": data})


@leads_bp.route("/sync-ghl-bulk", methods=["POST"])
@require_admin_password
def sync_ghl_bulk():
    """Sync many leads in the background; poll /sync-ghl-status for progress."""
    import threading
    from flask import current_app
    from services.ghl_sync import sync_many, get_sync_state

    if get_sync_state()["running"]:
        return jsonify({"message": "A sync is already running.", "state": get_sync_state()})

    data = request.get_json() or {}
    lead_ids = data.get("lead_ids")
    if not lead_ids:
        limit = min(int(data.get("limit", 200)), 1000)
        lead_ids = [
            l.id for l in db.session.execute(
                select(Lead).where(Lead.ghl_contact_id.isnot(None))
                .order_by(Lead.updated_at.desc().nullslast()).limit(limit)
            ).scalars().all()
        ]

    if not lead_ids:
        return jsonify({"message": "No GHL-linked leads to sync."})

    app = current_app._get_current_object()
    threading.Thread(target=sync_many, args=(app, lead_ids), daemon=True).start()
    return jsonify({"message": f"Syncing {len(lead_ids)} lead(s) from GHL in the background.",
                    "total": len(lead_ids)})


@leads_bp.route("/sync-ghl-status", methods=["GET"])
def sync_ghl_status():
    from services.ghl_sync import get_sync_state
    return jsonify(get_sync_state())


@leads_bp.route("/<int:lead_id>/status", methods=["PUT"])
@require_admin_password
def update_status(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json() or {}
    new_status = data.get("status", "")

    if new_status not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status. Choose from: {VALID_STATUSES}"}), 400

    lead.status = new_status
    lead.updated_at = datetime.utcnow()

    if new_status == "website_sent":
        lead.website_url_sent = data.get("website_url", lead.website_url_sent)
        lead.website_sent_at = datetime.utcnow()

    if data.get("notes"):
        lead.notes = data["notes"]

    db.session.commit()
    return jsonify({"lead": lead.to_dict()})


@leads_bp.route("/<int:lead_id>/notes", methods=["PUT"])
@require_admin_password
def update_notes(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json() or {}
    lead.notes = data.get("notes", lead.notes)
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"lead": lead.to_dict()})


@leads_bp.route("/<int:lead_id>/import-to-ghl", methods=["POST"])
@require_admin_password
def import_to_ghl(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    if not lead.phone:
        return jsonify({"error": "Lead has no phone number"}), 400
    try:
        ghl = GHLService()
        contact = ghl.create_or_update_contact(lead.to_dict())
        logger.info(f"GHL contact response: {contact}")
        contact_id = contact.get("id") or contact.get("contactId") or contact.get("contact", {}).get("id")
        if not contact_id:
            logger.warning(f"No contact ID in GHL response: {contact}")
            return jsonify({"error": f"GHL returned no contact ID. Response: {contact}"}), 500
        lead.ghl_contact_id = contact_id
        lead.imported_to_ghl = True
        lead.imported_to_ghl_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"message": f"Imported to GHL (ID: {contact_id})", "ghl_contact": contact, "lead": lead.to_dict()})
    except Exception as e:
        logger.error(f"GHL import failed for lead {lead_id}: {e}")
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/import-all-to-ghl", methods=["POST"])
@require_admin_password
def import_all_to_ghl():
    data = request.get_json() or {}
    status_filter = data.get("status", "")

    stmt = select(Lead).where(Lead.imported_to_ghl == False)
    if status_filter:
        stmt = stmt.where(Lead.status == status_filter)
    leads = db.session.execute(stmt).scalars().all()

    ghl = GHLService()
    results = {"success": 0, "failed": 0, "errors": []}

    for lead in leads:
        if not lead.phone:
            results["failed"] += 1
            continue
        try:
            contact = ghl.create_or_update_contact(lead.to_dict())
            lead.ghl_contact_id = contact.get("id") or contact.get("contactId")
            lead.imported_to_ghl = True
            lead.imported_to_ghl_at = datetime.utcnow()
            results["success"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"Lead {lead.id} ({lead.business_name}): {str(e)}")

    db.session.commit()
    return jsonify({"message": "Batch import complete", "results": results})


@leads_bp.route("/<int:lead_id>/send-initial-sms", methods=["POST"])
@require_admin_password
def send_initial_sms(lead_id):
    """
    Queue one lead's opening SMS.

    This queues rather than sends inline. The Leads page loops over selected
    leads calling this endpoint, so sending here directly put a whole batch out
    within a couple of seconds regardless of the configured gap.
    """
    from services.smart_sender import queue_leads_for_send, get_queue_state

    lead = db.get_or_404(Lead, lead_id)

    if not lead.ghl_contact_id:
        return jsonify({"error": "Lead not imported to GHL yet. Import first."}), 400
    if lead.status != "not_contacted":
        return jsonify({"error": f"Lead already contacted (status: {lead.status})"}), 400
    if lead.send_queued_at is not None:
        return jsonify({"message": "Already queued", "lead": lead.to_dict()})

    queue_leads_for_send([lead.id])
    state = get_queue_state()
    return jsonify({
        "message": f"Queued. {state['pending']} message(s) waiting, "
                   f"going out {state['min_gap_mins']}-{state['max_gap_mins']} minutes apart.",
        "queued": 1,
        "queue": state,
        "lead": lead.to_dict(),
    })


@leads_bp.route("/send-selected-sms", methods=["POST"])
@require_admin_password
def send_selected_sms():
    """Queue a specific set of leads in one call, instead of one call per lead."""
    from services.smart_sender import queue_leads_for_send, get_queue_state

    data = request.get_json() or {}
    lead_ids = data.get("lead_ids") or []
    if not lead_ids:
        return jsonify({"error": "lead_ids is required"}), 400

    queued = queue_leads_for_send(lead_ids)
    state = get_queue_state()
    return jsonify({
        "message": (f"Queued {queued} message(s). They go out one at a time, "
                    f"{state['min_gap_mins']}-{state['max_gap_mins']} minutes apart "
                    f"— roughly {state['estimated_minutes']} minutes in total. "
                    "You can close this page; sending continues on the server."),
        "queued": queued,
        "skipped": len(lead_ids) - queued,
        "queue": state,
    })


@leads_bp.route("/bulk-send-status", methods=["GET"])
def bulk_send_status():
    from services.smart_sender import get_queue_state
    state = get_queue_state()
    state["running"] = state["pending"] > 0
    state["total"] = state["pending"] + state["sent"]
    return jsonify(state)


@leads_bp.route("/send-next-now", methods=["POST"])
@require_admin_password
def send_next_now_route():
    """Force the next queued message out, ignoring the gap and sending window."""
    from flask import current_app
    from services.smart_sender import send_next_now, get_queue_state

    before = get_queue_state()
    if not before["pending"]:
        return jsonify({"message": "Nothing is queued.", "queue": before})

    # Deliberately works even when the background sender is down: forcing one
    # message through is the quickest way to tell a stalled scheduler apart
    # from a broken GHL connection.
    state = send_next_now(current_app._get_current_object())
    sent = before["pending"] - state["pending"]
    warning = ("" if before["scheduler_alive"] else
               " Note: the background sender is not running, so the rest of the queue "
               "will not move until the app is restarted on the server.")
    return jsonify({
        "message": ((f"Sent 1 message now. {state['pending']} still queued." + warning)
                    if sent else "Could not send — see the queue status for why."),
        "queue": state,
    })


@leads_bp.route("/bulk-send-cancel", methods=["POST"])
@require_admin_password
def bulk_send_cancel():
    from services.smart_sender import clear_send_queue
    removed = clear_send_queue()
    return jsonify({"message": f"Stopped — {removed} queued message(s) removed. "
                               "Anything already sent cannot be recalled."})


@leads_bp.route("/send-bulk-sms", methods=["POST"])
@require_admin_password
def send_bulk_sms():
    from models import AppSettings
    from datetime import datetime, timedelta
    from sqlalchemy import func as sqlfunc
    from services.smart_sender import queue_leads_for_send, get_queue_state

    # Block manual bulk send when smart sender is active — it handles timing
    if AppSettings.get("smart_send_enabled", "false").lower() == "true":
        return jsonify({
            "message": "Smart Send is active — it will send messages automatically at the right intervals. "
                       "Turn off Smart Send first if you want to send manually.",
            "results": {"sent": 0, "failed": 0, "errors": []}
        }), 200

    # Same counter and same limit the sender itself uses, so this cannot
    # disagree with what the queue reports.
    from flask import current_app
    from services.smart_sender import _count_sent_today, get_limit_info

    app_obj = current_app._get_current_object()
    limit_info = get_limit_info(app_obj)
    daily_limit = limit_info["limit"]
    sent_today = _count_sent_today(app_obj)
    remaining = max(0, daily_limit - sent_today)

    if remaining == 0:
        return jsonify({
            "message": (f"Daily limit of {daily_limit} reached ({sent_today} sent). "
                        f"Raise it in {limit_info['where']}, or try again tomorrow."),
            "results": {"sent": 0, "failed": 0, "errors": []}
        }), 200

    # Duplicate guard: skip any lead contacted in the last 24 hours
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)

    stmt = select(Lead).where(
        Lead.status == "not_contacted",
        Lead.imported_to_ghl == True,
        Lead.ghl_contact_id.isnot(None),
        or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < cutoff_24h),
    ).limit(remaining)
    lead_ids = [l.id for l in db.session.execute(stmt).scalars().all()]

    if not lead_ids:
        return jsonify({"message": "No leads are waiting to be contacted.",
                        "results": {"sent": 0, "failed": 0, "errors": []}}), 200

    queued = queue_leads_for_send(lead_ids)
    state = get_queue_state()
    return jsonify({
        "message": (f"Queued {queued} message(s). They go out one at a time, "
                    f"{state['min_gap_mins']}-{state['max_gap_mins']} minutes apart "
                    f"— roughly {state['estimated_minutes']} minutes in total. "
                    "You can close this page; sending continues on the server."),
        "queued": queued,
        "queue": state,
        "results": {"sent": 0, "failed": 0, "errors": [],
                    "daily_limit": daily_limit, "sent_today": sent_today},
    })


@leads_bp.route("/<int:lead_id>", methods=["DELETE"])
@require_admin_password
def delete_lead(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    db.session.execute(
        db.delete(Conversation).where(Conversation.lead_id == lead_id)
    )
    db.session.delete(lead)
    db.session.commit()
    return jsonify({"message": "Lead deleted"})


@leads_bp.route("/import-csv", methods=["POST"])
@require_admin_password
def import_csv():
    """
    Import leads from an Outscraper CSV export.
    Expects a multipart/form-data upload with field name 'file'.
    Automatically maps Outscraper column names, skips rows with a website,
    and deduplicates by place_id or phone.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded. Send field name: 'file'"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    min_rating = float(request.form.get("min_rating", 0))

    try:
        df = pd.read_csv(f, dtype=str, encoding="utf-8-sig", on_bad_lines="skip")
    except Exception as e:
        return jsonify({"error": f"Could not parse CSV: {e}"}), 400

    # Normalise column names: lowercase + strip spaces
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Build a mapping from df columns → our fields
    col_map = {}
    for col in df.columns:
        if col in OUTSCRAPER_COLUMN_MAP:
            our_field = OUTSCRAPER_COLUMN_MAP[col]
            if our_field not in col_map:   # first match wins
                col_map[col] = our_field

    if not col_map:
        return jsonify({
            "error": "No recognised Outscraper columns found.",
            "columns_received": list(df.columns),
            "hint": "Expected columns like: name, rating, phone, full_address, site, url"
        }), 400

    results = {"imported": 0, "skipped_has_website": 0, "skipped_duplicate": 0,
               "skipped_no_phone": 0, "skipped_low_rating": 0, "errors": []}

    for _, row in df.iterrows():
        mapped = {}
        for col, field in col_map.items():
            val = row.get(col, "")
            if pd.notna(val) and str(val).strip() not in ("", "nan", "None"):
                mapped[field] = str(val).strip()

        # Skip if has a website
        website = mapped.get("website", "")
        if website and website.lower() not in ("nan", "none", "n/a", "-"):
            results["skipped_has_website"] += 1
            continue

        # Skip if no phone
        phone = mapped.get("phone", "")
        if not phone:
            results["skipped_no_phone"] += 1
            continue

        # Clean phone — keep digits and leading +
        import re
        phone = re.sub(r"[^\d+]", "", phone)
        if len(phone) < 7:
            results["skipped_no_phone"] += 1
            continue

        # Skip if rating too low
        try:
            rating = float(mapped.get("rating", 0) or 0)
        except ValueError:
            rating = 0.0
        if min_rating > 0 and rating < min_rating:
            results["skipped_low_rating"] += 1
            continue

        # Deduplicate by place_id first, then phone
        place_id = mapped.get("place_id", "")
        if place_id:
            existing = db.session.execute(
                select(Lead).where(Lead.place_id == place_id)
            ).scalar_one_or_none()
            if existing:
                results["skipped_duplicate"] += 1
                continue
        else:
            existing = db.session.execute(
                select(Lead).where(Lead.phone == phone)
            ).scalar_one_or_none()
            if existing:
                results["skipped_duplicate"] += 1
                continue

        try:
            lead = Lead(
                business_name=mapped.get("business_name", ""),
                rating=rating,
                reviews_count=int(float(mapped.get("reviews_count", 0) or 0)),
                phone=phone,
                category=mapped.get("category", ""),
                address=mapped.get("address", ""),
                city=mapped.get("city", ""),
                state=mapped.get("state", ""),
                country=mapped.get("country", ""),
                google_maps_url=mapped.get("google_maps_url", ""),
                place_id=place_id or None,
                website="",
                status="not_contacted",
            )
            db.session.add(lead)
            results["imported"] += 1
        except Exception as e:
            results["errors"].append(str(e))

    db.session.commit()
    return jsonify({
        "message": f"Import complete — {results['imported']} leads added",
        "results": results,
    })


@leads_bp.route("/<int:lead_id>/pause-ai", methods=["POST"])
@require_admin_password
def pause_ai(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    lead.ai_paused = True
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    if lead.ghl_contact_id:
        try:
            GHLService().add_tag(lead.ghl_contact_id, "manual-takeover")
        except Exception:
            pass
    return jsonify({"message": "AI paused", "lead": lead.to_dict()})


@leads_bp.route("/<int:lead_id>/resume-ai", methods=["POST"])
@require_admin_password
def resume_ai(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    lead.ai_paused = False
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    if lead.ghl_contact_id:
        try:
            GHLService().remove_tag(lead.ghl_contact_id, "manual-takeover")
        except Exception:
            pass
    return jsonify({"message": "AI resumed", "lead": lead.to_dict()})


@leads_bp.route("/<int:lead_id>/send-manual-sms", methods=["POST"])
@require_admin_password
def send_manual_sms(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
    if not lead.ghl_contact_id:
        return jsonify({"error": "Lead not imported to GHL yet"}), 400

    ghl = GHLService()
    convo_id = lead.ghl_conversation_id
    if not convo_id:
        convo = ghl.get_or_create_conversation(lead.ghl_contact_id)
        convo_id = convo.get("id") or convo.get("conversationId")
        lead.ghl_conversation_id = convo_id

    result = ghl.send_sms(lead.ghl_contact_id, message, convo_id)
    lead.ai_paused = True
    lead.updated_at = datetime.utcnow()

    db.session.add(Conversation(
        lead_id=lead.id, direction="outbound", message=message,
        step=lead.conversation_step, status="sent_manual",
        ghl_message_id=result.get("messageId", ""),
    ))
    db.session.commit()

    try:
        ghl.add_tag(lead.ghl_contact_id, "manual-takeover")
    except Exception:
        pass

    return jsonify({"message": "SMS sent (AI paused)", "lead": lead.to_dict()})


@leads_bp.route("/export", methods=["GET"])
def export_leads():
    fmt         = request.args.get("format", "csv").lower()
    status      = request.args.get("status", "")
    has_phone   = request.args.get("has_phone", "")
    has_website = request.args.get("has_website", "")
    min_rating  = request.args.get("min_rating", "")

    stmt = select(Lead).order_by(Lead.created_at.desc())
    if status:
        stmt = stmt.where(Lead.status == status)
    if has_phone == "yes":
        stmt = stmt.where(Lead.phone.isnot(None), Lead.phone != "")
    elif has_phone == "no":
        stmt = stmt.where(or_(Lead.phone.is_(None), Lead.phone == ""))
    if has_website == "no":
        stmt = stmt.where(or_(Lead.website.is_(None), Lead.website == ""))
    elif has_website == "yes":
        stmt = stmt.where(Lead.website.isnot(None), Lead.website != "")
    if min_rating:
        try:
            stmt = stmt.where(Lead.rating >= float(min_rating))
        except ValueError:
            pass
    leads = db.session.execute(stmt).scalars().all()

    rows = [{
        "ID": l.id,
        "Business Name": l.business_name,
        "Rating": l.rating,
        "Reviews": l.reviews_count,
        "Phone": l.phone,
        "Category": l.category,
        "Address": l.address,
        "City": l.city,
        "State": l.state,
        "Google Maps URL": l.google_maps_url,
        "Google Reviews URL": f"https://search.google.com/local/reviews?placeid={l.place_id}&q=*&hl=en&gl=US" if l.place_id else "",
        "Status": l.status,
        "GHL Contact ID": l.ghl_contact_id,
        "Imported to GHL": l.imported_to_ghl,
        "Website Sent": l.website_url_sent,
        "Notes": l.notes,
        "Created At": l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else "",
        "Last Contacted": l.last_contacted_at.strftime("%Y-%m-%d %H:%M") if l.last_contacted_at else "",
    } for l in leads]

    df = pd.DataFrame(rows)
    buf = io.BytesIO()

    if fmt == "excel":
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        return send_file(buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name="leads_export.xlsx")
    else:
        buf.write(df.to_csv(index=False).encode("utf-8"))
        buf.seek(0)
        return send_file(buf, mimetype="text/csv",
            as_attachment=True, download_name="leads_export.csv")
