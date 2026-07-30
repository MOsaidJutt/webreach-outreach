import logging
from flask import Blueprint, request, jsonify
from services.conversation_ai import simulate_conversation

chat_bp = Blueprint("chat", __name__)
logger = logging.getLogger(__name__)


@chat_bp.route("/simulate", methods=["POST"])
def simulate():
    """
    Run the tester through the real production state machine.

    The response carries the template key and the mode alongside the reply so
    the tester can prove on screen which saved template produced the message.
    """
    try:
        data = request.get_json() or {}
        business_name = data.get("business_name") or "Test Business"
        try:
            rating = float(data.get("rating", 4.8))
        except (TypeError, ValueError):
            rating = 4.8
        try:
            reviews = int(data.get("reviews", 120))
        except (TypeError, ValueError):
            reviews = 120
        messages = data.get("messages", [])

        result = simulate_conversation(business_name, rating, reviews, messages)
        return jsonify(result)
    except Exception as e:
        logger.exception(f"Chat simulate error: {e}")
        return jsonify({"error": str(e)}), 500
