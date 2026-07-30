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

            ghl = GHLService()
            sent = 0
            for lead in due:
                try:
                    message = get_followup_message(lead)
                    convo_id = lead.ghl_conversation_id
                    result = ghl.send_sms(lead.ghl_contact_id, message, convo_id)

                    lead.followup_count = (lead.followup_count or 0) + 1
                    lead.last_contacted_at = datetime.utcnow()
                    lead.updated_at = datetime.utcnow()

                    db.session.add(Conversation(
                        lead_id=lead.id,
                        direction="outbound",
                        message=message,
                        step=lead.conversation_step,
                        status="sent",
                        ghl_message_id=result.get("messageId", ""),
                    ))
                    sent += 1
                except Exception as e:
                    logger.error(f"Follow-up failed for lead {lead.id}: {e}")

            db.session.commit()
            logger.info(f"Follow-up run complete: {sent}/{len(due)} sent")

        except Exception as e:
            logger.exception(f"Follow-up scheduler error: {e}")


def trigger_now(app):
    """Manually trigger a follow-up run (called from admin UI)."""
    _run_followups(app)
