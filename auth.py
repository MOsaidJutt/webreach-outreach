from functools import wraps
from flask import request, jsonify, current_app


def require_admin_password(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = current_app.config.get("ADMIN_PASSWORD", "")
        if not expected:
            return f(*args, **kwargs)
        provided = request.headers.get("X-Admin-Password", "")
        if provided != expected:
            return jsonify({"error": "Admin password required", "code": "PASSWORD_REQUIRED"}), 403
        return f(*args, **kwargs)
    return decorated
