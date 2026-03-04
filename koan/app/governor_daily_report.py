"""Governor daily report — LLM-narrated summary of the day's activity.

Collects data from watcher journal, advisor detections, and budget spend,
then generates a narrative summary via LLM and sends it as a Google Chat Card v2.

Reports are stored in instance/reports/YYYY-MM-DD.yaml for history.
Separate from daily_report.py (Koan's mission/journal report for Telegram).
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config, atomic_write

logger = logging.getLogger("governor.daily_report")

REPORTS_DIR = INSTANCE_DIR / "reports"

NARRATIVE_PROMPT = """\
Tu es l'AI Governor, assistant de gouvernance pour YourArtOfficial.
Génère un rapport de journée concis et utile en français, à partir de ces données.

Date: {date}
Événements totaux: {events_count}
Commits citizens: {citizen_events}
Détections advisor: {detections_count} (taux faux positifs: {fp_rate:.0%})
Alertes credentials: {credential_alerts}
Budget consommé: {budget_total:.2f}€

Top contributeurs:
{top_citizens}

Structure ton rapport en 3 sections:
1. **Résumé de la journée** (2-3 phrases)
2. **Points d'attention** (liste à puces, max 3)
3. **Recommandations pour demain** (liste à puces, max 3)

Si peu d'activité, dis-le simplement. Sois factuel et concis.
"""


def generate_daily_report(target_date: date | None = None,
                          config: dict | None = None) -> str:
    """Generate a daily report narrative.

    Returns:
        Narrative text of the report
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    if config is None:
        config = load_config()

    data = _collect_day_data(target_date)
    narrative = _generate_narrative(data, target_date, config)

    _store_report(target_date, data, narrative)

    return narrative


def _collect_day_data(target_date: date) -> dict:
    """Collect all data for a single day from existing modules."""
    data = {
        "events_count": 0,
        "citizen_events": {},
        "detections_count": 0,
        "fp_rate": 0.0,
        "credential_alerts": 0,
        "budget_spent": {},
        "budget_total": 0.0,
        "top_citizens": [],
    }

    try:
        from app.report_generator import (
            _count_watcher_events, _build_top_citizens,
            _count_advisor_detections, _get_budget_spend,
            _count_credential_alerts,
        )

        events_count, citizen_events = _count_watcher_events(target_date, target_date)
        data["events_count"] = events_count
        data["citizen_events"] = citizen_events
        data["top_citizens"] = _build_top_citizens(citizen_events)

        detections, fp_rate = _count_advisor_detections(target_date, target_date)
        data["detections_count"] = detections
        data["fp_rate"] = fp_rate

        data["budget_spent"], data["budget_total"] = _get_budget_spend(target_date, target_date)
        data["credential_alerts"] = _count_credential_alerts(target_date, target_date)
    except ImportError:
        logger.warning("report_generator not available, using empty data")
    except Exception as e:
        logger.warning("Error collecting day data: %s", e)

    return data


def _generate_narrative(data: dict, target_date: date, config: dict) -> str:
    """Generate narrative via LLM, with fallback to structured text."""
    top_text = "\n".join(
        f"  - {c['login']}: {c['events']} événements"
        for c in data.get("top_citizens", [])[:5]
    ) or "  (aucune activité citizen)"

    prompt = NARRATIVE_PROMPT.format(
        date=target_date.isoformat(),
        events_count=data.get("events_count", 0),
        citizen_events=len(data.get("citizen_events", {})),
        detections_count=data.get("detections_count", 0),
        fp_rate=data.get("fp_rate", 0.0),
        credential_alerts=data.get("credential_alerts", 0),
        budget_total=data.get("budget_total", 0.0),
        top_citizens=top_text,
    )

    try:
        from app.advisor.helpers import summarize_with_llm
        advisor_config = config.get("advisor", {})
        narrative = summarize_with_llm(prompt, advisor_config)
        if narrative:
            return narrative
    except ImportError:
        pass
    except Exception as e:
        logger.warning("LLM narrative generation failed: %s", e)

    return _fallback_narrative(data, target_date)


def _fallback_narrative(data: dict, target_date: date) -> str:
    """Structured text fallback when LLM is unavailable."""
    lines = [
        f"Rapport du {target_date.isoformat()}",
        "",
        f"Événements: {data.get('events_count', 0)}",
        f"Citizens actifs: {len(data.get('citizen_events', {}))}",
        f"Détections advisor: {data.get('detections_count', 0)}",
        f"Alertes credentials: {data.get('credential_alerts', 0)}",
        f"Budget consommé: {data.get('budget_total', 0.0):.2f}€",
    ]

    top = data.get("top_citizens", [])
    if top:
        lines.append("")
        lines.append("Top contributeurs:")
        for c in top[:5]:
            lines.append(f"  - {c['login']}: {c['events']} événements")

    return "\n".join(lines)


def _store_report(target_date: date, data: dict, narrative: str) -> None:
    """Store report to instance/reports/YYYY-MM-DD.yaml."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "events_count": data.get("events_count", 0),
            "citizen_count": len(data.get("citizen_events", {})),
            "detections_count": data.get("detections_count", 0),
            "fp_rate": round(data.get("fp_rate", 0.0), 3),
            "credential_alerts": data.get("credential_alerts", 0),
            "budget_total": round(data.get("budget_total", 0.0), 2),
            "top_citizens": data.get("top_citizens", []),
        },
        "narrative": narrative,
    }
    path = REPORTS_DIR / f"{target_date.isoformat()}.yaml"
    content = yaml.dump(report, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(path, content)
    logger.info("Report stored: %s", path)


def build_report_card(narrative: str, target_date: date) -> list:
    """Build a Google Chat Card v2 for the daily report."""
    return [{
        "cardId": f"report-{target_date.isoformat()}",
        "card": {
            "header": {
                "title": f"Rapport du {target_date.isoformat()}",
                "subtitle": "AI Governor — Rapport journalier",
                "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/summarize/default/24px.svg",
                "imageType": "CIRCLE",
            },
            "sections": [{
                "widgets": [{"textParagraph": {"text": narrative[:3000]}}]
            }],
        },
    }]


def send_daily_report(target_date: date | None = None, notify: bool = True) -> str:
    """Generate and optionally send the daily report.

    Returns the narrative text.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    narrative = generate_daily_report(target_date)

    if notify:
        try:
            from app.governor_cli import send_to_gchat
            send_to_gchat(
                f"Rapport {target_date.isoformat()}",
                narrative,
                thread_key=f"report-{target_date.isoformat()}",
            )
        except ImportError:
            logger.warning("governor_cli not available for sending report")

    return narrative
