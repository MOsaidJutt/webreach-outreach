from flask import Flask
from extensions import db, migrate, cors


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

        # Safe column additions (idempotent — ignored if column already exists)
        from sqlalchemy import text
        with db.engine.connect() as _conn:
            for _stmt in [
                "ALTER TABLE leads ADD COLUMN ai_paused BOOLEAN DEFAULT 0",
            ]:
                try:
                    _conn.execute(text(_stmt))
                    _conn.commit()
                except Exception:
                    pass

        # Seed default settings if not already present
        from models import AppSettings
        for key, val in AppSettings.DEFAULTS.items():
            if AppSettings.get(key) is None:
                db.session.add(AppSettings(key=key, value=val))
        db.session.commit()

    from services.followup_scheduler import start_scheduler
    from services.smart_sender import start_smart_scheduler
    start_scheduler(app)
    start_smart_scheduler(app)

    return app
