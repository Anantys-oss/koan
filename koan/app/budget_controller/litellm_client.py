"""LiteLLM Admin API client for Budget Controller.

Wraps the LiteLLM proxy admin REST API for user management,
virtual key management, and spend tracking.

All budget amounts in this module are in USD (LiteLLM native currency).
EUR conversion is handled at the skill/display layer.
"""

import os
from typing import Any, Dict, List, Optional

import requests


class LiteLLMClient:
    """Client for the LiteLLM Proxy admin API."""

    def __init__(self, base_url: str, master_key: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.timeout = timeout

    @classmethod
    def from_config(cls, config: dict) -> "LiteLLMClient":
        """Create a client from budget_controller config section."""
        bc = config.get("budget_controller", {})
        base_url = bc.get("litellm_url", "http://litellm-proxy:4000")
        key_env = bc.get("litellm_master_key_env", "LITELLM_MASTER_KEY")
        master_key = os.environ.get(key_env, "")
        return cls(base_url, master_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make an authenticated request to the LiteLLM API."""
        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", self.timeout)
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # --- Health ---

    def health(self) -> Dict[str, Any]:
        """Check proxy health."""
        return self._request("GET", "/health")

    # --- User management ---

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get user info including spend and budget."""
        return self._request("GET", "/user/info", params={"user_id": user_id})

    def list_users(self) -> List[Dict[str, Any]]:
        """List all users."""
        result = self._request("GET", "/user/list")
        return result if isinstance(result, list) else result.get("users", [])

    def create_user(self, user_id: str, max_budget: Optional[float] = None,
                    budget_duration: str = "30d") -> Dict[str, Any]:
        """Create a new user with optional budget."""
        data: Dict[str, Any] = {
            "user_id": user_id,
            "budget_duration": budget_duration,
        }
        if max_budget is not None:
            data["max_budget"] = max_budget
        return self._request("POST", "/user/new", json=data)

    def update_user(self, user_id: str, **kwargs) -> Dict[str, Any]:
        """Update user properties (max_budget, budget_duration, etc.)."""
        data = {"user_id": user_id, **kwargs}
        return self._request("POST", "/user/update", json=data)

    # --- Key management ---

    def list_keys(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List virtual keys, optionally filtered by user."""
        params = {}
        if user_id:
            params["user_id"] = user_id
        result = self._request("GET", "/key/list", params=params)
        return result if isinstance(result, list) else result.get("keys", [])

    def create_key(self, user_id: str, key_alias: Optional[str] = None,
                   models: Optional[List[str]] = None,
                   max_budget: Optional[float] = None) -> Dict[str, Any]:
        """Generate a virtual key for a user."""
        data: Dict[str, Any] = {"user_id": user_id}
        if key_alias:
            data["key_alias"] = key_alias
        if models:
            data["models"] = models
        if max_budget is not None:
            data["max_budget"] = max_budget
        return self._request("POST", "/key/generate", json=data)

    def block_key(self, key: str) -> Dict[str, Any]:
        """Block (revoke) a virtual key."""
        return self._request("POST", "/key/block", json={"key": key})

    # --- Spend tracking ---

    def get_spend_logs(self, start_date: Optional[str] = None,
                       end_date: Optional[str] = None,
                       user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get spend logs with optional date range and user filter."""
        params: Dict[str, str] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if user_id:
            params["user_id"] = user_id
        result = self._request("GET", "/spend/logs", params=params)
        return result if isinstance(result, list) else []

    def get_spend_by_model(self, start_date: Optional[str] = None,
                           end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get spend aggregated by model."""
        params: Dict[str, str] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        result = self._request("GET", "/global/spend/models", params=params)
        return result if isinstance(result, list) else []
