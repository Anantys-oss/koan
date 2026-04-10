"""Jira notification configuration helpers.

Reads Jira-specific settings from config.yaml (global) for the
notification-driven commands feature.

Config schema in config.yaml:
    jira:
      enabled: false
      base_url: "https://myorg.atlassian.net"
      email: "bot@example.com"
      api_token: ""               # or set KOAN_JIRA_API_TOKEN env var
      nickname: "koan-bot"        # @mention name in Jira comments
      commands_enabled: false
      authorized_users: ["*"]     # Jira account emails or ["*"]
      max_age_hours: 24
      check_interval_seconds: 60
      max_check_interval_seconds: 180
      projects: {}                # Jira project key → Kōan project name
"""

import os
from typing import Dict, List, Optional


def get_jira_enabled(config: dict) -> bool:
    """Check if Jira integration is enabled in config.yaml."""
    jira = config.get("jira") or {}
    return bool(jira.get("enabled", False))


def get_jira_commands_enabled(config: dict) -> bool:
    """Check if Jira notification commands are enabled in config.yaml."""
    jira = config.get("jira") or {}
    return bool(jira.get("commands_enabled", False))


def get_jira_base_url(config: dict) -> str:
    """Get the Jira instance base URL (e.g. https://myorg.atlassian.net)."""
    jira = config.get("jira") or {}
    return str(jira.get("base_url", "")).rstrip("/")


def get_jira_email(config: dict) -> str:
    """Get the Atlassian account email for Basic auth."""
    jira = config.get("jira") or {}
    return str(jira.get("email", ""))


def get_jira_api_token(config: dict) -> str:
    """Get the Jira API token.

    Checks KOAN_JIRA_API_TOKEN env var first, then config.yaml.
    Never logs the token value.
    """
    env_token = os.environ.get("KOAN_JIRA_API_TOKEN", "")
    if env_token:
        return env_token
    jira = config.get("jira") or {}
    return str(jira.get("api_token", ""))


def get_jira_nickname(config: dict) -> str:
    """Get the bot's Jira @mention nickname from config.yaml."""
    jira = config.get("jira") or {}
    return str(jira.get("nickname", "")).strip()


def get_jira_authorized_users(config: dict) -> List[str]:
    """Get the list of authorized Jira users (by account email).

    Returns ["*"] for wildcard (all users), or a list of emails.
    Returns empty list if not configured.
    """
    jira = config.get("jira") or {}
    users = jira.get("authorized_users", [])
    return users if isinstance(users, list) else []


def get_jira_max_age_hours(config: dict) -> int:
    """Get max age in hours for processing Jira comment notifications.

    Comments older than this are ignored (stale protection).
    Default: 24 hours.
    """
    jira = config.get("jira") or {}
    try:
        return int(jira.get("max_age_hours", 24))
    except (ValueError, TypeError):
        return 24


def get_jira_check_interval(config: dict) -> int:
    """Get the minimum interval in seconds between Jira notification checks.

    Controls throttling of Jira API calls.
    Default: 60 seconds.
    """
    jira = config.get("jira") or {}
    try:
        val = int(jira.get("check_interval_seconds", 60))
        return max(10, val)  # Floor at 10s to prevent API abuse
    except (ValueError, TypeError):
        return 60


def get_jira_max_check_interval(config: dict) -> int:
    """Get the maximum backoff interval in seconds for Jira notification checks.

    When consecutive checks find no notifications, the interval grows
    exponentially up to this cap. Default: 180 seconds (3 minutes).
    """
    jira = config.get("jira") or {}
    try:
        val = int(jira.get("max_check_interval_seconds", 180))
        return max(30, val)  # Floor at 30s
    except (ValueError, TypeError):
        return 180


def get_jira_project_map(config: dict) -> Dict[str, str]:
    """Get the mapping of Jira project keys to Kōan project names.

    Supports both simple and extended formats:

        # Simple (string value):
        jira:
          projects:
            FOO: myproject

        # Extended (object value with optional branch):
        jira:
          projects:
            FOO:
              project: myproject
              branch: "11.126"

    Returns:
        Dict of {jira_project_key: koan_project_name}.
    """
    jira = config.get("jira") or {}
    projects = jira.get("projects") or {}
    if not isinstance(projects, dict):
        return {}
    result = {}
    for k, v in projects.items():
        if isinstance(v, dict):
            result[str(k)] = str(v.get("project", ""))
        else:
            result[str(k)] = str(v)
    return result


def get_jira_branch_map(config: dict) -> Dict[str, str]:
    """Get the mapping of Jira project keys to target branches.

    Only returns entries that have an explicit branch configured
    via the extended format:

        jira:
          projects:
            FOO:
              project: myproject
              branch: "11.126"

    Returns:
        Dict of {jira_project_key: branch_name}. Keys without a branch
        are omitted.
    """
    jira = config.get("jira") or {}
    projects = jira.get("projects") or {}
    if not isinstance(projects, dict):
        return {}
    result = {}
    for k, v in projects.items():
        if isinstance(v, dict):
            branch = v.get("branch")
            if branch:
                result[str(k)] = str(branch)
    return result


def validate_jira_config(config: dict) -> Optional[str]:
    """Validate Jira configuration at startup.

    Returns an error message if config is invalid, or None if valid.
    Warns at startup if enabled: true but required fields are missing.
    """
    if not get_jira_enabled(config):
        return None  # Feature disabled, no validation needed

    base_url = get_jira_base_url(config)
    if not base_url:
        return "Jira integration enabled but 'jira.base_url' is not set in config.yaml"

    email = get_jira_email(config)
    if not email:
        return "Jira integration enabled but 'jira.email' is not set in config.yaml"

    api_token = get_jira_api_token(config)
    if not api_token:
        return (
            "Jira integration enabled but 'jira.api_token' is not set "
            "(set in config.yaml or KOAN_JIRA_API_TOKEN env var)"
        )

    nickname = get_jira_nickname(config)
    if not nickname:
        return "Jira integration enabled but 'jira.nickname' is not set in config.yaml"

    return None
