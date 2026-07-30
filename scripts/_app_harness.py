"""
Boot the real application against a throwaway database, seeded with the exact
leads from the client's screenshots.

Run by the project venv (which has Flask); the verification driver runs under a
different interpreter, so this is launched as a subprocess.

Nothing here stubs the application itself — it is the real create_app(), the
real routes and the real reply engine. Only the outbound SMS gateway is
redirected, via GHL_API_BASE_URL, so no real business is texted.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The leads whose replies went unanswered in the client's GHL screenshots.
SEED_LEADS = [
    ("Santa Fe Builders Llc",            "+15551110001", "ghl-contact-santafe",  4.9, 87),
    ("Clean Slate Cleaning LLC",         "+15551110002", "ghl-contact-cleanslate", 4.7, 52),
    ("J&C Roofing Co",                   "+15551110003", "ghl-contact-jcroofing", 4.8, 121),
    ("Walton's Exterior Home Cleaning LLC", "+15551110004", "ghl-contact-walton", 5.0, 34),
]


def main():
    port = int(os.environ.get("VERIFY_PORT", "5055"))

    from app_factory import create_app
    from extensions import db
    from models import Lead

    app = create_app()

    with app.app_context():
        db.create_all()
        for name, phone, contact_id, rating, reviews in SEED_LEADS:
            if db.session.query(Lead).filter_by(phone=phone).first():
                continue
            db.session.add(Lead(
                business_name=name, phone=phone, ghl_contact_id=contact_id,
                rating=rating, reviews_count=reviews,
                imported_to_ghl=True, imported_to_ghl_at=datetime.utcnow(),
                status="message_sent", conversation_step=0, followup_count=2,
                city="Santa Fe", state="NM", website="",
            ))
        db.session.commit()

    # Serve with waitress rather than Werkzeug's development server. The dev
    # server intermittently fails to deliver a response to Chromium when a page
    # fires several XHRs at once — the request completes server-side in
    # milliseconds but the browser keeps the socket open forever, eventually
    # exhausting its six-per-origin pool so the next navigation never starts.
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=port, threads=12, channel_timeout=60,
              _quiet=True)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
