from datetime import date, datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import AppSettings, Lead, Conversation, WebhookEvent
from sqlalchemy import select, func
from auth import require_admin_password

admin_bp = Blueprint("admin", __name__)


# ------------------------------------------------------------------ #
# Inbound diagnostics
# ------------------------------------------------------------------ #

@admin_bp.route("/webhook-events", methods=["GET"])
def webhook_events():
    """Every hit on /api/webhooks/ghl, including the ones we could not act on."""
    limit = min(int(request.args.get("limit", 50)), 500)
    outcome = request.args.get("outcome", "")

    stmt = select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(limit)
    if outcome:
        stmt = select(WebhookEvent).where(WebhookEvent.outcome == outcome) \
                                   .order_by(WebhookEvent.created_at.desc()).limit(limit)

    events = db.session.execute(stmt).scalars().all()
    return jsonify({"events": [e.to_dict() for e in events], "count": len(events)})


@admin_bp.route("/webhook-health", methods=["GET"])
def webhook_health():
    """
    One call that answers "why didn't the AI reply?".

    Distinguishes the three causes that used to look identical from the UI:
    GHL never called us, it called us but no lead matched, or it matched but
    the outbound send failed.
    """
    from services.conversation_ai import get_ai_mode, openai_available

    since = datetime.utcnow() - timedelta(days=7)
    counts = dict(db.session.execute(
        select(WebhookEvent.outcome, func.count(WebhookEvent.id))
        .where(WebhookEvent.created_at >= since)
        .group_by(WebhookEvent.outcome)
    ).all())

    last_event = db.session.execute(
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    last_inbound = db.session.execute(
        select(Conversation).where(Conversation.direction == "inbound")
        .order_by(Conversation.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    last_outbound = db.session.execute(
        select(Conversation).where(Conversation.direction == "outbound")
        .order_by(Conversation.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    ghl_configured = bool(current_app.config.get("GHL_ACCESS_TOKEN"))
    total = sum(counts.values())
    problems = []

    if total == 0:
        problems.append(
            "No webhook has ever reached this server. Point GHL at "
            f"{current_app.config.get('APP_URL', '')}/api/webhooks/ghl and make sure the "
            "workflow trigger is 'Customer Replied' / inbound SMS."
        )
    if counts.get("no_lead"):
        problems.append(
            f"{counts['no_lead']} inbound message(s) matched no lead. The number that "
            "texted is not in the lead list, or its GHL contact was recreated."
        )
    if counts.get("no_text"):
        problems.append(
            f"{counts['no_text']} webhook(s) arrived with no message text — map the "
            "message body to a 'message' or 'body' field in the GHL workflow."
        )
    if counts.get("send_failed"):
        problems.append(
            f"{counts['send_failed']} reply/replies were generated but GHL refused to send "
            "them. Check the GHL token scopes and the SMS from-number."
        )
    if not ghl_configured:
        problems.append("GHL_ACCESS_TOKEN is not set — no SMS can be sent.")
    if current_app.config.get("WEBHOOK_SECRET"):
        problems.append(
            "WEBHOOK_SECRET is set. Signatures are only checked when GHL sends one, "
            "but leave it blank unless you have configured signing in GHL."
        )

    from version import BUILD

    return jsonify({
        "build": BUILD,
        "healthy": not problems,
        "problems": problems,
        "ai_mode": get_ai_mode(),
        "openai_configured": openai_available(),
        "ghl_configured": ghl_configured,
        "webhook_url": f"{current_app.config.get('APP_URL', '')}/api/webhooks/ghl",
        "events_last_7_days": counts,
        "total_events_last_7_days": total,
        "last_event": last_event.to_dict() if last_event else None,
        "last_inbound_at": last_inbound.created_at.isoformat() if last_inbound else None,
        "last_outbound_at": last_outbound.created_at.isoformat() if last_outbound else None,
        "autocreate_leads": AppSettings.get("webhook_autocreate_leads", "true") == "true",
    })


@admin_bp.route("/diagnostics", methods=["GET"])
def diagnostics():
    """
    Everything needed to work out why a reply did or didn't happen, in one
    response — build marker, config state, recent webhooks, recent messages and
    the tail of the log. The Admin page copies this to the clipboard so it can
    be pasted straight into a support message.

    Secrets are never included; only whether each one is set.
    """
    import os
    from version import BUILD, CHANGES
    from services.conversation_ai import get_ai_mode, openai_available

    def _cfg_set(key):
        return bool(str(current_app.config.get(key, "") or "").strip())

    events = db.session.execute(
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(25)
    ).scalars().all()

    recent_msgs = db.session.execute(
        select(Conversation, Lead).join(Lead, Conversation.lead_id == Lead.id)
        .order_by(Conversation.created_at.desc()).limit(25)
    ).all()

    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", "app.log")
    log_tail = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_tail = [line.rstrip() for line in f.readlines()[-120:]]
        except Exception as e:
            log_tail = [f"(could not read log: {e})"]
    else:
        log_tail = ["(no log file yet — instance/app.log does not exist)"]

    settings_of_interest = [
        "ai_mode", "webhook_autocreate_leads", "smart_send_enabled",
        "daily_send_limit", "send_start_time", "send_end_time", "send_timezone",
        "send_min_interval_mins", "send_max_interval_mins",
        "warmup_start_date", "followup_enabled", "business_name", "sms_agent_name",
    ]

    return jsonify({
        "build": BUILD,
        "build_contains": CHANGES,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "webhook_url": f"{current_app.config.get('APP_URL', '')}/api/webhooks/ghl",
        "config": {
            "GHL_ACCESS_TOKEN_set": _cfg_set("GHL_ACCESS_TOKEN"),
            "GHL_LOCATION_ID_set": _cfg_set("GHL_LOCATION_ID"),
            "SMS_FROM_NUMBER_set": _cfg_set("SMS_FROM_NUMBER"),
            "OPENAI_API_KEY_set": openai_available(),
            "WEBHOOK_SECRET_set": _cfg_set("WEBHOOK_SECRET"),
            "APP_URL": current_app.config.get("APP_URL", ""),
            "ai_mode": get_ai_mode(),
        },
        "settings": {k: AppSettings.get(k) for k in settings_of_interest},
        "counts": {
            "leads": db.session.scalar(select(func.count(Lead.id))) or 0,
            "imported_to_ghl": db.session.scalar(
                select(func.count(Lead.id)).where(Lead.imported_to_ghl == True)) or 0,
            "conversations": db.session.scalar(select(func.count(Conversation.id))) or 0,
            "webhook_events_total": db.session.scalar(select(func.count(WebhookEvent.id))) or 0,
        },
        "webhook_events": [e.to_dict() for e in events],
        "recent_messages": [{
            "time": c.created_at.isoformat() if c.created_at else "",
            "direction": c.direction, "status": c.status, "step": c.step,
            "lead": l.business_name, "phone": l.phone,
            "message": (c.message or "")[:300],
        } for c, l in recent_msgs],
        "log_tail": log_tail,
    })


@admin_bp.route("/dry-run-inbound", methods=["POST"])
@require_admin_password
def dry_run_inbound():
    """
    Run a message through the exact live reply engine without sending an SMS
    and without touching the lead's state — so the whole pipeline can be
    verified against real leads with no risk of texting anyone.
    """
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    lead_id = data.get("lead_id")
    if lead_id:
        lead = db.session.get(Lead, lead_id)
        if not lead:
            return jsonify({"error": f"Lead {lead_id} not found"}), 404
    else:
        class _Lead:
            id = 0
            business_name = data.get("business_name") or "Joe's Hair Studio"
            rating = data.get("rating") or 4.8
            reviews_count = data.get("reviews") or 143
            conversation_step = int(data.get("step") or 0)
            status = "message_sent"
            ai_paused = False
        lead = _Lead()

    step_override = data.get("step")
    original_step = lead.conversation_step
    if step_override is not None:
        lead.conversation_step = int(step_override)

    from services.conversation_ai import resolve_reply, get_ai_mode
    try:
        decision = resolve_reply(lead, message, data.get("history") or [])
    finally:
        lead.conversation_step = original_step
        db.session.rollback()   # a dry run must never persist anything

    out = decision.to_dict()
    out.update({
        "mode": get_ai_mode(),
        "lead": getattr(lead, "business_name", ""),
        "from_step": int(step_override) if step_override is not None else original_step,
        "would_send": bool(decision.reply),
        "dry_run": True,
    })
    return jsonify(out)


@admin_bp.route("/settings", methods=["GET"])
def get_settings():
    settings = {}
    for key, default in AppSettings.DEFAULTS.items():
        settings[key] = AppSettings.get(key, default)
    return jsonify({"settings": settings})


@admin_bp.route("/settings", methods=["POST"])
@require_admin_password
def save_settings():
    data = request.get_json() or {}
    allowed = set(AppSettings.DEFAULTS.keys())
    saved = {}
    for key, val in data.items():
        if key in allowed:
            AppSettings.set(key, val)
            saved[key] = val

    # Tell the operator straight away when a setting they just changed is
    # overridden by, or has consequences for, another one.
    warnings = []
    try:
        from services.settings_advice import advise
        warnings = advise(saved)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.warning(f"Settings advice failed: {e}")

    return jsonify({"message": "Settings saved", "saved": saved, "warnings": warnings})


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


@admin_bp.route("/reset-default", methods=["POST"])
@require_admin_password
def reset_default():
    data = request.get_json() or {}
    key  = data.get("key", "")
    from models import AppSettings
    default = AppSettings.DEFAULTS.get(key)
    if default is None:
        return jsonify({"error": "Unknown key"}), 400
    AppSettings.set(key, default)
    return jsonify({"key": key, "value": default})


@admin_bp.route("/detect-timezone", methods=["POST"])
@require_admin_password
def detect_timezone():
    """Detect recommended timezone from a list of lead IDs."""
    data    = request.get_json() or {}
    lead_ids = data.get("lead_ids", [])
    if not lead_ids:
        # Use all imported leads
        from models import Lead
        from sqlalchemy import select
        leads = db.session.execute(
            select(Lead.id).where(Lead.imported_to_ghl == True)
        ).scalars().all()
        lead_ids = list(leads)

    from services.smart_sender import detect_timezone_from_leads
    result = detect_timezone_from_leads(lead_ids, current_app._get_current_object())
    return jsonify(result)


@admin_bp.route("/smart-send/status", methods=["GET"])
def smart_send_status():
    from models import AppSettings
    from services.smart_sender import _get_todays_limit, _count_sent_today
    app = current_app._get_current_object()
    limit      = _get_todays_limit(app)
    sent_today = _count_sent_today(app)
    return jsonify({
        "enabled":       AppSettings.get("smart_send_enabled", "false") == "true",
        "today_limit":   limit,
        "sent_today":    sent_today,
        "remaining":     max(0, limit - sent_today),
        "start_date":    AppSettings.get("warmup_start_date", ""),
        "send_timezone": AppSettings.get("send_timezone", "America/New_York"),
        "send_start":    AppSettings.get("send_start_time", "07:00"),
        "send_end":      AppSettings.get("send_end_time", "18:00"),
    })


@admin_bp.route("/smart-send/start-warmup", methods=["POST"])
@require_admin_password
def start_warmup():
    from models import AppSettings
    AppSettings.set("warmup_start_date", date.today().isoformat())
    AppSettings.set("smart_send_enabled", "true")
    return jsonify({"message": f"Warm-up started from today. Day 1 limit: {AppSettings.get('warmup_start_limit', '20')} messages."})


@admin_bp.route("/reset-all-defaults", methods=["POST"])
@require_admin_password
def reset_all_defaults():
    """Force-reset ALL settings to their default values."""
    from models import AppSettings
    count = 0
    for key, val in AppSettings.DEFAULTS.items():
        AppSettings.set(key, val)
        count += 1
    return jsonify({"message": f"Reset {count} settings to defaults"})


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
@require_admin_password
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
    """
    Outreach sent today against today's limit.

    Uses the same counter as the sender itself, so the dashboard cannot
    disagree with the thing actually deciding whether to send. It counts only
    outreach — not AI replies, manual messages, or history synced from GHL.
    """
    from services.smart_sender import _count_sent_today, get_limit_info

    app = current_app._get_current_object()
    sent_today = _count_sent_today(app)
    info = get_limit_info(app)
    limit = info["limit"]
    return jsonify({
        "sent_today": sent_today,
        "daily_limit": limit,
        "remaining": max(0, limit - sent_today),
        "limit_source": info["source"],
        "limit_where": info["where"],
    })


@admin_bp.route("/app-logs", methods=["GET"])
def app_logs():
    """Return last N lines of the application log file."""
    import os
    lines = int(request.args.get("lines", 200))
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", "app.log")
    if not os.path.exists(log_path):
        return jsonify({"lines": [], "message": "No log file yet — logs appear after first activity"})
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        last = all_lines[-lines:]
        return jsonify({"lines": [l.rstrip() for l in reversed(last)]})
    except Exception as e:
        return jsonify({"lines": [], "error": str(e)})


@admin_bp.route("/test-webhook", methods=["POST"])
@require_admin_password
def test_webhook():
    """Send a fake inbound SMS to test the full webhook → AI flow."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")

    from models import Lead
    from sqlalchemy import select

    if lead_id:
        lead = db.session.get(Lead, lead_id)
    else:
        lead = db.session.execute(
            select(Lead).where(
                Lead.imported_to_ghl == True,
                Lead.ghl_contact_id.isnot(None),
                Lead.status == "message_sent"
            ).limit(1)
        ).scalar_one_or_none()

    if not lead:
        return jsonify({"error": "No suitable lead found. Import a lead to GHL first."}), 400

    import requests as req
    test_payload = {
        "type": "InboundMessage",
        "messageType": "SMS",
        "contactId": lead.ghl_contact_id or "test_contact",
        "conversationId": lead.ghl_conversation_id or "test_conv",
        "message": data.get("message", "Yes"),
    }
    app_url = current_app.config.get("APP_URL", "http://localhost:5000")
    try:
        r = req.post(f"{app_url}/api/webhooks/ghl",
                     json=test_payload,
                     headers={"Content-Type": "application/json"},
                     timeout=15)
        return jsonify({
            "message": f"Test sent for lead: {lead.business_name}",
            "webhook_status": r.status_code,
            "webhook_response": r.json() if r.content else {},
            "test_message": test_payload["message"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/logs", methods=["GET"])
def logs():
    """Return recent webhook activity and conversations for debugging."""
    limit = int(request.args.get("limit", 50))
    from models import ScrapingJob

    # Recent inbound messages
    recent_inbound = db.session.execute(
        select(Conversation, Lead)
        .join(Lead, Conversation.lead_id == Lead.id)
        .where(Conversation.direction == "inbound")
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    ).all()

    # Recent outbound messages
    recent_outbound = db.session.execute(
        select(Conversation, Lead)
        .join(Lead, Conversation.lead_id == Lead.id)
        .where(Conversation.direction == "outbound")
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    ).all()

    inbound_logs = [{
        "time": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        "lead": l.business_name,
        "phone": l.phone,
        "message": c.message,
        "step": c.step,
        "status": l.status,
        "direction": "inbound",
    } for c, l in recent_inbound]

    outbound_logs = [{
        "time": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        "lead": l.business_name,
        "phone": l.phone,
        "message": c.message,
        "step": c.step,
        "status": l.status,
        "direction": "outbound",
    } for c, l in recent_outbound]

    # Merge and sort by time
    all_logs = sorted(inbound_logs + outbound_logs, key=lambda x: x["time"], reverse=True)[:limit]

    return jsonify({
        "logs": all_logs,
        "total_inbound": len(inbound_logs),
        "total_outbound": len(outbound_logs),
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
