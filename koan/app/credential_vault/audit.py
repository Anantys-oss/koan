"""Audit module — Cloud Audit Logs querying and anomaly detection.

Queries Google Cloud Audit Logs for Secret Manager access events.
Detects anomalies: stale secrets, unauthorized access, unusual volume.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("credential_vault.audit")


def _get_logging_client(project_id: str):
    """Lazy import and init of Cloud Logging client."""
    from google.cloud import logging as cloud_logging
    return cloud_logging.Client(project=project_id)


def query_access_logs(project_id: str, citizen: Optional[str] = None,
                      secret: Optional[str] = None,
                      hours: int = 24) -> List[Dict[str, Any]]:
    """Query Cloud Audit Logs for SecretManager access events."""
    client = _get_logging_client(project_id)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    timestamp_filter = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_parts = [
        'resource.type="secretmanager.googleapis.com/Secret"',
        f'timestamp>="{timestamp_filter}"',
        'protoPayload.methodName="google.cloud.secretmanager.v1.SecretManagerService.AccessSecretVersion"',
    ]

    if secret:
        filter_parts.append(f'protoPayload.resourceName:"{secret}"')

    if citizen:
        filter_parts.append(f'protoPayload.authenticationInfo.principalEmail="{citizen}"')

    log_filter = " AND ".join(filter_parts)

    entries = []
    for entry in client.list_entries(filter_=log_filter, order_by="timestamp desc", page_size=100):
        payload = entry.payload if hasattr(entry, "payload") else {}
        proto = payload if isinstance(payload, dict) else {}

        auth_info = proto.get("authenticationInfo", {})
        resource_name = proto.get("resourceName", "")
        secret_id = resource_name.split("/secrets/")[-1].split("/")[0] if "/secrets/" in resource_name else resource_name

        entries.append({
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
            "principal": auth_info.get("principalEmail", "unknown"),
            "secret": secret_id,
            "method": proto.get("methodName", "").split(".")[-1],
            "status": proto.get("status", {}).get("code", 0),
        })

    return entries


def query_admin_logs(project_id: str, hours: int = 24) -> List[Dict[str, Any]]:
    """Query admin activity logs (create, delete, update secrets)."""
    client = _get_logging_client(project_id)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    timestamp_filter = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    log_filter = (
        'resource.type="secretmanager.googleapis.com/Secret"'
        f' AND timestamp>="{timestamp_filter}"'
        ' AND protoPayload.methodName!="google.cloud.secretmanager.v1.SecretManagerService.AccessSecretVersion"'
    )

    entries = []
    for entry in client.list_entries(filter_=log_filter, order_by="timestamp desc", page_size=100):
        payload = entry.payload if hasattr(entry, "payload") else {}
        proto = payload if isinstance(payload, dict) else {}

        auth_info = proto.get("authenticationInfo", {})
        resource_name = proto.get("resourceName", "")
        method = proto.get("methodName", "").split(".")[-1]

        entries.append({
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
            "principal": auth_info.get("principalEmail", "unknown"),
            "resource": resource_name.split("/")[-1] if "/" in resource_name else resource_name,
            "method": method,
        })

    return entries


def format_audit_entry(entry: Dict[str, Any]) -> str:
    """Format a single audit entry as a readable line."""
    ts = entry.get("timestamp", "?")[:19]
    principal = entry.get("principal", "?")
    method = entry.get("method", "?")
    target = entry.get("secret", entry.get("resource", "?"))
    return f"[{ts}] {principal} → {method} {target}"


def format_audit_summary(project_id: str, citizen: Optional[str] = None,
                         hours: int = 24) -> str:
    """Format a full audit summary for display."""
    try:
        if citizen:
            entries = query_access_logs(project_id, citizen=citizen, hours=hours)
            title = f"Audit de {citizen} (dernières {hours}h)"
        else:
            access = query_access_logs(project_id, hours=hours)
            admin = query_admin_logs(project_id, hours=hours)
            entries = sorted(access + admin, key=lambda e: e.get("timestamp", ""), reverse=True)
            title = f"Audit global (dernières {hours}h)"

        if not entries:
            return f"{title}\n  Aucune activité détectée."

        lines = [title, "─" * len(title)]
        for entry in entries[:50]:
            lines.append(format_audit_entry(entry))

        if len(entries) > 50:
            lines.append(f"... et {len(entries) - 50} autres entrées")

        return "\n".join(lines)

    except ImportError:
        return "google-cloud-logging non installé. Run: pip install google-cloud-logging"
    except Exception as e:
        return f"Erreur lors de la requête d'audit : {e}"


def detect_anomalies(project_id: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect anomalies in secret access patterns."""
    anomalies = []
    alert_stale_days = config.get("alert_stale_days", 90)

    try:
        access_entries = query_access_logs(project_id, hours=alert_stale_days * 24)
    except Exception as e:
        logger.error("Cannot query audit logs: %s", e)
        return anomalies

    accessed_secrets = {e["secret"] for e in access_entries}

    from app.credential_vault import registry
    from app.utils import KOAN_ROOT
    koan_root = str(KOAN_ROOT)
    if koan_root:
        all_secrets = registry.list_secrets(koan_root)
        for s in all_secrets:
            if s["secret_id"] not in accessed_secrets:
                anomalies.append({
                    "type": "stale_secret",
                    "severity": "LOW",
                    "secret_id": s["secret_id"],
                    "message": f"Secret '{s['secret_id']}' non accédé depuis >{alert_stale_days} jours",
                })

    principals = {}
    for e in access_entries:
        p = e["principal"]
        principals[p] = principals.get(p, 0) + 1

    for principal, count in principals.items():
        if count > 100:
            anomalies.append({
                "type": "high_volume",
                "severity": "MEDIUM",
                "principal": principal,
                "count": count,
                "message": f"Volume élevé : {principal} a accédé {count} fois en {alert_stale_days}j",
            })

    return anomalies


def check_stale_secrets(project_id: str, config: Dict[str, Any]) -> str:
    """Check for stale secrets and format alerts. Write to outbox if any found."""
    anomalies = detect_anomalies(project_id, config)
    stale = [a for a in anomalies if a["type"] == "stale_secret"]

    if not stale:
        return "Aucun secret obsolète détecté."

    lines = [f"⚠️ {len(stale)} secret(s) potentiellement obsolète(s)", "─" * 40]
    for s in stale:
        lines.append(f"• {s['secret_id']} — {s['message']}")

    lines.append("")
    lines.append("Actions recommandées : vérifier si ces secrets sont encore nécessaires.")
    lines.append("Si oui, accédez-y pour réinitialiser le compteur. Sinon, /governor.vault revoke <id>")

    return "\n".join(lines)
