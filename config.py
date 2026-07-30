import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///outreach.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
    GHL_API_KEY = os.getenv("GHL_API_KEY", "")
    GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
    GHL_BASE_URL = os.getenv("GHL_BASE_URL", "https://rest.gohighlevel.com/v1")
    GHL_ACCESS_TOKEN = os.getenv("GHL_ACCESS_TOKEN", "")
    # Leave unset in production — only overridden to point the SMS calls at a
    # local stub gateway when verifying the end-to-end flow.
    GHL_API_BASE_URL = os.getenv("GHL_API_BASE_URL", "")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    APP_URL = os.getenv("APP_URL", "http://localhost:5000")
    SMS_FROM_NUMBER = os.getenv("SMS_FROM_NUMBER", "")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
