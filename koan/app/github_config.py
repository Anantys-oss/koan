"""GitHub notification configuration helpers.

Reads GitHub-specific settings from config.yaml (global) and projects.yaml
(per-project override) for the notification-driven commands feature.

Config schema in config.yaml:
    notification_polling:
      check_interval_seconds: 60
      max_check_interval_seconds: 300
    github:
      nickname: "koan-bot"
      commands_enabled: true
      authorized_users: ["*"]
      max_age_hours: 24
      reply_enabled: false
      reply_authorized_users: ["*"]   # separate from command permissions
      reply_rate_limit: 5             # max replies per user per hour
      ack_enabled: false              # post acknowledgment when a command is queued (opt-in)
      check_interval_seconds: 60       # optional provider override

Per-project override in projects.yaml:
    projects:
      myproject:
        github:
          authorized_users: ["alice", "bob"]
          reply_authorized_users: ["*"]
"""

import logging
from typing import List, Optional

log = logging.getLogger(__name__)

# Webhook receiver defaults. These live here (not in github_webhook) so the
# dependency flows one way — github_webhook imports github_config, never the
# reverse — avoiding the circular import the lazy imports used to mask.
#
# Port chosen to avoid collision with the dashboard (5001) and common dev
# servers. Host defaults to loopback: tunnels (smee/cloudflared) run on the same
# host and forward to localhost, so the receiver is never directly exposed.
DEFAULT_WEBHOOK_PORT = 8474
DEFAULT_WEBHOOK_HOST = "127.0.0.1"


def get_github_nickname(config: dict) -> str:
    """Get the bot's GitHub @mention nickname from config.yaml.

    Returns empty string if not configured.
    """
    github = config.get("github") or {}
    return str(github.get("nickname", "")).strip()


def get_github_commands_enabled(config: dict) -> bool:
    """Check if GitHub notification commands are enabled in config.yaml."""
    github = config.get("github") or {}
    return bool(github.get("commands_enabled", False))


def get_github_authorized_users(config: dict, project_name: Optional[str] = None,
                                 projects_config: Optional[dict] = None) -> List[str]:
    """Get the list of authorized GitHub users.

    If project_name and projects_config are provided, checks for per-project
    override first. Falls back to global config.yaml setting.

    Returns ["*"] for wildcard (all users), or a list of GitHub usernames.
    Returns empty list if not configured.
    """
    # Check per-project override first
    if project_name and projects_config:
        from app.projects_config import get_project_github_authorized_users
        project_users = get_project_github_authorized_users(projects_config, project_name)
        if project_users:
            return project_users

    # Fall back to global config.yaml
    github = config.get("github") or {}
    users = github.get("authorized_users", [])
    return users if isinstance(users, list) else []


def get_github_natural_language(config: dict, project_name: Optional[str] = None,
                                projects_config: Optional[dict] = None) -> bool:
    """Check if natural-language intent parsing is enabled for GitHub @mentions.

    When enabled, unrecognized commands are sent to Claude for intent
    classification before falling back to error/reply paths.

    Checks per-project override first (via projects.yaml), then falls back
    to global config.yaml setting.  Default: False.
    """
    # Check per-project override first
    if project_name and projects_config:
        from app.projects_config import get_project_github_natural_language
        project_value = get_project_github_natural_language(projects_config, project_name)
        if project_value is not None:
            return project_value

    # Fall back to global config.yaml
    github = config.get("github") or {}
    return bool(github.get("natural_language", False))


def get_github_reply_authorized_users(config: dict, project_name: Optional[str] = None,
                                       projects_config: Optional[dict] = None) -> Optional[List[str]]:
    """Get the list of users authorized to receive AI replies.

    Separate from command authorized_users — allows broader audience for
    read-only replies while keeping command permissions restricted.

    Returns a list of usernames or ["*"] if explicitly configured.
    Returns None if not configured (caller should fall back to authorized_users).
    """
    # Check per-project override first
    if project_name and projects_config:
        from app.projects_config import get_project_github_reply_authorized_users
        project_users = get_project_github_reply_authorized_users(projects_config, project_name)
        if project_users is not None:
            return project_users

    # Fall back to global config.yaml
    github = config.get("github") or {}
    users = github.get("reply_authorized_users")
    if users is None:
        return None
    return users if isinstance(users, list) else None


