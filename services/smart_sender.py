"""
Smart Send Scheduler
- Sends messages at random intervals within working hours
- Auto warm-up: increases daily limit by N each day
- Timezone-aware: respects lead location timezone
- Runs as a background APScheduler job
"""
import logging
import random
from datetime import datetime, timedelta, date

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
_scheduler = None

# US State → timezone mapping
STATE_TIMEZONE = {
    # Eastern
    "CT": "America/New_York", "DE": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "IN": "America/New_York", "KY": "America/New_York",
    "ME": "America/New_York", "MD": "America/New_York", "MA": "America/New_York",
    "MI": "America/New_York", "NH": "America/New_York", "NJ": "America/New_York",
    "NY": "America/New_York", "NC": "America/New_York", "OH": "America/New_York",
    "PA": "America/New_York", "RI": "America/New_York", "SC": "America/New_York",
    "VT": "America/New_York", "VA": "America/New_York", "WV": "America/New_York",
    "DC": "America/New_York",
    # Central
    "AL": "America/Chicago", "AR": "America/Chicago", "IL": "America/Chicago",
    "IA": "America/Chicago", "KS": "America/Chicago", "LA": "America/Chicago",
    "MN": "America/Chicago", "MS": "America/Chicago", "MO": "America/Chicago",
    "NE": "America/Chicago", "ND": "America/Chicago", "OK": "America/Chicago",
    "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago",
    "WI": "America/Chicago",
    # Mountain
    "AZ": "America/Phoenix", "CO": "America/Denver", "ID": "America/Denver",
    "MT": "America/Denver", "NM": "America/Denver", "UT": "America/Denver",
    "WY": "America/Denver",
    # Pacific
    "CA": "America/Los_Angeles", "NV": "America/Los_Angeles",
    "OR": "America/Los_Angeles", "WA": "America/Los_Angeles",
    # Other US
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
    # UK
    "UK": "Europe/London", "GB": "Europe/London",
}


def get_timezone_for_state(state: str) -> str:
    """Return pytz timezone string for a US state code."""
    if not state:
        return None
    return STATE_TIMEZONE.get(state.strip().upper())


def detect_timezone_from_leads(lead_ids: list, app) -> dict:
    """
    Analyse a list of lead IDs and return timezone recommendation.
    Returns: { recommended_tz, tz_counts, total }
    """
    with app.app_context():
        from models import Lead
        from extensions import db
        from sqlalchemy import select

        stmt = select(Lead).where(Lead.id.in_(lead_ids))
        leads = db.session.execute(stmt).scalars().all()

        tz_counts = {}
        for lead in leads:
            tz = get_timezone_for_state(lead.state)
            if not tz and lead.country in ("GB", "UK"):
                tz = "Europe/London"
            if tz:
                tz_counts[tz] = tz_counts.get(tz, 0) + 1

        if not tz_counts:
            return {"recommended_tz": "America/New_York", "tz_counts": {}, "total": len(leads)}

        recommended = max(tz_counts, key=tz_counts.get)
        return {"recommended_tz": recommended, "tz_counts": tz_counts, "total": len(leads)}


def _get_todays_limit(app) -> int:
    """Calculate today's send limit based on warm-up curve."""
    with app.app_context():
        from models import AppSettings
        start_limit    = int(AppSettings.get("warmup_start_limit", "20"))
        daily_increase = int(AppSettings.get("warmup_daily_increase", "2"))
        max_limit      = int(AppSettings.get("warmup_max_limit", "200"))
        start_date_str = AppSettings.get("warmup_start_date", "")
        manual_limit   = int(AppSettings.get("daily_send_limit", "50"))

        if not start_date_str:
            # Not started yet — use manual limit
            return manual_limit

        try:
            start_date = date.fromisoformat(start_date_str)
            days_elapsed = (date.today() - start_date).days
            limit = start_limit + (days_elapsed * daily_increase)
            return min(limit, max_limit)
        except Exception:
            return manual_limit


def _count_sent_today(app) -> int:
    with app.app_context():
        from models import Conversation
        from extensions import db
        from sqlalchemy import select, func
        today = datetime.utcnow().date()
        return db.session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.direction == "outbound",
                func.date(Conversation.created_at) == str(today),
            )
        ) or 0


def _is_within_working_hours(app) -> bool:
    """Check if current time is within configured working hours."""
    with app.app_context():
        from models import AppSettings
        tz_name    = AppSettings.get("send_timezone", "America/New_York")
        start_time = AppSettings.get("send_start_time", "07:00")
        end_time   = AppSettings.get("send_end_time", "18:00")

        try:
            tz   = pytz.timezone(tz_name)
            now  = datetime.now(tz)
            s_h, s_m = map(int, start_time.split(":"))
            e_h, e_m = map(int, end_time.split(":"))
            start = now.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
            end   = now.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
            return start <= now <= end
        except Exception as e:
            logger.warning(f"Working hours check failed: {e}")
            return True


def _send_one(app):
    """Send one SMS to the next eligible lead."""
    with app.app_context():
        from models import AppSettings, Lead, Conversation
        from extensions import db
        from sqlalchemy import select
        from services.conversation_ai import get_initial_message
        from services.ghl_service import GHLService

        enabled = AppSettings.get("smart_send_enabled", "false").lower() == "true"
        if not enabled:
            return

        if not _is_within_working_hours(app):
            return

        today_limit = _get_todays_limit(app)
        sent_today  = _count_sent_today(app)

        if sent_today >= today_limit:
            logger.info(f"Smart sender: daily limit reached ({sent_today}/{today_limit})")
            return

        # Pick next eligible lead
        lead = db.session.execute(
            select(Lead).where(
                Lead.imported_to_ghl == True,
                Lead.status == "not_contacted",
                Lead.ghl_contact_id.isnot(None),
                Lead.phone.isnot(None),
            ).limit(1)
        ).scalar_one_or_none()

        if not lead:
            logger.info("Smart sender: no eligible leads to contact")
            return

        try:
            ghl     = GHLService()
            message = get_initial_message(lead)
            convo   = ghl.get_or_create_conversation(lead.ghl_contact_id)
            convo_id = convo.get("id") or convo.get("conversationId")
            result  = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

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
            logger.info(f"Smart sender: sent to '{lead.business_name}' ({sent_today + 1}/{today_limit})")

        except Exception as e:
            logger.error(f"Smart sender: failed for lead {lead.id}: {e}")


def start_smart_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(daemon=True)

    # Check every minute — but only send based on random interval setting
    _scheduler.add_job(
        func=lambda: _maybe_send(app),
        trigger="interval",
        minutes=1,
        id="smart_send_job",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Smart send scheduler started")


# Track last send time
_last_send_time = None


def _maybe_send(app):
    """Called every minute — decides whether to send based on random interval."""
    global _last_send_time

    with app.app_context():
        from models import AppSettings
        enabled = AppSettings.get("smart_send_enabled", "false").lower() == "true"
        if not enabled:
            return

        min_interval = int(AppSettings.get("send_min_interval_mins", "3"))
        max_interval = int(AppSettings.get("send_max_interval_mins", "12"))

    now = datetime.utcnow()

    if _last_send_time is None:
        # First run — pick a random delay before first send
        _last_send_time = now - timedelta(minutes=random.randint(min_interval, max_interval))

    elapsed = (now - _last_send_time).total_seconds() / 60
    required = random.uniform(min_interval, max_interval)

    if elapsed >= required:
        _send_one(app)
        _last_send_time = now
