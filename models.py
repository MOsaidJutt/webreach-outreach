from datetime import datetime
from extensions import db


class AppSettings(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            db.session.add(cls(key=key, value=str(value)))
        db.session.commit()

    # Default settings
    DEFAULTS = {
        # Business identity
        "followup_enabled":       "true",
        "followup_interval_days": "3",
        "followup_max_count":     "2",
        "business_website":       "https://amzusdigital.com/",
        "business_name":          "AMZUS Digital",
        "sms_agent_name":         "Sarah",
        "daily_send_limit":       "50",

        # Message templates
        "msg_opening": "Hi, is this the owner of {business_name}?",

        "msg_compliment": (
            "We noticed something on your Google profile that's actually costing you customers right now.\n\n"
            "You have {reviews} reviews and a {rating}-star rating — that's better than most businesses in your area. "
            "But because there's no website linked, Google won't show you to people actively searching for what you offer.\n\n"
            "Your competitors with worse ratings are getting those customers instead."
        ),

        "msg_offer": (
            "Here's what we'd like to do — we'll build {business_name} a complete professional website, "
            "completely free of charge.\n\n"
            "No upfront cost. No obligation. If you love it, we can talk about keeping it live. "
            "If not, walk away with nothing to lose.\n\n"
            "We've already started on a draft. Want to see it?"
        ),

        "msg_interested": (
            "Perfect — we'll have it ready for you very shortly.\n\n"
            "One of our team will send you the link so you can see exactly what we've built. "
            "We think you'll be pleasantly surprised. 😊"
        ),

        "msg_not_interested": (
            "No problem at all — I completely respect that. "
            "If things ever change and you want to explore it, we're here. "
            "All the best! 👋"
        ),

        "msg_opt_out": (
            "Understood — I've removed you from our list and you won't hear from us again. "
            "Sorry for the interruption and have a great day! 🙏"
        ),

        "msg_unclear": (
            "Just to be crystal clear — there's zero cost and zero obligation. "
            "We build the site, you take a look. "
            "If it's not for you, no hard feelings whatsoever.\n\n"
            "Does that sound fair? Yes or no works perfectly 😊"
        ),

        "msg_followup_1": (
            "Hi again — just checking back in. "
            "We still have that free website ready for {business_name}. "
            "Every day without a website is another day customers are going elsewhere. "
            "Want us to send you the link?"
        ),

        "msg_followup_2": (
            "Last one from us — the free website for {business_name} is ready to go. "
            "No cost, no commitment. Just take a look and decide. "
            "If not, we completely understand and won't contact you again."
        ),

        "msg_who_are_you": (
            "I'm {agent_name} from {business_name} — we specialise in building professional websites "
            "for local businesses. You can check us out at {website}\n\n"
            "I reached out because I noticed your Google profile doesn't have a website linked."
        ),

        "msg_number_question": (
            "I found your business listed on Google Maps — your details are publicly visible there. "
            "I noticed you have great reviews but no website, so I reached out.\n\n"
            "If you'd prefer not to be contacted, just reply STOP and I'll remove you immediately."
        ),

        # AI System Prompt
        "ai_system_prompt": (
            "You are {agent_name}, a friendly and professional outreach agent for {business_name}.\n\n"
            "You are having a text message (SMS) conversation with the owner of '{lead_name}', "
            "a local business with a {rating}-star Google rating and {reviews} reviews.\n\n"
            "GOAL: Build trust, highlight that they're losing customers without a website, "
            "and offer to build them one completely free with no obligation.\n\n"
            "CONVERSATION STAGES:\n"
            "1. Confirm you're speaking to the right person\n"
            "2. Point out they have no website and are losing customers to competitors\n"
            "3. Offer a completely free website - no cost, no obligation\n"
            "4. If they say yes - confirm team will be in touch\n\n"
            "RULES:\n"
            "- Keep messages SHORT (SMS - 2-4 sentences max per message)\n"
            "- Be warm, human, and conversational - never robotic\n"
            "- Build curiosity and urgency naturally\n"
            "- Never be pushy or desperate\n"
            "- If they ask about cost - it's completely free\n"
            "- If they ask who you are - mention {business_name} and website: {website}\n"
            "- If they say STOP/unsubscribe - apologise and confirm removal\n"
            "- Handle any objection with empathy and redirect to the offer\n"
            "- Remember and reference the full conversation history\n\n"
            "The website you offer is completely free. The business only pays if they love it and want to keep it live."
        ),
    }


class ScrapingJob(db.Model):
    __tablename__ = "scraping_jobs"

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(500), nullable=False)
    location = db.Column(db.String(200))
    min_rating = db.Column(db.Float, default=4.0)
    limit = db.Column(db.Integer, default=100)
    status = db.Column(db.String(50), default="pending")
    total_found = db.Column(db.Integer, default=0)
    total_imported = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    leads = db.relationship("Lead", backref="scraping_job", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "query": self.search_query,
            "location": self.location,
            "min_rating": self.min_rating,
            "limit": self.limit,
            "status": self.status,
            "total_found": self.total_found,
            "total_imported": self.total_imported,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    scraping_job_id = db.Column(db.Integer, db.ForeignKey("scraping_jobs.id"))

    business_name = db.Column(db.String(500))
    rating = db.Column(db.Float)
    reviews_count = db.Column(db.Integer)
    phone = db.Column(db.String(50))
    category = db.Column(db.String(200))
    address = db.Column(db.String(500))
    city = db.Column(db.String(200))
    state = db.Column(db.String(100))
    country = db.Column(db.String(100))
    google_maps_url = db.Column(db.String(1000))
    place_id = db.Column(db.String(200), unique=True)
    website = db.Column(db.String(500))

    ghl_contact_id = db.Column(db.String(200))
    ghl_conversation_id = db.Column(db.String(200))
    imported_to_ghl = db.Column(db.Boolean, default=False)
    imported_to_ghl_at = db.Column(db.DateTime)

    status = db.Column(db.String(50), default="not_contacted")
    conversation_step = db.Column(db.Integer, default=0)
    followup_count = db.Column(db.Integer, default=0)
    last_followup_at = db.Column(db.DateTime)

    website_url_sent = db.Column(db.String(500))
    website_sent_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_contacted_at = db.Column(db.DateTime)

    conversations = db.relationship(
        "Conversation", backref="lead", lazy=True, order_by="Conversation.created_at"
    )

    STATUS_LABELS = {
        "not_contacted": "Not Contacted",
        "message_sent": "Message Sent",
        "replied": "Replied",
        "interested": "Interested",
        "not_interested": "Not Interested",
        "opted_out": "Opted Out",
        "website_sent": "Website Sent",
        "converted": "Converted",
    }

    STATUS_COLORS = {
        "not_contacted": "secondary",
        "message_sent": "info",
        "replied": "primary",
        "interested": "warning",
        "not_interested": "danger",
        "opted_out": "dark",
        "website_sent": "success",
        "converted": "success",
    }

    def to_dict(self):
        reviews_url = (
            f"https://search.google.com/local/reviews?placeid={self.place_id}&q=*&hl=en&gl=US"
            if self.place_id else ""
        )
        return {
            "id": self.id,
            "business_name": self.business_name,
            "rating": self.rating,
            "reviews_count": self.reviews_count,
            "phone": self.phone,
            "category": self.category,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "google_maps_url": self.google_maps_url,
            "google_reviews_url": reviews_url,
            "place_id": self.place_id,
            "ghl_contact_id": self.ghl_contact_id,
            "imported_to_ghl": self.imported_to_ghl,
            "status": self.status,
            "status_label": self.STATUS_LABELS.get(self.status, self.status),
            "status_color": self.STATUS_COLORS.get(self.status, "secondary"),
            "conversation_step": self.conversation_step,
            "followup_count": self.followup_count,
            "website_url_sent": self.website_url_sent,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_contacted_at": (
                self.last_contacted_at.isoformat() if self.last_contacted_at else None
            ),
        }


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)

    direction = db.Column(db.String(10))
    message = db.Column(db.Text)
    ghl_message_id = db.Column(db.String(200))
    step = db.Column(db.Integer)
    status = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "direction": self.direction,
            "message": self.message,
            "step": self.step,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
