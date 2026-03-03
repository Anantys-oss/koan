"""Shared helpers for credential_vault skills and modules."""

from pathlib import Path

import yaml

from app.utils import load_config, KOAN_ROOT, append_to_outbox, atomic_write

INJECTIONS_PATH = "instance/vault_injections.yaml"


def get_config():
    return load_config()


def get_gsm():
    from app.credential_vault.gsm_client import GSMClient
    return GSMClient.from_config(get_config())


def get_vault_config():
    return get_config().get("vault", {})


def get_governors():
    return get_vault_config().get("governors", [])


def notify(message):
    """Write a message to outbox.md with proper file locking."""
    outbox = KOAN_ROOT / "instance" / "outbox.md"
    append_to_outbox(outbox, f"\n{message}\n")


def save_yaml(path, data):
    """Save YAML data atomically to prevent corruption."""
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(Path(path), content)


def load_injections():
    path = KOAN_ROOT / INJECTIONS_PATH
    if not path.exists():
        return {"injections": [], "next_id": 1}
    with open(path) as f:
        return yaml.safe_load(f) or {"injections": [], "next_id": 1}


def save_injections(data):
    path = KOAN_ROOT / INJECTIONS_PATH
    save_yaml(path, data)


def invalidate_injections(data, *, secret_id=None, citizen=None, project=None,
                          reason="revoked", revoked_by="system"):
    """Invalidate active injections matching the given criteria.

    Returns the list of affected injections.
    """
    affected = []
    for inj in data.get("injections", []):
        if inj.get("status") != "active":
            continue
        if secret_id and secret_id not in inj.get("secrets_injected", []):
            continue
        if citizen and inj.get("citizen") != citizen:
            continue
        if project and inj.get("project") != project:
            continue
        inj["status"] = reason
        inj["revoked_by"] = revoked_by
        affected.append(inj)
    return affected
