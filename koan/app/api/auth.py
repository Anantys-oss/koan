"""Bearer token authentication for the Kōan REST API."""

import hmac
import os
from functools import wraps

from flask import current_app, g, request

from app.config import get_api_token


def _get_token() -> str:
    """Resolve API token: env var → config → empty string."""
    return get_api_token()


def check_token(provided: str) -> bool:
    """Constant-time token comparison. Returns False if no token configured."""
    expected = _get_token()
    if not expected:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())


def require_token(f):
    """Decorator: require valid Bearer token. Returns 401/403 on failure."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return (
                {"error": {"code": "missing_token", "message": "Authorization header required"}},
                401,
            )
        token = auth_header[len("Bearer "):]
        if not token:
            return (
                {"error": {"code": "missing_token", "message": "Token is empty"}},
                401,
            )
        if not check_token(token):
            return (
                {"error": {"code": "invalid_token", "message": "Invalid token"}},
                403,
            )
        g.authenticated = True
        return f(*args, **kwargs)

    return decorated
