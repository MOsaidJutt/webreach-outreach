from flask import Blueprint, render_template

views_bp = Blueprint("views", __name__)


@views_bp.route("/")
def dashboard():
    return render_template("dashboard.html")

@views_bp.route("/leads")
def leads():
    return render_template("leads.html")

@views_bp.route("/leads/<int:lead_id>")
def lead_detail(lead_id):
    return render_template("lead_detail.html", lead_id=lead_id)

@views_bp.route("/scraping")
def scraping():
    return render_template("scraping.html")

@views_bp.route("/settings")
def settings():
    return render_template("settings.html")

@views_bp.route("/conversations")
def conversations():
    return render_template("conversations.html")

@views_bp.route("/admin")
def admin():
    return render_template("admin.html")

@views_bp.route("/ai-chat")
def ai_chat():
    return render_template("ai_chat.html")
