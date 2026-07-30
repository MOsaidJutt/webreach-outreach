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

        # AI Model
        "ai_model": "gpt-4o",

        # How replies are produced.
        #   "templates" — send the saved template VERBATIM for every recognised
        #                 reply. What you see on the AI Settings page is exactly
        #                 what the lead receives. OpenAI is never consulted.
        #   "hybrid"    — templates verbatim for the scripted steps and the
        #                 known objections; OpenAI only for genuinely off-script
        #                 questions that no template covers.
        #   "ai"        — OpenAI writes every reply, using the templates only as
        #                 a style guide (this paraphrases them).
        "ai_mode": "templates",

        # Create a lead automatically when an inbound SMS arrives from a number
        # that isn't in the database yet. Without this a message from any
        # untracked number is silently dropped.
        "webhook_autocreate_leads": "true",

        # Smart Send Scheduler
        "smart_send_enabled":        "false",
        "warmup_start_limit":        "20",
        "warmup_daily_increase":     "2",
        "warmup_max_limit":          "200",
        "warmup_start_date":         "",
        "send_start_time":           "07:00",
        "send_end_time":             "18:00",
        "send_timezone":             "America/New_York",
        "send_min_interval_mins":    "3",
        "send_max_interval_mins":    "12",

        # Message templates
        #
        # Placeholders:
        #   {business_name} / {lead_name} — the LEAD's business (e.g. "Joe's Hair Studio")
        #   {company_name}  / {my_company} — YOUR agency (e.g. "AMZUS Digital")
        #   {agent_name}                   — the SMS persona (e.g. "Sarah")
        #   {rating} {reviews}             — the lead's Google rating / review count
        #   {website}                      — your agency website
        #
        # {business_name} always means the LEAD. Use {company_name} when you
        # mean your own agency — these used to be the same token, which made
        # "I'm Sarah from {business_name}" render as "I'm Sarah from Joe's Hair
        # Studio".
        "msg_opening": "Hi, is this the owner of {business_name}?",

        "msg_compliment": (
            "Great! Thanks for confirming. I'm {agent_name} from {company_name} — we build professional websites for local businesses.\n\n"
            "I noticed something on your Google profile that's actively costing you customers, "
            "and I'd love to show you how to fix it — completely free of charge, no catch, no obligation.\n\n"
            "Do you have 60 seconds for me to explain?"
        ),

        "msg_offer": (
            "Here's our proposal — and please note, this is completely free.\n\n"
            "We'll build {business_name} a full professional website as a demo. "
            "You pay absolutely nothing to see it.\n\n"
            "If you love it and want to keep it live, we can talk about that then. "
            "If you don't like it, we shake hands and part ways — zero obligations, zero charges.\n\n"
            "We've actually already started a draft. Would you like to see it?"
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
            "Hi again! I messaged a couple of days ago but may have caught you at a busy time.\n\n"
            "Just wanted to make sure my message reached the right person — is this still {business_name}?"
        ),

        "msg_followup_2": (
            "Last message from us, promise! I had something I genuinely think could help {business_name}, "
            "but want to make sure you actually received my earlier message.\n\n"
            "If this is the wrong number or not a good time, just let me know. No hard feelings at all 👋"
        ),

        "msg_who_are_you": (
            "I'm {agent_name} from {company_name} — we specialise in building professional websites "
            "for local businesses. You can check us out at {website}\n\n"
            "I reached out because I noticed your Google profile doesn't have a website linked."
        ),

        "msg_cost_question": (
            "Great question — the demo costs you absolutely nothing. We build it, you look at it, "
            "and if it's not for you we part ways with zero charge.\n\n"
            "You'd only ever pay if you loved it and wanted to keep it live. Shall I show you?"
        ),

        "msg_number_question": (
            "I found your business listed on Google Maps — your details are publicly visible there. "
            "I noticed you have great reviews but no website, so I reached out.\n\n"
            "If you'd prefer not to be contacted, just reply STOP and I'll remove you immediately."
        ),

        # AI System Prompt
        "ai_system_prompt": (
            "You are {agent_name}, a warm and professional outreach specialist for {company_name} "
            "({website}) — a web design agency that builds websites for local businesses.\n\n"
            "You are texting the owner of '{lead_name}', a local business with a "
            "{rating}-star Google rating and {reviews} reviews, who currently has NO website.\n\n"
            "YOUR MISSION:\n"
            "Build rapport, introduce yourself properly, create curiosity about what they're missing, "
            "and offer them a completely FREE website demo with zero obligation.\n\n"
            "CONVERSATION FLOW:\n"
            "1. Confirm you have the right person\n"
            "2. Introduce yourself + company + ALWAYS mention it's completely FREE + end with a direct question\n"
            "3. Compliment their rating, then reveal the gap (no website = lost customers)\n"
            "4. Make the free demo offer — ZERO cost, ZERO obligation to keep it\n"
            "5. If interested — confirm team will send the link shortly\n\n"
            "KEY MESSAGES TO CONVEY:\n"
            "- You are from {company_name}, specialists in local business websites\n"
            "- The demo/preview website is COMPLETELY FREE — no payment required ever to see it\n"
            "- They only pay if they LOVE it and CHOOSE to keep it live\n"
            "- Without a website, Google hides them from people actively searching\n"
            "- Competitors with worse ratings ARE getting those customers\n\n"
            "SMS RULES:\n"
            "- Keep each message to 2-4 sentences MAX — this is SMS not email\n"
            "- Be human, warm, conversational — never sound like a bot or salesperson\n"
            "- Build curiosity before making the offer — don't rush\n"
            "- Never be pushy — one gentle ask, then respect their answer\n"
            "- Handle ALL objections with empathy\n"
            "- If asked about cost: the DEMO is 100% free, no card needed, no obligation\n"
            "- If asked who you are: mention {company_name} and {website}\n"
            "- If they say STOP/remove me: apologise, confirm removal\n"
            "- Remember EVERYTHING in the conversation history\n"
            "- Never repeat yourself — always move the conversation forward\n\n"
            "CRITICAL: The demo website is ALWAYS free. They pay NOTHING to see it. "
            "They only pay if they love it and want to keep it. Make this crystal clear."
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
    ai_paused = db.Column(db.Boolean, default=False)

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
            "ai_paused": bool(self.ai_paused),
            "website_url_sent": self.website_url_sent,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_contacted_at": (
                self.last_contacted_at.isoformat() if self.last_contacted_at else None
            ),
        }


