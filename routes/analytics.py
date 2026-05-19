from flask import Blueprint, jsonify
from extensions import db
from models import Lead, Conversation, ScrapingJob
from sqlalchemy import func, select

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/summary", methods=["GET"])
def summary():
    total_scraped = db.session.scalar(select(func.count(Lead.id))) or 0
    total_with_phone = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.phone.isnot(None), Lead.phone != "")
    ) or 0
    imported_to_ghl = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.imported_to_ghl == True)
    ) or 0
    messages_sent = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.status != "not_contacted")
    ) or 0
    total_replied = db.session.scalar(
        select(func.count(Lead.id)).where(
            Lead.status.in_(["replied", "interested", "website_sent", "converted"])
        )
    ) or 0
    total_interested = db.session.scalar(
        select(func.count(Lead.id)).where(
            Lead.status.in_(["interested", "website_sent", "converted"])
        )
    ) or 0
    websites_sent = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.status.in_(["website_sent", "converted"]))
    ) or 0
    converted = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.status == "converted")
    ) or 0
    opted_out = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.status == "opted_out")
    ) or 0
    not_interested = db.session.scalar(
        select(func.count(Lead.id)).where(Lead.status == "not_interested")
    ) or 0

    response_rate = round((total_replied / messages_sent * 100) if messages_sent > 0 else 0, 1)
    interest_rate = round((total_interested / total_replied * 100) if total_replied > 0 else 0, 1)
    conversion_rate = round((converted / total_interested * 100) if total_interested > 0 else 0, 1)

    status_counts = db.session.execute(
        select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    ).all()
    status_breakdown = {s: c for s, c in status_counts}

    category_counts = db.session.execute(
        select(Lead.category, func.count(Lead.id))
        .where(Lead.category.isnot(None), Lead.category != "")
        .group_by(Lead.category)
        .order_by(func.count(Lead.id).desc())
        .limit(10)
    ).all()
    top_categories = [{"category": c, "count": n} for c, n in category_counts]

    recent_jobs = db.session.execute(
        select(ScrapingJob).order_by(ScrapingJob.created_at.desc()).limit(5)
    ).scalars().all()

    from datetime import datetime, timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    daily_sms = db.session.execute(
        select(
            func.date(Conversation.created_at).label("date"),
            func.count(Conversation.id).label("count"),
        )
        .where(
            Conversation.direction == "outbound",
            Conversation.created_at >= thirty_days_ago,
        )
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
    ).all()

    return jsonify({
        "metrics": {
            "total_scraped": total_scraped,
            "total_with_phone": total_with_phone,
            "imported_to_ghl": imported_to_ghl,
            "messages_sent": messages_sent,
            "total_replied": total_replied,
            "total_interested": total_interested,
            "websites_sent": websites_sent,
            "converted": converted,
            "opted_out": opted_out,
            "not_interested": not_interested,
            "response_rate": response_rate,
            "interest_rate": interest_rate,
            "conversion_rate": conversion_rate,
        },
        "status_breakdown": status_breakdown,
        "top_categories": top_categories,
        "recent_jobs": [j.to_dict() for j in recent_jobs],
        "daily_sms": [{"date": str(d), "count": c} for d, c in daily_sms],
    })
