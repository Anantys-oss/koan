"""Credential Vault — Google Secret Manager integration for AI Governor.

Provides:
- gsm_client: CRUD operations on Google Secret Manager secrets
- registry: Metadata registry for secrets (vault_registry.yaml)
- grants: Citizen-to-project authorization management (vault_grants.yaml)
- injector: Temporary .env file generation with TTL
- scanner: Credential leak detection via detect-secrets
- audit: Cloud Audit Logs querying and anomaly detection
"""

import logging

__version__ = "0.1.0"

logger = logging.getLogger("credential_vault")
