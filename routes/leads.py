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

    # New data-quality filters
    has_phone   = request.args.get("has_phone", "")    # "yes" | "no" | ""
    has_website = request.args.get("has_website", "")  # "yes" | "no" | ""
    min_rating  = request.args.get("min_rating", "")   # "3" | "4" | "4.5" | "5" | ""

    stmt = select(Lead)

    if status:
        stmt = stmt.where(Lead.status == status)

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
    lead = db.get_or_404(Lead, lead_id)
    data = lead.to_dict()
    data["conversations"] = [c.to_dict() for c in lead.conversations]
    return jsonify({"lead": data})


@leads_bp.route("/<int:lead_id>/status", methods=["PUT"])
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
def update_notes(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    data = request.get_json() or {}
    lead.notes = data.get("notes", lead.notes)
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"lead": lead.to_dict()})


@leads_bp.route("/<int:lead_id>/import-to-ghl", methods=["POST"])
def import_to_ghl(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    if not lead.phone:
        return jsonify({"error": "Lead has no phone number"}), 400
    try:
        ghl = GHLService()
        contact = ghl.create_or_update_contact(lead.to_dict())
        lead.ghl_contact_id = contact.get("id") or contact.get("contactId")
        lead.imported_to_ghl = True
        lead.imported_to_ghl_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"message": "Imported to GHL", "ghl_contact": contact, "lead": lead.to_dict()})
    except Exception as e:
        logger.error(f"GHL import failed for lead {lead_id}: {e}")
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/import-all-to-ghl", methods=["POST"])
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
def send_initial_sms(lead_id):
    lead = db.get_or_404(Lead, lead_id)

    if not lead.ghl_contact_id:
        return jsonify({"error": "Lead not imported to GHL yet. Import first."}), 400
    if lead.status != "not_contacted":
        return jsonify({"error": f"Lead already contacted (status: {lead.status})"}), 400

    try:
        ghl = GHLService()
        message = get_initial_message(lead)
        convo = ghl.get_or_create_conversation(lead.ghl_contact_id)
        convo_id = convo.get("id") or convo.get("conversationId")
        result = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

        lead.ghl_conversation_id = convo_id
        lead.status = "message_sent"
        lead.conversation_step = 0
        lead.last_contacted_at = datetime.utcnow()
        lead.updated_at = datetime.utcnow()

        db.session.add(Conversation(
            lead_id=lead.id, direction="outbound", message=message,
            step=0, status="sent", ghl_message_id=result.get("messageId", ""),
        ))
        db.session.commit()
        return jsonify({"message": "SMS sent", "lead": lead.to_dict()})
    except Exception as e:
        logger.error(f"SMS send failed for lead {lead_id}: {e}")
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/send-bulk-sms", methods=["POST"])
def send_bulk_sms():
    stmt = select(Lead).where(
        Lead.status == "not_contacted",
        Lead.imported_to_ghl == True,
        Lead.ghl_contact_id.isnot(None),
    )
    leads = db.session.execute(stmt).scalars().all()

    ghl = GHLService()
    results = {"sent": 0, "failed": 0, "errors": []}

    for lead in leads:
        try:
            message = get_initial_message(lead)
            convo = ghl.get_or_create_conversation(lead.ghl_contact_id)
            convo_id = convo.get("id") or convo.get("conversationId")
            result = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

            lead.ghl_conversation_id = convo_id
            lead.status = "message_sent"
            lead.conversation_step = 0
            lead.last_contacted_at = datetime.utcnow()
            lead.updated_at = datetime.utcnow()

            db.session.add(Conversation(
                lead_id=lead.id, direction="outbound", message=message,
                step=0, status="sent", ghl_message_id=result.get("messageId", ""),
            ))
            results["sent"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"Lead {lead.id} ({lead.business_name}): {str(e)}")

    db.session.commit()
    return jsonify({"message": "Bulk SMS complete", "results": results})


@leads_bp.route("/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    db.session.execute(
        db.delete(Conversation).where(Conversation.lead_id == lead_id)
    )
    db.session.delete(lead)
    db.session.commit()
    return jsonify({"message": "Lead deleted"})


@leads_bp.route("/import-csv", methods=["POST"])
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
