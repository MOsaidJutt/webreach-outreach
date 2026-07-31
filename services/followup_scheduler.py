import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
_scheduler = None


def start_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    # Only the process that owns the scheduler lock runs follow-ups, so a
    # multi-worker gunicorn deploy cannot send each follow-up twice.
    from services.smart_sender import _acquire_scheduler_lock
    if not _acquire_scheduler_lock():
        logger.info("Follow-up scheduler not started — another worker owns it")
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=lambda: _run_followups(app),
        trigger="interval",
        hours=6,           # check every 6 hours
        id="followup_job",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Follow-up scheduler started (checks every 6 hours)")


def _run_followups(app):
    with app.app_context():
        try:
            from models import Lead, Conversation, AppSettings
            from extensions import db
            from services.ghl_service import GHLService
            from services.conversation_ai import get_followup_message
            from sqlalchemy import select

            enabled = AppSettings.get("followup_enabled", "true").lower() == "true"
            if not enabled:
                logger.info("Follow-ups disabled in settings — skipping")
                return

            interval_days = int(AppSettings.get("followup_interval_days", "3"))
            max_count = int(AppSettings.get("followup_max_count", "2"))
            cutoff = datetime.utcnow() - timedelta(days=interval_days)

            # Find leads that:
            # - are imported to GHL
            # - status is 'message_sent' (sent but no reply)
            # - last contacted more than interval_days ago
            # - follow-up count is below max
            stmt = (
                select(Lead)
                .where(
                    Lead.imported_to_ghl == True,
                    Lead.status == "message_sent",
                    Lead.followup_count < max_count,
                    Lead.last_contacted_at <= cutoff,
                    Lead.ghl_contact_id.isnot(None),
                )
            )
            due = db.session.execute(stmt).scalars().all()
            logger.info(f"Follow-up run: {len(due)} leads due")

            if not due:
                return

            # Queue rather than send. This loop used to call send_sms for every
            # due lead back to back, so a batch of follow-ups all landed within
            # the same second — the same fault the initial send had. The paced
            # queue releases them one at a time inside the sending window.
            from services.smart_sender import queue_leads_for_send
            queued = queue_leads_for_send([l.id for l in due], kind="followup")
            logger.info(f"Follow-up run complete: {queued}/{len(due)} queued for paced sending")

        except Exception as e:
            logger.exception(f"Follow-up scheduler error: {e}")


def trigger_now(app):
    """Manually trigger a follow-up run (called from admin UI)."""
    _run_followups(app)
