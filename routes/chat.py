import logging
from flask import Blueprint, request, jsonify
from services.conversation_ai import simulate_conversation

chat_bp = Blueprint("chat", __name__)
logger = logging.getLogger(__name__)


@chat_bp.route("/simulate", methods=["POST"])
def simulate():
    try:
        data = request.get_json() or {}
        business_name = data.get("business_name", "Test Business")
        rating = float(data.get("rating", 4.8))
        reviews = int(data.get("reviews", 120))
        messages = data.get("messages", [])

        reply = simulate_conversation(business_name, rating, reviews, messages)
        return jsonify({"reply": reply})
    except Exception as e:
        logger.exception(f"Chat simulate error: {e}")
        return jsonify({"error": str(e)}), 500
