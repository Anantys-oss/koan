"""Alert formatter for LiteLLM budget webhook events.

Converts raw webhook payloads into human-readable messages
for dispatch via Koan's messaging bridge (outbox.md).
"""

from typing import Any, Dict


EVENT_LABELS = {
    "threshold_crossed": "⚠️ Seuil budget atteint",
    "budget_crossed": "🔴 Budget dépassé",
    "projected_limit_exceeded": "📈 Dépassement projeté",
}


def format_alert(payload: Dict[str, Any], eur_usd_rate: float = 1.08) -> str:
    """Format a LiteLLM webhook payload into a readable alert message.

    Args:
        payload: Raw webhook JSON from LiteLLM.
        eur_usd_rate: Conversion rate for USD→EUR display.

    Returns:
        Formatted alert message string.
    """
    event = payload.get("event", "unknown")
    event_label = EVENT_LABELS.get(event, f"📢 Événement : {event}")
    event_message = payload.get("event_message", "")

    spend_usd = payload.get("spend", 0) or 0
    max_budget_usd = payload.get("max_budget", 0) or 0

    spend_eur = spend_usd / eur_usd_rate if eur_usd_rate else spend_usd
    max_eur = max_budget_usd / eur_usd_rate if eur_usd_rate else max_budget_usd
    remaining_eur = max_eur - spend_eur

    pct = (spend_usd / max_budget_usd * 100) if max_budget_usd > 0 else 0

    user_id = payload.get("user_id", "inconnu")
    name = user_id.split("@")[0].capitalize() if "@" in user_id else user_id
    key_alias = payload.get("key_alias", "")

    lines = [
        event_label,
        "────────────────",
        f"Citizen : {name} ({user_id})",
    ]

    if key_alias:
        lines.append(f"Clé : {key_alias}")

    lines.extend([
        f"Consommé : {spend_eur:.2f} € / {max_eur:.2f} € ({pct:.0f}%)",
        f"Restant : {remaining_eur:.2f} €",
    ])

    if event == "budget_crossed":
        lines.append("")
        lines.append("→ Le citizen est maintenant bloqué.")
        lines.append(f"→ /governor.budget status {user_id}")
    elif event == "threshold_crossed":
        lines.append("")
        lines.append(f"→ /governor.budget status {user_id}")

    if event_message:
        lines.append(f"\nDétail : {event_message}")

    return "\n".join(lines)
