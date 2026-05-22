import logging
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import ScrapingJob, Lead
from services.outscraper_service import OutscraperService
from sqlalchemy import select
from auth import require_admin_password

scraping_bp = Blueprint("scraping", __name__)
logger = logging.getLogger(__name__)


@scraping_bp.route("/jobs", methods=["GET"])
def list_jobs():
    jobs = db.session.execute(
        select(ScrapingJob).order_by(ScrapingJob.created_at.desc()).limit(50)
    ).scalars().all()
    return jsonify({"jobs": [j.to_dict() for j in jobs]})


@scraping_bp.route("/jobs/<int:job_id>", methods=["GET"])
def get_job(job_id):
    job = db.get_or_404(ScrapingJob, job_id)
    return jsonify({"job": job.to_dict()})


@scraping_bp.route("/start", methods=["POST"])
@require_admin_password
def start_scraping():
    data = request.get_json() or {}
    search_query = data.get("query", "").strip()
    location = data.get("location", "").strip()
    limit = int(data.get("limit", 200))

    if not search_query:
        return jsonify({"error": "query is required"}), 400
    if limit < 1 or limit > 500:
        return jsonify({"error": "limit must be between 1 and 500"}), 400

    job = ScrapingJob(
        search_query=search_query,
        location=location,
        min_rating=0,       # no pre-filtering — all results stored
        limit=limit,
        status="pending",
    )
    db.session.add(job)
    db.session.commit()

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_scraping_job, args=(app, job.id), daemon=True)
    thread.start()

    return jsonify({"message": "Scraping job started", "job": job.to_dict()}), 202


def _run_scraping_job(app, job_id: int):
    with app.app_context():
        job = db.session.get(ScrapingJob, job_id)
        if not job:
            return

        job.status = "running"
        db.session.commit()

        try:
            svc = OutscraperService()
            # Fetch ALL results — no filtering, everything stored
            results = svc.search_all(
                query=job.search_query,
                location=job.location,
                limit=job.limit,
            )

            imported = 0
            skipped = 0
            for biz in results:
                place_id = biz.get("place_id", "")
                if place_id:
                    existing = db.session.execute(
                        select(Lead).where(Lead.place_id == place_id)
                    ).scalar_one_or_none()
                    if existing:
                        skipped += 1
                        continue

                lead = Lead(
                    scraping_job_id=job.id,
                    business_name=biz.get("business_name"),
                    rating=biz.get("rating"),
                    reviews_count=biz.get("reviews_count"),
                    phone=biz.get("phone"),
                    category=biz.get("category"),
                    address=biz.get("address"),
                    city=biz.get("city"),
                    state=biz.get("state"),
                    country=biz.get("country"),
                    google_maps_url=biz.get("google_maps_url"),
                    place_id=place_id or None,
                    website=biz.get("website", ""),
                    status="not_contacted",
                )
                db.session.add(lead)
                imported += 1

            job.total_found = len(results)
            job.total_imported = imported
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"Job {job.id}: {imported} imported, {skipped} duplicates skipped")

        except Exception as e:
            logger.exception(f"Scraping job {job_id} failed: {e}")
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()
            db.session.commit()


@scraping_bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@require_admin_password
def delete_job(job_id):
    job = db.get_or_404(ScrapingJob, job_id)
    db.session.delete(job)
    db.session.commit()
    return jsonify({"message": "Job deleted"})


@scraping_bp.route("/jobs/clear-empty", methods=["DELETE"])
@require_admin_password
def clear_empty_jobs():
    """Delete all jobs with 0 results."""
    from sqlalchemy import select
    empty = db.session.execute(
        select(ScrapingJob).where(ScrapingJob.total_found == 0)
    ).scalars().all()
    count = len(empty)
    for job in empty:
        db.session.delete(job)
    db.session.commit()
    return jsonify({"message": f"{count} empty jobs deleted"})


@scraping_bp.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@require_admin_password
def cancel_job(job_id):
    job = db.get_or_404(ScrapingJob, job_id)
    if job.status in ("completed", "failed"):
        return jsonify({"error": "Job already finished"}), 400
    job.status = "failed"
    job.error_message = "Cancelled by user"
    job.completed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"message": "Job cancelled", "job": job.to_dict()})
