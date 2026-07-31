"""
Smart Send Scheduler
- Sends messages at random intervals within working hours
- Auto warm-up: increases daily limit by N each day
- Timezone-aware: respects lead location timezone
- Runs as a background APScheduler job
"""
import logging
import os
import random
import socket
import time
from datetime import datetime, timedelta, date

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
_scheduler = None

# Progress of a paced bulk run, read by the dashboard.
_bulk_state = {"running": False, "sent": 0, "failed": 0, "total": 0,
               "started_at": None, "next_at": None, "errors": [], "cancel": False}

_lock_socket = None


def _acquire_scheduler_lock() -> bool:
    """
    Let exactly one process own the background senders.

    Deployment runs `gunicorn -w 2`, so create_app() executes in every worker.
    Without this each worker started its own scheduler and its own copy of the
    send timer, which double-sent messages and made the configured gap
    meaningless. Binding a local port is an atomic, cross-platform claim that
    is released automatically if the owning process dies.
    """
    global _lock_socket
    if _lock_socket is not None:
        return True

    port = int(os.getenv("SCHEDULER_LOCK_PORT", "5199"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        _lock_socket = s          # held for the life of the process
        return True
    except OSError:
        s.close()
        return False


# Queue bookkeeping is written to AppSettings rather than kept in module
# globals. Only one gunicorn worker owns the scheduler, so a status request
# served by the other worker saw empty globals and could report neither when
# the next message was due nor why nothing was moving.
QUEUE_TICK_KEY = "queue_last_tick"          # heartbeat: the drain job is alive
QUEUE_NEXT_KEY = "queue_next_send_at"       # when the gap expires
QUEUE_SENT_KEY = "queue_sent_count"
QUEUE_FAIL_KEY = "queue_failed_count"


def _now_iso():
    return datetime.utcnow().isoformat()


def _get_dt(key):
    from models import AppSettings
    raw = AppSettings.get(key, "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _bump(key):
    from models import AppSettings
    try:
        AppSettings.set(key, str(int(AppSettings.get(key, "0") or 0) + 1))
    except Exception:
        pass


def _window_bounds(app):
    """(within_window, opens_at_local, now_local, tz_name) for the send window."""
    from models import AppSettings
    tz_name = AppSettings.get("send_timezone", "America/New_York")
    start_time = AppSettings.get("send_start_time", "07:00")
    end_time = AppSettings.get("send_end_time", "18:00")
    try:
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        s_h, s_m = map(int, start_time.split(":"))
        e_h, e_m = map(int, end_time.split(":"))
        start = now.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
        end = now.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
        within = start <= now <= end
        opens = start if now < start else start + timedelta(days=1)
        return within, (now if within else opens), now, tz_name
    except Exception:
        return True, None, None, tz_name


def queue_leads_for_send(lead_ids, kind: str = "initial") -> int:
    """
    Mark leads to receive an outreach SMS, paced.

    Every outreach path funnels through here — the dashboard's bulk button, the
    Leads page's "SMS selected", a single lead's Send button, and the follow-up
    scheduler. Nothing sends inline any more, so no amount of clicking or
    looping can put two messages out in the same second.

    `kind` only controls which leads are eligible to be queued. What actually
    gets sent is decided at drain time from the lead's status, so the queue
    needs no extra column to tell the two apart.

    Returns the number newly queued.
    """
    from models import Lead
    from extensions import db
    from sqlalchemy import select

    if not lead_ids:
        return 0

    now = datetime.utcnow()
    leads = db.session.execute(
        select(Lead).where(Lead.id.in_(list(lead_ids)))
    ).scalars().all()

    queued = 0
    for lead in leads:
        if not lead.ghl_contact_id:
            continue
        if lead.send_queued_at is not None:
            continue                       # already waiting its turn
        if kind == "initial" and lead.status != "not_contacted":
            continue
        if kind == "followup" and lead.status != "message_sent":
            continue
        lead.send_queued_at = now
        queued += 1

    db.session.commit()
    logger.info(f"Queued {queued} lead(s) for paced outreach ({kind})")
    return queued


def get_queue_state(app=None) -> dict:
    """
    Queue depth, when the next message is due, and — when nothing is moving —
    the reason why. A silent stalled queue is indistinguishable from a broken
    one, which is exactly how an hour gets lost.
    """
    from models import Lead, AppSettings
    from extensions import db
    from sqlalchemy import select, func
    from flask import current_app

    app = app or current_app._get_current_object()

    pending = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.send_queued_at.isnot(None))
    ) or 0

    min_gap = int(AppSettings.get("send_min_interval_mins", "3"))
    max_gap = int(AppSettings.get("send_max_interval_mins", "12"))
    average = (min_gap + max_gap) / 2

    last_tick = _get_dt(QUEUE_TICK_KEY)
    next_at = _get_dt(QUEUE_NEXT_KEY)
    now = datetime.utcnow()

    # The drain job ticks every 20s. No tick for two minutes means no process
    # is running it — the single most important thing to surface.
    scheduler_alive = bool(last_tick and (now - last_tick).total_seconds() < 120)

    within_window, opens_at, now_local, tz_name = _window_bounds(app)
    today_limit = _get_todays_limit(app)
    sent_today = _count_sent_today(app)
    limit_reached = sent_today >= today_limit

    blocked_reason = None
    if pending:
        if not scheduler_alive:
            blocked_reason = ("The background sender is not running, so the queue is stuck. "
                              "Restart the app on the server (systemctl restart webreach).")
        elif limit_reached:
            blocked_reason = (f"Daily limit reached ({sent_today}/{today_limit}). "
                              "Sending resumes tomorrow, or raise the limit in Admin.")
        elif not within_window:
            when = opens_at.strftime("%H:%M on %d %b") if opens_at else "the next window"
            blocked_reason = (f"Outside your sending hours "
                              f"({AppSettings.get('send_start_time', '07:00')}"
                              f"–{AppSettings.get('send_end_time', '18:00')} {tz_name}). "
                              f"Sending resumes at {when} local time.")
        elif next_at and now < next_at:
            secs = int((next_at - now).total_seconds())
            blocked_reason = f"Waiting {secs // 60}m {secs % 60}s for the gap before the next message."

    return {
        "pending": pending,
        "next_at": next_at.isoformat() if next_at else None,
        "min_gap_mins": min_gap,
        "max_gap_mins": max_gap,
        "estimated_minutes": int(max(0, pending - 1) * average),
        "sent": int(AppSettings.get(QUEUE_SENT_KEY, "0") or 0),
        "failed": int(AppSettings.get(QUEUE_FAIL_KEY, "0") or 0),
        "errors": _bulk_state.get("errors", [])[-5:],
        "scheduler_alive": scheduler_alive,
        "last_tick_at": last_tick.isoformat() if last_tick else None,
        "within_window": within_window,
        "window_opens_at": opens_at.isoformat() if opens_at else None,
        "server_time_local": now_local.strftime("%H:%M") if now_local else None,
        "timezone": tz_name,
        "daily_limit": today_limit,
        "sent_today": sent_today,
        "blocked_reason": blocked_reason,
    }


def send_next_now(app=None) -> dict:
    """
    Force the next queued message out immediately, ignoring the gap and the
    sending window. Used by the Send now button for testing.
    """
    global _next_send_at
    from models import AppSettings
    from flask import current_app

    app = app or current_app._get_current_object()
    _next_send_at = None
    AppSettings.set(QUEUE_NEXT_KEY, "")
    _drain_send_queue(app, ignore_window=True)
    return get_queue_state(app)


def clear_send_queue() -> int:
    """Unqueue everything still waiting."""
    from models import Lead
    from extensions import db
    from sqlalchemy import select

    leads = db.session.execute(
        select(Lead).where(Lead.send_queued_at.isnot(None))
    ).scalars().all()
    for lead in leads:
        lead.send_queued_at = None
    db.session.commit()
    logger.info(f"Cleared {len(leads)} lead(s) from the send queue")
    return len(leads)


def _drain_send_queue(app, ignore_window: bool = False):
    """
    Send at most ONE queued opening SMS per call, and only once the random gap
    since the previous one has elapsed. Runs on a short timer in the single
    process that owns the scheduler lock.
    """
    global _next_send_at

    with app.app_context():
        from models import AppSettings, Lead, Conversation
        from extensions import db
        from sqlalchemy import select
        from services.conversation_ai import get_initial_message, get_followup_message
        from services.ghl_service import GHLService

        # Heartbeat first, and unconditionally: the dashboard uses it to tell
        # "waiting its turn" apart from "nothing is running at all".
        try:
            AppSettings.set(QUEUE_TICK_KEY, _now_iso())
        except Exception:
            pass

        lead = db.session.execute(
            select(Lead).where(Lead.send_queued_at.isnot(None))
            .order_by(Lead.send_queued_at.asc()).limit(1)
        ).scalar_one_or_none()
        if not lead:
            return

        now = datetime.utcnow()
        stored_next = _get_dt(QUEUE_NEXT_KEY)
        next_due = _next_send_at or stored_next
        if next_due and now < next_due and not ignore_window:
            return                                   # still inside the gap

        if not ignore_window and not _is_within_working_hours(app):
            return                                   # outside sending hours

        today_limit = _get_todays_limit(app)
        if _count_sent_today(app) >= today_limit:
            logger.info(f"Send queue paused: daily limit of {today_limit} reached")
            return

        # A lead still on "not_contacted" is due its opening message; anything
        # else in the queue was put there by the follow-up scheduler.
        is_followup = lead.status != "not_contacted"

        # Claim the lead before the network call so a crash cannot resend it.
        lead.send_queued_at = None
        lead.last_contacted_at = now
        lead.updated_at = now
        if is_followup:
            message = get_followup_message(lead)
            lead.followup_count = (lead.followup_count or 0) + 1
            lead.last_followup_at = now
        else:
            message = get_initial_message(lead)
            lead.status = "message_sent"
            lead.conversation_step = 0
        db.session.commit()

        try:
            ghl = GHLService()
            convo_id = lead.ghl_conversation_id
            if not convo_id:
                convo = ghl.get_or_create_conversation(lead.ghl_contact_id)
                convo_id = convo.get("id") or convo.get("conversationId")
            result = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

            lead.ghl_conversation_id = convo_id
            db.session.add(Conversation(
                lead_id=lead.id, direction="outbound", message=message,
                step=lead.conversation_step, status="sent",
                ghl_message_id=result.get("messageId", ""),
            ))
            db.session.commit()
            _bulk_state["sent"] = _bulk_state.get("sent", 0) + 1
            _bump(QUEUE_SENT_KEY)
            logger.info(f"Paced {'follow-up' if is_followup else 'send'} -> {lead.business_name}")
        except Exception as e:
            db.session.rollback()
            _bulk_state["failed"] = _bulk_state.get("failed", 0) + 1
            _bulk_state.setdefault("errors", []).append(f"{lead.business_name}: {e}")
            _bump(QUEUE_FAIL_KEY)
            logger.error(f"Paced send failed for lead {lead.id}: {e}")

        min_gap = int(AppSettings.get("send_min_interval_mins", "3"))
        max_gap = int(AppSettings.get("send_max_interval_mins", "12"))
        if max_gap < min_gap:
            min_gap, max_gap = max_gap, min_gap
        gap = random.uniform(min_gap, max_gap)
        _next_send_at = datetime.utcnow() + timedelta(minutes=gap)
        try:
            AppSettings.set(QUEUE_NEXT_KEY, _next_send_at.isoformat())
        except Exception:
            pass
        logger.info(f"Next queued message in {gap:.1f} min (at {_next_send_at:%H:%M:%S} UTC)")


_retry_thread = None


def _retry_lock_later(app, interval: int = 60):
    """
    Poll for the scheduler lock in the background so a worker can take over if
    the process that owned it goes away. Without this, losing the owning worker
    left the send queue frozen until someone restarted the service by hand.
    """
    global _retry_thread
    if _retry_thread and _retry_thread.is_alive():
        return

    import threading

    def _loop():
        while True:
            time.sleep(interval)
            if _scheduler and _scheduler.running:
                return
            if _acquire_scheduler_lock():
                logger.info("Scheduler lock acquired on retry — taking over the queue")
                start_smart_scheduler(app)
                from services.followup_scheduler import start_scheduler
                start_scheduler(app)
                return

    _retry_thread = threading.Thread(target=_loop, daemon=True)
    _retry_thread.start()


def get_bulk_state() -> dict:
    state = dict(_bulk_state)
    state["started_at"] = (state["started_at"].isoformat()
                           if isinstance(state["started_at"], datetime) else state["started_at"])
    state["errors"] = state["errors"][-10:]
    return state


def cancel_bulk():
    _bulk_state["cancel"] = True

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

        # Pick next eligible lead — skip any contacted in last 24h (duplicate guard)
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)
        lead = db.session.execute(
            select(Lead).where(
                Lead.imported_to_ghl == True,
                Lead.status == "not_contacted",
                Lead.ghl_contact_id.isnot(None),
                Lead.phone.isnot(None),
                (Lead.last_contacted_at.is_(None)) | (Lead.last_contacted_at < cutoff_24h),
            ).limit(1)
        ).scalar_one_or_none()

        if not lead:
            logger.info("Smart sender: no eligible leads to contact")
            return

        try:
            # Mark as contacted immediately before GHL call to prevent duplicate sends on crash
            lead.last_contacted_at = datetime.utcnow()
            lead.status = "message_sent"
            lead.conversation_step = 0
            lead.updated_at = datetime.utcnow()
            db.session.commit()

            ghl      = GHLService()
            message  = get_initial_message(lead)
            convo    = ghl.get_or_create_conversation(lead.ghl_contact_id)
            convo_id = convo.get("id") or convo.get("conversationId")
            result   = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

            lead.ghl_conversation_id = convo_id
            db.session.add(Conversation(
                lead_id=lead.id, direction="outbound", message=message,
                step=0, status="sent", ghl_message_id=result.get("messageId", ""),
            ))
            db.session.commit()
            logger.info(f"Smart sender: sent to '{lead.business_name}' ({sent_today + 1}/{today_limit})")

        except Exception as e:
            db.session.rollback()
            logger.error(f"Smart sender: failed for lead {lead.id}: {e}")


# send_bulk_paced() lived here. It has been removed: the database-backed
# queue drained by _drain_send_queue() is now the single path for outreach
# sends, so there is no second implementation to fall out of step with it.


def start_smart_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    if not _acquire_scheduler_lock():
        # Another worker owns it — but if that worker later dies, nothing would
        # ever pick the schedulers back up and the queue would stall forever.
        # Keep checking so the survivor takes over on its own.
        logger.info("Smart send scheduler not started — another worker owns it; will retry")
        _retry_lock_later(app)
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
    # Drain the queued-outreach list. Runs on a shorter tick so the first
    # message of a batch goes out promptly; the gap between messages is
    # enforced by _next_send_at, not by this interval.
    _scheduler.add_job(
        func=lambda: _drain_send_queue(app),
        trigger="interval",
        seconds=20,
        id="send_queue_job",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Smart send scheduler started (queue drain every 20s)")


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