def get_github_reply_rate_limit(config: dict) -> int:
    """Get the max number of AI replies per user per hour.

    Prevents API quota abuse when replies are open to a broad audience.
    Default: 5. Floor: 1.
    """
    github = config.get("github") or {}
    try:
        val = int(github.get("reply_rate_limit", 5))
        return max(1, val)
    except (ValueError, TypeError):
        return 5


def get_github_reply_enabled(config: dict) -> bool:
    """Check if AI-powered replies to non-command @mentions are enabled.

    When enabled, the bot will generate contextual replies to questions
    from authorized users, rather than only responding to known commands.
    """
    github = config.get("github") or {}
    return bool(github.get("reply_enabled", False))


def get_github_max_age_hours(config: dict) -> int:
    """Get max age in hours for processing notifications.

    Notifications older than this are ignored (stale protection).
    Default: 24 hours.
    """
    github = config.get("github") or {}
    try:
        return int(github.get("max_age_hours", 24))
    except (ValueError, TypeError):
        return 24


def get_github_stale_drain_hours(config: dict) -> int:
    """Get the age threshold for draining stale notifications in multi-instance mode.

    In multi-instance mode, notifications from unregistered repos are
    normally left untouched for sibling instances.  However, notifications
    older than this threshold are safe to mark as read — no sibling will
    process them at that point, and leaving them accumulates cruft that
    can block future @mention detection on the same thread.

    Default: 48 hours.  Set to 0 to disable stale draining entirely.
    """
    github = config.get("github") or {}
    try:
        return int(github.get("stale_drain_hours", 48))
    except (ValueError, TypeError):
        return 48


def get_github_check_interval(config: dict) -> int:
    """Get the minimum interval in seconds between notification checks.

    Controls throttling of GitHub API calls for notification polling.
    Default: 60 seconds.
    """
    from app.notification_config import get_notification_check_interval

    return get_notification_check_interval(config, "github")


def get_github_max_check_interval(config: dict) -> int:
    """Get the maximum backoff interval in seconds for notification checks.

    When consecutive checks find no notifications, the interval grows
    exponentially up to this cap. Default: 300 seconds (5 minutes).
    """
    from app.notification_config import get_notification_max_check_interval

    return get_notification_max_check_interval(config, "github")


def get_github_parallel_workers(config: dict) -> int:
    """Max worker threads for concurrent notification processing.

    During cold start the bot may receive many notifications at once
    (typically 10+ from a 24h lookback). Each notification triggers
    several sequential ``gh`` API calls (fetch comment, check subject
    state, mark read, react). Processing them serially adds 5-20s of
    wall-clock latency during startup.

    Workers >1 process notifications concurrently; the work is I/O bound
    (subprocess + HTTP) so threads scale linearly. Default: 4.
    Floor: 1 (effectively disables parallelism). Ceiling: 16 (above
    that GitHub secondary rate limits become a risk).
    """
    github = config.get("github") or {}
    try:
        val = int(github.get("parallel_workers", 4))
        return max(1, min(16, val))
    except (ValueError, TypeError):
        return 4


def get_github_ack_enabled(config: dict) -> bool:
    """Check if command acknowledgment replies are enabled.

    When enabled, the bot posts a brief reply to the triggering comment
    when a command is queued, so the user knows the bot received it.
    Default: False (opt-in; the emoji reaction already signals receipt).
    """
    github = config.get("github") or {}
    return bool(github.get("ack_enabled", False))


def get_github_subscribe_enabled(config: dict) -> bool:
    """Check if thread subscription monitoring is enabled.

    When enabled, Kōan monitors GitHub threads for new comments and
    queues /reply missions for actionable ones.
    """
    github = config.get("github") or {}
    return bool(github.get("subscribe_enabled", False))


