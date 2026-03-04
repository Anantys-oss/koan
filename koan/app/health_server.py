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
        date: YYYY-MM-DD (optional, defaults to today UTC)

    Returns JSON: {"status": "sent"|"no_activity"|"error", "date": "YYYY-MM-DD"}
    """
    from app.governor_daily_report import send_daily_report, _collect_day_data

    date_str = request.args.get("date")
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"status": "error", "error": f"Invalid date: {date_str}"}), 400
    else:
        target_date = datetime.now(timezone.utc).date()

    try:
        data = _collect_day_data(target_date)
        has_activity = data.get("events_count", 0) > 0
        send_daily_report(target_date=target_date, notify=True)
        status = "sent" if has_activity else "no_activity"
        return jsonify({"status": status, "date": target_date.isoformat()})
    except Exception as e:
        logger.exception("Failed to generate daily report")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/advisor-scan", methods=["POST"])
def advisor_scan():
    """POST /api/advisor-scan — launch advisor scan in background thread.

    Query params:
        full: if present, run full scan (default: incremental)

    Returns JSON: {"status": "started", "mode": "full"|"incremental"}
    Check progress via GET /api/advisor-scan/status
    """
    import threading
    from app.advisor.indexer import run_full_scan, run_incremental_scan
    from app.advisor.helpers import get_advisor_config

    config = get_advisor_config()
    full = request.args.get("full") is not None

    def _run_scan():
        try:
            if full:
                run_full_scan(config)
            else:
                run_incremental_scan(config)
        except Exception as e:
            logger.exception("Advisor scan failed: %s", e)

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return jsonify({"status": "started", "mode": "full" if full else "incremental"})


@app.route("/api/advisor-scan/status", methods=["GET"])
def advisor_scan_status():
    """GET /api/advisor-scan/status — return scan progress."""
    from app.advisor.indexer import get_scan_progress
    progress = get_scan_progress()
    if not progress:
        return jsonify({"status": "idle"})
    return jsonify(progress)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
