import logging
import os
from logging.handlers import RotatingFileHandler
from app_factory import create_app

app = create_app()

# File logger so we can view logs in admin panel
log_path = os.path.join(os.path.dirname(__file__), "instance", "app.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
))
logging.getLogger().addHandler(file_handler)
logging.getLogger().setLevel(logging.INFO)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