def get_github_subscribe_max_per_cycle(config: dict) -> int:
    """Max subscription notifications to process per polling cycle.

    Prevents excessive API usage when many threads are active.
    Default: 5.
    """
    github = config.get("github") or {}
    try:
        return max(1, int(github.get("subscribe_max_per_cycle", 5)))
    except (ValueError, TypeError):
        return 5


def get_github_max_replies_per_thread(config: dict) -> int:
    """Max bot replies/acks allowed on a single thread per rolling hour.

    Circuit breaker against runaway reply loops: once a thread reaches this
    many bot-posted comments within the window, further acks/errors/replies
    are suppressed (the operator is warned once via Telegram). Set to 0 to
    disable the breaker. Default: 10.
    """
    github = config.get("github") or {}
    try:
        return max(0, int(github.get("max_replies_per_thread_per_hour", 10)))
    except (ValueError, TypeError):
        return 10


def get_github_webhook_enabled(config: dict) -> bool:
    """Check if the push-based webhook receiver is enabled.

    When enabled (and KOAN_GITHUB_WEBHOOK_SECRET is set), the bridge starts a
    local HTTP receiver. Incoming GitHub events trigger an immediate
    notification poll instead of waiting out the polling backoff. Default: off.
    """
    github = config.get("github") or {}
    webhook = github.get("webhook") or {}
    return bool(webhook.get("enabled", False))


def get_github_webhook_port(config: dict) -> int:
    """Port the webhook receiver binds to. Default: 8474.

    A configured value that is non-numeric or outside 1-65535 is rejected and
    the default is used — with a logged warning so the operator can spot the
    typo in startup logs rather than silently binding the wrong port.
    """
    github = config.get("github") or {}
    webhook = github.get("webhook") or {}
    # Distinguish "not configured" (use default silently) from "configured but
    # invalid" (use default *and* warn).
    if "port" not in webhook:
        return DEFAULT_WEBHOOK_PORT
    raw = webhook.get("port")
    try:
        val = int(raw)
        if 1 <= val <= 65535:
            return val
    except (ValueError, TypeError):
        pass
    log.warning(
        "Invalid github.webhook.port %r — must be an integer in 1-65535; "
        "using default %d", raw, DEFAULT_WEBHOOK_PORT,
    )
    return DEFAULT_WEBHOOK_PORT


def get_github_webhook_host(config: dict) -> str:
    """Host the webhook receiver binds to. Default: 127.0.0.1.

    The default is loopback-only because tunnels (smee/cloudflared) run on the
    same host and forward to localhost — the receiver should not be directly
    internet-exposed. Set to "0.0.0.0" only for direct exposure behind your own
    TLS terminator.

    A configured value that is not a non-empty string is rejected and the
    default loopback host is used — with a logged warning, so an operator who
    meant to bind 0.0.0.0 isn't silently left on loopback.
    """
    github = config.get("github") or {}
    webhook = github.get("webhook") or {}
    if "host" not in webhook:
        return DEFAULT_WEBHOOK_HOST
    host = webhook.get("host")
    if isinstance(host, str) and host.strip():
        return host.strip()
    log.warning(
        "Invalid github.webhook.host %r — must be a non-empty string; "
        "using default %s", host, DEFAULT_WEBHOOK_HOST,
    )
    return DEFAULT_WEBHOOK_HOST


def validate_github_config(config: dict) -> Optional[str]:
    """Validate GitHub configuration at startup.

    Returns an error message if config is invalid, or None if valid.
    """
    if not get_github_commands_enabled(config):
        return None  # Feature disabled, no validation needed

    nickname = get_github_nickname(config)
    if not nickname:
        return "GitHub commands enabled but 'github.nickname' is not set in config.yaml"

    warn_reply_wildcard(config)
    return None


def warn_reply_wildcard(config: dict) -> None:
    """Log a warning when reply_enabled + wildcard auth exposes replies to all GitHub users."""
    if not get_github_reply_enabled(config):
        return
    reply_users = get_github_reply_authorized_users(config)
    if reply_users is None:
        reply_users = get_github_authorized_users(config)
    if reply_users == ["*"]:
        log.warning(
            "GitHub reply: authorized to ALL GitHub users with repo write access "
            "— consider restricting reply_authorized_users in config.yaml"
        )
