"""Governor — Periodic report generator.

Aggregates data from all modules (watcher, advisor, budget, vault) into a
PeriodicReport dict for governors. Can be triggered by scheduler or skill.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config

logger = logging.getLogger("governor.report")


def generate_report(period_start: date, period_end: date) -> dict:
    """Generate a periodic report aggregating data from all modules.

    Returns a dict conforming to the PeriodicReport data model.
    """
    report = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_count": 0,
        "detections_count": 0,
        "false_positive_rate": 0.0,
        "budget_spent": {},
        "budget_total": 0.0,
        "credential_alerts": 0,
        "top_citizens": [],
    }

    report["events_count"], citizen_events = _count_watcher_events(period_start, period_end)
    report["top_citizens"] = _build_top_citizens(citizen_events)
    detections, fp_rate = _count_advisor_detections(period_start, period_end)
    report["detections_count"] = detections
    report["false_positive_rate"] = fp_rate
    report["budget_spent"], report["budget_total"] = _get_budget_spend(period_start, period_end)
    report["credential_alerts"] = _count_credential_alerts(period_start, period_end)

    return report


def _count_watcher_events(start: date, end: date) -> tuple[int, dict]:
    """Count watcher events in the JSONL journal for the period."""
    events_dir = INSTANCE_DIR / "watcher" / "events"
    total = 0
    citizen_events: dict[str, int] = {}

    if not events_dir.exists():
        return 0, {}

    current = start
    while current <= end:
        journal_file = events_dir / f"{current.isoformat()}.jsonl"
        if journal_file.exists():
            try:
                for line in open(journal_file):
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        evt = json.loads(line)
                        author = evt.get("author", "")
                        if evt.get("author_type") == "citizen" and author:
                            citizen_events[author] = citizen_events.get(author, 0) + 1
                    except json.JSONDecodeError:
                        pass
            except OSError:
                pass
        current += timedelta(days=1)

    return total, citizen_events


def _build_top_citizens(citizen_events: dict) -> list[dict]:
    """Build top citizens list sorted by event count."""
    top = sorted(citizen_events.items(), key=lambda x: x[1], reverse=True)
    return [{"login": login, "events": count} for login, count in top[:10]]


def _count_advisor_detections(start: date, end: date) -> tuple[int, float]:
    """Count advisor detections and false positive rate for the period."""
    detections_path = INSTANCE_DIR / "advisor" / "detections.yaml"
    if not detections_path.exists():
        return 0, 0.0

    try:
        with open(detections_path) as f:
            data = yaml.safe_load(f) or {}
        all_dets = data.get("detections", [])
    except (yaml.YAMLError, OSError):
        return 0, 0.0

    start_iso = start.isoformat()
    end_iso = (end + timedelta(days=1)).isoformat()

    period_dets = [
        d for d in all_dets
        if start_iso <= d.get("created_at", "")[:10] <= end_iso
    ]

    total = len(period_dets)
    fp = sum(1 for d in period_dets if d.get("status") == "false_positive")
    rate = fp / total if total > 0 else 0.0

    return total, rate


def _get_budget_spend(start: date, end: date) -> tuple[dict, float]:
    """Get budget spend from LiteLLM API for the period."""
    try:
        from app.budget_controller.litellm_client import LiteLLMClient

        config = load_config()
        client = LiteLLMClient.from_config(config)
        eur_usd = config.get("budget_controller", {}).get("eur_usd_rate", 1.08)

        logs = client.get_spend_logs(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

        spend_by_user: dict[str, float] = {}
        for log in logs:
            user = log.get("user", "unknown")
            amount_usd = float(log.get("spend", 0))
            amount_eur = amount_usd / eur_usd
            spend_by_user[user] = spend_by_user.get(user, 0) + amount_eur

        total = sum(spend_by_user.values())
        return spend_by_user, round(total, 2)
    except Exception as e:
        logger.warning("Could not fetch budget spend: %s", e)
        return {}, 0.0


def _count_credential_alerts(start: date, end: date) -> int:
    """Count credential alerts from watcher journal."""
    events_dir = INSTANCE_DIR / "watcher" / "events"
    alerts = 0

    if not events_dir.exists():
        return 0

    current = start
    while current <= end:
        journal_file = events_dir / f"{current.isoformat()}.jsonl"
        if journal_file.exists():
            try:
                for line in open(journal_file):
                    if "credential_detected" in line:
                        alerts += 1
            except OSError:
                pass
        current += timedelta(days=1)

    return alerts
