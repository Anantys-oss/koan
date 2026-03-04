"""Flask server for Cloud Run — health checks, GitHub webhooks, and API triggers.

Registers:
- /health (blueprint from app.health)
- /webhook/github (POST, from watcher webhook_handler)
- /api/trigger-report (POST, generates and sends daily report)

Serves on PORT (default 8080).
"""

import logging
import os
from datetime import date, datetime, timezone

from flask import Flask, jsonify, request

from app.health import health_bp
from app.utils import INSTANCE_DIR
from app.watcher.webhook_handler import register_webhooks

logger = logging.getLogger("governor.health_server")

app = Flask(__name__)
app.register_blueprint(health_bp)

register_webhooks(app, INSTANCE_DIR)


@app.route("/api/trigger-report", methods=["POST"])
def trigger_report():
    """POST /api/trigger-report — generate and send the daily report.

    Query params:
        date: YYYY-MM-DD (optional, defaults to yesterday)

    Returns JSON: {"status": "sent"|"no_activity"|"error", "date": "YYYY-MM-DD"}
    """
    from app.governor_daily_report import send_daily_report

    date_str = request.args.get("date")
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"status": "error", "error": f"Invalid date: {date_str}"}), 400
    else:
        target_date = datetime.now(timezone.utc).date()

    try:
        narrative = send_daily_report(target_date=target_date, notify=True)
        status = "no_activity" if "aucune activité" in narrative.lower() else "sent"
        return jsonify({"status": status, "date": target_date.isoformat()})
    except Exception as e:
        logger.exception("Failed to generate daily report")
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
