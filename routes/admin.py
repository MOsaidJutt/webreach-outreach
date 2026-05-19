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


@admin_bp.route("/followup/trigger", methods=["POST"])
def trigger_followup():
    """Manually run the follow-up scheduler right now."""
    app = current_app._get_current_object()
    import threading
    from services.followup_scheduler import trigger_now
    thread = threading.Thread(target=trigger_now, args=(app,), daemon=True)
    thread.start()
    return jsonify({"message": "Follow-up run started in background"})


@admin_bp.route("/followup/preview", methods=["GET"])
def preview_followup():
    """Show how many leads are due for follow-up."""
    from datetime import datetime, timedelta
    interval_days = int(AppSettings.get("followup_interval_days", "3"))
    max_count = int(AppSettings.get("followup_max_count", "2"))
    cutoff = datetime.utcnow() - timedelta(days=interval_days)

    stmt = select(func.count(Lead.id)).where(
        Lead.imported_to_ghl == True,
        Lead.status == "message_sent",
        Lead.followup_count < max_count,
        Lead.last_contacted_at <= cutoff,
        Lead.ghl_contact_id.isnot(None),
    )
    count = db.session.scalar(stmt) or 0
    return jsonify({"leads_due_for_followup": count, "interval_days": interval_days})


@admin_bp.route("/daily-stats", methods=["GET"])
def daily_stats():
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    sent_today = db.session.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.direction == "outbound",
            func.date(Conversation.created_at) == str(today),
        )
    ) or 0
    limit = int(AppSettings.get("daily_send_limit", "50"))
    return jsonify({
        "sent_today": sent_today,
        "daily_limit": limit,
        "remaining": max(0, limit - sent_today),
    })


@admin_bp.route("/stats", methods=["GET"])
def stats():
    from models import ScrapingJob
    return jsonify({
        "total_leads": db.session.scalar(select(func.count(Lead.id))) or 0,
        "total_jobs":  db.session.scalar(select(func.count(ScrapingJob.id))) or 0,
        "total_conversations": db.session.scalar(select(func.count(Conversation.id))) or 0,
        "followup_sent": db.session.scalar(
            select(func.sum(Lead.followup_count))
        ) or 0,
    })
