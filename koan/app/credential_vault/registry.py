"""Vault Registry — manages vault_registry.yaml.

Stores extended metadata for each secret beyond what GSM labels support.
Source of truth for project mapping, env_var names, and migration history.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.utils import atomic_write

logger = logging.getLogger("credential_vault.registry")

DEFAULT_PATH = "instance/vault_registry.yaml"


def _registry_path(koan_root: str) -> Path:
    return Path(koan_root) / DEFAULT_PATH


def load_registry(koan_root: str) -> Dict[str, Any]:
    path = _registry_path(koan_root)
    if not path.exists():
        return {"secrets": {}}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data


def save_registry(koan_root: str, data: Dict[str, Any]) -> None:
    path = _registry_path(koan_root)
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(path, content)
    logger.info("Registry saved to %s", path)


def add_secret(koan_root: str, secret_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Register a new secret with its metadata."""
    data = load_registry(koan_root)
    if secret_id in data["secrets"]:
        raise ValueError(f"Secret '{secret_id}' already in registry. Use rotate instead.")
    metadata.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    data["secrets"][secret_id] = metadata
    save_registry(koan_root, data)
    return metadata


def get_secret(koan_root: str, secret_id: str) -> Optional[Dict[str, Any]]:
    data = load_registry(koan_root)
    return data["secrets"].get(secret_id)


def list_secrets(koan_root: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    data = load_registry(koan_root)
    results = []
    for sid, meta in data["secrets"].items():
        if project and meta.get("project") != project:
            continue
        results.append({"secret_id": sid, **meta})
    return results


def update_secret(koan_root: str, secret_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    data = load_registry(koan_root)
    if secret_id not in data["secrets"]:
        raise KeyError(f"Secret '{secret_id}' not found in registry")
    data["secrets"][secret_id].update(updates)
    save_registry(koan_root, data)
    return data["secrets"][secret_id]


def remove_secret(koan_root: str, secret_id: str) -> None:
    data = load_registry(koan_root)
    if secret_id not in data["secrets"]:
        raise KeyError(f"Secret '{secret_id}' not found in registry")
    del data["secrets"][secret_id]
    save_registry(koan_root, data)
    logger.info("Removed %s from registry", secret_id)


def get_secrets_for_project(koan_root: str, project: str) -> Dict[str, Dict[str, Any]]:
    """Get all secrets for a specific project as {secret_id: metadata}."""
    data = load_registry(koan_root)
    return {
        sid: meta
        for sid, meta in data["secrets"].items()
        if meta.get("project") == project
    }
