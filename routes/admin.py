from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import AppSettings, Lead, Conversation
from sqlalchemy import select, func

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/settings", methods=["GET"])
def get_settings():
    settings = {}
    for key, default in AppSettings.DEFAULTS.items():
        settings[key] = AppSettings.get(key, default)
    return jsonify({"settings": settings})


@admin_bp.route("/settings", methods=["POST"])
def save_settings():
    data = request.get_json() or {}
    allowed = set(AppSettings.DEFAULTS.keys())
    saved = {}
    for key, val in data.items():
        if key in allowed:
            AppSettings.set(key, val)
            saved[key] = val
    return jsonify({"message": "Settings saved", "saved": saved})


@admin_bp.route("/debug-ghl", methods=["GET"])
def debug_ghl():
    """Show exactly what the GHL token has access to."""
    import requests as req
    token       = current_app.config.get("GHL_ACCESS_TOKEN", "")
    location_id = current_app.config.get("GHL_LOCATION_ID", "")
    headers     = {"Authorization": f"Bearer {token}", "Version": "2021-07-28"}

    results = {"token_prefix": token[:20] + "...", "location_id_in_env": location_id}

    # Get userinfo — shows what this token is for
    r = req.get("https://services.leadconnectorhq.com/oauth/userinfo", headers=headers, timeout=10)
    results["userinfo_status"] = r.status_code
    results["userinfo"] = r.json() if r.ok else r.text

    # Try contacts without locationId
    r2 = req.get("https://services.leadconnectorhq.com/contacts/", headers=headers,
                 params={"limit": 1}, timeout=10)
    results["contacts_no_location_status"] = r2.status_code
    results["contacts_no_location"] = r2.json() if r2.ok else r2.text

    # Try contacts with locationId
    r3 = req.get("https://services.leadconnectorhq.com/contacts/", headers=headers,
                 params={"locationId": location_id, "limit": 1}, timeout=10)
    results["contacts_with_location_status"] = r3.status_code
    results["contacts_with_location"] = r3.json() if r3.ok else r3.text

    return jsonify(results)


@admin_bp.route("/test-ghl", methods=["GET"])
def test_ghl():
    """Test GHL API connection and return detailed status."""
    import requests as req

    token       = current_app.config.get("GHL_ACCESS_TOKEN", "")
    location_id = current_app.config.get("GHL_LOCATION_ID", "")

    if not token:
        return jsonify({"ok": False, "error": "GHL_ACCESS_TOKEN is not set in .env"}), 400
    if not location_id:
        return jsonify({"ok": False, "error": "GHL_LOCATION_ID is not set in .env"}), 400

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }

    # Test 1: verify token is valid
    try:
        r = req.get("https://services.leadconnectorhq.com/oauth/userinfo",
                    headers=headers, timeout=10)
        if r.status_code == 401:
            return jsonify({"ok": False, "error": "GHL token is invalid or expired. Generate a new Private Integration token in GHL → Settings → Private Integrations."}), 400
        user_info = r.json() if r.ok else {}
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not reach GHL API: {e}"}), 400

    # Test 2: verify contacts scope by fetching one contact
    try:
        r2 = req.get(
            f"https://services.leadconnectorhq.com/contacts/?locationId={location_id}&limit=1",
            headers=headers, timeout=10
        )
        if r2.status_code == 401:
            return jsonify({"ok": False, "error": "Token valid but missing 'Contacts' scope. Edit your Private Integration in GHL and enable Contacts read+write."}), 400
        if r2.status_code == 422:
            return jsonify({"ok": False, "error": f"Location ID '{location_id}' not found or not accessible with this token."}), 400
        contacts_ok = r2.ok
    except Exception as e:
        contacts_ok = False

    return jsonify({
        "ok": True,
        "token_valid": True,
        "contacts_scope": contacts_ok,
        "location_id": location_id,
        "user": user_info.get("email") or user_info.get("name") or "verified",
        "message": "GHL connection is working correctly ✅" if contacts_ok else "Token valid but contacts scope may be missing",
    })


@admin_bp.route("/followup/trigger", methods=["POST"])
def trigger_followup():
    app = current_app._get_current_object()
    import threading
    from services.followup_scheduler import trigger_now
    thread = threading.Thread(target=trigger_now, args=(app,), daemon=True)
    thread.start()
    return jsonify({"message": "Follow-up run started in background"})


@admin_bp.route("/followup/preview", methods=["GET"])
def preview_followup():
    from datetime import datetime, timedelta
    interval_days = int(AppSettings.get("followup_interval_days", "3"))
    max_count     = int(AppSettings.get("followup_max_count", "2"))
    cutoff        = datetime.utcnow() - timedelta(days=interval_days)
    count = db.session.scalar(
        select(func.count(Lead.id)).where(
            Lead.imported_to_ghl == True,
            Lead.status == "message_sent",
            Lead.followup_count < max_count,
            Lead.last_contacted_at <= cutoff,
            Lead.ghl_contact_id.isnot(None),
        )
    ) or 0
    return jsonify({"leads_due_for_followup": count, "interval_days": interval_days})


@admin_bp.route("/daily-stats", methods=["GET"])
def daily_stats():
    from datetime import datetime
    today     = datetime.utcnow().date()
    sent_today = db.session.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.direction == "outbound",
            func.date(Conversation.created_at) == str(today),
        )
    ) or 0
    limit     = int(AppSettings.get("daily_send_limit", "50"))
    return jsonify({
        "sent_today": sent_today,
        "daily_limit": limit,
        "remaining": max(0, limit - sent_today),
    })


@admin_bp.route("/stats", methods=["GET"])
def stats():
    from models import ScrapingJob
    return jsonify({
        "total_leads":         db.session.scalar(select(func.count(Lead.id))) or 0,
        "total_jobs":          db.session.scalar(select(func.count(ScrapingJob.id))) or 0,
        "total_conversations": db.session.scalar(select(func.count(Conversation.id))) or 0,
        "followup_sent":       db.session.scalar(select(func.sum(Lead.followup_count))) or 0,
    })
