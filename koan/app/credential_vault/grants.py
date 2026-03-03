"""Vault Grants — manages vault_grants.yaml.

Controls which citizens are authorized to inject credentials for which projects.
Each grant links a citizen to a project with a specific scope.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.utils import atomic_write

logger = logging.getLogger("credential_vault.grants")

DEFAULT_PATH = "instance/vault_grants.yaml"


def _grants_path(koan_root: str) -> Path:
    return Path(koan_root) / DEFAULT_PATH


def load_grants(koan_root: str) -> Dict[str, Any]:
    path = _grants_path(koan_root)
    if not path.exists():
        return {"grants": []}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if "grants" not in data:
        data["grants"] = []
    return data


def save_grants(koan_root: str, data: Dict[str, Any]) -> None:
    path = _grants_path(koan_root)
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(path, content)
    logger.info("Grants saved to %s", path)


def add_grant(koan_root: str, citizen: str, project: str,
              scope: str = "inject", granted_by: str = "") -> Dict[str, Any]:
    """Grant a citizen access to a project's credentials."""
    data = load_grants(koan_root)
    for g in data["grants"]:
        if g["citizen"] == citizen and g["project"] == project:
            raise ValueError(f"Grant already exists for {citizen} on {project}")
    grant = {
        "citizen": citizen,
        "project": project,
        "scope": scope,
        "granted_by": granted_by,
        "granted_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": None,
    }
    data["grants"].append(grant)
    save_grants(koan_root, data)
    logger.info("Granted %s access to %s (by %s)", citizen, project, granted_by)
    return grant


def remove_grant(koan_root: str, citizen: str, project: str) -> bool:
    """Remove a citizen's access to a project. Returns True if found and removed."""
    data = load_grants(koan_root)
    original_len = len(data["grants"])
    data["grants"] = [
        g for g in data["grants"]
        if not (g["citizen"] == citizen and g["project"] == project)
    ]
    if len(data["grants"]) == original_len:
        return False
    save_grants(koan_root, data)
    logger.info("Revoked %s access to %s", citizen, project)
    return True


def get_grants_for_citizen(koan_root: str, citizen: str) -> List[Dict[str, Any]]:
    data = load_grants(koan_root)
    return [g for g in data["grants"] if g["citizen"] == citizen]


def get_grants_for_project(koan_root: str, project: str) -> List[Dict[str, Any]]:
    data = load_grants(koan_root)
    return [g for g in data["grants"] if g["project"] == project]


def is_authorized(koan_root: str, citizen: str, project: str) -> bool:
    """Check if a citizen is authorized to access a project's credentials."""
    data = load_grants(koan_root)
    return any(
        g["citizen"] == citizen and g["project"] == project
        for g in data["grants"]
    )


def revoke_all_for_citizen(koan_root: str, citizen: str) -> int:
    """Revoke all grants for a citizen. Returns count of removed grants."""
    data = load_grants(koan_root)
    original_len = len(data["grants"])
    data["grants"] = [g for g in data["grants"] if g["citizen"] != citizen]
    removed = original_len - len(data["grants"])
    if removed > 0:
        save_grants(koan_root, data)
        logger.info("Revoked all %d grants for %s", removed, citizen)
    return removed
