"""Webhook handler for LiteLLM budget alerts.

Registers a Flask route that receives budget event POSTs from LiteLLM
and dispatches formatted alerts via Koan's outbox.md messaging bridge.

Usage:
    from app.budget_controller.webhook_handler import register_webhook
    register_webhook(app, instance_dir)
"""

import json
from datetime import datetime
from pathlib import Path

from flask import Response, request

from app.budget_controller.alert_formatter import format_alert
from app.journal import append_to_journal
from app.utils import append_to_outbox, load_config


def register_webhook(app, instance_dir: Path):
    """Register the LiteLLM budget webhook route on the Flask app.

    Args:
        app: Flask application instance.
        instance_dir: Path to koan instance/ directory.
    """

    @app.route("/webhook/litellm-budget", methods=["POST"])
    def litellm_budget_webhook():
        try:
            payload = request.get_json(force=True)
        except Exception:
            return Response(
                json.dumps({"status": "error", "message": "invalid JSON"}),
                status=400,
                content_type="application/json",
            )

        config = load_config()
        eur_usd_rate = config.get("budget_controller", {}).get("eur_usd_rate", 1.08)
        message = format_alert(payload, eur_usd_rate)

        append_to_outbox(instance_dir / "outbox.md", f"\n{message}\n")
        _log_to_journal(instance_dir, payload)

        return Response(
            json.dumps({"status": "received"}),
            status=200,
            content_type="application/json",
        )


def _log_to_journal(instance_dir: Path, payload: dict):
    """Log the raw webhook event to the daily journal."""
    event = payload.get("event", "unknown")
    user_id = payload.get("user_id", "unknown")
    spend = payload.get("spend", 0)
    max_budget = payload.get("max_budget", 0)
    timestamp = datetime.now().strftime("%H:%M:%S")

    entry = (
        f"\n### [{timestamp}] Budget alert: {event}\n"
        f"- User: {user_id}\n"
        f"- Spend: ${spend:.2f} / ${max_budget:.2f}\n"
    )
    append_to_journal(instance_dir, "governor", entry)
