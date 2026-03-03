"""Google Secret Manager client for Credential Vault.

Wraps the google-cloud-secret-manager SDK for CRUD operations on secrets.
Uses Application Default Credentials (ADC) — never service account key files.
"""

import logging
from typing import Any, Dict, List, Optional

from google.cloud import secretmanager
from google.api_core import exceptions as gcp_exceptions

logger = logging.getLogger("credential_vault.gsm")


class GSMClient:
    """Client for Google Secret Manager operations."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.client = secretmanager.SecretManagerServiceClient()
        self.parent = f"projects/{project_id}"

    @classmethod
    def from_config(cls, config: dict) -> "GSMClient":
        vault_cfg = config.get("vault", {})
        project_id = vault_cfg.get("gcp_project_id", "")
        if not project_id:
            raise ValueError("vault.gcp_project_id is required in config.yaml")
        return cls(project_id)

    def create_secret(self, secret_id: str, labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Create a new secret (without a version/value yet)."""
        secret = {"replication": {"automatic": {}}}
        if labels:
            secret["labels"] = labels
        response = self.client.create_secret(
            request={
                "parent": self.parent,
                "secret_id": secret_id,
                "secret": secret,
            }
        )
        logger.info("Created secret %s", secret_id)
        return {"name": response.name, "labels": dict(response.labels)}

    def add_secret_version(self, secret_id: str, payload: str) -> Dict[str, Any]:
        """Add a new version (value) to an existing secret."""
        name = f"{self.parent}/secrets/{secret_id}"
        response = self.client.add_secret_version(
            request={
                "parent": name,
                "payload": {"data": payload.encode("utf-8")},
            }
        )
        logger.info("Added version %s to %s", response.name.split("/")[-1], secret_id)
        return {"name": response.name, "state": response.state.name}

    def access_secret_version(self, secret_id: str, version: str = "latest") -> str:
        """Read the value of a secret version."""
        name = f"{self.parent}/secrets/{secret_id}/versions/{version}"
        response = self.client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")

    def list_secrets(self, filter_label: Optional[str] = None) -> List[Dict[str, Any]]:
        """List secrets, optionally filtered by label (e.g. 'labels.managed-by=ai-governor')."""
        request = {"parent": self.parent}
        if filter_label:
            request["filter"] = filter_label
        secrets = []
        for secret in self.client.list_secrets(request=request):
            secrets.append({
                "name": secret.name,
                "secret_id": secret.name.split("/")[-1],
                "labels": dict(secret.labels),
                "create_time": secret.create_time.isoformat() if secret.create_time else None,
            })
        return secrets

    def get_secret(self, secret_id: str) -> Optional[Dict[str, Any]]:
        """Get a single secret's metadata (not its value)."""
        name = f"{self.parent}/secrets/{secret_id}"
        try:
            secret = self.client.get_secret(request={"name": name})
            return {
                "name": secret.name,
                "secret_id": secret.name.split("/")[-1],
                "labels": dict(secret.labels),
                "create_time": secret.create_time.isoformat() if secret.create_time else None,
            }
        except gcp_exceptions.NotFound:
            return None

    def disable_secret_version(self, secret_id: str, version: str) -> Dict[str, Any]:
        """Disable a specific version of a secret."""
        name = f"{self.parent}/secrets/{secret_id}/versions/{version}"
        response = self.client.disable_secret_version(request={"name": name})
        logger.info("Disabled version %s of %s", version, secret_id)
        return {"name": response.name, "state": response.state.name}

    def destroy_secret_version(self, secret_id: str, version: str) -> Dict[str, Any]:
        """Destroy a specific version (irreversible)."""
        name = f"{self.parent}/secrets/{secret_id}/versions/{version}"
        response = self.client.destroy_secret_version(request={"name": name})
        logger.warning("Destroyed version %s of %s", version, secret_id)
        return {"name": response.name, "state": response.state.name}

    def delete_secret(self, secret_id: str) -> None:
        """Delete a secret and all its versions (irreversible)."""
        name = f"{self.parent}/secrets/{secret_id}"
        self.client.delete_secret(request={"name": name})
        logger.warning("Deleted secret %s", secret_id)

    def list_secret_versions(self, secret_id: str) -> List[Dict[str, Any]]:
        """List all versions of a secret."""
        name = f"{self.parent}/secrets/{secret_id}"
        versions = []
        for v in self.client.list_secret_versions(request={"parent": name}):
            versions.append({
                "name": v.name,
                "version": v.name.split("/")[-1],
                "state": v.state.name,
                "create_time": v.create_time.isoformat() if v.create_time else None,
            })
        return versions