class WebhookEvent(db.Model):
    """
    Every inbound hit on /api/webhooks/ghl, recorded before any validation can
    reject it — including the ones we could not act on.

    Without this the only evidence of a dropped webhook was a line in a log file
    on the VPS, so "the AI didn't reply" and "GHL never called us" looked
    identical from the dashboard. `outcome` says which one it was.
    """

    __tablename__ = "webhook_events"

    id = db.Column(db.Integer, primary_key=True)

    outcome = db.Column(db.String(50))        # replied | no_text | no_lead | manual_mode | send_failed | ignored | error
    detail = db.Column(db.Text)               # human-readable explanation
    contact_id = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    message = db.Column(db.Text)
    lead_id = db.Column(db.Integer)
    reply = db.Column(db.Text)
    raw_body = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    OUTCOME_COLORS = {
        "replied":     "success",
        "manual_mode": "info",
        "ignored":     "secondary",
        "no_text":     "warning",
        "no_lead":     "warning",
        "send_failed": "danger",
        "error":       "danger",
    }

    def to_dict(self):
        return {
            "id": self.id,
            "outcome": self.outcome,
            "outcome_color": self.OUTCOME_COLORS.get(self.outcome, "secondary"),
            "detail": self.detail,
            "contact_id": self.contact_id,
            "phone": self.phone,
            "message": self.message,
            "lead_id": self.lead_id,
            "reply": self.reply,
            "raw_body": self.raw_body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
