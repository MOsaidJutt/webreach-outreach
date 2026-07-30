import logging
import os
import re
from logging.handlers import RotatingFileHandler

from flask import Flask
from extensions import db, migrate, cors


def _configure_file_logging(app):
    """
    Write the app log to instance/app.log so the Admin page can show it.

    This lives here rather than in app.py because gunicorn imports the app
    through either entry point, and under `create_app()` there was no file
    handler at all — which is why the Admin log viewer stayed empty on the
    server while the diagnosis it was meant to provide was most needed.
    """
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "app.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root = logging.getLogger()
    already = any(
        isinstance(h, RotatingFileHandler)
        and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(log_path)
        for h in root.handlers
    )
    if already:
        return

    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _repair_placeholder_collision():
    """
    One-time repair of templates saved before {company_name} existed.

    {business_name} means the LEAD's business everywhere. Templates that used
    it to mean our own agency rendered "I'm Sarah from {business_name}" as
    "I'm Sarah from Joe's Hair Studio". Rewrite only that specific usage in the
    two templates that had it; every other customisation is left untouched.
    """
    from models import AppSettings

    # Deliberately narrow. Only "<agent> from {business_name}" is rewritten,
    # because that phrasing can only ever mean our own agency. Something like
    # "we build websites for {business_name}" is left alone — there
    # {business_name} correctly means the lead.
    from_pattern = re.compile(r"\bfrom\s+\{business_name\}")
    prompt_patterns = [
        re.compile(r"\b(specialist|specialists|outreach specialist)\s+for\s+\{business_name\}"),
        re.compile(r"\bYou are from\s+\{business_name\}"),
    ]

    repaired = []
    for key in ("msg_compliment", "msg_who_are_you", "ai_system_prompt"):
        value = AppSettings.get(key)
        if not value:
            continue

        new_value = from_pattern.sub("from {company_name}", value)
        if key == "ai_system_prompt":
            for pattern in prompt_patterns:
                new_value = pattern.sub(
                    lambda m: m.group(0).replace("{business_name}", "{company_name}"),
                    new_value,
                )

        if new_value != value:
            AppSettings.set(key, new_value)
            repaired.append(key)

    if repaired:
        logging.getLogger(__name__).info(
            f"Repaired agency-vs-lead placeholder in: {', '.join(repaired)}"
        )


def create_app(config_object=None):
    app = Flask(__name__)

    if config_object is None:
        from config import Config
        app.config.from_object(Config)
    else:
        app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app)

    with app.app_context():
        import models  # noqa: F401

        from routes.scraping import scraping_bp
        from routes.leads import leads_bp
        from routes.webhooks import webhooks_bp
        from routes.analytics import analytics_bp
        from routes.admin import admin_bp
        from routes.chat import chat_bp
        from routes.views import views_bp

        app.register_blueprint(scraping_bp, url_prefix="/api/scraping")
        app.register_blueprint(leads_bp,    url_prefix="/api/leads")
        app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
        app.register_blueprint(analytics_bp, url_prefix="/api/analytics")
        app.register_blueprint(admin_bp,    url_prefix="/api/admin")
        app.register_blueprint(chat_bp,     url_prefix="/api/chat")
        app.register_blueprint(views_bp)

        db.create_all()

        # Bring the schema up to date with migrations/. A brand-new database
        # (or one that pre-dates migrations, already matching models.py via
        # create_all() above) gets stamped at the current baseline; anything
        # already tracked gets any newer migrations applied automatically —
        # so a future `models.py` column change just needs a migration
        # script committed, with no manual DB step on deploy.
        from sqlalchemy import inspect as sa_inspect
        from flask_migrate import upgrade as migrate_upgrade, stamp as migrate_stamp

        inspector = sa_inspect(db.engine)
        try:
            if "alembic_version" not in inspector.get_table_names():
                migrate_stamp(revision="head")
            else:
                migrate_upgrade()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Migration bootstrap failed: {e}")

        # Seed default settings if not already present
        from models import AppSettings
        for key, val in AppSettings.DEFAULTS.items():
            if AppSettings.get(key) is None:
                db.session.add(AppSettings(key=key, value=val))
        db.session.commit()

        _repair_placeholder_collision()

    _configure_file_logging(app)

    from services.followup_scheduler import start_scheduler
    from services.smart_sender import start_smart_scheduler
    start_scheduler(app)
    start_smart_scheduler(app)

    return app
