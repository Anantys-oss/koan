"""Minimal Flask server for Cloud Run health checks.

Registers the /health blueprint and serves it on PORT (default 8080).
Used as a companion process alongside chat_receiver in the Worker Pool.
"""

import os
from flask import Flask
from app.health import health_bp

app = Flask(__name__)
app.register_blueprint(health_bp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
