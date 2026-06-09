"""Gogs authentication helpers.

Reads KOAN_GOGS_HOST and KOAN_GOGS_TOKEN from the environment.
Both values are required for any Gogs API interaction.

Env vars:
    KOAN_GOGS_HOST   — Base URL of the Gogs instance (e.g. https://git.example.com)
    KOAN_GOGS_TOKEN  — Personal access token for authentication
"""

import logging
import os
from typing import Dict

log = logging.getLogger(__name__)


def get_gogs_host() -> str:
    """Return the configured Gogs base URL, stripped of trailing slashes.

    Normalizes the value by prepending ``https://`` when no scheme is present
    so callers can safely pass the result to ``urllib.parse.urlparse`` or use
    it directly as an HTTP base URL.  Both ``git.example.com`` and
    ``https://git.example.com`` are accepted in KOAN_GOGS_HOST.
    """
    host = os.environ.get("KOAN_GOGS_HOST", "").rstrip("/")
    if host and not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host


def get_gogs_token() -> str:
    """Return the configured Gogs API token."""
    return os.environ.get("KOAN_GOGS_TOKEN", "")


def get_gogs_auth_headers() -> Dict[str, str]:
    """Return HTTP headers for authenticated Gogs API requests.

    Returns an empty dict when KOAN_GOGS_TOKEN is not set, which will
    result in anonymous (read-only) access to public repos.
    """
    token = get_gogs_token()
    if token:
        return {"Authorization": f"token {token}"}
    log.warning("KOAN_GOGS_TOKEN is not set — Gogs API requests will use anonymous access; write operations will fail")
    return {}


def is_gogs_configured() -> bool:
    """Return True if both KOAN_GOGS_HOST and KOAN_GOGS_TOKEN are set."""
    return bool(get_gogs_host() and get_gogs_token())
